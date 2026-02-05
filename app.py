import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json
from datetime import datetime
from google.cloud import vision
import yfinance as yf

# --- 1. 核心邏輯 ---

COUNTRY_NAMES = {
    "dk_params": "🇩🇰 丹麥",
    "es_params": "🇪🇸 西班牙",
    "at_params": "🇦🇹 奧地利",
    "cz_params": "🇨🇿 捷克",
    "tr_params": "🇹🇷 土耳其",
    "jp_params": "🇯🇵 日本",
    "kr_params": "🇰🇷 南韓"
}

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code):
    if currency_code == "TWD": return 1.0
    try:
        direct_ticker = f"{currency_code}TWD=X"
        data = yf.Ticker(direct_ticker).history(period="1d")
        if not data.empty: return round(data['Close'].iloc[-1], 2)
        
        c_usd = f"{currency_code}USD=X"
        u_twd = "USDTWD=X"
        d1 = yf.Ticker(c_usd).history(period="1d")
        d2 = yf.Ticker(u_twd).history(period="1d")
        if not d1.empty and not d2.empty:
            return round(d1['Close'].iloc[-1] * d2['Close'].iloc[-1], 2)
        return 35.0
    except: return 35.0

def load_all_configs():
    configs = {}
    files = glob.glob("configs/*.json")
    for f in files:
        if "users.json" in f: continue 
        fn = os.path.splitext(os.path.basename(f))[0]
        dn = COUNTRY_NAMES.get(fn.lower(), fn.replace("_params", "").capitalize())
        with open(f, 'r', encoding='utf-8') as j: configs[dn] = json.load(j)
    return configs

def load_users():
    try:
        with open("configs/users.json", "r", encoding="utf-8") as f:
            return json.load(f).get("users", ["預設登錄員"])
    except: return ["預設登錄員"]

def normalize_date_pro(text, month_map):
    # 1. 強化清理：處理 Jun'25 這種黏在一起的情況
    clean_text = text.replace("'", " ").replace("\n", " ")
    
    # 2. 月份轉換 (處理 Jun, Jun', June 等)
    for m_name, m_num in month_map.items():
        # 不使用 \b，改用更寬鬆的替換
        clean_text = re.sub(rf'{m_name}', f" {m_num} ", clean_text, flags=re.IGNORECASE)
    
    # 3. 搜尋日期模式 (日 月 年)
    # 模式：1-2位數字 + 分隔符 + 1-2位數字 + 分隔符 + 2或4位數字
    date_patterns = [
        r'(\d{1,2})[./\s-]+(\d{1,2})[./\s-]+(\d{4})',
        r'(\d{1,2})[./\s-]+(\d{1,2})[./\s-]+(\d{2})'
    ]
    
    for p in date_patterns:
        matches = list(re.finditer(p, clean_text))
        for m in reversed(matches): # 從後往前找，通常收據日期在中間
            g = m.groups()
            try:
                y = g[2] if len(g[2]) == 4 else f"20{g[2]}"
                v_dt = datetime(int(y), int(g[1]), int(g[0]))
                if 2020 <= v_dt.year <= 2026: return v_dt.date()
            except: continue
    return datetime.now().date()

def extract_data(text, params):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    vendor = lines[0] if lines else "未知商店"
    sep = re.escape(params['decimal_separator'])
    curr = params['currency_code'].upper()
    total_keywords = params.get('keywords', [])

    # --- 金額辨識 (優先找最後出現的總計) ---
    money_regex = r'(\d+[.,]\d{2})'
    candidates = []
    for i, line in enumerate(lines):
        for match in re.finditer(money_regex, line):
            val = float(match.group(1).replace(',', '.'))
            score = i # 越後面的金額權重越高
            if any(k.upper() in line.upper() for k in total_keywords): score += 500
            if curr in line.upper(): score += 200
            if "MOMS" in line.upper() or "TAX" in line.upper(): score -= 300 # 排除稅金行
            candidates.append({'val': val, 'score': score})
    
    final_amount = sorted(candidates, key=lambda x: x['score'], reverse=True)[0]['val'] if candidates else 0.0

    # --- 品項辨識 (針對丹麥收據優化) ---
    raw_items = []
    # 排除地址/電話等雜訊 (通常包含過多數字)
    for line in lines[1:]:
        # 排除包含大量連續數字的行 (如 CVR, Tlf, 帳號)
        if len(re.findall(r'\d', line)) > 8: continue
        # 排除包含日期格式的行
        if re.search(r'\d{2,4}[./\s]\d{1,2}[./\s]\d{2,4}', line): continue
        
        # 特徵：該行必須有文字 + 且有金額格式數字
        has_text = re.search(r'[A-Za-z\u4e00-\u9fff]{3,}', line)
        has_price = re.search(r'\d+[.,]\d{2}', line)
        
        if has_text and has_price:
            # 排除總計相關行
            if any(k.upper() in line.upper() for k in total_keywords + ["MOMS", "NET TOTAL", "VAT"]): continue
            
            # 清理：移除金額與行首數量
            name = re.sub(r'\d+[.,]\d{2}.*', '', line).strip()
            name = re.sub(r'^\d+\s?([xX*]\s?)?', '', name).strip()
            if len(name) > 3: raw_items.append(name)

    unique_items = list(dict.fromkeys(raw_items)) # 去重
    item_summary = "、".join(unique_items[:3]) + ("等" if unique_items else "")
    return vendor, final_amount, item_summary

# --- 2. 網頁介面 ---
st.set_page_config(page_title="支出登錄系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")
if 'data' not in st.session_state: st.session_state['data'] = []

with st.expander("👤 步驟 1：基本設定", expanded=True):
    c1, c2, c3 = st.columns([1, 1, 1])
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
        fee = st.number_input("手續費率 (%)", value=1.5, step=0.1) / 100
        st.link_button("📂 打開試算表", "https://docs.google.com/spreadsheets/d/1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM/edit", use_container_width=True)

st.subheader("📸 步驟 2：上傳收據")
files = st.file_uploader("批次上傳", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files:
    if st.button("🔍 執行 AI 辨識", type="primary", use_container_width=True):
        new_batch = []
        try:
            creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
            client = vision.ImageAnnotatorClient(credentials=creds)
            prog = st.progress(0)
            for idx, f in enumerate(files):
                res = client.document_text_detection(image=vision.Image(content=f.read()))
                txt = res.full_text_annotation.text
                v, a, it = extract_data(txt, p)
                d = normalize_date_pro(txt, p.get('month_map', {}))
                new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
                prog.progress((idx+1)/len(files))
            st.session_state['data'] = new_batch
            st.success("✅ 辨識完成！")
        except Exception as e: st.error(f"辨識出錯：{e}")

if st.session_state['data']:
    st.markdown("---")
    st.subheader("📝 步驟 3：數據確認")
    df = pd.DataFrame(st.session_state['data'])
    df["消費日期"] = pd.to_datetime(df["消費日期"]).dt.date
    edf = st.data_editor(df[["商店名稱", "參考品項", "消費日期", "外幣金額", "匯率", "備註"]], use_container_width=True)
    edf["原始台幣"] = (edf["外幣金額"] * edf["匯率"]).round(0)
    edf["手續費"] = (edf["原始台幣"] * fee).round(0)
    edf["總計台幣"] = edf["原始台幣"] + edf["手續費"]

    for idx, row in edf.iterrows():
        with st.container(border=True):
            ca, cb = st.columns([2, 1])
            ca.markdown(f"**{row['商店名稱']}** ({row['消費日期']}) \n\n 品項：{row['參考品項'] or '無'}")
            cb.markdown(f"**NT$ {int(row['總計台幣']):,}**")

    if st.button("📤 同步至雲端", type="primary", use_container_width=True):
        if sync_to_sheets(edf, final_u, p['currency_code']):
            st.toast("同步成功！"); st.balloons()
            st.session_state['data'] = []; st.rerun()