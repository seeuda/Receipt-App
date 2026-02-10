# Base Indent: 0 spaces
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

def calculate_salted_uid(file_content: bytes, user_name: str) -> str:
    """計算加鹽後的唯一識別碼 (Salted UID) 以支援修正後覆蓋"""
    file_hash = hashlib.md5(file_content).hexdigest()
    return hashlib.md5(f"{file_hash}{user_name}".encode()).hexdigest()

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
        return 35.0
    except Exception: return 35.0

# --- II. 專案管理與動態註冊邏輯 ---

def load_project_registry() -> Dict[str, str]:
    """解析管理總表並讀取專案 """
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
    """註冊新專案，自動處理時間戳記避讓 """
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(st.secrets["admin_registry_id"])
        wks = sh.get_worksheet(0); headers = wks.row_values(1)
        idx_n = headers.index(next(h for h in headers if "專案名稱" in h))
        idx_i = headers.index(next(h for h in headers if "試算表 ID" in h))
        idx_s = headers.index(next(h for h in headers if "啟用狀態" in h))
        if sheet_id in wks.col_values(idx_i + 1): return False
        new_row = [""] * len(headers)
        new_row[idx_n], new_row[idx_i], new_row[idx_s] = name, sheet_id, "TRUE"
        if headers[0] in ["時間戳記", "Timestamp"]:
            new_row[0] = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        return True
    except Exception: return False

def load_project_users(tid: str) -> List[str]:
    """載入專案人員名單 """
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(tid); wks = sh.worksheet("人員名單")
        return [n for n in wks.col_values(1)[1:] if n.strip()]
    except Exception: return []

def load_all_configs() -> Dict:
    """載入多國在地化 JSON 配置檔 """
    configs = {}
    emoji_map = {"tw": "🇹🇼", "jp": "🇯🇵", "de": "🇩🇪", "us": "🇺🇸", "gb": "🇬🇧", "fr": "🇫🇷", "kr": "🇰🇷", "vn": "🇻🇳", "th": "🇹🇭"}
    for f in glob.glob("configs/*.json"):
        iso = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            d = json.load(j); label = f"{emoji_map.get(iso, '🌐')} {d.get('country', iso)}"; configs[label] = d
    return configs

# --- III. 智慧辨識引擎 (語義提取) ---

def normalize_date_pro(text: str, params: Dict, target_year: int):
    """依照在地化參數解析收據日期 """
    order = params.get('date_order', 'YMD')
    t_c = text.replace("/", " ").replace("-", " ").replace(".", " ")
    lines = t_c.splitlines()
    for i, line in enumerate(lines):
        fm = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        for g1, g2, y_s in fm:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == target_year:
                d, m = (int(g1), int(g2)) if order == "DMY" else (int(g2), int(g1))
                try: return datetime(y, m, d).date(), i
                except: continue
    return datetime(target_year, 1, 1).date(), -1

def extract_data(text: str, params: Dict, date_idx: int) -> Tuple[str, float, str]:
    """語義強化提取：商店名稱、金額、參考品項 """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines: return "未知商店", 0.0, ""
    d_sep, t_sep = params.get('decimal_sep', '.'), params.get('thousand_sep', ',')
    addr_re = re.compile(params.get('address_regex', r'(Tel:)'), re.I)
    h_skips = [s.upper() for s in params.get('header_skips', [])]

    shop_name = "未知商店"
    for l in lines:
        if any(gs in l.upper() for gs in h_skips) or addr_re.search(l): continue
        if re.search(r'\d{4}[. /-]\d{1,2}', l): continue
        shop_name = l; break

    cands = []
    for i, line in enumerate(lines):
        prices = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2,3})', line)
        if not prices: continue
        try:
            val = float(prices[-1].replace(t_sep, '').replace(d_sep, '.'))
            score = i + (5000 if any(k in line.upper() for k in params.get('keywords', [])) else 0)
            cands.append({'val': val, 'idx': i, 'score': score})
        except: continue
    best = sorted(cands, key=lambda x: x['score'], reverse=True)[0] if cands else {'val': 0.0, 'idx': len(lines)}

    nq = []
    start = lines.index(shop_name) + 1 if shop_name in lines else 1
    for line in lines[start:best['idx']]:
        if any(sk in line.upper() for sk in params.get('stop_keywords', [])) or addr_re.search(line): break
        clean_l = re.sub(params.get('tax_symbols', r'(\*)'), '', line).strip()
        if len(clean_l) > 1 and not clean_l.replace('.','').replace(',','').replace(" ","").isdigit(): nq.append(clean_l)
    return shop_name, best['val'], "、".join(list(dict.fromkeys(nq))[:3])

# --- IV. 雲端同步引擎 (Upsert 支援) ---

def sync_to_sheets(df: pd.DataFrame, u_n: str, c_c: str, tid: str) -> Tuple[int, int]:
    """同步資料：比對 M 欄 UID，存在則覆蓋，不存在則新增 """
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(tid); wks = sh.get_worksheet(0)
        existing_uids = wks.col_values(13) # M 欄為 UID
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        to_append, upd_count = [], 0
        
        for _, r in df.iterrows():
            uid = r["UID"]
            base = r["外幣金額"] * r["匯率"]
            row = [now, u_n, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], c_c, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"], uid]
            
            if uid in existing_uids:
                row_idx = existing_uids.index(uid) + 1
                wks.update(f"A{row_idx}:M{row_idx}", [row], value_input_option='USER_ENTERED')
                upd_count += 1
            else:
                to_append.append(row)
        
        if to_append: wks.append_rows(to_append, value_input_option='USER_ENTERED')
        return len(to_append), upd_count
    except Exception as e:
        st.error(f"同步異常: {e}"); return 0, 0

# --- V. UI 主程式 (恢復結構對齊版) ---

def main():
    st.set_page_config(page_title="考察支出登錄系統", layout="wide")
    init_session(); all_cfg = load_all_configs(); registry = load_project_registry()

    with st.sidebar:
        # 1. 專案選擇 (頂部)
        st.header("🏢 專案選擇")
        if registry:
            sel_p = st.selectbox("請選擇執行專案", list(registry.keys())); tid = registry[sel_p]
            u_l = load_project_users(tid)
            if not u_l: st.info("ℹ️ 提示：請在開啟試算表後，於『人員名單』分頁填入報帳姓名。")
        else:
            st.warning("⚠️ 查無授權專案。"); tid = None; u_l = []
        
        st.markdown("---")
        # 2. 辨識控制 (中部)
        st.header("⚙️ 辨識控制")
        t_year = st.number_input("📅 年度鎖定", value=2026)
        debug = st.checkbox("🔍 OCR 偵錯模式")
        if st.button("清空目前列表", use_container_width=True):
            st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()
            
        st.markdown("---")
        # 3. 建立與註冊 (底部)
        st.header("🆕 建立新專案")
        st.info("💡 沒有您的專案？請複製範本、建立新專案並完成授權。")
        st.link_button("📥 連結範本建立歸屬試算表", "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit?usp=sharing", use_container_width=True)
        
        with st.expander("註冊新專案至系統"):
            new_p_name = st.text_input("專案名稱")
            new_p_id = st.text_input("試算表 ID")
            if st.button("確認註冊", use_container_width=True) and new_p_name and new_p_id:
                if add_project_to_registry(new_p_name, new_p_id):
                    st.success("✅ 註冊成功，請刷新。"); st.rerun()

    # --- Main UI: 多欄位排版 ---
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_u = st.selectbox("報帳人員", u_l + ["其他"]) if u_l else st.text_input("人員姓名")
        final_u = st.text_input("確認姓名") if sel_u == "其他" else sel_u
    with c2:
        sel_l = st.selectbox("📍 記帳國家", list(all_cfg.keys())); p = all_cfg[sel_l]
    with c3:
        f_r = get_rate_by_date(p['currency_code'], datetime.now().date())
        st.number_input(f"預設匯率 ({p['currency_code']})", value=float(f_r), step=0.01)
    with c4:
        st.write("🔗 快速操作")
        if tid: st.link_button("📂 開啟試算表", f"https://docs.google.com/spreadsheets/d/{tid}/edit", use_container_width=True)

    st.markdown("---")
    files = st.file_uploader("📸 批次上傳收據 (JPG/PNG)", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
        if not tid: st.error("❌ 未選擇專案")
        else:
            try:
                client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
                st.session_state['data'] = [] 
                for f in files:
                    f.seek(0); content = f.read()
                    salted_uid = calculate_salted_uid(content, final_u) # 使用 Salted UID
                    txt = client.document_text_detection(image=vision.Image(content=content)).full_text_annotation.text
                    if debug: st.code(txt)
                    d, d_i = normalize_date_pro(txt, p, t_year)
                    v, a, it = extract_data(txt, p, d_i)
                    a_r = get_rate_by_date(p['currency_code'], d)
                    st.session_state['data'].append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":a_r, "備註":"", "UID": salted_uid})
                st.rerun()
            except Exception as e: st.error(f"辨識異常: {e}")

    if st.session_state['data']:
        st.info("💡 修正錯誤後同步，系統將依據圖片指紋自動更新雲端對應資料。")
        edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True)
        if st.button("📤 同步至雲端", type="primary", use_container_width=True):
            sc, uc = sync_to_sheets(edf, final_u, p['currency_code'], tid)
            st.success(f"✅ 同步完成：新增 {sc} 筆，覆蓋更新 {uc} 筆。")
            st.session_state['data'] = []; st.rerun()

if __name__ == "__main__": main()