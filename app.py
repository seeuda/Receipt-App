import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, hashlib
from datetime import datetime, timedelta
from google.cloud import vision
from PIL import Image
import yfinance as yf
from typing import Dict, List, Tuple, Optional

# --- I. 數據中心與匯率引擎 ---

def init_session() -> None:
    """初始化工作區狀態與參數追蹤"""
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'processed_hashes' not in st.session_state: 
        st.session_state['processed_hashes'] = set()
    if 'last_config_key' not in st.session_state:
        st.session_state['last_config_key'] = ""

def calculate_hash(file_content: bytes) -> str:
    """計算檔案 MD5 指紋"""
    return hashlib.md5(file_content).hexdigest()

def get_gspread_client():
    """安全授權 Google Sheets API"""
    creds_info = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        creds_info, 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=3600)
def get_rate_by_date(currency_code: str, target_date: datetime.date) -> float:
    """依日期抓取歷史匯率"""
    if currency_code == "TWD": return 1.0
    try:
        ticker = yf.Ticker(f"{currency_code}TWD=X")
        sd, ed = target_date, target_date + timedelta(days=3)
        hist = ticker.history(start=sd, end=ed)
        if not hist.empty: return round(hist['Close'].iloc[0], 2)
        fb = ticker.history(period="1mo")
        return round(fb['Close'].asof(pd.Timestamp(target_date)), 2)
    except Exception: return 35.0

# --- II. 專案管理與動態註冊邏輯 ---

def load_project_registry() -> Dict[str, str]:
    """解析管理總表標題索引並讀取專案"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(st.secrets["admin_registry_id"])
        wks = sh.get_worksheet(0)
        data = wks.get_all_records()
        if not data: return {}
        headers = wks.row_values(1)
        k_n = next((h for h in headers if "專案名稱" in h), "專案名稱")
        k_i = next((h for h in headers if "試算表 ID" in h), "試算表 ID")
        k_s = next((h for h in headers if "啟用狀態" in h), "啟用狀態")
        return {r[k_n]: r[k_i] for r in data if str(r.get(k_s, "")).strip().upper() == "TRUE"}
    except Exception: return {}

def add_project_to_registry(name: str, sheet_id: str) -> bool:
    """自動辨識欄位位置並註冊新專案 (相容 Google Form 時間戳記)"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(st.secrets["admin_registry_id"])
        wks = sh.get_worksheet(0)
        headers = wks.row_values(1)
        
        try:
            idx_name = headers.index(next(h for h in headers if "專案名稱" in h))
            idx_id = headers.index(next(h for h in headers if "試算表 ID" in h))
            idx_status = headers.index(next(h for h in headers if "啟用狀態" in h))
        except StopIteration:
            st.error("管理總表欄位格式不符"); return False

        if sheet_id in wks.col_values(idx_id + 1): return False

        new_row = [""] * len(headers)
        new_row[idx_name] = name
        new_row[idx_id] = sheet_id
        new_row[idx_status] = "TRUE"
        
        if headers[0] == "時間戳記" or "Timestamp" in headers[0]:
            new_row[0] = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

        wks.append_row(new_row, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        st.error(f"註冊技術錯誤: {e}"); return False

def load_project_users(tid: str) -> List[str]:
    """載入專案人員名單"""
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(tid); wks = sh.worksheet("人員名單")
        return [n for n in wks.col_values(1)[1:] if n.strip()]
    except Exception: return []

def load_all_configs() -> Dict:
    """載入多國在地化 JSON 配置檔"""
    configs = {}
    emoji_map = {"de": "🇩🇪", "at": "🇦🇹", "ch": "🇨🇭", "cz": "🇨🇿", "pl": "🇵🇱", "tr": "🇹🇷", "gb": "🇬🇧", "fr": "🇫🇷", "nl": "🇳🇱", "be": "🇧🇪", "ie": "🇮🇪", "dk": "🇩🇰", "no": "🇳🇴", "se": "🇸🇪", "fi": "🇫🇮", "is": "🇮🇸", "it": "🇮🇹", "es": "🇪🇸", "pt": "🇵🇹", "gr": "🇬🇷", "tw": "🇹🇼", "jp": "🇯🇵", "kr": "🇰🇷", "sg": "🇸🇬", "vn": "🇻🇳", "th": "🇹🇭", "my": "🇲🇾", "ph": "🇵🇭", "id": "🇮🇩", "in": "🇮🇳", "ae": "🇦🇪", "il": "🇮🇱", "sa": "🇸🇦", "us": "🇺🇸", "ca": "🇨🇦", "br": "🇧🇷", "mx": "🇲🇽", "au": "🇦🇺", "nz": "🇳🇿", "za": "🇿🇦"}
    for f in glob.glob("configs/*.json"):
        iso = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            d = json.load(j); label = f"{emoji_map.get(iso, '🌐')} {d.get('country', iso)}"; configs[label] = d
    return configs

# --- III. 智慧辨識引擎 (語義與過濾強化版) ---

def normalize_date_pro(text: str, params: Dict, target_year: int):
    """依照 date_order 與在地化月份解析日期"""
    m_map = params.get('month_map', {}); order = params.get('date_order', 'YMD')
    t_c = text.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
    for m_n, m_v in sorted(m_map.items(), key=lambda x: len(x[0]), reverse=True):
        t_c = re.sub(rf'\b{m_n}\b', f" {m_v} ", t_c, flags=re.IGNORECASE)
    
    lines = t_c.splitlines()
    for i, line in enumerate(lines):
        fm = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        for g1, g2, y_s in fm:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == target_year:
                d, m = (int(g1), int(g2)) if order == "DMY" else (int(g2), int(g1))
                try: return datetime(y, m, d).date(), i
                except: continue
        pm = re.findall(r'\b(\d{1,2})\s+(\d{1,2})\b', line)
        for g1, g2 in pm:
            d, m = (int(g1), int(g2)) if order == "DMY" else (int(g2), int(g1))
            try:
                if 1 <= m <= 12 and 1 <= d <= 31: return datetime(target_year, m, d).date(), i
            except: continue
    return datetime(target_year, 1, 1).date(), -1

def extract_data(text: str, params: Dict, date_idx: int) -> Tuple[str, float, str]:
    """語義強化提取：精確過濾地址、雜訊標頭並處理分組金額"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines: return "未知商店", 0.0, ""
    
    d_sep, t_sep = params.get('decimal_sep', '.'), params.get('thousand_sep', ',')
    addr_re = re.compile(params.get('address_regex', r'(Tel:)|(Fax:)'), re.I)
    tax_re = re.compile(params.get('tax_symbols', r'(\*)'), re.I)
    h_skips = [s.upper() for s in params.get('header_skips', [])]

    # 1. 商店名稱辨識
    shop_name = "未知商店"
    for l in lines:
        l_up = l.upper()
        if re.search(r'\d{4}[. /-]\d{1,2}', l): continue
        if any(gs in l_up for gs in h_skips): continue
        if addr_re.search(l): continue
        if l.isdigit() and len(l) <= 6: continue
        shop_name = l; break

    # 2. 金額提取
    cands = []
    for i, line in enumerate(lines):
        prices = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2,3})', line)
        if not prices: continue
        v_s = prices[-1].replace(t_sep, '').replace(d_sep, '.')
        try:
            val = float(v_s); sc = i 
            if any(k in line.upper() for k in params.get('keywords', [])): sc += 5000 
            cands.append({'val': val, 'score': sc, 'idx': i})
        except: continue
    best = sorted(cands, key=lambda x: x['score'], reverse=True)[0] if cands else {'val': 0.0, 'idx': len(lines)}
    f_amt, t_idx = best['val'], best['idx']

    # 3. 品項摘要提取 (嚴格排除地址與雜訊日期)
    name_q, start_idx = [], lines.index(shop_name) + 1 if shop_name in lines else 1
    for line in lines[start_idx:t_idx]:
        if any(sk in line.upper() for sk in params.get('stop_keywords', [])): break
        if addr_re.search(line): continue
        if re.search(r'\d{4}[. /-]\d{1,2}', line): continue
        clean_l = tax_re.sub('', line).strip()
        if len(clean_l) > 1 and not clean_l.replace('.','').replace(',','').replace(" ","").isdigit():
            name_q.append(clean_l)

    summary = "、".join(list(dict.fromkeys(name_q))[:3]) + ("等" if len(name_q) > 3 else "")
    return shop_name, f_amt, summary

def sync_to_sheets(df: pd.DataFrame, u_n: str, c_c: str, tid: str) -> Tuple[int, int]:
    """資料同步並標定 UID 於第 13 欄 (M)"""
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(tid); wks = sh.get_worksheet(0)
        uids = set(wks.col_values(13)[1:]); now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        to_app, skip = [], 0
        for _, r in df.iterrows():
            uid = hashlib.md5(f"{r['商店名稱']}{r['消費日期']}{r['外幣金額']}".encode()).hexdigest()
            if uid in uids: skip += 1; continue
            base = r["外幣金額"] * r["匯率"]
            # 寫入 13 欄位 A-M，UID 置於末端
            to_app.append([now, u_n, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], c_c, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"], uid])
        if to_app: wks.append_rows(to_app, value_input_option='USER_ENTERED')
        return len(to_app), skip
    except Exception as e:
        st.error(f"同步異常: {e}"); return 0, 0

# --- IV. UI 主程式 (Sidebar 排版優化) ---

def main():
    st.set_page_config(page_title="考察支出登錄系統", layout="wide")
    init_session(); all_cfg = load_all_configs(); registry = load_project_registry()

    with st.sidebar:
        # 1. 專案選擇 (優先)
        st.header("🏢 專案選擇")
        if registry:
            sel_p = st.selectbox("請選擇執行專案", list(registry.keys())); tid = registry[sel_p]
            u_l = load_project_users(tid)
            if not u_l: st.info("ℹ️ 提示：請在開啟試算表後，於『人員名單』分頁人工填入報帳姓名。")
        else:
            st.warning("⚠️ 查無授權專案。"); tid = None; u_l = []
        
        st.markdown("---")

        # 2. 辨識控制 (中部)
        st.header("⚙️ 辨識控制")
        t_year = st.number_input("📅 年度鎖定", value=2025)
        debug = st.checkbox("🔍 OCR 偵錯模式")
        if st.button("清空目前列表", use_container_width=True):
            st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()
            
        st.markdown("---")

        # 3. 建立新專案 (底部)
        st.header("🆕 建立新專案")
        # 恢復提示框資訊
        st.info("💡 沒有您的專案？請複製範本、建立新專案並完成授權。")
        
        st.link_button("📥 連結範本建立歸屬試算表", "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit?usp=sharing", use_container_width=True)
        
        with st.expander("註冊新專案至系統"):
            new_p_name = st.text_input("專案名稱 (例: 2024德國考察)")
            new_p_id = st.text_input("試算表 ID")
            if st.button("確認註冊", use_container_width=True):
                if new_p_name and new_p_id:
                    if add_project_to_registry(new_p_name, new_p_id):
                        st.success("✅ 註冊成功，請刷新頁面。"); st.rerun()
                    else: st.error("❌ 註冊失敗 (ID 重複或格式錯誤)")
                else: st.warning("請填寫完整資訊")

    # --- Main UI ---
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_u = st.selectbox("報帳人員", u_l + ["其他"]) if u_l else st.text_input("人員姓名")
        final_u = st.text_input("確認姓名") if sel_u == "其他" else sel_u

    with c2:
        reg_map = {}
        for l, cfg in all_cfg.items():
            rk = cfg['sub_region']
            if rk not in reg_map: reg_map[rk] = []
            reg_map[rk].append((l, cfg))
        sorted_rk = sorted(reg_map.keys(), key=lambda x: 0 if "東亞/東南亞" in x else 1)
        sel_reg = st.selectbox("🌍 區域範圍", sorted_rk)
        s_c = sorted(reg_map[sel_reg], key=lambda x: (x[1].get('priority', 100), x[0]))
        sel_l = st.selectbox("📍 記帳國家", [i[0] for i in s_c]); p = next(i[1] for i in s_c if i[0] == sel_l)
        
        # 參數變更檢測刷新快取
        cur_k = f"{sel_l}_{t_year}"
        if st.session_state['last_config_key'] != cur_k:
            st.session_state['processed_hashes'] = set(); st.session_state['last_config_key'] = cur_k

    with c3:
        f_r = get_rate_by_date(p['currency_code'], datetime.now().date())
        st.number_input(f"預設匯率 ({p['currency_code']})", value=float(f_r), step=0.01)
    with c4:
        st.number_input("手續費(%)", value=1.5 if p['currency_code'] != "TWD" else 0.0) / 100
        if tid: st.link_button("📂 開啟試算表", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

    st.markdown("---")
    files = st.file_uploader("📸 批次上傳收據 (JPG/PNG)", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files:
        with st.expander("🖼️ 影像預覽與狀態", expanded=False):
            img_c = st.columns(5)
            for idx, f in enumerate(files):
                f.seek(0); f_b = f.read(); f_h = calculate_hash(f_b)
                with img_c[idx % 5]:
                    st.image(Image.open(io.BytesIO(f_b)), use_container_width=True)
                    st.caption(f"#{idx+1} {'⚠️ 已辨識' if f_h in st.session_state['processed_hashes'] else '🟢 待'}")

    if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
        if not tid: st.error("❌ 未選擇專案")
        else:
            try:
                client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
                st.session_state['data'] = [] 
                for f in files:
                    f.seek(0); content = f.read(); f_h = calculate_hash(content)
                    txt = client.document_text_detection(image=vision.Image(content=content)).full_text_annotation.text
                    if debug: st.code(txt)
                    d, d_i = normalize_date_pro(txt, p, t_year)
                    v, a, it = extract_data(txt, p, d_i)
                    a_r = get_rate_by_date(p['currency_code'], d)
                    st.session_state['data'].append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":a_r, "備註":""})
                    st.session_state['processed_hashes'].add(f_h)
                st.rerun()
            except Exception as e: st.error(f"辨識異常: {e}")

    if st.session_state['data']:
        edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True)
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("🔄 依日期重抓匯率", use_container_width=True):
                for i, row in edf.iterrows(): edf.at[i, '匯率'] = get_rate_by_date(p['currency_code'], row['消費日期'])
                st.session_state['data'] = edf.to_dict('records'); st.rerun()
        with bc2:
            if st.button("📤 同步至雲端", type="primary", use_container_width=True):
                sc, sk = sync_to_sheets(edf, final_u, p['currency_code'], tid)
                if sc > 0 or sk > 0:
                    st.success(f"✅ 同步 {sc} 筆，跳過重複 {sk} 筆。")
                    st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()

if __name__ == "__main__": main()