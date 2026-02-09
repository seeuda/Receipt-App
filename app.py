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

# --- I. 核心數據與匯率引擎 ---

def init_session() -> None:
    if 'data' not in st.session_state: st.session_state['data'] = []
    if 'processed_hashes' not in st.session_state: st.session_state['processed_hashes'] = set()
    if 'last_config_key' not in st.session_state: st.session_state['last_config_key'] = ""

def calculate_hash(file_content: bytes) -> str:
    return hashlib.md5(file_content).hexdigest()

def get_gspread_client():
    creds_info = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        creds_info, 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=3600)
def get_rate_by_date(currency_code: str, target_date: datetime.date) -> float:
    if currency_code == "TWD": return 1.0
    try:
        ticker = yf.Ticker(f"{currency_code}TWD=X")
        sd, ed = target_date, target_date + timedelta(days=3)
        hist = ticker.history(start=sd, end=ed)
        if not hist.empty: return round(hist['Close'].iloc[0], 2)
        fallback = ticker.history(period="1mo")
        return round(fallback['Close'].asof(pd.Timestamp(target_date)), 2)
    except Exception: return 35.0

def load_project_registry() -> Dict[str, str]:
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(st.secrets["admin_registry_id"])
        data = sh.get_worksheet(0).get_all_records()
        if not data: return {}
        k_n = next((k for k in data[0].keys() if "專案名稱" in k), "專案名稱")
        k_i = next((k for k in data[0].keys() if "試算表 ID" in k), "試算表 ID")
        k_s = next((k for k in data[0].keys() if "啟用狀態" in k), "啟用狀態")
        return {r[k_n]: r[k_i] for r in data if str(r.get(k_s, "")).strip().upper() == "TRUE"}
    except Exception: return {}

def load_project_users(target_sheet_id: str) -> List[str]:
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(target_sheet_id)
        wks = sh.worksheet("人員名單")
        names = wks.col_values(1)[1:] 
        return [n for n in names if n.strip()]
    except Exception: return []

def load_all_configs() -> Dict:
    configs = {}
    emoji_map = {
        "de": "🇩🇪", "at": "🇦🇹", "ch": "🇨🇭", "cz": "🇨🇿", "pl": "🇵🇱", "tr": "🇹🇷",
        "gb": "🇬🇧", "fr": "🇫🇷", "nl": "🇳🇱", "be": "🇧🇪", "ie": "🇮🇪", "dk": "🇩🇰",
        "no": "🇳🇴", "se": "🇸🇪", "fi": "🇫🇮", "is": "🇮🇸", "it": "🇮🇹", "es": "🇪🇸",
        "pt": "🇵🇹", "gr": "🇬🇷", "tw": "🇹🇼", "jp": "🇯🇵", "kr": "🇰🇷", "sg": "🇸🇬",
        "vn": "🇻🇳", "th": "🇹🇭", "my": "🇲🇾", "ph": "🇵🇭", "id": "🇮🇩", "in": "🇮🇳",
        "ae": "🇦🇪", "il": "🇮🇱", "sa": "🇸🇦", "us": "🇺🇸", "ca": "🇨🇦", "br": "🇧🇷",
        "mx": "🇲🇽", "au": "🇦🇺", "nz": "🇳🇿", "za": "🇿🇦"
    }
    for f in glob.glob("configs/*.json"):
        iso = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            data = json.load(j)
            label = f"{emoji_map.get(iso, '🌐')} {data.get('country', iso)}"
            configs[label] = data
    return configs

# --- II. 智慧辨識引擎 (在地化解耦版) ---

def normalize_date_pro(text: str, params: Dict, target_year: int):
    """依照 date_order 參數智慧解析日期"""
    month_map = params.get('month_map', {})
    order = params.get('date_order', 'YMD')
    
    t_clean = text.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
    for m_n, m_v in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t_clean = re.sub(rf'\b{m_n}\b', f" {m_v} ", t_clean, flags=re.IGNORECASE)
    
    lines = t_clean.splitlines()
    for i, line in enumerate(lines):
        fm = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        for g1, g2, y_s in fm:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == target_year:
                # 根據國家日期順序分配 Day/Month
                d, m = (int(g1), int(g2)) if order == "DMY" else (int(g2), int(g1))
                try: return datetime(y, m, d).date(), i
                except: continue
        
        pm = re.findall(r'\b(\d{1,2})\s+(\d{1,2})\b', line)
        for g1, g2 in pm:
            d, m = (int(g1), int(g2)) if order == "DMY" else (int(g2), int(g1))
            try:
                if 1 <= m <= 12 and 1 <= d <= 31:
                    return datetime(target_year, m, d).date(), i
            except: continue
    return datetime(target_year, 1, 1).date(), -1

def extract_data(text: str, params: Dict, date_idx: int) -> Tuple[str, float, str]:
    """依照在地化標記提取金額與過濾雜訊"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    d_sep = params.get('decimal_sep', '.')
    t_sep = params.get('thousand_sep', ',')
    addr_re = re.compile(params.get('address_regex', r'(Tel:)|(Fax:)'), re.I)
    tax_re = re.compile(params.get('tax_symbols', r'(\*)'), re.I)
    
    cands = []
    for i, line in enumerate(lines):
        # 匹配包含小數或千分位的數字模式
        prices = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2,3})', line)
        if not prices: continue
        # 轉換為標準 float 格式
        v_s = prices[-1].replace(t_sep, '').replace(d_sep, '.')
        try:
            val = float(v_s); sc = i 
            if any(k in line.upper() for k in params.get('keywords', [])): sc += 5000 
            cands.append({'val': val, 'score': sc, 'idx': i})
        except: continue
    
    best = sorted(cands, key=lambda x: x['score'], reverse=True)[0] if cands else {'val': 0.0, 'idx': len(lines)}
    final_amt, t_idx = best['val'], best['idx']

    name_q = []
    for line in lines[1:t_idx]:
        if any(sk in line.upper() for sk in params.get('stop_keywords', [])): break
        if addr_re.search(line): continue
        if re.search(r'\d{4}[. /-]\d{1,2}', line): continue
        # 移除品項名稱旁的稅務標記雜訊
        clean_line = tax_re.sub('', line).strip()
        if len(clean_line) > 1: name_q.append(clean_line)

    summary = "、".join(list(dict.fromkeys(name_q))[:3]) + ("等" if len(name_q) > 3 else "")
    shop_name = lines[0] if not re.search(r'\d{4}[. /-]\d{1,2}', lines[0]) else "未知商店"
    return shop_name, final_amt, summary

def sync_to_sheets(df: pd.DataFrame, u_n: str, c_c: str, t_id: str) -> Tuple[int, int]:
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(t_id)
        wks = sh.get_worksheet(0)
        uids = set(wks.col_values(13)[1:]) 
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        to_app, skip = [], 0
        for _, r in df.iterrows():
            uid = hashlib.md5(f"{r['商店名稱']}{r['消費日期']}{r['外幣金額']}".encode()).hexdigest()
            if uid in uids: skip += 1; continue
            base = r["外幣金額"] * r["匯率"]
            to_app.append([now, u_n, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], c_c, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"], uid])
        if to_app: wks.append_rows(to_app, value_input_option='USER_ENTERED')
        return len(to_app), skip
    except Exception: return 0, 0

# --- III. 主程式 ---

def main():
    st.set_page_config(page_title="考察支出登錄系統", layout="wide")
    init_session()
    TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit?usp=sharing"
    all_cfg = load_all_configs()
    registry = load_project_registry()

    with st.sidebar:
        st.header("🏢 專案授權管理")
        if registry:
            sel_p = st.selectbox("1. 選擇執行專案", list(registry.keys())); tid = registry[sel_p]
            u_l = load_project_users(tid)
        else:
            st.warning("⚠️ 查無授權專案。"); tid = None; u_l = []
        st.markdown("---")
        st.header("⚙️ 辨識控制")
        t_year = st.number_input("📅 年度鎖定", value=2025)
        debug = st.checkbox("🔍 OCR 偵錯模式")
        if st.button("清空列表", use_container_width=True):
            st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()

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
        c_in_r = reg_map[sel_reg]
        s_c = sorted(c_in_r, key=lambda x: (x[1].get('priority', 100), x[0]))
        f_l = [i[0] for i in s_c]
        sel_l = st.selectbox("📍 記帳國家", f_l); p = next(i[1] for i in s_c if i[0] == sel_l)

        # 參數變更檢測
        current_key = f"{sel_l}_{t_year}"
        if st.session_state['last_config_key'] != current_key:
            st.session_state['processed_hashes'] = set(); st.session_state['last_config_key'] = current_key

    with c3:
        f_r = get_rate_by_date(p['currency_code'], datetime.now().date())
        m_r = st.number_input(f"預設匯率 ({p['currency_code']})", value=float(f_r), step=0.01)
    with c4:
        fee = st.number_input("手續費(%)", value=1.5 if p['currency_code'] != "TWD" else 0.0) / 100
        if tid: st.link_button("📂 開啟試算表", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

    st.markdown("---")
    files = st.file_uploader("📸 批次上傳", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files:
        with st.expander("🖼️ 影像預覽", expanded=False):
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
            except Exception as e: st.error(f"辨識錯誤: {e}")

    if st.session_state['data']:
        st.markdown("### 📝 暫存編輯區")
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
                    st.success(f"✅ 同步 {sc} 筆，跳過重複 {sk} 筆。"); st.session_state['data'] = []
                    st.session_state['processed_hashes'] = set(); st.rerun()

if __name__ == "__main__": main()