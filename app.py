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
    "dk_params": "🇩🇰 丹麥", "es_params": "🇪🇸 西班牙", "at_params": "🇦🇹 奧地利",
    "cz_params": "🇨🇿 捷克", "tr_params": "🇹🇷 土耳其", "jp_params": "🇯🇵 日本", "kr_params": "🇰🇷 南韓"
}

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code):
    if currency_code == "TWD": return 1.0
    try:
        direct = f"{currency_code}TWD=X"
        data = yf.Ticker(direct).history(period="1d")
        if not data.empty: return round(data['Close'].iloc[-1], 2)
        d1 = yf.Ticker(f"{currency_code}USD=X").history(period="1d")
        d2 = yf.Ticker("USDTWD=X").history(period="1d")
        if not d1.empty and not d2.empty:
            return round(d1['Close'].iloc[-1] * d2['Close'].iloc[-1], 2)
        return 35.0
    except: return 35.0

def load_all_configs():
    configs = {}
    for f in glob.glob("configs/*.json"):
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
    # 1. 激進清理：將所有常見分隔符符號轉為空格
    t = text.replace("'", " ").replace("/", " ").replace(".", " ").replace("-", " ").replace("\n", " ")
    
    # 2. 月份替換 (Juni, Jun -> 06)
    for m_name, m_num in month_map.items():
        t = re.sub(rf'\b{m_name}\b', f" {m_num} ", t, flags=re.IGNORECASE)
    
    # 3. 抓取所有數字組合
    date_regex = r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})'
    matches = list(re.finditer(date_regex, t))
    
    # 從後往前找（通常日期在收據中下段），但排除掉顯然不是年份的長數字
    for m in reversed(matches):
        g = m.groups()
        try:
            day, month = int(g[0]), int(g[1])
            year_str = g[2]
            year = int(year_str) if len(year_str) == 4 else int(f"20{year_str}")
            if 2020 <= year <= 2026 and 1 <= month <= 12 and 1 <= day <= 31:
                return datetime(year, month, day).date()
        except: continue
    return datetime.now().date()

def extract_data(text, params):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    vendor = lines[0] if lines else "未知商店"
    total_keys = params.get('keywords', []) + ["TOTAL", "PAYMENT", "SUBTOTAL", "MOMS", "VAT", "DANKORT"]

    # 金額辨識
    money_regex = r'(\d+[.,]\d{2})'
    candidates = []
    for i, line in enumerate(lines):
        for match in re.finditer(money_regex, line):
            val = float(match.group(1).replace(',', '.'))
            score = i
            if any(k.upper() in line.upper() for k in params.get('keywords', [])): score += 500
            if "MOMS" in line.upper(): score -= 400
            candidates.append({'val': val, 'score': score})
    final_amount = sorted(candidates, key=lambda x: x['score'], reverse=True)[0]['val'] if candidates else 0.0

    # 品項辨識
    raw_items = []
    for line in lines[1:]:
        # 排除地址/電話/日期行
        if len(re.findall(r'\d', line)) > 10 or re.search(r'\d{1,2}\s+\d{1,2}\s+\d{2}', line): continue
        
        has_text = re.search(r'[A-Za-z\u4e00-\u9fff]{3,}', line)
        has_price = re.search(r'\d+[.,]\d{2}', line)
        
        if has_text and has_price:
            if any(k.upper() in line.upper() for k in total_keys): continue
            # 移除金額與數量
            name = re.sub(r'\d+[.,]\d{2}.*', '', line).strip()
            name = re.sub(r'^[\d\s]+[xX*]?\s*', '', name).strip()
            if len(name) > 3: raw_items.append(name)

    unique_items = list(dict.fromkeys(raw_items))
    item_summary = "、".join(unique_items[:3]) + ("等" if unique_items else "")
    return vendor, final_amount, item_summary

def sync_to_sheets(df, user_name, curr_code):
    try:
        creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
        gc = gspread.authorize(creds)
        sh = gc.open_by_key("1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM")
        wks = sh.get_worksheet(0)
        output_data = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for _, row in df.iterrows():
            output_data.append([
                now_str, user_name, row["商店名稱"], row["參考品項"], str(row["消費日期"]),
                row["外幣金額"], curr_code, row["匯率"], row["原始台幣"], row["手續費"], 
                row["總計台幣"], row["備註"]
            ])
        wks.append_rows(output_data, value_input_option='USER_ENTERED')
        return True
    except: return False

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
                img = vision.Image(content=f.read())
                txt = client.document_text_detection(image=img).full_text_annotation.text
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
            ca.markdown(f"**{row['商店名稱']}** ({row['消費日期']})\n\n品項：{row['參考品項']}")
            cb.markdown(f"**NT$ {int(row['總計台幣']):,}**")

    if st.button("📤 同步至雲端", type="primary", use_container_width=True):
        if sync_to_sheets(edf, final_u, p['currency_code']):
            st.toast("同步成功！"); st.balloons()
            st.session_state['data'] = []; st.rerun()