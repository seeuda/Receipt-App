# Base Indent: 0 spaces
import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, json, hashlib, yfinance as yf
from datetime import datetime
import google.generativeai as genai
from PIL import Image

# ==========================================
# I. 戰術參數與基礎設施
# ==========================================

REGISTRY_ID = "1rPQlGHtvx6M630vnZ_FANMRyR_EnMrzje85V3mZ2H0M"
TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit"

def init_session():
    if 'data' not in st.session_state: st.session_state['data'] = []
    if 'dev_auth_passed' not in st.session_state: st.session_state['dev_auth_passed'] = False

def get_gc():
    """初始化 Google Sheets 授權"""
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=300)
def load_registry():
    """讀取註冊表與 Auth 密碼"""
    try:
        gc = get_gc()
        sh = gc.open_by_key(REGISTRY_ID)
        # 讀取密碼
        pwd = str(sh.worksheet("Auth").acell('A1').value).strip()
        # 讀取專案
        reg_data = sh.get_worksheet(0).get_all_records()
        p_list = {r["專案名稱"]: r["試算表 ID"] for r in reg_data if str(r.get("啟用狀態","")).upper() == "TRUE"}
        return pwd, p_list
    except Exception as e:
        st.error(f"無法讀取註冊表: {e}")
        return "ADMIN_ONLY", {}

# ==========================================
# II. 核心 VLM 辨識邏輯
# ==========================================

def call_gemini_vlm(api_key, image_bytes, target_year):
    """執行 Gemini 1.5 Flash 辨識 (VLM 換腦)"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        prompt = f"""
        你是一個精密的財務審計助手。分析收據影像並回傳 JSON。
        年份基準: {target_year}。欄位: shop, amount(float), date(YYYY-MM-DD), currency, items。
        注意: 歐陸收據(如德文)的逗點(,)需轉為小數點。
        """
        img = Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, img])
        return json.loads(response.text.replace('```json', '').replace('```', '').strip())
    except Exception as e:
        st.error(f"辨識出錯: {e}")
        return None

# ==========================================
# III. UI 佈局 (完全復刻業務需求)
# ==========================================

st.set_page_config(page_title="考察支出登錄系統 v4.6", layout="wide")
init_session()

# 載入雲端配置
admin_pwd, project_dict = load_registry()

with st.sidebar:
    st.title("🛡️ 權限與參數")
    
    # API 彈藥分流邏輯
    st.subheader("🔑 API 授權模式")
    auth_mode = st.radio("選擇來源", ["開發者配額 (需密碼)", "自備 API KEY"])
    
    active_api_key = None
    if auth_mode == "開發者配額 (需密碼)":
        user_pwd = st.text_input("輸入 A1 授權碼", type="password")
        if user_pwd == admin_pwd:
            st.success("✅ 授權通過")
            active_api_key = st.secrets["gemini_api_key"]
            st.session_state['dev_auth_passed'] = True
    else:
        user_key = st.text_input("輸入個人 Gemini Key")
        if user_key: active_api_key = user_key

    st.divider()
    
    # 專案選擇
    st.subheader("📂 任務目標")
    selected_project = st.selectbox("選擇專案", list(project_dict.keys()) + ["+ 新增我的專案"])
    target_year = st.number_input("📅 西元年度", value=2026)
    
    if st.button("🗑️ 清空暫存"):
        st.session_state['data'] = []; st.rerun()

# --- 主介面 Tab 分流 ---
tab_main, tab_reg = st.tabs(["🚀 支出偵察與同步", "🆕 專案快速註冊"])

with tab_main:
    if selected_project == "+ 新增我的專案":
        st.warning("請先切換至『專案快速註冊』分頁完成註冊手續。")
    else:
        tid = project_dict[selected_project]
        # 讀取專案人員清單
        try:
            gc = get_gc()
            user_sheet = gc.open_by_key(tid).worksheet("人員名單")
            names = [n for n in user_sheet.col_values(1)[1:] if n.strip()]
        except: names = ["請先設定人員名單"]
        
        col_u, col_f = st.columns(2)
        with col_u: reporter = st.selectbox("報帳人姓名", names)
        with col_f: fee_rate = st.number_input("跨國手續費率 (%)", value=1.5) / 100
        
        st.link_button(f"查看 {selected_project} 試算表", f"https://docs.google.com/spreadsheets/d/{tid}/edit")
        
        files = st.file_uploader("批次上傳收據 (可多選)", accept_multiple_files=True, type=['jpg','jpeg','png'])
        
        if st.button("⚡ 啟動 AI 辨識", type="primary"):
            if not active_api_key:
                st.error("❌ 尚未取得 API 彈藥，請檢查側邊欄。")
            elif files:
                with st.spinner("VLM 深度理解中..."):
                    for f in files:
                        content = f.read()
                        res = call_gemini_vlm(active_api_key, content, target_year)
                        if res:
                            uid = hashlib.md5(content + reporter.encode()).hexdigest()[:12]
                            st.session_state['data'].append({
                                "UID": uid, "商店名稱": res['shop'], "消費日期": res['date'], 
                                "外幣金額": res['amount'], "幣別": res['currency'], 
                                "品項": res['items'], "備註": ""
                            })
                st.rerun()

    # 數據確認區 (A-M 欄位對齊)
    if st.session_state['data']:
        df = pd.DataFrame(st.session_state['data'])
        edf = st.data_editor(df, use_container_width=True, num_rows="dynamic")
        
        if st.button("📤 同步至雲端", type="secondary"):
            with st.spinner("正在進行 A-M 欄位映射與判重..."):
                try:
                    gc = get_gc()
                    sh = gc.open_by_key(tid).get_worksheet(0)
                    existing_uids = sh.col_values(13)
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    rows_to_send = []
                    for _, r in edf.iterrows():
                        if r['UID'] not in existing_uids:
                            # 匯率換算
                            rate = yf.Ticker(f"{r['幣別']}TWD=X").fast_info['lastPrice'] if r['幣別'] != "TWD" else 1.0
                            base_twd = round(r['外幣金額'] * rate, 0)
                            # $$Total = Base \times (1 + Fee)$$
                            total_twd = round(base_twd * (1 + fee_rate), 0)
                            
                            rows_to_send.append([
                                now, reporter, r['商店名稱'], r['品項'], r['消費日期'],
                                r['外幣金額'], r['幣別'], round(rate, 3), base_twd,
                                total_twd - base_twd, total_twd, r['備註'], r['UID']
                            ])
                    
                    if rows_to_send:
                        sh.append_rows(rows_to_send, value_input_option='USER_ENTERED')
                        st.success(f"🎉 成功同步 {len(rows_to_send)} 筆新帳目！")
                        st.session_state['data'] = []
                    else: st.warning("資料已存在或無更新。")
                except Exception as e: st.error(f"同步失敗: {e}")

with tab_reg:
    st.header("🛠️ 專案註冊流程")
    st.markdown(f"""
    1. **複製範本**：[點此開啟出國記帳試算表範本]({TEMPLATE_URL})
    2. **建立副本**：點選『檔案』 > 『建立副本』。
    3. **授權權限**：請確保試算表共用權限包含 `st.secrets` 中的 Service Account Email。
    4. **註冊 ID**：複製網址中 `/d/` 之後的那串 ID 到下方。
    """)
    
    reg_name = st.text_input("專案名稱 (例如: 2026 東京考察)")
    reg_id = st.text_input("試算表 ID (Spreadsheet ID)")
    
    if st.button("✅ 提交註冊至中央清單"):
        if reg_name and reg_id:
            try:
                gc = get_gc()
                reg_sh = gc.open_by_key(REGISTRY_ID).get_worksheet(0)
                reg_sh.append_row([datetime.now().strftime("%Y/%m/%d %H:%M"), reg_name, reg_id, "TRUE"])
                st.success("註冊成功！請重新整理頁面。")
            except Exception as e: st.error(f"註冊失敗: {e}")