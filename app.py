import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json
from datetime import datetime
from google.cloud import vision
import yfinance as yf

# --- 1. 核心工具函式 (解決 NameError) ---

def load_all_configs():
    """載入 configs 目錄下所有的國家設定檔"""
    configs = {}
    for f in glob.glob("configs/*.json"):
        if "users.json" in f: continue 
        fn = os.path.splitext(os.path.basename(f))[0]
        # 對應顯示名稱
        dn_map = {
            "dk_params": "🇩🇰 丹麥", "es_params": "🇪🇸 西班牙", "at_params": "🇦🇹 奧地利",
            "cz_params": "🇨🇿 捷克", "tr_params": "🇹🇷 土耳其", "jp_params": "🇯🇵 日本", "kr_params": "🇰🇷 南韓"
        }
        dn = dn_map.get(fn.lower(), fn.replace("_params", "").capitalize())
        with open(f, 'r', encoding='utf-8') as j: 
            configs[dn] = json.load(j)
    return configs

def load_users():
    """載入使用者清單"""
    try:
        with open("configs/users.json", "r", encoding="utf-8") as f:
            return json.load(f).get("users", ["楊欣怡", "王素梅", "鄭乃元"])
    except:
        return ["楊欣怡", "王素梅", "鄭乃元"]

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code):
    """交叉匯率補強邏輯"""
    if currency_code == "TWD": return 1.0
    try:
        direct = f"{currency_code}TWD=X"
        data = yf.Ticker(direct).history(period="1d")
        if not data.empty: return round(data['Close'].iloc[-1], 2)
        
        # 啟動橋接模式: Foreign -> USD -> TWD
        c_usd = f"{currency_code}USD=X"
        u_twd = "USDTWD=X"
        d1, d2 = yf.Ticker(c_usd).history(period="1d"), yf.Ticker(u_twd).history(period="1d")
        if not d1.empty and not d2.empty:
            return round(d1['Close'].iloc[-1] * d2['Close'].iloc[-1], 2)
        return 35.0
    except: return 35.0

# --- 2. 核心辨識邏輯 (雙隊列算法) ---

def normalize_date_pro(text, month_map):
    """分層日期辨識"""
    # 1. 優先找標準 DD/MM/YYYY
    std = re.findall(r'(\d{1,2})[./\s-](\d{1,2})[./\s-](\d{4})', text)
    if std:
        d, m, y = std[-1]
        try: return datetime(int(y), int(m), int(d)).date()
        except: pass

    # 2. 針對 Salon/Dubliner 清理救援
    t = text.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ").replace("\n", " ")
    for m_n, m_v in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t = re.sub(rf'{m_n}', f" {m_v} ", t, flags=re.IGNORECASE)
    
    matches = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', t)
    for d_s, m_s, y_s in reversed(matches):
        try:
            day, month = int(d_s), int(m_s)
            year = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if 2020 <= year <= 2026: return datetime(year, month, day).date()
        except: continue
    return datetime.now().date()

def extract_data(text, params):
    """終極配對算法"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    vendor = lines[0] if lines else "未知商店"
    total_keys = params.get('keywords', []) + ["TOTAL", "PAYMENT", "IALT", "MOMS", "VAT", "DANKORT", "ALT", "KONTANT"]

    # 1. 金額權重辨識
    money_cands = []
    for i, line in enumerate(lines):
        for m in re.findall(r'(-?\d+[.,]\d{2})', line):
            val = float(m.replace(',', '.'))
            score = i * 2
            if any(k.upper() in line.upper() for k in total_keys): score += 500
            if i > 0 and any(k.upper() in lines[i-1].upper() for k in total_keys): score += 400
            money_cands.append({'val': val, 'score': score})
    final_amt = sorted(money_cands, key=lambda x: x['score'], reverse=True)[0]['val'] if money_cands else 0.0

    # 2. 雙隊列配對算法 (解決 A B C / 1 2 3)
    name_queue, price_queue = [], []
    for line in lines[1:]:
        if len(re.findall(r'\d', line)) > 10: continue
        if any(k.upper() in line.upper() for k in total_keys): continue
        
        has_text = re.search(r'[A-Za-zÀ-ÿ]{3,}', line)
        prices = re.findall(r'(-?\d+[.,]\d{2})', line)
        
        if has_text and prices:
            name = re.sub(r'-?\d+[.,]\d{2}.*', '', line)
            name = re.sub(r'^[\d\s]+[xX*]?\s*', '', name).strip()
            if len(name) > 2: name_queue.append(name); price_queue.append(prices[-1])
        elif has_text:
            name = re.sub(r'^[\d\s]+[xX*]?\s*', '', line).strip()
            if len(name) > 2: name_queue.append(name)
        elif prices:
            price_queue.append(prices[-1])

    # 索引縫合
    raw_items = []
    for n, p in zip(name_queue, price_queue):
        raw_items.append(n)
    
    unique_items = list(dict.fromkeys(raw_items))
    item_summary = "、".join(unique_items[:3]) + ("等" if unique_items else "")
    return vendor, final_amt, item_summary

def sync_to_sheets(df, user_name, curr_code):
    """寫入 Google Sheets"""
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
    except Exception as e:
        st.error(f"同步失敗: {e}")
        return False

# --- 3. Streamlit 介面 ---

st.set_page_config(page_title="支出登錄系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")

if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.header("⚙️ 系統設定")
    debug_mode = st.checkbox("🔍 開啟偵錯模式 (查看文本結構)")

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
        m_rate = st.number_input(f"參考匯率 ({p['currency_code']}→TWD)", value=float(f_rate), step=0.01)
    with c3:
        fee_pct = st.number_input("手續費率 (%)", value=1.5, step=0.1) / 100
        st.link_button("📂 查看 Google Sheet", "https://docs.google.com/spreadsheets/d/1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM/edit")

st.subheader("📸 步驟 2：上傳收據")
files = st.file_uploader("批次上傳", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files:
    if st.button("🔍 執行全自動 AI 辨識", type="primary", use_container_width=True):
        new_batch = []
        try:
            creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
            client = vision.ImageAnnotatorClient(credentials=creds)
            for f in files:
                txt = client.document_text_detection(image=vision.Image(content=f.read())).full_text_annotation.text
                if debug_mode: st.text_area(f"Raw Text: {f.name}", txt, height=150)
                v, a, it = extract_data(txt, p)
                d = normalize_date_pro(txt, p.get('month_map', {}))
                new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
            st.session_state['data'] = new_batch
            st.success("✅ 辨識完成！")
        except Exception as e: st.error(f"辨識出錯：{e}")

if st.session_state['data']:
    st.markdown("---")
    st.subheader("📝 步驟 3：數據確認與同步")
    df = pd.DataFrame(st.session_state['data'])
    df["消費日期"] = pd.to_datetime(df["消費日期"]).dt.date
    edf = st.data_editor(df, use_container_width=True, key="data_editor")
    
    total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
    st.metric("本批次總金額 (含 1.5% 手續費)", f"NT$ {int(total_twd):,} 元")

    if st.button("📤 同步到雲端試算表", type="primary", use_container_width=True):
        if sync_to_sheets(edf, final_u, p['currency_code']):
            st.toast("同步成功！資料已寫入試算表。")
            st.balloons(); st.session_state['data'] = []; st.rerun()