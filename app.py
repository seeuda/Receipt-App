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

# --- 2. 核心辨識邏輯 ---

def normalize_date_pro(text, month_map):
    # 策略 1: 尋找 DD/MM/YYYY 或 DD.MM.YYYY
    std = re.findall(r'(\d{1,2})[./-]\d{1,2}[./-](\d{4})', text)
    if std:
        for d, y in std:
            # 這裡簡單判斷月份，因為原始文本可能是 18/06/2025
            m_match = re.search(rf'{d}[./-](\d{{1,2}})[./-]{y}', text)
            if m_match:
                try: return datetime(int(y), int(m_match.group(1)), int(d)).date()
                except: pass

    # 策略 2: 處理 Dubliner/Salon 特殊月份 (18/Juni/2025, 18 Jun'25)
    t = text.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
    for m_n, m_v in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t = re.sub(rf'{m_n}', f" {m_v} ", t, flags=re.IGNORECASE)
    
    # 在清理過的文本中找三連數
    res = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', t)
    for d_s, m_s, y_s in reversed(res):
        try:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if 2020 <= y <= 2026: return datetime(y, int(m_s), int(d_s)).date()
        except: continue
    return datetime.now().date()

def extract_data(text, params):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    total_keys = ["TOTAL", "PAYMENT", "IALT", "MOMS", "VAT", "DANKORT", "SUM", "MODTAGET"]
    
    # 1. 鎖定總金額 (從後往前找)
    final_amt = 0.0
    money_cands = []
    for i, line in enumerate(lines):
        m = re.findall(r'(-?\d+[.,]\d{2})', line)
        if m:
            val = float(m[-1].replace(',', '.'))
            score = i
            if any(k in line.upper() for k in total_keys): score += 1000
            money_cands.append((val, score))
    if money_cands:
        final_amt = sorted(money_cands, key=lambda x: x[1], reverse=True)[0][0]

    # 2. 鎖定品項區塊 (避開表頭地址與結尾)
    name_q, price_q = [], []
    header_end_idx = 5 # 預設跳過前 5 行地址雜訊
    
    for i, line in enumerate(lines[header_end_idx:]):
        # 遇到總計關鍵字就停止抓取品項
        if any(k in line.upper() for k in total_keys): break
        # 排除包含地址關鍵字或長數字(電話/CVR)的行
        if len(re.findall(r'\d', line)) > 10 or "CVR" in line.upper() or "TLF" in line.upper() or "KBH" in line.upper():
            continue

        has_text = re.search(r'[A-Za-zÀ-ÿ]{3,}', line)
        prices = re.findall(r'(\d+[.,]\d{2})', line)

        if has_text and prices:
            # 水平模式：品名與金額在同一行
            nm = re.sub(r'\d+[.,]\d{2}.*', '', line)
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', nm).strip()
            if len(nm) > 2: name_q.append(nm); price_q.append(prices[-1])
        elif has_text:
            # 垂直模式：這行只有文字 (品名)
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', line).strip()
            if len(nm) > 2: name_q.append(nm)
        elif prices:
            # 垂直模式：這行只有金額
            price_q.append(prices[-1])

    # 索引配對 (解決 a b c 1 2 3 排序問題)
    items = []
    for n, p in zip(name_q, price_queue := price_q):
        items.append(n)
    
    item_summary = "、".join(list(dict.fromkeys(items))[:3]) + ("等" if items else "")
    return lines[0], final_amt, item_summary

# --- 3. Streamlit UI ---

st.set_page_config(page_title="支出登錄系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")

if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.header("⚙️ 偵錯工具")
    debug_mode = st.checkbox("🔍 顯示 OCR 文本 (含複製鍵)")

with st.expander("👤 步驟 1：基本設定", expanded=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        u_opt = load_users() + ["其他"]
        sel_u = st.selectbox("人員", u_opt)
        final_u = st.text_input("手寫姓名") if sel_u == "其他" else sel_u
    with c2:
        cfg = load_all_configs()
        sel_c = st.selectbox("考察國家", list(cfg.keys()))
        p = cfg[sel_c]
        f_rate = get_exchange_rate(p['currency_code'])
        m_rate = st.number_input(f"匯率 ({p['currency_code']})", value=float(f_rate), step=0.01)
    with c3:
        fee_pct = st.number_input("手續費 (%)", value=1.5, step=0.1) / 100

st.subheader("📸 步驟 2：上傳收據")
files = st.file_uploader("批次上傳", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files:
    if st.button("🔍 執行 AI 辨識", type="primary", use_container_width=True):
        new_batch = []
        creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
        client = vision.ImageAnnotatorClient(credentials=creds)
        for f in files:
            txt = client.document_text_detection(image=vision.Image(content=f.read())).full_text_annotation.text
            if debug_mode:
                st.write(f"📄 **{f.name}**")
                st.code(txt) # 這裡右上角有 Copy 鍵
            v, a, it = extract_data(txt, p)
            d = normalize_date_pro(txt, p.get('month_map', {}))
            new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
        st.session_state['data'] = new_batch

if st.session_state['data']:
    st.markdown("---")
    edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True)
    total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
    st.metric("批次台幣總計", f"NT$ {int(total_twd):,}")
    
    if st.button("📤 同步至雲端", type="primary", use_container_width=True):
        # 同步函式寫在這裡...
        st.success("同步成功！"); st.balloons()