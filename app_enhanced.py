# Base Indent: 0 spaces
import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, hashlib, yfinance as yf
from datetime import datetime, timedelta
import google.generativeai as genai
from PIL import Image

# ==========================================
# I. 基礎設施與金鑰初始化 (保留註冊表機制)
# ==========================================

def init_session() -> None:
    if 'data' not in st.session_state: st.session_state['data'] = []
    if 'auth_active' not in st.session_state: st.session_state['auth_active'] = False

def get_gspread_client(creds_info=None):
    if creds_info is None: creds_info = st.secrets["gcp_service_account"]
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=scope)
    return gspread.authorize(creds)

def init_gemini():
    """初始化 Gemini 1.5 Flash 引擎"""
    genai.configure(api_key=st.secrets["gemini_api_key"])
    return genai.GenerativeModel('models/gemini-1.5-flash')

def calculate_salted_uid(file_content: bytes, user_name: str) -> str:
    file_hash = hashlib.md5(file_content).hexdigest()
    return hashlib.md5(f"{file_hash}{user_name}".encode()).hexdigest()

# ==========================================
# II. 核心 VLM 辨識邏輯 (取代原本的 Regex 解析)
# ==========================================

def get_vlm_analysis(image_bytes: bytes, target_year: int, country_name: str):
    """
    使用 Gemini 1.5 Flash 直接提取結構化資料。
    不再需要 classify_diagnose 與 extract_structured_data。
    """
    model = init_gemini()
    prompt = f"""
    你是一個專業的財務審計員。請分析這張來自 {country_name} 的收據影像。
    當前年份基準為 {target_year} 年。
    
    請回傳純 JSON 格式，包含以下欄位：
    - shop: 商店名稱
    - amount: 總金額 (float, 僅數字)
    - date: 日期 (格式 YYYY-MM-DD，若年份缺失請補上 {target_year})
    - currency: 幣別代碼 (例如 EUR, JPY, TWD)
    - items: 關鍵品項摘要 (最多三個)
    
    注意：處理歐陸收據時，請將逗點(,)正確解析為小數點。只回傳 JSON，不要有 Markdown。
    """
    img = Image.open(io.BytesIO(image_bytes))
    try:
        response = model.generate_content([prompt, img])
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_json)
    except:
        return {"shop": "辨識失敗", "amount": 0.0, "date": f"{target_year}-01-01", "currency": "TWD", "items": "N/A"}

# ==========================================
# III. UI 佈局 (完全遵循 v2 版架構)
# ==========================================

def main():
    st.set_page_config(page_title="考察支出登錄系統 v4.5", layout="wide")
    init_session()
    
    # 載入註冊表 (保留 admin_registry_id 機制)
    try:
        gc_sys = get_gspread_client(st.secrets["gcp_service_account"])
        sh_reg = gc_sys.open_by_key(st.secrets["admin_registry_id"])
        reg_data = sh_reg.get_worksheet(0).get_all_records()
        project_registry = {r["專案名稱"]: r["試算表 ID"] for r in reg_data if str(r.get("啟用狀態","")).upper() == "TRUE"}
    except: project_registry = {}

    with st.sidebar:
        st.header("🏢 專案與授權")
        # 1. 專案選擇與人員清單聯動
        tid, user_list = None, []
        if project_registry:
            sel_p = st.selectbox("選擇執行專案", list(project_registry.keys()))
            tid = project_registry[sel_p]
            try:
                wks_user = gc_sys.open_by_key(tid).worksheet("人員名單")
                user_list = [n for n in wks_user.col_values(1)[1:] if n.strip()]
            except: user_list = []
        
        st.divider()
        # 2. 授權驗證 (保留遠端 A1 密碼機制)
        try:
            auth_sh = gc_sys.open_by_key("1rPQlGHtvx6M630vnZ_FANMRyR_EnMrzje85V3mZ2H0M")
            remote_pwd = str(auth_sh.worksheet("Auth").acell('A1').value).strip()
            pwd_input = st.text_input("輸入授權密碼", type="password")
            if pwd_input == remote_pwd:
                st.success("✅ 授權成功")
                st.session_state['auth_active'] = True
        except: pass

        target_year = st.number_input("📅 年度鎖定", value=2026)
        if st.button("清空暫存列表"):
            st.session_state['data'] = []; st.rerun()

    # --- 主畫面區 ---
    st.title("📸 考察支出登錄系統 v4.5 (VLM)")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        sel_u = st.selectbox("報帳人員", user_list + ["其他"]) if user_list else st.text_input("人員姓名")
        final_u = st.text_input("確認姓名", value=sel_u) if sel_u == "其他" else sel_u
    with col2:
        # 此處可簡化，因為 Gemini 不再需要複雜的 configs/*.json，只要國家名
        country_name = st.text_input("📍 記帳國家", value="德國")
    with col3:
        # 預設匯率，辨識後會手動調整
        f_rate = st.number_input("匯率 (手動修正)", value=35.0, step=0.01)
    with col4:
        fee_rate = st.number_input("手續費 (%)", value=1.5, step=0.1) / 100
        if tid: st.link_button("📂 開啟專案 Sheet", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

    st.divider()
    files = st.file_uploader("批次上傳收據", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files and st.button("🚀 執行 AI 自動辨識", type="primary"):
        if not tid or not st.session_state['auth_active']:
            st.error("❌ 拒絕執行：請確認專案已選擇且完成授權。")
        else:
            with st.spinner("Gemini 正在理解語義..."):
                for f in files:
                    content = f.read()
                    uid = calculate_salted_uid(content, final_u)
                    
                    # 調用 Gemini 引擎
                    res = get_vlm_analysis(content, target_year, country_name)
                    
                    # 嘗試抓取即時匯率 (yfinance)
                    try:
                        ticker = f"{res['currency']}TWD=X"
                        live_rate = yf.Ticker(ticker).fast_info['lastPrice']
                    except: live_rate = f_rate

                    st.session_state['data'].append({
                        "商店名稱": res['shop'],
                        "參考品項": res['items'],
                        "消費日期": res['date'],
                        "外幣金額": res['amount'],
                        "匯率": round(live_rate, 2),
                        "幣別": res['currency'],
                        "備註": "",
                        "UID": uid
                    })
            st.rerun()

    if st.session_state['data']:
        df = pd.DataFrame(st.session_state['data'])
        edf = st.data_editor(df, use_container_width=True, num_rows="dynamic")
        
        if st.button("📤 同步至雲端 (A-M 欄位)", type="primary"):
            try:
                sh = gc_sys.open_by_key(tid).get_worksheet(0)
                uids = sh.col_values(13)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                to_append = []
                for _, r in edf.iterrows():
                    # 財務公式：Total = Base * (1 + Fee)
                    base_twd = r["外幣金額"] * r["匯率"]
                    fee_twd = base_twd * fee_rate
                    total_twd = base_twd + fee_twd
                    
                    # 依照你要求的 A-M 欄位順序
                    row = [
                        now, final_u, r["商店名稱"], r["參考品項"], r["消費日期"],
                        r["外幣金額"], r["幣別"], r["匯率"], round(base_twd, 0),
                        round(fee_twd, 0), round(total_twd, 0), r["備註"], r["UID"]
                    ]
                    
                    if r["UID"] not in uids:
                        to_append.append(row)
                
                if to_append:
                    sh.append_rows(to_append, value_input_option='USER_ENTERED')
                    st.success(f"✅ 同步完成！新增 {len(to_append)} 筆")
                    st.session_state['data'] = []
                    st.rerun()
                else:
                    st.warning("⚠️ 資料已存在，未重複新增。")
            except Exception as e: st.error(f"同步失敗: {e}")

if __name__ == "__main__": main()