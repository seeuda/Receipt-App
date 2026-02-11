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
from typing import Dict, List, Tuple, Optional, Any

# --- I. 數據中心與財務引擎 ---

def init_session() -> None:
    """初始化工作區狀態"""
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'diagnostics' not in st.session_state:
        st.session_state['diagnostics'] = []
    if 'processed_hashes' not in st.session_state: 
        st.session_state['processed_hashes'] = set()

def calculate_salted_uid(file_content: bytes, user_name: str) -> str:
    """計算 MD5(File + Name) 以實現個人化覆蓋邏輯"""
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

# --- II. 專案管理與區域分類邏輯 ---

def load_project_registry() -> Dict[str, str]:
    """讀取管理總表"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(st.secrets["admin_registry_id"])
        wks = sh.get_worksheet(0)
        data = wks.get_all_records()
        headers = wks.row_values(1)
        k_n = next((h for h in headers if "專案名稱" in h), "專案名稱")
        k_i = next((h for h in headers if "試算表 ID" in h), "試算表 ID")
        k_s = next((h for h in headers if "啟用狀態" in h), "啟用狀態")
        return {r[k_n]: r[k_i] for r in data if str(r.get(k_s, "")).strip().upper() == "TRUE"}
    except Exception: return {}

def add_project_to_registry(name: str, sheet_id: str) -> bool:
    """註冊新專案，自動處理標題索引位移"""
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(st.secrets["admin_registry_id"])
        wks = sh.get_worksheet(0); h = wks.row_values(1)
        idx_n, idx_i, idx_s = h.index(next(x for x in h if "專案名稱" in x)), h.index(next(x for x in h if "試算表 ID" in x)), h.index(next(x for x in h if "啟用狀態" in x))
        if sheet_id in wks.col_values(idx_i + 1): return False
        new_row = [""] * len(h)
        new_row[idx_n], new_row[idx_i], new_row[idx_s] = name, sheet_id, "TRUE"
        if h[0] in ["時間戳記", "Timestamp"]: new_row[0] = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        wks.append_row(new_row, value_input_option='USER_ENTERED'); return True
    except: return False

def load_all_configs() -> Dict:
    """載入多國配置與 Emoji 映射"""
    configs = {}
    emoji_map = {"tw": "🇹🇼", "jp": "🇯🇵", "kr": "🇰🇷", "sg": "🇸🇬", "vn": "🇻🇳", "th": "🇹🇭", "my": "🇲🇾", "ph": "🇵🇭", "id": "🇮🇩", "in": "🇮🇳", "ae": "🇦🇪", "il": "🇮🇱", "sa": "🇸🇦", "de": "🇩🇪", "at": "🇦🇹", "ch": "🇨🇭", "cz": "🇨🇿", "pl": "🇵🇱", "tr": "🇹🇷", "gb": "🇬🇧", "fr": "🇫🇷", "nl": "🇳🇱", "be": "🇧🇪", "ie": "🇮🇪", "dk": "🇩🇰", "no": "🇳🇴", "se": "🇸🇪", "fi": "🇫🇮", "is": "🇮🇸", "it": "🇮🇹", "es": "🇪🇸", "pt": "🇵🇹", "gr": "🇬🇷", "us": "🇺🇸", "ca": "🇨🇦", "br": "🇧🇷", "mx": "🇲🇽", "au": "🇦🇺", "nz": "🇳🇿", "za": "🇿🇦"}
    for f in glob.glob("configs/*.json"):
        iso = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            d = json.load(j); d['emoji'] = emoji_map.get(iso, "🌐"); configs[iso] = d
    return configs

# --- III. 地址特徵辨識與結構化診斷 ---

def is_address_feature(line: str, params: Dict) -> bool:
    """基於文字特徵判定是否為地址，而非依賴標題字眼"""
    addr_re = re.compile(params.get('address_regex', r'(Tel:)|(Fax:)'), re.I)
    if addr_re.search(line): return True
    if re.search(r'\b\d{5}\b', line) or re.search(r'\b\d{3}-\d{4}\b', line): return True
    digit_ratio = sum(c.isdigit() for c in line) / (len(line) + 1)
    symbol_density = sum(c in ",-/#." for c in line)
    if digit_ratio > 0.3 and symbol_density >= 2: return True
    return False

def classify_diagnose(lines: List[str], params: Dict) -> List[Dict]:
    """語義標記分類器"""
    diagnostics = []
    d_sep, t_sep = params.get('decimal_sep', '.'), params.get('thousand_sep', ',')
    pay_re = re.compile(r'(VISA|MASTER|CASH|現金|卡號|CHANGE|TENDERED)', re.I)
    tax_re = re.compile(r'(TAX|VAT|GST|MWST|稅|税)', re.I)
    total_re = re.compile(r'(' + '|'.join(params.get('keywords', ['TOTAL', 'SUM'])) + r')', re.I)
    for i, line in enumerate(lines):
        line = line.strip(); label = "ITEM"
        prices = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', line)
        if is_address_feature(line, params): label = "ADDRESS"
        elif any(h.upper() in line.upper() for h in params.get('header_skips', [])): label = "HEADER"
        elif pay_re.search(line): label = "PAYMENT"
        elif tax_re.search(line): label = "TAX"
        elif total_re.search(line): label = "TOTAL"
        elif i == 0: label = "SHOP"
        diagnostics.append({"row": i, "content": line, "label": label, "has_price": "Yes" if prices else "No"})
    return diagnostics

def extract_structured_data(text: str, params: Dict, target_year: int) -> Tuple[Dict, List]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines: return {"shop": "未知", "amount": 0.0, "items": "無", "date": datetime.now().date()}, []
    tags = classify_diagnose(lines, params)
    d_sep, t_sep = params.get('decimal_sep', '.'), params.get('thousand_sep', ',')
    shop = next((t['content'] for t in tags if t['label'] == "SHOP"), "未知商店")
    amount = 0.0
    total_rows = [t for t in tags if t['label'] == "TOTAL"]
    if total_rows:
        p_list = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', total_rows[0]['content'])
        if p_list: amount = float(p_list[-1].replace(t_sep, '').replace(d_sep, '.'))
    if amount == 0.0:
        all_p = []
        for t in tags:
            p_l = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', t['content'])
            for p in p_l: all_p.append(float(p.replace(t_sep, '').replace(d_sep, '.')))
        if all_p: amount = max(all_p)
    items = [t['content'] for t in tags if t['label'] == "ITEM" and len(t['content']) > 2]
    date_val = datetime(target_year, 1, 1).date()
    t_c = text.replace("/", " ").replace("-", " ").replace(".", " ")
    for line in t_c.splitlines():
        fm = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        if fm:
            g1, g2, y_s = fm[0]; y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == target_year:
                order = params.get('date_order', 'YMD')
                d, m = (int(g1), int(g2)) if order == "DMY" else (int(g2), int(g1))
                try: date_val = datetime(y, m, d).date(); break
                except: continue
    return {"shop": shop, "amount": amount, "items": "、".join(list(dict.fromkeys(items))[:3]), "date": date_val}, tags

# --- IV. 雲端同步與財務計算 ---

def sync_to_sheets(df: pd.DataFrame, u_n: str, c_c: str, tid: str, fee_rate: float) -> Tuple[int, int]:
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(tid); wks = sh.get_worksheet(0)
        uids = wks.col_values(13); now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        to_app, upd_count = [], 0
        for _, r in df.iterrows():
            uid = r["UID"]; base = r["外幣金額"] * r["匯率"]
            fee = base * fee_rate; total = base + fee
            row = [now, u_n, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], c_c, r["匯率"], round(base,0), round(fee,0), round(total,0), r["備註"], uid]
            if uid in uids:
                idx = uids.index(uid) + 1; wks.update(f"A{idx}:M{idx}", [row], value_input_option='USER_ENTERED'); upd_count += 1
            else: to_app.append(row)
        if to_app: wks.append_rows(to_app, value_input_option='USER_ENTERED')
        return len(to_app), upd_count
    except Exception as e: st.error(f"同步異常: {e}"); return 0, 0

# --- V. UI 主程式 ---

def main():
    st.set_page_config(page_title="考察支出登錄系統", layout="wide"); init_session()
    all_cfg = load_all_configs(); registry = load_project_registry()

    with st.sidebar:
        # 1. 專案選擇 (頂部) 
        st.header("🏢 專案選擇")
        if registry:
            sel_p = st.selectbox("請選擇執行專案", list(registry.keys())); tid = registry[sel_p]
            try:
                gc = get_gspread_client(); sh = gc.open_by_key(tid); wks = sh.worksheet("人員名單")
                u_l = [n for n in wks.col_values(1)[1:] if n.strip()]
            except: u_l = []
        else: st.warning("⚠️ 查無專案"); tid, u_l = None, []
        
        st.markdown("---")
        # 2. 辨識控制 (中部) 
        st.header("⚙️ 辨識控制")
        t_year = st.number_input("📅 年度鎖定", value=2026); debug = st.checkbox("🔍 OCR 診斷模式")
        if st.button("清空目前列表", use_container_width=True):
            st.session_state['data'], st.session_state['diagnostics'] = [], []; st.rerun()
            
        st.markdown("---")
        # 3. 建立、註冊與回報連結 (底部) 
        st.header("🆕 建立新專案")
        st.link_button("📥 範本連結", "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit?usp=sharing", use_container_width=True)
        with st.expander("註冊新專案至系統"):
            n_p, i_p = st.text_input("專案名稱"), st.text_input("試算表 ID")
            if st.button("確認註冊") and n_p and i_p:
                if add_project_to_registry(n_p, i_p): st.success("註冊成功"); st.rerun()
        
        st.markdown("---")
        st.header("🆘 客服支援")
        st.link_button("💬 小幫手問題回報中心", "https://line.me/ti/g/twX_HfMGBd", use_container_width=True)

    # --- Main UI 4 欄排版 --- 
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_u = st.selectbox("報帳人員", u_l + ["其他"]) if u_l else st.text_input("人員姓名")
        final_u = st.text_input("確認姓名") if sel_u == "其他" else sel_u
    with c2:
        reg_map = {}
        for iso, cfg in all_cfg.items():
            rk = cfg.get('sub_region', '其他')
            if rk not in reg_map: reg_map[rk] = []
            reg_map[rk].append((f"{cfg['emoji']} {cfg['country']}", cfg))
        sel_reg = st.selectbox("🌍 區域範圍", sorted(reg_map.keys()))
        sel_c_name = st.selectbox("📍 記帳國家", [c[0] for c in reg_map[sel_reg]])
        p = next(c[1] for c in reg_map[sel_reg] if c[0] == sel_c_name)
    with c3:
        f_r = get_rate_by_date(p['currency_code'], datetime.now().date())
        cur_rate = st.number_input(f"匯率 ({p['currency_code']})", value=float(f_r), step=0.01)
    with c4:
        fee_rate = st.number_input("手續費 (%)", value=1.5 if p['currency_code'] != "TWD" else 0.0) / 100
        if tid: st.link_button("📂 開啟試算表", f"https://docs.google.com/spreadsheets/d/{tid}/edit", use_container_width=True)

    st.markdown("---")
    files = st.file_uploader("📸 批次上傳收據", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files:
        with st.expander("🖼️ 影像預覽與狀態", expanded=True): [cite: 3]
            img_c = st.columns(5)
            for idx, f in enumerate(files):
                f.seek(0); content = f.read(); uid_check = calculate_salted_uid(content, final_u)
                with img_c[idx % 5]:
                    st.image(Image.open(io.BytesIO(content)), use_container_width=True)
                    st.caption(f"#{idx+1} {'⚠️ 已辨識' if any(d['UID'] == uid_check for d in st.session_state['data']) else '🟢 待處理'}")

    if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
        if not tid: st.error("❌ 未選擇專案")
        else:
            try:
                client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
                st.session_state['data'], st.session_state['diagnostics'] = [], []
                for f in files:
                    f.seek(0); content = f.read(); salted_uid = calculate_salted_uid(content, final_u)
                    txt = client.document_text_detection(image=vision.Image(content=content)).full_text_annotation.text
                    res, diag = extract_structured_data(txt, p, t_year)
                    st.session_state['data'].append({"商店名稱": res['shop'], "參考品項": res['items'], "消費日期": res['date'], "外幣金額": res['amount'], "匯率": cur_rate, "備註": "", "UID": salted_uid})
                    if debug: st.session_state['diagnostics'].append({"file": f.name, "report": diag})
                st.rerun()
            except Exception as e: st.error(f"辨識異常: {e}")

    if debug and st.session_state['diagnostics']:
        for r in st.session_state['diagnostics']:
            with st.expander(f"🛠️ 診斷報告: {r['file']}"): st.table(r['report'])

    if st.session_state['data']:
        edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True)
        if st.button("📤 同步至雲端", type="primary", use_container_width=True):
            sc, uc = sync_to_sheets(edf, final_u, p['currency_code'], tid, fee_rate)
            st.success(f"✅ 同步完成：新增 {sc} 筆，覆蓋更新 {uc} 筆。"); st.session_state['data'] = []; st.rerun()

if __name__ == "__main__": main()