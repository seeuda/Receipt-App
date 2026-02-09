import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, hashlib
from datetime import datetime
from google.cloud import vision
import yfinance as yf
from PIL import Image
from typing import Dict, List, Optional

# --- I. 數據中心與初始化 (Data & Config Hub) ---

def init_session() -> None:
    """初始化工作區狀態，確保資料與指紋在對話中持續"""
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'processed_hashes' not in st.session_state: 
        st.session_state['processed_hashes'] = set()

def calculate_hash(file_content: bytes) -> str:
    """計算檔案 MD5 指紋，防止重複辨識"""
    return hashlib.md5(file_content).hexdigest()

def get_gspread_client():
    """授權並取得 gspread 客戶端，遵循安全性規範從 Secrets 讀取"""
    creds_info = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        creds_info, 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

def load_project_registry() -> Dict[str, str]:
    """從管理員註冊表智慧載入授權專案，自動適應表單標題"""
    try:
        gc = get_gspread_client()
        registry_id = st.secrets["admin_registry_id"]
        sh = gc.open_by_key(registry_id)
        data = sh.get_worksheet(0).get_all_records()
        if not data: return {}

        all_keys = data[0].keys()
        k_name = next((k for k in all_keys if "專案名稱" in k), "專案名稱")
        k_id = next((k for k in all_keys if "試算表 ID" in k), "試算表 ID")
        k_status = next((k for k in all_keys if "啟用狀態" in k), "啟用狀態")

        return {r[k_name]: r[k_id] for r in data if str(r.get(k_status, "")).strip().upper() == "TRUE"}
    except Exception as e:
        st.error(f"❌ 註冊表載入失敗: {e}")
        return {}

def load_project_users(target_sheet_id: str) -> List[str]:
    """從專案試算表的『人員名單』分頁動態載入，實踐分散式管理"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(target_sheet_id)
        try:
            wks = sh.worksheet("人員名單")
            names = wks.col_values(1)[1:]  # 跳過標題列
            return [n for n in names if n.strip()]
        except gspread.exceptions.WorksheetNotFound:
            return []
    except Exception:
        return []

def load_all_configs() -> Dict:
    """載入多國參數並讀取大洲資訊"""
    configs = {}
    emoji_map = {
        "de": "🇩🇪", "nl": "🇳🇱", "at": "🇦🇹", "cz": "🇨🇿", "tr": "🇹🇷", "gb": "🇬🇧",
        "jp": "🇯🇵", "kr": "🇰🇷", "au": "🇦🇺", "nz": "🇳🇿", "us": "🇺🇸", "sg": "🇸🇬",
        "es": "🇪🇸", "pt": "🇵🇹", "ch": "🇨🇭", "it": "🇮🇹", "be": "🇧🇪", "no": "🇳🇴",
        "se": "🇸🇪", "fi": "🇫🇮", "is": "🇮🇸", "vn": "🇻🇳", "th": "🇹🇭", "my": "🇲🇾",
        "fr": "🇫🇷", "ph": "🇵🇭", "in": "🇮🇳", "br": "🇧🇷", "ca": "🇨🇦", "ae": "🇦🇪",
        "za": "🇿🇦", "gr": "🇬🇷", "dk": "🇩🇰", "tw": "🇹🇼", "ie": "🇮🇪", "pl": "🇵🇱",
        "id": "🇮🇩", "mx": "🇲🇽", "il": "🇮🇱", "sa": "🇸🇦"
    }
    for f in glob.glob("configs/*.json"):
        iso = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            data = json.load(j)
            label = f"{emoji_map.get(iso, '🌐')} {data.get('country', iso)}"
            configs[label] = data
    return configs

@st.cache_data(ttl=3600)
def get_exchange_rate(currency_code: str) -> float:
    """解決傍晚失效問題：改用 1mo 週期抓取最新有效收盤價"""
    if currency_code == "TWD": return 1.0
    try:
        ticker = yf.Ticker(f"{currency_code}TWD=X")
        hist = ticker.history(period="1mo")
        if not hist.empty:
            valid_closes = hist['Close'].dropna()
            if not valid_closes.empty:
                return round(valid_closes.iloc[-1], 2)
        return 35.0
    except Exception:
        return 35.0

# --- II. AI 辨識引擎 (OCR & Extraction) ---

def is_unlikely_item(text: str, params: Dict) -> bool:
    """過濾不合理的品項文字"""
    t = text.strip().upper()
    if len(t) < 2 or re.search(r'\d{1,2}:\d{2}', t): return True
    headers = params.get("header_headers", [])
    if any(h == t or h in t for h in headers): return True
    return False

def normalize_date_pro(text: str, month_map: Dict, target_year: int):
    """日期特徵提取與格式化"""
    t_clean = text.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
    for m_n, m_v in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t_clean = re.sub(rf'\b{m_n}\b', f" {m_v} ", t_clean, flags=re.IGNORECASE)
    for i, line in enumerate(t_clean.splitlines()):
        matches = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        for d_s, m_s, y_s in matches:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == target_year:
                try: return datetime(y, int(m_s), int(d_s)).date(), i
                except: continue
    return datetime.now().date(), -1

def extract_data(text: str, params: Dict, date_idx: int) -> Tuple[str, float, str]:
    """核心辨識邏輯：商店、金額與品項摘要"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    curr = params.get('currency_code', '').upper()
    t_keys, e_keys, s_keys = params.get('keywords', []), params.get('exclude_keywords', []), params.get('stop_keywords', [])

    money_cands = []
    for i, line in enumerate(lines):
        prices = re.findall(r'(-?\d+[.,]\d{2,3})', line) # 支援台幣整數或多位數
        if not prices: continue
        val = float(prices[-1].replace(',', '')) if curr == "TWD" else float(prices[-1].replace(',', '.'))
        score = i 
        if any(k in line.upper() for k in t_keys): score += 5000 
        money_cands.append({'val': val, 'score': score, 'idx': i})

    best = sorted(money_cands, key=lambda x: x['score'], reverse=True)[0] if money_cands else {'val': 0.0, 'idx': len(lines)}
    final_amt, total_idx = best['val'], best['idx']

    name_q = []
    for line in lines[1:total_idx]: # 從商店名稱後開始抓
        if any(sk in line.upper() for sk in s_keys): break
        if not is_unlikely_item(line, params): name_q.append(line)

    summary = "、".join(list(dict.fromkeys(name_q))[:3]) + ("等" if len(name_q) > 3 else "")
    return lines[0], final_amt, summary

# --- III. 同步與 UI 介面 (Sync & Interface) ---

def sync_to_sheets(df: pd.DataFrame, user_name: str, curr_code: str, target_id: str) -> bool:
    """執行數據同步至指定的雲端試算表"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(target_id)
        wks = sh.get_worksheet(0)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output = []
        for _, r in df.iterrows():
            base = r["外幣金額"] * r["匯率"]
            uid = hashlib.md5(f"{r['商店名稱']}{r['消費日期']}{r['外幣金額']}".encode()).hexdigest()
            output.append([now_str, user_name, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], curr_code, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"], uid])
        wks.append_rows(output, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        st.error(f"❌ 同步失敗: {e}"); return False

def main():
    st.set_page_config(page_title="考察支出登錄系統", layout="wide")
    init_session()
    T_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit?usp=sharing"

    st.title("📊 國外考察支出登錄統計系統")
    registry = load_project_registry()

    with st.sidebar:
        st.header("🏢 專案授權管理")
        st.link_button("📥 下載歸屬試算表範本", T_URL, use_container_width=True)
        st.markdown("---")
        if registry:
            sel_proj = st.selectbox("1. 選擇執行專案", list(registry.keys()))
            tid = registry[sel_proj]
            u_list = load_project_users(tid)
        else:
            st.warning("⚠️ 查無授權專案。"); tid = None; u_list = []

        st.markdown("---")
        st.header("⚙️ 辨識控制")
        debug = st.checkbox("🔍 OCR 偵錯模式")
        t_year = st.number_input("📅 年度鎖定", value=2025)
        if st.button("🧹 清空清單", use_container_width=True):
            st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_u = st.selectbox("報帳人員", u_list + ["其他"]) if u_list else st.text_input("報帳人員")
        final_u = st.text_input("輸入姓名") if sel_u == "其他" else sel_u
    with c2:
all_cfg = load_all_configs()

# 1. 取得不重複的大洲清單
continents = sorted(list(set(cfg['continent'] for cfg in all_cfg.values())))

with c2:
    # 第一階段：選擇大洲
    sel_continent = st.selectbox("🌍 區域", continents)
    
    # 第二階段：根據大洲過濾國家
    filtered_countries = [
        label for label, cfg in all_cfg.items() 
        if cfg['continent'] == sel_continent
    ]
    
    sel_label = st.selectbox("📍 記帳國家", filtered_countries)
    p = all_cfg[sel_label]
    with c3:
        f_rate = get_exchange_rate(p['currency_code'])
        m_rate = st.number_input(f"匯率 ({p['currency_code']})", value=float(f_rate), step=0.01, format="%.2f")
    with c4:
        fee = st.number_input("手續費(%)", value=1.5 if p['currency_code'] != "TWD" else 0.0) / 100
        if tid: st.link_button("📂 開啟試算表", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

    st.markdown("---")
    files = st.file_uploader("📸 批次上傳收據", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
        if not tid: st.error("❌ 未選擇專案。")
        else:
            new_batch = []
            try:
                client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
                for f in files:
                    content = f.read(); f_hash = calculate_hash(content)
                    if f_hash in st.session_state['processed_hashes']: continue
                    txt = client.document_text_detection(image=vision.Image(content=content)).full_text_annotation.text
                    if debug: st.code(f"--- {f.name} --- \n{txt}")
                    d, d_idx = normalize_date_pro(txt, p.get('month_map', {}), t_year)
                    v, a, it = extract_data(txt, p, d_idx)
                    new_batch.append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":m_rate, "備註":""})
                    st.session_state['processed_hashes'].add(f_hash)
                st.session_state['data'].extend(new_batch)
                st.rerun()
            except Exception as e: st.error(f"辨識錯誤: {e}")

    if st.session_state['data']:
        df = pd.DataFrame(st.session_state['data'])
        edf = st.data_editor(df, use_container_width=True, num_rows="dynamic")
        total = (edf["外幣金額"] * edf["匯率"] * (1 + fee)).sum()
        st.metric(f"批次總計 ({sel_proj})", f"NT$ {int(total):,}")
        if st.button("📤 同步至雲端", type="primary", use_container_width=True):
            if sync_to_sheets(edf, final_u, p['currency_code'], tid):
                st.balloons(); st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()

if __name__ == "__main__":
    main()