import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, hashlib
from datetime import datetime
from google.cloud import vision
import yfinance as yf
from PIL import Image
from typing import Dict, List, Optional

# --- I. 數據中心與初始化 (Data & Config Hub) ---

def init_session():
    """初始化工作區狀態"""
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'processed_hashes' not in st.session_state: 
        st.session_state['processed_hashes'] = set()

def calculate_hash(file_content: bytes) -> str:
    """計算檔案 MD5 指紋"""
    return hashlib.md5(file_content).hexdigest()

def get_gspread_client():
    """授權並取得 gspread 客戶端"""
    creds_info = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        creds_info, 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

def load_project_registry() -> Dict[str, str]:
    """
    智慧載入註冊表：自動適應 Google 表單產生的長標題。
    """
    try:
        gc = get_gspread_client()
        registry_id = st.secrets["admin_registry_id"]
        sh = gc.open_by_key(registry_id)
        # 讀取第一張工作表 (表單回應頁面)
        data = sh.get_worksheet(0).get_all_records()
        
        if not data: return {}

        # 智慧尋找對應的 Key 名稱 (解決括號與提示文字問題)
        all_keys = data[0].keys()
        key_name = next((k for k in all_keys if "專案名稱" in k), "專案名稱")
        key_id = next((k for k in all_keys if "試算表 ID" in k), "試算表 ID")
        key_status = next((k for k in all_keys if "啟用狀態" in k), "啟用狀態")

        projects = {}
        for r in data:
            # 取得狀態並統一轉為大寫字串進行比較
            status_val = str(r.get(key_status, "")).strip().upper()
            if status_val == "TRUE":
                p_name = r.get(key_name)
                p_id = r.get(key_id)
                if p_name and p_id:
                    projects[p_name] = p_id
        return projects
    except Exception as e:
        st.error(f"❌ 註冊表判讀失敗。錯誤詳情: {e}")
        return {}

def load_project_users(target_sheet_id: str) -> List[str]:
    """從專案試算表的『人員名單』分頁動態載入人員"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(target_sheet_id)
        try:
            wks = sh.worksheet("人員名單")
            names = wks.col_values(1)[1:]  # 跳過標題
            return [n for n in names if n.strip()]
        except gspread.exceptions.WorksheetNotFound:
            return []
    except Exception:
        return []

def load_all_configs() -> Dict:
    configs = {}
    for f in glob.glob("configs/*.json"):
        if "users.json" in f: continue 
        fn = os.path.splitext(os.path.basename(f))[0]
        display_name = fn.replace("_params", "").capitalize()
        emoji_map = {"dk": "🇩🇰", "es": "🇪🇸", "at": "🇦🇹", "cz": "🇨🇿", "tr": "🇹🇷", "jp": "🇯🇵", "kr": "🇰🇷"}
        prefix = emoji_map.get(fn.split('_')[0].lower(), "🌐")
        with open(f, 'r', encoding='utf-8') as j: 
            configs[f"{prefix} {display_name}"] = json.load(j)
    return configs

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code: str) -> float:
    if currency_code == "TWD": return 1.0
    try:
        ticker = yf.Ticker(f"{currency_code}TWD=X")
        hist = ticker.history(period="5d")
        if not hist.empty: return round(hist['Close'].iloc[-1], 2)
        return 35.0
    except Exception: return 35.0

# --- II. AI 辨識核心邏輯 (Logic Engine) ---
# [此部分邏輯維持穩定，無需變動]

def is_unlikely_item(text: str, params: Dict) -> bool:
    t = text.strip().upper()
    if len(t) < 2: return True
    currencies = ["DKK", "EUR", "SEK", "NOK", "USD"]
    if sum(1 for c in currencies if c in t) >= 1: return True
    if re.search(r'\d{1,2}:\d{2}', t) or re.search(r'\b(AM|PM)\b', t): return True
    headers = params.get("header_headers", [])
    if any(h == t or h in t for h in headers): return True
    return False

def normalize_date_pro(text: str, month_map: Dict, target_year: int):
    t_clean = re.sub(r'\d{1,2}:\d{2}(:\d{2})?', ' ', text)
    t_clean = t_clean.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
    for m_n, m_v in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t_clean = re.sub(rf'\b{m_n}\b', f" {m_v} ", t_clean, flags=re.IGNORECASE)
    for i, line in enumerate(t_clean.splitlines()):
        matches = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        for d_s, m_s, y_s in matches:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == target_year:
                try: return datetime(y, int(m_s), int(d_s)).date(), i
                except: continue
    return datetime.now().date(), -1

def extract_data(text: str, params: Dict, date_idx: int):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    curr_code = params.get('currency_code', 'DKK').upper()
    total_keys, exclude_keys, stop_keys = params.get('keywords', []), params.get('exclude_keywords', []), params.get('stop_keywords', [])

    money_cands = []
    for i, line in enumerate(lines):
        prices = re.findall(r'(-?\d+[.,]\d{2})', line)
        if not prices: continue
        val = float(prices[-1].replace(',', '.'))
        score = i 
        if any(k in line.upper() for k in total_keys): score += 5000 
        if curr_code in line.upper(): score += 1500
        if any(e in line.upper() for e in exclude_keys): score -= 4000
        money_cands.append({'val': val, 'score': score, 'idx': i})

    best = sorted(money_cands, key=lambda x: x['score'], reverse=True)[0] if money_cands else {'val': 0.0, 'idx': len(lines)}
    final_amt, total_idx = best['val'], best['idx']

    start_anchor = 0
    header_anchors = params.get("header_headers", [])
    for i, line in enumerate(lines[:15]):
        if any(h in line.upper() for h in header_anchors):
            start_anchor = i + 1; break
    if 0 <= date_idx < total_idx: start_anchor = max(start_anchor, date_idx + 1)
    if start_anchor == 0: start_anchor = 1

    name_q, price_q = [], []
    for line in lines[start_anchor:total_idx]:
        if any(sk in line.upper() for sk in stop_keys): break
        if any(k in line.upper() for k in total_keys + exclude_keys): continue
        if is_unlikely_item(line, params): continue
        has_text = re.search(r'[A-Za-zÀ-ÿ]{2,}', line)
        prices = re.findall(r'(-?\d+[.,]\d{2})', line)
        if has_text and prices:
            nm = re.sub(r'-?\d+[.,]\d{2}.*', '', line).strip()
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', nm).strip()
            if not is_unlikely_item(nm, params):
                name_q.append(nm); price_q.append(prices[-1])
        elif has_text:
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', line).strip()
            if not is_unlikely_item(nm, params): name_q.append(nm)
        elif prices: price_q.append(prices[-1])

    items = [n for n, p in zip(name_q, price_q)] or name_q
    item_summary = "、".join(list(dict.fromkeys(items))[:3]) + ("等" if len(items) > 3 else "等" if items else "")
    vendor = lines[0] if "ORIGINAL" not in lines[0].upper() else lines[1]
    return vendor, final_amt, item_summary

# --- III. 同步與介面 (Sync & UI) ---

def sync_to_sheets(df: pd.DataFrame, user_name: str, curr_code: str, target_id: str) -> bool:
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(target_id)
        wks = sh.get_worksheet(0)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output = []
        for _, r in df.iterrows():
            base = r["外幣金額"] * r["匯率"]
            uid = hashlib.md5(f"{r['商店名稱']}{r['消費日期']}{r['外幣金額']}".encode()).hexdigest()
            output.append([now_str, user_name, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], curr_code, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"], uid])
        wks.append_rows(output, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        st.error(f"❌ 同步失敗: {e}"); return False

def main():
    st.set_page_config(page_title="考察支出登錄系統", layout="wide")
    init_session()
    TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit?usp=sharing"

    st.title("📊 國外考察支出登錄統計系統")

    project_registry = load_project_registry()

    with st.sidebar:
        st.header("🏢 專案授權管理")
        if project_registry:
            selected_project = st.selectbox("1. 選擇執行專案", list(project_registry.keys()))
            target_sheet_id = project_registry[selected_project]
            project_users = load_project_users(target_sheet_id)
        else:
            st.warning("⚠️ 查無授權專案。"); target_sheet_id = None; project_users = []

        st.markdown("---")
        st.info("💡 沒有您的專案？請複製範本、建立新專案並完成授權。")
        st.link_button("📥 連結範本建立歸屬試算表", TEMPLATE_URL, use_container_width=True)
        st.markdown("---")
        
        st.header("⚙️ 辨識與控制")
        debug_mode = st.checkbox("🔍 OCR 偵錯模式")
        target_year = st.number_input("📅 年度鎖定", value=2025)
        if st.button("清空目前列表", use_container_width=True):
            st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if project_users:
            sel_u = st.selectbox("報帳人員 (來自專案)", project_users + ["其他"])
            final_u = st.text_input("輸入姓名") if sel_u == "其他" else sel_u
        else:
            final_u = st.text_input("報帳人員 (請手動輸入)")
    with c2:
        all_cfg = load_all_configs()
        sel_c = st.selectbox("考察國家", list(all_cfg.keys()))
        p = all_cfg[sel_c]
    with c3:
        f_rate = get_exchange_rate(p['currency_code'])
        m_rate = st.number_input(f"匯率 ({p['currency_code']})", value=float(f_rate), step=0.01, format="%.2f")
    with c4:
        fee_pct = st.number_input("手續費(%)", value=1.5) / 100
        if target_sheet_id:
            st.link_button("📂 開啟專案試算表", f"https://docs.google.com/spreadsheets/d/{target_sheet_id}/edit")

    st.markdown("---")
    files = st.file_uploader("📸 批次上傳收據", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files:
        with st.expander("🖼️ 收據預覽", expanded=True):
            img_cols = st.columns(min(len(files), 4))
            for idx, f in enumerate(files):
                f.seek(0); f_hash = calculate_hash(f.read())
                with img_cols[idx % 4]:
                    st.image(Image.open(f), caption=f"收據 {idx+1}", use_container_width=True)
                    if f_hash in st.session_state['processed_hashes']: st.caption("✅ 已在待處理清單")

    if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
        if not target_sheet_id: st.error("❌ 未選擇有效專案。")
        else:
            new_batch = []; skipped = 0
            try:
                client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
                for f in files:
                    f.seek(0); content = f.read(); f_hash = calculate_hash(content)
                    if f_hash in st.session_state['processed_hashes']:
                        skipped += 1; continue
                    txt = client.document_text_detection(image=vision.Image(content=content)).full_text_annotation.text
                    if debug_mode: st.code(f"--- {f.name} --- \n{txt}")
                    d, d_idx = normalize_date_pro(txt, p.get('month_map', {}), target_year)
                    v, a, it = extract_data(txt, p, d_idx)
                    new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
                    st.session_state['processed_hashes'].add(f_hash)
                if new_batch: st.session_state['data'].extend(new_batch); st.success(f"✅ 成功辨識 {len(new_batch)} 筆！")
                elif skipped > 0: st.info(f"ℹ️ 無新文件增加。")
            except Exception as e: st.error(f"辨識出錯：{e}")

    if st.session_state['data']:
        st.markdown("---")
        df_view = pd.DataFrame(st.session_state['data'])
        edf = st.data_editor(df_view, use_container_width=True, num_rows="dynamic")
        total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
        st.metric(f"本批次預估總金額 ({selected_project})", f"NT$ {int(total_twd):,}")
        if st.button("📤 同步至雲端專案試算表", type="primary", use_container_width=True):
            if sync_to_sheets(edf, final_u, p['currency_code'], target_sheet_id):
                st.toast("✅ 已同步！"); st.balloons(); st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()

if __name__ == "__main__":
    main()