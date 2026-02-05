import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, hashlib
from datetime import datetime
from google.cloud import vision
import yfinance as yf
from PIL import Image

# --- I. 數據中心與初始化 (Data & Initialization) ---

def init_session():
    """初始化工作區，確保資料與圖片指紋在對話中持續存在"""
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'processed_hashes' not in st.session_state: 
        st.session_state['processed_hashes'] = set()

def calculate_hash(file_content):
    """計算圖片指紋，用於防止重複辨識"""
    return hashlib.md5(file_content).hexdigest()

def load_all_configs():
    """動態載入 configs/*.json，完全解耦國家資訊"""
    configs = {}
    for f in glob.glob("configs/*.json"):
        if "users.json" in f: continue 
        fn = os.path.splitext(os.path.basename(f))[0]
        display_name = fn.replace("_params", "").capitalize()
        # 國旗圖標對照 (僅作介面美化)
        emoji_map = {"dk": "🇩🇰", "es": "🇪🇸", "at": "🇦🇹", "cz": "🇨🇿", "tr": "🇹🇷", "jp": "🇯🇵", "kr": "🇰🇷"}
        prefix = emoji_map.get(fn.split('_')[0].lower(), "🌐")
        
        with open(f, 'r', encoding='utf-8') as j: 
            configs[f"{prefix} {display_name}"] = json.load(j)
    return configs

def load_users():
    """從 users.json 載入報帳人員名單"""
    try:
        with open("configs/users.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("users", [])
    except Exception:
        return []

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code):
    """獲取即時匯率"""
    if currency_code == "TWD": return 1.0
    try:
        ticker = f"{currency_code}TWD=X"
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty: return round(data['Close'].iloc[-1], 2)
        return 35.0
    except Exception:
        return 35.0

# --- II. AI 智慧引擎 (核心辨識與防火牆邏輯) ---

def is_unlikely_item(text, params):
    """
    智慧過濾器：排除時間、多幣別雜訊、行政標題。
    """
    t = text.strip().upper()
    if len(t) < 2: return True
    
    # 1. 幣別阻斷器：排除 Dubliner 常見的多幣別換算行 (DKK/EUR/SEK 並存)
    currencies = ["DKK", "EUR", "SEK", "NOK", "USD"]
    if sum(1 for c in currencies if c in t) >= 1: return True
    
    # 2. 時間戳記排除 (解決 15:36 PM 等雜訊)
    if re.search(r'\d{1,2}:\d{2}', t) or re.search(r'\b(AM|PM)\b', t): return True
    
    # 3. 月份碎片排除 (利用 JSON 內的 month_map)
    months_pattern = "|".join([m.upper() for m in params.get("month_map", {}).keys() if len(m) >= 2])
    if months_pattern and re.search(rf'\b({months_pattern})\b', t): return True

    # 4. 行政標題排除 (根據 JSON 的 header_headers)
    headers = params.get("header_headers", [])
    if any(h == t or h in t for h in headers): return True
    
    # 5. 排除長串數字 (如收據編號或電話，但保留金額格式)
    if re.search(r'\d{5,}', t) and not re.search(r'\d+[.,]\d{2}', t): return True
    
    return False

def normalize_date_pro(text, month_map, target_year):
    """提取日期，並排除時間特徵干擾"""
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

def extract_data(text, params, date_idx):
    """核心提取：結合權重判定金額與雙隊列縫合品項"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    curr_code = params.get('currency_code', 'DKK').upper()
    total_keys = params.get('keywords', [])
    exclude_keys = params.get('exclude_keywords', [])
    stop_keys = params.get('stop_keywords', [])

    # A. 權重總額判定 (解決折扣行與稅額干擾)
    money_cands = []
    for i, line in enumerate(lines):
        prices = re.findall(r'(-?\d+[.,]\d{2})', line)
        if not prices: continue
        val = float(prices[-1].replace(',', '.'))
        score = i 
        if any(k in line.upper() for k in total_keys): score += 5000 
        if curr_code in line.upper(): score += 1500
        if any(e in line.upper() for e in exclude_keys): score -= 4000 # 高負向權重
        money_cands.append({'val': val, 'score': score, 'idx': i})

    best = sorted(money_cands, key=lambda x: x['score'], reverse=True)[0] if money_cands else {'val': 0.0, 'idx': len(lines)}
    final_amt, total_idx = best['val'], best['idx']

    # B. 品項採集 (物理邊界起點 + 交易止損點)
    start_anchor = 0
    header_anchors = params.get("header_headers", [])
    for i, line in enumerate(lines[:15]): # 在前15行尋找標題起始錨點
        if any(h in line.upper() for h in header_anchors):
            start_anchor = i + 1; break
    
    if 0 <= date_idx < total_idx: start_anchor = max(start_anchor, date_idx + 1)
    if start_anchor == 0: start_anchor = 1

    name_q, price_q = [], []
    for line in lines[start_anchor:total_idx]:
        # 1. 絕對止損：遇到 KØB, VISA, WWW 等關鍵字立即中斷品項掃描
        if any(sk in line.upper() for sk in stop_keys):
            break
            
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

# --- III. 外部服務 (Sheets & Vision) ---

def sync_to_sheets(df, user_name, curr_code):
    """將辨識結果寫入雲端，並生成 UID 防止重複寫入"""
    try:
        creds_info = st.secrets["gcp_service_account"]
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        sh = gspread.authorize(creds).open_by_key("1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM")
        wks = sh.get_worksheet(0)
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output = []
        for _, r in df.iterrows():
            base = r["外幣金額"] * r["匯率"]
            # 生成 UID 防止重複同步
            uid = hashlib.md5(f"{r['商店名稱']}{r['消費日期']}{r['外幣金額']}".encode()).hexdigest()
            output.append([
                now_str, user_name, r["商店名稱"], r["參考品項"], str(r["消費日期"]), 
                r["外幣金額"], curr_code, r["匯率"], round(base,0), 
                round(base*0.015,0), round(base*1.015,0), r["備註"], uid
            ])
        wks.append_rows(output, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        st.error(f"同步至試算表失敗：{e}")
        return False

# --- IV. Streamlit 介面系統 ---

st.set_page_config(page_title="考察支出登錄系統", layout="wide")
init_session()

st.title("📊 國外考察支出登錄統計系統")

# 1. 頂部配置區域
with st.sidebar:
    st.header("⚙️ 辨識與偵錯")
    debug_mode = st.checkbox("🔍 開啟 OCR 原始文本顯示")
    target_year = st.number_input("📅 考察年度鎖定", value=2025)
    if st.button("🗑️ 清除快取指紋"):
        st.session_state['processed_hashes'] = set()
        st.toast("已清除圖片辨識紀錄")

c1, c2, c3, c4 = st.columns(4)
with c1:
    u_list = load_users()
    sel_u = st.selectbox("報帳人員", u_list + ["其他"]) if u_list else st.text_input("人員姓名")
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
    st.link_button("📂 開啟雲端試算表", "https://docs.google.com/spreadsheets/d/1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM/edit")

# 2. 上傳與預覽區域
st.markdown("---")
files = st.file_uploader("📸 批次上傳收據 (JPG/PNG)", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files:
    with st.expander("🖼️ 查看收據縮圖 (確認圖文對照用)", expanded=True):
        img_cols = st.columns(min(len(files), 4))
        for idx, f in enumerate(files):
            # 預先讀取指紋檢查重複
            f.seek(0)
            content = f.read()
            f_hash = calculate_hash(content)
            
            with img_cols[idx % 4]:
                st.image(Image.open(f), caption=f"收據 {idx+1}", use_container_width=True)
                if f_hash in st.session_state['processed_hashes']:
                    st.caption("✅ 此張已辨識")
                else:
                    st.caption("🆕 等待辨識")

# 3. 執行辨識邏識
if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
    new_batch = []
    skipped = 0
    try:
        client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
        
        for f in files:
            f.seek(0)
            content = f.read()
            f_hash = calculate_hash(content)
            
            # 指紋阻斷重複上傳
            if f_hash in st.session_state['processed_hashes']:
                skipped += 1
                continue
            
            txt = client.document_text_detection(image=vision.Image(content=content)).full_text_annotation.text
            if debug_mode: st.code(f"--- {f.name} --- \n{txt}")
            
            d, d_idx = normalize_date_pro(txt, p.get('month_map', {}), target_year)
            v, a, it = extract_data(txt, p, d_idx)
            
            new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
            st.session_state['processed_hashes'].add(f_hash)
            
        st.session_state['data'] += new_batch
        if skipped > 0: st.warning(f"已跳過 {skipped} 張重複辨識的收據。")
        st.success("✅ 辨識完成！請對照上方圖片核對下方表格。")
    except Exception as e:
        st.error(f"辨識出錯：{e}")

# 4. 數據編輯與同步
if st.session_state['data']:
    st.markdown("---")
    df_view = pd.DataFrame(st.session_state['data'])
    edf = st.data_editor(df_view, use_container_width=True, num_rows="dynamic")
    
    total_twd = (edf["外幣金額"] * edf["匯率"] * (1 + fee_pct)).sum()
    st.metric("本批次總計 (含刷卡手續費)", f"NT$ {int(total_twd):,}")
    
    if st.button("📤 同步至雲端試算表", type="primary", use_container_width=True):
        if sync_to_sheets(edf, final_u, p['currency_code']):
            st.toast("同步成功！資料已排入雲端試算表。")
            st.balloons()
            # 同步後不清除指紋，但清除編輯清單，避免同一批次誤按兩次
            st.session_state['data'] = []
            st.rerun()