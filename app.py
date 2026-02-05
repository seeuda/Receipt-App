import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json
from datetime import datetime
from google.cloud import vision
import yfinance as yf

# --- I. 數據中心與動態配置 (Data & Config Hub) ---

def load_all_configs():
    """動態載入 configs/*.json，排除 users.json。國家對照完全由檔名決定。"""
    configs = {}
    for f in glob.glob("configs/*.json"):
        if "users.json" in f: continue 
        fn = os.path.splitext(os.path.basename(f))[0]
        # 國家名稱對應邏輯 (不硬編碼特定名稱)
        display_name = fn.replace("_params", "").capitalize()
        # 針對常見縮寫提供圖標 (僅作顯示優化)
        emoji_map = {"dk": "🇩🇰", "es": "🇪🇸", "at": "🇦🇹", "cz": "🇨🇿", "tr": "🇹🇷", "jp": "🇯🇵", "kr": "🇰🇷"}
        prefix = emoji_map.get(fn.split('_')[0].lower(), "🌐")
        
        with open(f, 'r', encoding='utf-8') as j: 
            configs[f"{prefix} {display_name}"] = json.load(j)
    return configs

def load_users():
    """只從 configs/users.json 載入人員名單，不留任何預設名單。"""
    try:
        with open("configs/users.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("users", [])
    except Exception:
        return []

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code):
    """獲取即時匯率，支援外幣 -> USD -> TWD 交叉換算以提高穩定性。"""
    if currency_code == "TWD": return 1.0
    try:
        ticker = f"{currency_code}TWD=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty: return round(data['Close'].iloc[-1], 2)
        
        # 交叉換算
        c_usd = yf.Ticker(f"{currency_code}USD=X").history(period="1d")
        u_twd = yf.Ticker("USDTWD=X").history(period="1d")
        if not c_usd.empty and not u_twd.empty:
            return round(c_usd['Close'].iloc[-1] * u_twd['Close'].iloc[-1], 2)
        return 35.0
    except Exception:
        return 35.0

# --- II. AI 智慧引擎 (核心辨識與過濾邏輯) ---

def is_unlikely_item(text, params):
    """
    智慧特徵過濾器：排除時間戳記、日期碎片、行政標題、長數字雜訊。
    過濾詞彙完全依賴傳入的 params (JSON) 配置。
    """
    t = text.strip().upper()
    if len(t) < 2: return True
    
    # 1. 排除時間戳記與日期碎片 (解決 Jun'25 15:36 PM 等錯誤)
    # 匹配 HH:MM, HH:MM:SS, AM/PM
    if re.search(r'\d{1,2}:\d{2}', t) or re.search(r'\b(AM|PM)\b', t): return True
    # 匹配月份與年份縮寫 (如 Jun'25)
    months_pattern = "|".join([m.upper() for m in params.get("month_map", {}).keys() if len(m) >= 2])
    if months_pattern and re.search(rf'\b({months_pattern})\b', t): return True
    if re.search(r'\'\d{2}\b', t) or re.search(r'\b20\d{2}\b', t): return True

    # 2. 排除行政標題行 (如 ANT, NAVN, KVITTERING)
    headers = params.get("header_headers", [])
    if any(h == t or h in t for h in headers): return True
    
    # 3. 排除總額與幣別字眼
    junks = params.get("keywords", []) + params.get("exclude_keywords", [])
    if any(j == t for j in junks): return True
    
    # 4. 排除非金額格式的長數字 (收據編號或電話)
    if re.search(r'\d{5,}', t) and not re.search(r'\d+[.,]\d{2}', t): return True
    
    # 5. 排除純數字比例過高的無效行
    if len(t) > 0 and (sum(c.isdigit() for c in t) / len(t) > 0.6) and not re.search(r'\d+[.,]\d{2}', t): return True
    
    return False

def normalize_date_pro(text, month_map, target_year):
    """抹除時間干擾，優先鎖定目標年度。"""
    t_clean = re.sub(r'\d{1,2}:\d{2}(:\d{2})?', ' ', text)
    t_clean = t_clean.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
    
    # 依長度排序替換，防止短詞誤傷長詞
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
    """權重計分判定總額與雙隊列縫合品項。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    curr_code = params.get('currency_code', 'DKK').upper()
    total_keys = params.get('keywords', [])
    exclude_keys = params.get('exclude_keywords', [])

    # A. 權重總額判定 (解決折扣與多幣別干擾)
    money_cands = []
    for i, line in enumerate(lines):
        prices = re.findall(r'(-?\d+[.,]\d{2})', line)
        if not prices: continue
        val = float(prices[-1].replace(',', '.'))
        score = i 
        if any(k in line.upper() for k in total_keys): score += 5000 
        if curr_code in line.upper(): score += 1500
        if i > 0 and any(k in lines[i-1].upper() for k in total_keys): score += 2000
        if any(e in line.upper() for e in exclude_keys): score -= 3000
        if any(c in line.upper() for c in ["SEK", "EUR", "USD", "NOK"]) and curr_code not in line.upper():
            score -= 4000
        money_cands.append({'val': val, 'score': score, 'idx': i})

    best = sorted(money_cands, key=lambda x: x['score'], reverse=True)[0] if money_cands else {'val': 0.0, 'idx': len(lines)}
    final_amt, total_idx = best['val'], best['idx']

    # B. 品項採集 (空間錨點與雜訊排除)
    # 品項應在「標題行/日期行」之後，且在「總計行」之前
    start_anchor = 0
    header_anchors = params.get("header_headers", [])
    for i, line in enumerate(lines[:15]):
        if any(h in line.upper() for h in header_anchors):
            start_anchor = i + 1; break
    
    # 校正日期行產生的邊界
    if 0 <= date_idx < total_idx:
        start_anchor = max(start_anchor, date_idx + 1)
    if start_anchor == 0: start_anchor = 1

    name_q, price_q = [], []
    for line in lines[start_anchor:total_idx]:
        # 排除總計與稅務行
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

    # 執行隊列配對
    items = [n for n, p in zip(name_q, price_q)] or name_q
    item_summary = "、".join(list(dict.fromkeys(items))[:3]) + ("等" if len(items) > 3 else "等" if items else "")
    vendor = lines[0] if "ORIGINAL" not in lines[0].upper() else lines[1]
    return vendor, final_amt, item_summary

def sync_to_sheets(df, user_name, curr_code):
    """將結果寫入雲端試算表。"""
    try:
        creds_info = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        sh = gspread.authorize(creds).open_by_key("1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM")
        wks = sh.get_worksheet(0)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output = []
        for _, r in df.iterrows():
            base = r["外幣金額"] * r["匯率"]
            output.append([now_str, user_name, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], curr_code, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"]])
        wks.append_rows(output, value_input_option='USER_ENTERED')
        return True
    except Exception:
        return False

# --- III. Streamlit 介面系統 ---

st.set_page_config(page_title="考察支出登錄系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")

if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.header("⚙️ 辨識設定")
    debug_mode = st.checkbox("🔍 開啟偵錯文本")
    target_year = st.number_input("📅 考察年度鎖定", value=2025)

c1, c2, c3, c4 = st.columns(4)
with c1:
    u_list = load_users()
    sel_u = st.selectbox("報帳人員", u_list + ["其他"]) if u_list else st.text_input("請手動輸入人員姓名")
    final_u = st.text_input("輸入姓名") if sel_u == "其他" else (sel_u if u_list else sel_u)
with c2:
    all_cfg = load_all_configs()
    sel_c = st.selectbox("考察國家", list(all_cfg.keys()))
    p = all_cfg[sel_c]
with c3:
    f_rate = get_exchange_rate(p['currency_code'])
    m_rate = st.number_input(f"匯率 ({p['currency_code']})", value=float(f_rate), step=0.01)
with c4:
    fee_pct = st.number_input("手續費(%)", value=1.5) / 100
    st.link_button("📂 查看試算表", "https://docs.google.com/spreadsheets/d/1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM/edit")

st.subheader("📸 上傳收據圖檔")
files = st.file_uploader("批次上傳 (支援多張)", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
    new_batch = []
    try:
        client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
        for f in files:
            content = f.read()
            txt = client.document_text_detection(image=vision.Image(content=content)).full_text_annotation.text
            if debug_mode: st.code(txt)
            d, d_idx = normalize_date_pro(txt, p.get('month_map', {}), target_year)
            v, a, it = extract_data(txt, p, d_idx)
            new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
        st.session_state['data'] = new_batch
        st.success("✅ 辨識完成！請核對數據。")
    except Exception as e:
        st.error(f"辨識出錯：{e}")

if st.session_state['data']:
    st.markdown("---")
    df_view = pd.DataFrame(st.session_state['data'])
    edf = st.data_editor(df_view, use_container_width=True)
    
    total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
    st.metric("本批次預算總金額", f"NT$ {int(total_twd):,}")
    
    if st.button("📤 同步至雲端試算表", type="primary", use_container_width=True):
        if sync_to_sheets(edf, final_u, p['currency_code']):
            st.toast("同步成功！已寫入試算表。")
            st.balloons()
            st.session_state['data'] = []
            st.rerun()