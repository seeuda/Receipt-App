import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, hashlib
from datetime import datetime, timedelta
from google.cloud import vision
import yfinance as yf
from PIL import Image
from typing import Dict, List, Tuple, Optional

# --- I. 數據中心與匯率引擎 (Data & FX Engine) ---

def init_session() -> None:
    """初始化工作區狀態"""
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'processed_hashes' not in st.session_state: 
        st.session_state['processed_hashes'] = set()

def calculate_hash(file_content: bytes) -> str:
    """計算檔案 MD5 指紋"""
    return hashlib.md5(file_content).hexdigest()

def get_gspread_client():
    """從 Secrets 安全授權 gspread"""
    creds_info = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        creds_info, 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=3600)
def get_rate_by_date(currency_code: str, target_date: datetime.date) -> float:
    """依據單據日期檢索歷史匯率"""
    if currency_code == "TWD": return 1.0
    try:
        ticker = yf.Ticker(f"{currency_code}TWD=X")
        start_d = target_date
        end_d = target_date + timedelta(days=3)
        hist = ticker.history(start=start_d, end=end_d)
        if not hist.empty: return round(hist['Close'].iloc[0], 2)
        fallback = ticker.history(period="1mo")
        return round(fallback['Close'].asof(pd.Timestamp(target_date)), 2)
    except Exception: return 35.0

def load_project_registry() -> Dict[str, str]:
    """動態載入專案註冊表"""
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
    except Exception: return {}

def load_project_users(target_sheet_id: str) -> List[str]:
    """從個別專案動態載入人員名單"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(target_sheet_id)
        wks = sh.worksheet("人員名單")
        names = wks.col_values(1)[1:] 
        return [n for n in names if n.strip()]
    except Exception: return []

def load_all_configs() -> Dict:
    """載入 40 國參數，支援大洲分類與優先權屬性"""
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

# --- II. 智慧辨識引擎 (OCR & Inference) ---

def normalize_date_pro(text: str, month_map: Dict, target_year: int):
    """智慧年份補全：若 OCR 缺失年份，結合鎖定年度"""
    t_clean = text.replace("'", " ").replace("/", " ").replace("-", " ").replace(".", " ")
    for m_n, m_v in sorted(month_map.items(), key=lambda x: len(x[0]), reverse=True):
        t_clean = re.sub(rf'\b{m_n}\b', f" {m_v} ", t_clean, flags=re.IGNORECASE)
    lines = t_clean.splitlines()
    for i, line in enumerate(lines):
        full_m = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        for d_s, m_s, y_s in full_m:
            y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == target_year:
                try: return datetime(y, int(m_s), int(d_s)).date(), i
                except: continue
        part_m = re.findall(r'\b(\d{1,2})\s+(\d{1,2})\b', line)
        for d_s, m_s in part_m:
            try:
                if 1 <= int(m_s) <= 12 and 1 <= int(d_s) <= 31:
                    return datetime(target_year, int(m_s), int(d_s)).date(), i
            except: continue
    return datetime(target_year, 1, 1).date(), -1

def extract_data(text: str, params: Dict, date_idx: int) -> Tuple[str, float, str]:
    """標靶修復資料提取"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    curr = params.get('currency_code', '').upper()
    t_keys, s_keys = params.get('keywords', []), params.get('stop_keywords', [])
    money_cands = []
    for i, line in enumerate(lines):
        prices = re.findall(r'(-?\d+[.,]\d{2,3})', line)
        if not prices: continue
        val_str = prices[-1].replace(',', '') if curr == "TWD" else prices[-1].replace(',', '.')
        try:
            val = float(val_str); score = i 
            if any(k in line.upper() for k in t_keys): score += 5000 
            money_cands.append({'val': val, 'score': score, 'idx': i})
        except: continue
    best = sorted(money_cands, key=lambda x: x['score'], reverse=True)[0] if money_cands else {'val': 0.0, 'idx': len(lines)}
    final_amt, total_idx = best['val'], best['idx']
    name_q = []
    for line in lines[1:total_idx]:
        if any(sk in line.upper() for sk in s_keys): break
        name_q.append(line)
    summary = "、".join(list(dict.fromkeys(name_q))[:3]) + ("等" if len(name_q) > 3 else "")
    return lines[0], final_amt, summary

# --- III. 同步與去重邏輯 ---

def sync_to_sheets(df: pd.DataFrame, user_name: str, curr_code: str, target_id: str) -> Tuple[int, int]:
    """執行同步並防止跨人重複輸入 (UID 比對)"""
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(target_id)
        wks = sh.get_worksheet(0)
        existing_uids = set(wks.col_values(13)[1:]) 
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        to_append, skip_count = [], 0
        for _, r in df.iterrows():
            uid = hashlib.md5(f"{r['商店名稱']}{r['消費日期']}{r['外幣金額']}".encode()).hexdigest()
            if uid in existing_uids:
                skip_count += 1; continue
            base = r["外幣金額"] * r["匯率"]
            to_append.append([now_str, user_name, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], curr_code, r["匯率"], round(base,0), round(base*0.015,0), round(base*1.015,0), r["備註"], uid])
        if to_append: wks.append_rows(to_append, value_input_option='USER_ENTERED')
        return len(to_append), skip_count
    except Exception: return 0, 0

# --- IV. Streamlit UI 系統 ---

def main():
    st.set_page_config(page_title="考察支出登錄系統", layout="wide")
    init_session()
    TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit?usp=sharing"
    all_cfg = load_all_configs()
    project_registry = load_project_registry()

    with st.sidebar:
        st.header("🏢 專案授權管理")
        if project_registry:
            selected_project = st.selectbox("1. 選擇執行專案", list(project_registry.keys()))
            target_sheet_id = project_registry[selected_project]
            project_users = load_project_users(target_sheet_id)
        else:
            st.warning("⚠️ 查無授權專案。"); target_sheet_id = None; project_users = []
        st.markdown("---")
        st.info("💡 沒有您的專案？請複製範本、建立新專案並完成授權。")
        st.link_button("📥 連結範本建立歸屬試算表", TEMPLATE_URL, use_container_width=True)
        st.markdown("---")
        st.header("⚙️ 辨識與控制")
        target_year = st.number_input("📅 年度鎖定", value=2025)
        debug_mode = st.checkbox("🔍 OCR 偵錯模式")
        if st.button("清空目前列表", use_container_width=True):
            st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_u = st.selectbox("報帳人員", project_users + ["其他"]) if project_users else st.text_input("人員姓名")
        final_u = st.text_input("確認姓名") if sel_u == "其他" else sel_u

    with c2:
        # 大洲過濾與優先權排序邏輯
        continents = sorted(list(set(cfg['continent'] for cfg in all_cfg.values())))
        sel_continent = st.selectbox("🌍 區域", continents)
        
        country_items = []
        for label, cfg in all_cfg.items():
            if cfg['continent'] == sel_continent:
                prio = cfg.get('priority', 100)
                sub = cfg.get('sub_region', '其他')
                display_name = f"[{sub}] {label}"
                country_items.append((prio, sub, display_name, label))
        
        # 多重排序：優先權 > 子區域 > 名稱
        sorted_countries = sorted(country_items, key=lambda x: (x[0], x[1], x[2]))
        final_labels = [item[2] for item in sorted_countries]
        sel_display = st.selectbox("📍 記帳國家", final_labels)
        original_label = next(item[3] for item in sorted_countries if item[2] == sel_display)
        p = all_cfg[original_label]

    with c3:
        f_rate = get_rate_by_date(p['currency_code'], datetime.now().date())
        m_rate = st.number_input(f"預設匯率 ({p['currency_code']})", value=float(f_rate), step=0.01)
    with c4:
        fee = st.number_input("手續費(%)", value=1.5 if p['currency_code'] != "TWD" else 0.0) / 100
        if target_sheet_id: st.link_button("📂 開啟試算表", f"https://docs.google.com/spreadsheets/d/{target_sheet_id}/edit")

    st.markdown("---")
    files = st.file_uploader("📸 批次上傳收據 (建議單次 < 20 張)", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files:
        with st.expander("🖼️ 影像預覽與狀態 (點擊展開)", expanded=False):
            img_cols = st.columns(5)
            for idx, f in enumerate(files):
                f.seek(0); f_bytes = f.read(); f_hash = calculate_hash(f_bytes)
                with img_cols[idx % 5]:
                    st.image(Image.open(io.BytesIO(f_bytes)), use_container_width=True)
                    st.caption(f"#{idx+1} {'⚠️ 已辨識' if f_hash in st.session_state['processed_hashes'] else '🟢 待辨識'}")

    if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
        if not target_sheet_id: st.error("❌ 未選擇專案")
        else:
            try:
                client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"]))
                for f in files:
                    f.seek(0) # 修正：重置文件指標
                    content = f.read(); f_hash = calculate_hash(content)
                    if f_hash in st.session_state['processed_hashes']: continue
                    txt = client.document_text_detection(image=vision.Image(content=content)).full_text_annotation.text
                    if debug_mode: st.code(txt)
                    d, d_idx = normalize_date_pro(txt, p.get('month_map', {}), target_year)
                    v, a, it = extract_data(txt, p, d_idx)
                    auto_rate = get_rate_by_date(p['currency_code'], d)
                    st.session_state['data'].append({"商店名稱":v, "參考品項":it, "消費日期":d, "外幣金額":a, "匯率":auto_rate, "備註":""})
                    st.session_state['processed_hashes'].add(f_hash)
                st.rerun()
            except Exception as e: st.error(f"辨識錯誤: {e}")

    if st.session_state['data']:
        st.markdown("### 📝 暫存編輯區")
        df_temp = pd.DataFrame(st.session_state['data'])
        edf = st.data_editor(df_temp, use_container_width=True)
        btn_c1, btn_c2 = st.columns(2)
        with btn_c1:
            if st.button("🔄 依日期重抓匯率", use_container_width=True):
                for i, row in edf.iterrows(): edf.at[i, '匯率'] = get_rate_by_date(p['currency_code'], row['消費日期'])
                st.session_state['data'] = edf.to_dict('records'); st.rerun()
        with btn_c2:
            if st.button("📤 同步至雲端", type="primary", use_container_width=True):
                success, skipped = sync_to_sheets(edf, final_u, p['currency_code'], target_sheet_id)
                if success > 0 or skipped > 0:
                    st.success(f"✅ 完成！同步 {success} 筆，偵測重複跳過 {skipped} 筆。")
                    if success > 0: st.balloons()
                    st.session_state['data'] = []; st.session_state['processed_hashes'] = set(); st.rerun()

if __name__ == "__main__":
    main()