import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, hashlib
from datetime import datetime
from google.cloud import vision
import yfinance as yf
from PIL import Image

# --- I. 數據中心與初始化 ---

def init_session():
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'processed_hashes' not in st.session_state: 
        st.session_state['processed_hashes'] = set()

def calculate_hash(file_content):
    return hashlib.md5(file_content).hexdigest()

def load_all_configs():
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

def load_users():
    try:
        with open("configs/users.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("users", [])
    except Exception: return []

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code):
    """強化版匯率抓取：擴大查詢區間以因應休市問題"""
    if currency_code == "TWD": return 1.0
    try:
        ticker_symbol = f"{currency_code}TWD=X"
        # 使用 5d 確保在週末也能抓到週五的價格
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="5d")
        if not hist.empty:
            return round(hist['Close'].iloc[-1], 2)
        
        # 交叉匯率備援 (Currency -> USD -> TWD)
        c_usd = yf.Ticker(f"{currency_code}USD=X").history(period="5d")
        u_twd = yf.Ticker("USDTWD=X").history(period="5d")
        if not c_usd.empty and not u_twd.empty:
            return round(c_usd['Close'].iloc[-1] * u_twd['Close'].iloc[-1], 2)
        return 35.0
    except Exception:
        return 35.0

# --- II. AI 引擎 (過濾與提取) ---

def is_unlikely_item(text, params):
    t = text.strip().upper()
    if len(t) < 2: return True
    currencies = ["DKK", "EUR", "SEK", "NOK", "USD"]
    if sum(1 for c in currencies if c in t) >= 1: return True
    if re.search(r'\d{1,2}:\d{2}', t) or re.search(r'\b(AM|PM)\b', t): return True
    months_pattern = "|".join([m.upper() for m in params.get("month_map", {}).keys() if len(m) >= 2])
    if months_pattern and re.search(rf'\b({months_pattern})\b', t): return True
    headers = params.get("header_headers", [])
    if any(h == t or h in t for h in headers): return True
    return False

def normalize_date_pro(text, month_map, target_year):
    t_clean = re.sub(r'\d{1,2}:\d{2}(:\d{2})?', ' ', text)
    t_clean = t_clean.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
    for m_n, m_v in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t_clean = re.sub(rf'\b{m_n}\b', f" {m_v} ", t_clean, flags=re.IGNORECASE)
    for i, line in enumerate(t_clean.splitlines()):
        matches = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        for d_s, m_s, y_s in matches:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == int(target_year):
                try: return datetime(y, int(m_s), int(d_s)).date(), i
                except: continue
    return datetime.now().date(), -1

def extract_data(text, params, date_idx):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    curr_code = params.get('currency_code', 'DKK').upper()
    total_keys = params.get('keywords', [])
    exclude_keys = params.get('exclude_keywords', [])
    stop_keys = params.get('stop_keywords', [])

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
            nm = re.sub(r'-?\d+[.,]\d{2}.*', '', line)
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', nm).strip()
            if not is_unlikely_item(nm, params):
                name_q.append(nm); price_q.append(prices[-1])
        elif has_text:
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', line).strip()
            if not is_unlikely_item(nm, params): name_q.append(nm)
        elif prices:
            price_q.append(prices[-1])

    items = [n for n, p in zip(name_q, price_q)] or name_q
    item_summary = "、".join(list(dict.fromkeys(items))[:3]) + ("等" if len(items) > 3 else "等" if items else "")
    vendor = lines[0] if "ORIGINAL" not in lines[0].upper() else lines[1]
    return vendor, final_amt, item_summary

# --- III. 同步與介面 ---

def sync_to_sheets(df, user_name, curr_code):
    try:
        creds_info = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        sh = gspread.authorize(creds).open_by_key("1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM")
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
        st.error(f"同步失敗：{e}")
        return False

# --- Streamlit UI ---

st.set_page_config(page_title="考察支出登錄系統", layout="wide")
init_session()

st.title("📊 國外考察支出登錄統計系統")

with st.sidebar:
    st.header("⚙️ 辨識與控制")
    debug_mode = st.checkbox("🔍 OCR 偵錯模式")
    target_year = st.number_input("📅 年度鎖定", value=2025)
    if st.button("🧹 清空目前列表", type="secondary", use_container_width=True):
        st.session_state['data'] = []
        st.session_state['processed_hashes'] = set()
        st.rerun()

c1, c2, c3, c4 = st.columns(4)
with c1:
    u_list = load_users()
    sel_u = st.selectbox("報帳人員", u_list + ["其他"]) if u_list else st.text_input("人員姓名")
    final_u = st.text_input("輸入姓名") if sel_u == "其他" else (sel_u if u_list else sel_u)
with c2:
    all_cfg = load_all_configs()
    sel_c = st.selectbox("考察國家", list(all_cfg.keys()))
    p = all_cfg[sel_c]
with c3:
    # 這裡會觸發新的 get_exchange_rate 邏輯
    f_rate = get_exchange_rate(p['currency_code'])
    m_rate = st.number_input(f"匯率 ({p['currency_code']})", value=float(f_rate), step=0.01, format="%.2f")
with c4:
    fee_pct = st.number_input("手續費(%)", value=1.5) / 100
    st.link_button("📂 試算表連結", "https://docs.google.com/spreadsheets/d/1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM/edit")

st.markdown("---")
files = st.file_uploader("📸 批次上傳收據", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files:
    with st.expander("🖼️ 收據預覽 (已過濾重複)", expanded=True):
        img_cols = st.columns(min(len(files), 4))
        for idx, f in enumerate(files):
            f.seek(0)
            f_hash = calculate_hash(f.read())
            with img_cols[idx % 4]:
                st.image(Image.open(f), caption=f"收據 {idx+1}", use_container_width=True)
                if f_hash in st.session_state['processed_hashes']:
                    st.caption("✅ 已在待處理清單中")

if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
    new_batch = []
    skipped = 0
    try:
        client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
        for f in files:
            f.seek(0)
            content = f.read()
            f_hash = calculate_hash(content)
            
            if f_hash in st.session_state['processed_hashes']:
                skipped += 1
                continue
            
            txt = client.document_text_detection(image=vision.Image(content=content)).full_text_annotation.text
            if debug_mode: st.code(txt)
            d, d_idx = normalize_date_pro(txt, p.get('month_map', {}), target_year)
            v, a, it = extract_data(txt, p, d_idx)
            
            new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
            st.session_state['processed_hashes'].add(f_hash)
        
        # 修正：確保 new_batch 有內容才進行累加，避免 ghost rows
        if new_batch:
            st.session_state['data'].extend(new_batch)
            st.success(f"✅ 新增 {len(new_batch)} 筆辨識結果！")
        elif skipped > 0:
            st.info(f"ℹ️ 本次上傳皆為重複文件，未新增任何數據。")
            
    except Exception as e:
        st.error(f"辨識出錯：{e}")

if st.session_state['data']:
    st.markdown("---")
    # 將 Session State 轉為 DataFrame 供 Data Editor 使用
    df_view = pd.DataFrame(st.session_state['data'])
    edf = st.data_editor(df_view, use_container_width=True, num_rows="dynamic")
    
    total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
    st.metric("本批次總金額 (含手續費)", f"NT$ {int(total_twd):,}")
    
    if st.button("📤 同步至雲端試算表", type="primary", use_container_width=True):
        if sync_to_sheets(edf, final_u, p['currency_code']):
            st.toast("同步成功！資料已排入雲端。")
            st.balloons()
            st.session_state['data'] = []
            st.session_state['processed_hashes'] = set() # 同步完後清空，讓下次可以重新辨識
            st.rerun()