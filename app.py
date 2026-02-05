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
            return json.load(f).get("users", ["楊欣怡", "王素梅", "鄭乃元"])
    except: return ["預設登錄員"]

def normalize_date_pro(text, month_map):
    # 1. 嘗試尋找 YYYY/MM/DD 或 DD/MM/YYYY (最穩定的標準格式)
    std_pattern = r'(\d{2,4})[./\s- ]+(\d{1,2})[./\s- ]+(\d{2,4})'
    std_matches = list(re.finditer(std_pattern, text))
    for m in reversed(std_matches):
        g = m.groups()
        try:
            # 判斷哪一個是年 (2020-2026)
            parts = [int(g[0]), int(g[1]), int(g[2])]
            year = month = day = 0
            if 2020 <= parts[0] <= 2026: year, month, day = parts[0], parts[1], parts[2]
            elif 2020 <= parts[2] <= 2026: year, month, day = parts[2], parts[1], parts[0]
            if 1 <= month <= 12 and 1 <= day <= 31:
                return datetime(year, month, day).date()
        except: continue

    # 2. 月份映射救援 (針對 18 Jun'25 或 18/Juni/2025)
    t = text.replace("'", " ").replace("/", " ").replace("-", " ")
    for m_name, m_num in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t = re.sub(rf'{m_name}', f" {m_num} ", t, flags=re.IGNORECASE)
    
    rescue_pattern = r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})'
    rescue_matches = list(re.finditer(rescue_pattern, t))
    for m in reversed(rescue_matches):
        g = m.groups()
        try:
            d, m_val = int(g[0]), int(g[1])
            y_str = g[2]
            y = int(y_str) if len(y_str) == 4 else int(f"20{y_str}")
            if 2020 <= y <= 2026 and 1 <= m_val <= 12 and 1 <= d <= 31:
                return datetime(y, m_val, d).date()
        except: continue

    return datetime.now().date()

def extract_data(text, params):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    vendor = lines[0] if lines else "未知商店"
    total_keys = params.get('keywords', []) + ["TOTAL", "PAYMENT", "SUBTOTAL", "MOMS", "VAT", "DANKORT", "ALT", "Ialt"]

    # 1. 金額辨識 (權重系統)
    candidates = []
    for i, line in enumerate(lines):
        # 尋找所有 0.00 格式
        found = re.findall(r'(\d+[.,]\d{2})', line)
        for val_str in found:
            try:
                val = float(val_str.replace(',', '.'))
                score = i
                if any(k.upper() in line.upper() for k in total_keys): score += 500
                if "MOMS" in line.upper(): score -= 400
                candidates.append({'val': val, 'score': score})
            except: continue
    final_amount = sorted(candidates, key=lambda x: x['score'], reverse=True)[0]['val'] if candidates else 0.0

    # 2. 品項辨識 (北歐特殊字元救援)
    raw_items = []
    for line in lines[1:]:
        # 排除地址/長數字/純日期
        if len(re.findall(r'\d', line)) > 10: continue
        
        # 特徵：包含歐洲文字 [A-Za-zÀ-ÿ] 且包含金額
        has_text = re.search(r'[A-Za-zÀ-ÿ]{3,}', line)
        has_price = re.search(r'\d+[.,]\d{2}', line)
        
        if has_text and has_price:
            if any(k.upper() in line.upper() for k in total_keys): continue
            
            # 取得該行中「最後一個金額」之前的所有內容
            parts = re.split(r'\d+[.,]\d{2}', line)
            name = parts[0].strip()
            
            # 清理：移除數量標記 (如 1, 2 x)
            name = re.sub(r'^\d+\s?([xX*]\s?)?', '', name).strip()
            
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
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output = []
        for _, r in df.iterrows():
            base = r["外幣金額"] * r["匯率"]
            output.append([now_str, user_name, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], curr_code, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"]])
        wks.append_rows(output, value_input_option='USER_ENTERED')
        return True
    except: return False

# --- 2. UI ---
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
                img_content = f.read()
                res = client.document_text_detection(image=vision.Image(content=img_content))
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
    
    for idx, row in edf.iterrows():
        with st.container(border=True):
            ca, cb = st.columns([2, 1])
            total_twd = round(row["外幣金額"] * row["匯率"] * 1.015, 0)
            ca.markdown(f"**{row['商店名稱']}** ({row['消費日期']})\n\n品項：{row['參考品項']}")
            cb.markdown(f"**NT$ {int(total_twd):,}**")

    if st.button("📤 同步至雲端", type="primary", use_container_width=True):
        if sync_to_sheets(edf, final_u, p['currency_code']):
            st.toast("同步成功！"); st.balloons()
            st.session_state['data'] = []; st.rerun()