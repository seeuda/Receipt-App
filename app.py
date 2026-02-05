import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json
from datetime import datetime
from google.cloud import vision
import yfinance as yf

# --- 1. 核心工具 ---

def load_all_configs():
    configs = {}
    for f in glob.glob("configs/*.json"):
        if "users.json" in f: continue 
        fn = os.path.splitext(os.path.basename(f))[0]
        dn_map = {"dk_params": "🇩🇰 丹麥", "es_params": "🇪🇸 西班牙", "at_params": "🇦🇹 奧地利", "cz_params": "🇨🇿 捷克", "tr_params": "🇹🇷 土耳其", "jp_params": "🇯🇵 日本", "kr_params": "🇰🇷 南韓"}
        dn = dn_map.get(fn.lower(), fn.replace("_params", "").capitalize())
        with open(f, 'r', encoding='utf-8') as j: configs[dn] = json.load(j)
    return configs

def load_users():
    try:
        with open("configs/users.json", "r", encoding="utf-8") as f:
            return json.load(f).get("users", ["楊欣怡", "王素梅", "鄭乃元"])
    except: return ["楊欣怡", "王素梅", "鄭乃元"]

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code):
    if currency_code == "TWD": return 1.0
    try:
        data = yf.Ticker(f"{currency_code}TWD=X").history(period="1d")
        if not data.empty: return round(data['Close'].iloc[-1], 2)
        d1 = yf.Ticker(f"{currency_code}USD=X").history(period="1d")
        d2 = yf.Ticker("USDTWD=X").history(period="1d")
        return round(d1['Close'].iloc[-1] * d2['Close'].iloc[-1], 2)
    except: return 35.0

# --- 2. 核心辨識邏輯 (空間座標化) ---

def normalize_date_pro(text, month_map, target_year):
    """優先鎖定使用者指定的年度，並回傳日期對應的行索引"""
    lines = [l.strip() for l in text.splitlines()]
    target_year_short = str(target_year)[-2:] # '2025' -> '25'
    
    found_date = datetime.now().date()
    found_idx = -1

    for i, line in enumerate(lines):
        # 預處理：清除時間與符號
        t = re.sub(r'\d{1,2}:\d{2}', ' ', line)
        t = t.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
        for m_n, m_v in month_map.items():
            t = re.sub(rf'\b{m_n}\b', f" {m_v} ", t, flags=re.IGNORECASE)
        
        # 尋找包含指定年度的組合
        matches = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', t)
        for d_s, m_s, y_s in matches:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == int(target_year):
                try:
                    found_date = datetime(y, int(m_s), int(d_s)).date()
                    found_idx = i
                    return found_date, found_idx
                except: continue
    return found_date, found_idx

def extract_data(text, params, date_idx):
    """利用日期行索引 (date_idx) 鎖定品項區間"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    total_keys = ["TOTAL", "PAYMENT", "IALT", "MOMS", "VAT", "DANKORT", "SUM", "AMOUNT"]
    
    # 1. 鎖定總金額與其位置 (total_idx)
    final_amt, total_idx = 0.0, len(lines)
    for i, line in enumerate(lines):
        m = re.findall(r'(-?\d+[.,]\d{2})', line)
        if m and any(k in line.upper() for k in total_keys):
            final_amt = float(m[-1].replace(',', '.'))
            total_idx = i
            break

    # 2. 定義「品項區塊」
    # 根據你的觀察：品項在日期資訊往中間(總計)的一側
    if date_idx != -1 and date_idx < total_idx:
        # 情況 A：日期在上方 (如 Gahon)，從日期後開始抓
        search_range = lines[date_idx+1 : total_idx]
    else:
        # 情況 B：日期在下方 (如 Dubliner) 或沒抓到，從商店名後開始抓
        search_range = lines[1 : total_idx]

    name_q, price_q = [], []
    for line in search_range:
        # 排除雜訊特徵：過長數字(電話/CVR/桌號)
        if len(re.findall(r'\d', line)) > 10 or "CVR" in line.upper() or "TBL" in line.upper():
            continue

        has_text = re.search(r'[A-Za-zÀ-ÿ]{3,}', line)
        prices = re.findall(r'(\d+[.,]\d{2})', line)

        if has_text and prices:
            nm = re.sub(r'\d+[.,]\d{2}.*', '', line)
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', nm).strip()
            if len(nm) > 2: name_q.append(nm); price_q.append(prices[-1])
        elif has_text:
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', line).strip()
            if len(nm) > 2: name_q.append(nm)
        elif prices:
            price_q.append(prices[-1])

    items = [n for n, p in zip(name_q, price_q)]
    item_summary = "、".join(list(dict.fromkeys(items))[:3]) + ("等" if items else "")
    return lines[0], final_amt, item_summary

# --- 3. UI 介面 ---

st.set_page_config(page_title="支出登錄系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")

if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.header("⚙️ 偵錯工具")
    debug_mode = st.checkbox("🔍 顯示 OCR 文本 (含複製鍵)")

with st.expander("👤 步驟 1：基本設定", expanded=True):
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    with c1:
        u_opt = load_users() + ["其他"]
        sel_u = st.selectbox("人員", u_opt)
        final_u = st.text_input("手寫姓名") if sel_u == "其他" else sel_u
    with c2:
        cfg = load_all_configs()
        sel_c = st.selectbox("考察國家", list(cfg.keys()))
        p = cfg[sel_c]
    with c3:
        # 年度預載功能
        target_year = st.number_input("考察年度", value=2025, step=1)
        f_rate = get_exchange_rate(p['currency_code'])
        m_rate = st.number_input(f"匯率 ({p['currency_code']})", value=float(f_rate), step=0.01)
    with c4:
        fee_pct = st.number_input("手續費 (%)", value=1.5, step=0.1) / 100
        st.link_button("📂 查看試算表", "https://docs.google.com/spreadsheets/d/1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM/edit")

st.subheader("📸 步驟 2：上傳收據")
files = st.file_uploader("批次上傳", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files:
    if st.button("🔍 執行全自動辨識", type="primary", use_container_width=True):
        new_batch = []
        creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
        client = vision.ImageAnnotatorClient(credentials=creds)
        for f in files:
            txt = client.document_text_detection(image=vision.Image(content=f.read())).full_text_annotation.text
            if debug_mode:
                st.write(f"📄 **{f.name}**")
                st.code(txt)
            
            # 先跑日期，抓到日期在哪一行
            d, d_idx = normalize_date_pro(txt, p.get('month_map', {}), target_year)
            # 再跑品項，利用日期行索引過濾雜訊
            v, a, it = extract_data(txt, p, d_idx)
            
            new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
        st.session_state['data'] = new_batch

if st.session_state['data']:
    st.markdown("---")
    edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True)
    total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
    st.metric("批次預估總台幣", f"NT$ {int(total_twd):,}")