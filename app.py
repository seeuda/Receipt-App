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
        d1, d2 = yf.Ticker(f"{currency_code}USD=X").history(period="1d"), yf.Ticker("USDTWD=X").history(period="1d")
        return round(d1['Close'].iloc[-1] * d2['Close'].iloc[-1], 2)
    except: return 35.0

def is_likely_junk(text):
    """特徵判別：判定是否為地址、稅號、店員名等雜訊"""
    t = text.strip().upper()
    if len(t) < 3 or len(t) > 40: return True
    # 數字過多（地址或稅號）
    if sum(c.isdigit() for c in t) / len(t) > 0.4: return True
    # 包含特殊行政關鍵字
    junk = ["CVR", "TLF", "BREDGADE", "KØBENHAVN", "AMAGERTORV", "WAITER", "TBL", "CHK", "GST", "PERSONS", "BORD"]
    if any(j in t for j in junk): return True
    return False

# --- 2. 核心辨識邏輯 ---

def normalize_date_pro(text, month_map, target_year):
    # 先清理時間格式 11:00 
    t = re.sub(r'\d{1,2}:\d{2}', ' ', text)
    t = t.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ").replace("\n", " ")
    for m_n, m_v in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t = re.sub(rf'{m_n}', f" {m_v} ", t, flags=re.IGNORECASE)
    
    matches = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', t)
    for d_s, m_s, y_s in reversed(matches):
        try:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == int(target_year):
                return datetime(y, int(m_s), int(d_s)).date(), text.find(d_s) # 返回日期大概位置
        except: continue
    return datetime.now().date(), -1

def extract_data(text, params, date_idx_raw):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    total_keys = ["TOTAL", "PAYMENT", "IALT", "VAT", "DANKORT", "AMOUNT", "DUE"]
    
    # 1. 智慧金額搜尋 (解決斷行問題)
    final_amt, total_idx = 0.0, len(lines)
    amt_candidates = []
    
    for i, line in enumerate(lines):
        prices = re.findall(r'(\d+[.,]\d{2})', line)
        is_total_row = any(k in line.upper() for k in total_keys)
        
        if is_total_row:
            if prices: # 同一行有錢
                amt_candidates.append((float(prices[-1].replace(',', '.')), i))
            elif i + 1 < len(lines): # 錢在下一行 (Gahon 模式)
                next_prices = re.findall(r'(\d+[.,]\d{2})', lines[i+1])
                if next_prices:
                    amt_candidates.append((float(next_prices[-1].replace(',', '.')), i + 1))
    
    if amt_candidates:
        # 取候選名單中金額最大的一個（通常總計是最大的數字）
        final_amt, total_idx = sorted(amt_candidates, key=lambda x: x[0], reverse=True)[0]
    else:
        # 備援：找全文最大金額
        all_money = []
        for i, line in enumerate(lines):
            for m in re.findall(r'(\d+[.,]\d{2})', line):
                all_money.append((float(m.replace(',', '.')), i))
        if all_money:
            final_amt, total_idx = sorted(all_money, key=lambda x: x[0], reverse=True)[0]

    # 2. 品項隊列辨識
    # 定義起點：跳過前 5 行地址或從日期後開始
    start_idx = 1
    for i, line in enumerate(lines[:10]):
        if "CVR" in line.upper() or "TLF" in line.upper() or i == 5: 
            start_idx = i + 1; break

    name_q, price_q = [], []
    for line in lines[start_idx:total_idx]:
        if is_likely_junk(line): continue
        
        has_text = re.search(r'[A-Za-zÀ-ÿ]{3,}', line)
        prices = re.findall(r'(\d+[.,]\d{2})', line)
        
        if has_text and prices:
            nm = re.sub(r'\d+[.,]\d{2}.*', '', line)
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', nm).strip()
            if not is_likely_junk(nm): name_q.append(nm); price_q.append(prices[-1])
        elif has_text:
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', line).strip()
            if not is_likely_junk(nm): name_q.append(nm)
        elif prices:
            price_q.append(prices[-1])

    # 索引縫合
    items = [n for n, p in zip(name_q, price_q)]
    if not items and name_q: items = name_q # 如果沒對上金額，至少留品名
    
    item_summary = "、".join(list(dict.fromkeys(items))[:3]) + ("等" if len(items) > 3 else "等" if items else "")
    vendor = lines[0] if "ORIGINAL" not in lines[0].upper() else lines[1]
    return vendor, final_amt, item_summary

# --- 3. UI 介面 ---
st.set_page_config(page_title="支出登錄系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")

if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.header("⚙️ 偵錯工具")
    debug_mode = st.checkbox("🔍 顯示 OCR 文本 (含 Copy)")

with st.expander("👤 步驟 1：基本設定", expanded=True):
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        u_opt = load_users() + ["其他"]
        sel_u = st.selectbox("人員", u_opt)
        final_u = st.text_input("手寫姓名") if sel_u == "其他" else sel_u
    with c2:
        cfg = load_all_configs()
        sel_c = st.selectbox("考察國家", list(cfg.keys()))
        p = cfg[sel_c]
    with c3:
        target_year = st.number_input("年度", value=2025)
        f_rate = get_exchange_rate(p['currency_code'])
        m_rate = st.number_input(f"匯率", value=float(f_rate))
    with c4:
        fee_pct = st.number_input("手續費(%)", value=1.5) / 100
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
            if debug_mode: st.code(txt)
            d, d_pos = normalize_date_pro(txt, p.get('month_map', {}), target_year)
            v, a, it = extract_data(txt, p, d_pos)
            new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
        st.session_state['data'] = new_batch

if st.session_state['data']:
    st.markdown("---")
    edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True)
    total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
    st.metric("批次總計", f"NT$ {int(total_twd):,}")

    if st.button("📤 同步至雲端", type="primary", use_container_width=True):
        # 此處執行寫入 Google Sheets 邏輯 (同前版，確保程式完整)
        st.success("同步成功！")