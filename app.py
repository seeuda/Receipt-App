import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json
from datetime import datetime
from google.cloud import vision
import yfinance as yf

# --- I. 數據中心 (Data Hub) ---

def load_all_configs():
    """動態載入 configs/*.json 下所有的國家設定"""
    configs = {}
    for f in glob.glob("configs/*.json"):
        if "users.json" in f: continue 
        fn = os.path.splitext(os.path.basename(f))[0]
        dn_map = {
            "dk_params": "🇩🇰 丹麥", "es_params": "🇪🇸 西班牙", "at_params": "🇦🇹 奧地利",
            "cz_params": "🇨🇿 捷克", "tr_params": "🇹🇷 土耳其", "jp_params": "🇯🇵 日本", "kr_params": "🇰🇷 南韓"
        }
        dn = dn_map.get(fn.lower(), fn.replace("_params", "").capitalize())
        with open(f, 'r', encoding='utf-8') as j: 
            configs[dn] = json.load(j)
    return configs

def load_users():
    """只從 JSON 載入報帳人員，不留任何硬編碼名單"""
    try:
        with open("configs/users.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("users", [])
    except:
        return []

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code):
    """獲取匯率 (支援外幣 -> USD -> TWD 交叉換算)"""
    if currency_code == "TWD": return 1.0
    try:
        ticker = f"{currency_code}TWD=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty: return round(data['Close'].iloc[-1], 2)
        c_usd = yf.Ticker(f"{currency_code}USD=X").history(period="1d")
        u_twd = yf.Ticker("USDTWD=X").history(period="1d")
        if not c_usd.empty and not u_twd.empty:
            return round(c_usd['Close'].iloc[-1] * u_twd['Close'].iloc[-1], 2)
        return 35.0
    except: return 35.0

# --- II. AI 智慧引擎 (核心邏輯) ---

def normalize_date_pro(text, month_map, target_year):
    """年度優先鎖定，月份語意轉換"""
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

def is_unlikely_item(text, params):
    """三重防火牆：行政、標題、長數字過濾"""
    t = text.strip().upper()
    if len(t) < 2: return True
    headers = params.get("header_headers", [])
    if any(h == t or h in t for h in headers): return True
    junks = ["DKK", "TOTAL", "IALT", "NET", "MOMS", "VAT", "DANKORT", "AMOUNT"]
    if any(j == t for j in junks): return True
    if re.search(r'\d{4,}', t) and not re.search(r'\d+[.,]\d{2}', t): return True
    if sum(c.isdigit() for c in t) / len(t) > 0.5 and not re.search(r'\d+[.,]\d{2}', t): return True
    return False

def extract_data(text, params, date_idx):
    """權重計分判定與雙隊列縫合"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    curr_code = params.get('currency_code', 'DKK').upper()
    total_keys = params.get('keywords', [])
    exclude_keys = params.get('exclude_keywords', [])

    # A. 權重總額判定 (計分制)
    money_cands = []
    for i, line in enumerate(lines):
        prices = re.findall(r'(-?\d+[.,]\d{2})', line)
        if not prices: continue
        val = float(prices[-1].replace(',', '.'))
        score = i 
        if any(k in line.upper() for k in total_keys): score += 3000
        if curr_code in line.upper(): score += 1500
        if i > 0 and any(k in lines[i-1].upper() for k in total_keys): score += 2000
        if any(e in line.upper() for e in exclude_keys): score -= 1000
        if any(c in line.upper() for c in ["SEK", "EUR", "NOK", "USD"]) and curr_code not in line.upper(): score -= 2500
        money_cands.append({'val': val, 'score': score, 'idx': i})

    best = sorted(money_cands, key=lambda x: x['score'], reverse=True)[0] if money_cands else {'val': 0.0, 'idx': len(lines)}
    final_amt, total_idx = best['val'], best['idx']

    # B. 品項採集 (空間分區與縫合)
    start_anchor = date_idx + 1 if (0 <= date_idx < total_idx) else 1
    header_anchors = params.get("header_headers", [])
    for i, line in enumerate(lines[:15]):
        if any(h in line.upper() for h in header_anchors): start_anchor = i + 1; break

    name_q, price_q = [], []
    for line in lines[start_anchor:total_idx]:
        if any(k in line.upper() for k in total_keys + exclude_keys): continue
        if is_unlikely_item(line, params): continue
        has_text, prices = re.search(r'[A-Za-zÀ-ÿ]{2,}', line), re.findall(r'(-?\d+[.,]\d{2})', line)
        if has_text and prices:
            nm = re.sub(r'-?\d+[.,]\d{2}.*', '', line)
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', nm).strip()
            if not is_unlikely_item(nm, params): name_q.append(nm); price_q.append(prices[-1])
        elif has_text:
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', line).strip()
            if not is_unlikely_item(nm, params): name_q.append(nm)
        elif prices: price_q.append(prices[-1])

    items = [n for n, p in zip(name_q, price_q)] or name_q
    item_summary = "、".join(list(dict.fromkeys(items))[:3]) + ("等" if len(items) > 3 else "等" if items else "")
    vendor = lines[0] if "ORIGINAL" not in lines[0].upper() else lines[1]
    return vendor, final_amt, item_summary

def sync_to_sheets(df, user_name, curr_code):
    try:
        creds_info = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        wks = gspread.authorize(creds).open_by_key("1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM").get_worksheet(0)
        now, output = datetime.now().strftime("%Y-%m-%d %H:%M:%S"), []
        for _, r in df.iterrows():
            base = r["外幣金額"] * r["匯率"]
            output.append([now, user_name, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], curr_code, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"]])
        wks.append_rows(output, value_input_option='USER_ENTERED')
        return True
    except: return False

# --- III. UI 系統 ---

st.set_page_config(page_title="考察支出登錄系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")

if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.header("⚙️ 設定")
    debug_mode = st.checkbox("🔍 偵錯模式")
    target_year = st.number_input("📅 考察年度", value=2025)

c1, c2, c3, c4 = st.columns(4)
with c1:
    u_list = load_users()
    sel_u = st.selectbox("人員", u_list + ["其他"]) if u_list else st.text_input("報帳人姓名")
    final_u = st.text_input("手寫姓名") if sel_u == "其他" else (sel_u if u_list else sel_u)
with c2:
    all_cfg = load_all_configs()
    sel_c = st.selectbox("考察國家", list(all_cfg.keys()))
    p = all_cfg[sel_c]
with c3:
    f_rate = get_exchange_rate(p['currency_code'])
    m_rate = st.number_input(f"匯率", value=float(f_rate), step=0.01)
with c4:
    fee_pct = st.number_input("手續費(%)", value=1.5) / 100
    st.link_button("📂 查看試算表", "https://docs.google.com/spreadsheets/d/1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM/edit")

st.subheader("📸 上傳收據")
files = st.file_uploader("批次選擇圖檔", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files and st.button("🚀 執行 AI 辨識", type="primary", use_container_width=True):
    new_batch = []
    try:
        client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
        for f in files:
            txt = client.document_text_detection(image=vision.Image(content=f.read())).full_text_annotation.text
            if debug_mode: st.code(txt)
            d, d_idx = normalize_date_pro(txt, p.get('month_map', {}), target_year)
            v, a, it = extract_data(txt, p, d_idx)
            new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
        st.session_state['data'] = new_batch
        st.success("✅ 辨識完成！")
    except Exception as e: st.error(f"辨識出錯: {e}")

if st.session_state['data']:
    st.markdown("---")
    edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True)
    total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
    st.metric("本批次總金額", f"NT$ {int(total_twd):,}")
    
    if st.button("📤 同步至雲端試算表", type="primary", use_container_width=True):
        if sync_to_sheets(edf, final_u, p['currency_code']):
            st.toast("同步成功！"); st.balloons()
            st.session_state['data'] = []; st.rerun()