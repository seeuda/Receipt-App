import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json
from datetime import datetime
from google.cloud import vision
import yfinance as yf

# --- I. 基礎配置與工具函式 ---

def load_all_configs():
    """動態載入國家設定檔"""
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
    """載入報帳人員名單"""
    try:
        with open("configs/users.json", "r", encoding="utf-8") as f:
            return json.load(f).get("users", ["楊欣怡", "王素梅", "鄭乃元"])
    except:
        return ["楊欣怡", "王素梅", "鄭乃元"]

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code):
    """交叉匯率獲取 (支援 DKK -> USD -> TWD 橋接)"""
    if currency_code == "TWD": return 1.0
    try:
        # 優先嘗試直接匯率
        direct = yf.Ticker(f"{currency_code}TWD=X").history(period="1d")
        if not direct.empty: return round(direct['Close'].iloc[-1], 2)
        
        # 橋接模式
        c_usd = yf.Ticker(f"{currency_code}USD=X").history(period="1d")
        u_twd = yf.Ticker("USDTWD=X").history(period="1d")
        if not c_usd.empty and not u_twd.empty:
            return round(c_usd['Close'].iloc[-1] * u_twd['Close'].iloc[-1], 2)
        return 35.0
    except:
        return 35.0

# --- II. 核心 AI 辨識邏輯 ---

def normalize_date_pro(text, month_map, target_year):
    """
    1. 抹除時間干擾
    2. 年度鎖定優先匹配
    3. 回傳日期與行索引 (作為空間錨點)
    """
    lines = [l.strip() for l in text.splitlines()]
    # 預處理：清除時間 11:00:00 與符號
    t_clean = re.sub(r'\d{1,2}:\d{2}(:\d{2})?', ' ', text)
    t_clean = t_clean.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
    
    # 月份名稱轉換
    for m_n, m_v in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t_clean = re.sub(rf'\b{m_n}\b', f" {m_v} ", t_clean, flags=re.IGNORECASE)
    
    # 在清理過的文本行中尋找
    clean_lines = t_clean.splitlines()
    for i, line in enumerate(clean_lines):
        matches = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        for d_s, m_s, y_s in matches:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == int(target_year):
                try:
                    return datetime(y, int(m_s), int(d_s)).date(), i
                except: continue
    return datetime.now().date(), -1

def is_unlikely_item(text, params):
    """智慧特徵過濾：排除地址、稅號、收據標題、行政雜訊"""
    t = text.strip().upper()
    if len(t) < 3: return True
    
    # 讀取 JSON 中定義的標題黑名單
    headers = params.get("header_headers", ["ANT", "NAVN", "ENHEDSPRIS", "KVITTERING", "CVR", "TLF", "TABLE", "WAITER"])
    if any(h in t for h in headers): return True
    
    # 排除單獨的幣別或總計字眼
    junks = ["DKK", "TOTAL", "IALT", "NET", "MOMS", "VAT", "SUBTOTAL", "AMOUNT", "DANKORT"]
    if t in junks: return True
    
    # 數字佔比過高判定 (排除電話或稅號)
    digit_ratio = sum(c.isdigit() for c in t) / len(t)
    if digit_ratio > 0.4 and not re.search(r'\d+[.,]\d{2}', t): return True
    
    # 長數字判定 (排除收據編號)
    if re.search(r'\d{5,}', t) and not re.search(r'\d+[.,]\d{2}', t): return True
    
    return False

def extract_data(text, params, date_idx):
    """
    1. 權重計分制尋找總額 (解決多幣別與折扣混淆)
    2. 雙隊列緩衝配對 (解決 A B C / 1 2 3 分欄)
    3. 利用日期錨點鎖定範圍
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    curr_code = params.get('currency_code', 'DKK').upper()
    total_keys = params.get('keywords', ["TOTAL", "IALT", "PAYMENT", "DUE", "DANKORT", "VISA"])
    exclude_keys = params.get('exclude_keywords', ["MOMS", "VAT", "TAX", "SUBTOTAL", "NET"])

    # --- A. 總額判定 ---
    money_cands = []
    for i, line in enumerate(lines):
        prices = re.findall(r'(-?\d+[.,]\d{2})', line)
        if not prices: continue
        val = float(prices[-1].replace(',', '.'))
        score = i # 基礎分
        if any(k in line.upper() for k in total_keys): score += 2000
        if curr_code in line.upper(): score += 1500
        if any(e in line.upper() for e in exclude_keys): score -= 1000
        # Gahon 斷行修正
        if i > 0 and any(k in lines[i-1].upper() for k in total_keys): score += 1500
        money_cands.append({'val': val, 'score': score, 'idx': i})

    best = sorted(money_cands, key=lambda x: x['score'], reverse=True)[0] if money_cands else {'val': 0.0, 'idx': len(lines)}
    final_amt, total_idx = best['val'], best['idx']

    # --- B. 品項採集 (空間分區) ---
    # 起點錨點：如果日期在上方則從日期後開始，否則從第 5 行後開始
    start_idx = date_idx + 1 if (0 <= date_idx < total_idx) else 5
    
    name_q, price_q = [], []
    for line in lines[start_idx:total_idx]:
        if is_unlikely_item(line, params): continue
        if any(k in line.upper() for k in total_keys + exclude_keys): continue
        
        has_text = re.search(r'[A-Za-zÀ-ÿ]{3,}', line)
        prices = re.findall(r'(-?\d+[.,]\d{2})', line)
        
        if has_text and prices:
            nm = re.sub(r'-?\d+[.,]\d{2}.*', '', line)
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', nm).strip()
            if not is_unlikely_item(nm, params): name_q.append(nm); price_q.append(prices[-1])
        elif has_text:
            nm = re.sub(r'^[\d\s]+[xX*]?\s*', '', line).strip()
            if not is_unlikely_item(nm, params): name_q.append(nm)
        elif prices:
            price_q.append(prices[-1])

    items = [n for n, p in zip(name_q, price_q)]
    if not items and name_q: items = name_q
    
    item_summary = "、".join(list(dict.fromkeys(items))[:3]) + ("等" if len(items) > 3 else "等" if items else "")
    vendor = lines[0] if "ORIGINAL" not in lines[0].upper() else lines[1]
    return vendor, final_amt, item_summary

def sync_to_sheets(df, user_name, curr_code):
    """同步到 Google Sheets"""
    try:
        creds_info = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        gc = gspread.authorize(creds)
        sh = gc.open_by_key("1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM")
        wks = sh.get_worksheet(0)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output = []
        for _, r in df.iterrows():
            base = r["外幣金額"] * r["匯率"]
            output.append([now_str, user_name, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], curr_code, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"]])
        wks.append_rows(output, value_input_option='USER_ENTERED')
        return True
    except: return False

# --- III. Streamlit 介面系統 ---

st.set_page_config(page_title="考察支出登錄系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")

if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.header("⚙️ 偵錯與設定")
    debug_mode = st.checkbox("🔍 開啟 OCR 文本模式 (含 Copy 鍵)")
    target_year = st.number_input("📅 考察年度鎖定", value=2025)

with st.expander("👤 步驟 1：人員與環境設定", expanded=True):
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
        m_rate = st.number_input(f"匯率 ({p['currency_code']}→TWD)", value=float(f_rate), step=0.01)
    with c3:
        fee_pct = st.number_input("海外刷卡手續費 (%)", value=1.5, step=0.1) / 100
        st.link_button("📂 查看 Google 試算表", "https://docs.google.com/spreadsheets/d/1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM/edit")

st.subheader("📸 步驟 2：上傳收據圖檔")
files = st.file_uploader("批次上傳 (支援多張)", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files:
    if st.button("🔍 執行 AI 自動辨識", type="primary", use_container_width=True):
        new_batch = []
        try:
            creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
            client = vision.ImageAnnotatorClient(credentials=creds)
            for f in files:
                txt = client.document_text_detection(image=vision.Image(content=f.read())).full_text_annotation.text
                if debug_mode:
                    st.write(f"📄 **{f.name} 的原始文本：**")
                    st.code(txt)
                
                # 執行雙階段辨識
                d, d_idx = normalize_date_pro(txt, p.get('month_map', {}), target_year)
                v, a, it = extract_data(txt, p, d_idx)
                
                new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
            st.session_state['data'] = new_batch
            st.success("✅ 辨識完成！請於下方確認數據。")
        except Exception as e: st.error(f"辨識過程出錯：{e}")

if st.session_state['data']:
    st.markdown("---")
    st.subheader("📝 步驟 3：數據核對與雲端同步")
    df = pd.DataFrame(st.session_state['data'])
    df["消費日期"] = pd.to_datetime(df["消費日期"]).dt.date
    edf = st.data_editor(df, use_container_width=True)
    
    total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
    st.metric("本批次總金額 (含手續費)", f"NT$ {int(total_twd):,} 元")

    if st.button("📤 確定無誤，同步至雲端試算表", type="primary", use_container_width=True):
        if sync_to_sheets(edf, final_u, p['currency_code']):
            st.toast("同步成功！已更新至試算表。")
            st.balloons()
            st.session_state['data'] = []
            st.rerun()