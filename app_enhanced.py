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
# I. 基礎設施：中央註冊表與範本定義
# ==========================================

REGISTRY_ID = "1rPQlGHtvx6M630vnZ_FANMRyR_EnMrzje85V3mZ2H0M"
TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit"

def get_gc():
    """使用系統 Service Account 權限"""
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=60)
def fetch_registry_data():
    """讀取中央註冊表：時間戳記, 專案名稱, 試算表 ID, 啟用狀態"""
    try:
        gc = get_gc()
        sh = gc.open_by_key(REGISTRY_ID)
        # 1. 抓取 Auth 密碼
        auth_pwd = str(sh.worksheet("Auth").acell('A1').value).strip()
        # 2. 抓取專案清單 (分頁：表單回覆 1)
        raw_reg = sh.get_worksheet(0).get_all_records()
        valid_projects = {
            r["專案名稱"]: r["試算表 ID"] 
            for r in raw_reg 
            if str(r.get("啟用狀態(請選TRUE)", "")).upper() == "TRUE"
        }
        return auth_pwd, valid_projects
    except Exception as e:
        st.error(f"❌ 註冊表讀取失敗: {e}")
        return "ADMIN", {}

# ==========================================
# II. VLM 核心：多模態辨識
# ==========================================

def run_vlm_analysis(api_key, image_bytes, target_year):
    """Gemini 1.5 Flash 辨識引擎"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        prompt = f"分析此收據，回傳純 JSON 格式: {{'shop':'', 'amount':0.0, 'date':'YYYY-MM-DD', 'currency':'', 'items':''}}。年份基準為 {target_year}。"
        img = Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, img])
        return json.loads(response.text.replace('```json', '').replace('```', '').strip())
    except: return None

# ==========================================
# III. UI 佈局 (完全復刻 app_v2.py)
# ==========================================

st.set_page_config(page_title="考察支出登錄系統 v4.6.2", layout="wide")
if 'data' not in st.session_state: st.session_state['data'] = []

# --- SIDEBAR: 專案選擇與授權 ---
remote_pwd, project_list = fetch_registry_data()

with st.sidebar:
    st.title("🛡️ 戰術控制台")
    
    # 1. 專案選擇區 (最上方)
    st.subheader("📁 專案清單")
    selected_p = st.selectbox("選擇執行專案", list(project_list.keys()) + ["+ 註冊新專案"])
    
    st.divider()
    
    # 2. API 授權區
    st.subheader("🔑 API 彈藥")
    auth_mode = st.radio("授權模式", ["使用開發者金鑰 (需密碼)", "自備個人 API KEY"])
    active_key = None
    if auth_mode == "使用開發者金鑰 (需密碼)":
        if st.text_input("輸入 A1 授權碼", type="password") == remote_pwd:
            st.success("✅ 授權成功")
            active_key = st.secrets["gemini_api_key"]
    else:
        u_key = st.text_input("輸入你的 Gemini API KEY")
        if u_key: active_key = u_key

    st.divider()
    target_year = st.number_input("📅 基準年度", value=2026)
    if st.button("🗑️ 清空暫存清單"):
        st.session_state['data'] = []; st.rerun()

# --- MAIN: 專案內容與辨識 ---
tab_main, tab_reg = st.tabs(["🚀 辨識同步", "🆕 快速註冊"])

with tab_main:
    if selected_p == "+ 註冊新專案":
        st.warning("請切換至『快速註冊』分頁完成試算表 ID 登錄。")
    else:
        tid = project_list[selected_p]
        # 讀取「人員名單」
        try:
            gc = get_gc()
            user_sheet = gc.open_by_key(tid).worksheet("人員名單")
            names = [n for n in user_sheet.col_values(1)[1:] if n.strip()]
        except: names = []

        # 頁面上方選擇區 (4 欄位佈局)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            sel_user = st.selectbox("報帳人員", names + ["其他人員"])
            final_user = st.text_input("輸入人員姓名", value="") if sel_user == "其他人員" else sel_user
        with c2:
            region = st.selectbox("區域", ["歐洲", "亞洲", "美洲", "其他"])
            country = st.text_input("國家", value="德國")
        with c3:
            fee_rate = st.number_input("手續費 (%)", value=1.5) / 100
        with c4:
            st.write("🔗 專案試算表")
            st.link_button("開啟 Google Sheets", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

        st.divider()
        
        # 批次上傳與辨識
        files = st.file_uploader("批次上傳收據照片", accept_multiple_files=True, type=['jpg','png','jpeg'])
        if st.button("🚀 執行 AI 自動偵察", type="primary") and active_key:
            if files:
                with st.spinner("Gemini VLM 分析中..."):
                    for f in files:
                        content = f.read()
                        res = run_vlm_analysis(active_key, content, target_year)
                        if res:
                            uid = hashlib.md5(content + final_user.encode()).hexdigest()[:12]
                            st.session_state['data'].append({
                                "商店": res['shop'], "日期": res['date'], "金額": res['amount'],
                                "幣別": res['currency'], "品項": res['items'], "UID": uid
                            })
                st.rerun()

    # 數據編輯與 A-M 欄位同步
    if st.session_state['data']:
        df = pd.DataFrame(st.session_state['data'])
        edf = st.data_editor(df, use_container_width=True, num_rows="dynamic")
        
        if st.button("📤 確認無誤，同步至雲端"):
            with st.spinner("正在進行 A-M 欄位轉換..."):
                gc = get_gc()
                sh = gc.open_by_key(tid).get_worksheet(0)
                uids = sh.col_values(13)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                rows = []
                for _, r in edf.iterrows():
                    if r['UID'] not in uids:
                        # 即時匯率
                        rate = yf.Ticker(f"{r['幣別']}TWD=X").fast_info['lastPrice'] if r['幣別'] != "TWD" else 1.0
                        base_twd = round(r['金額'] * rate, 0)
                        total_twd = round(base_twd * (1 + fee_rate), 0)
                        # A-M 欄位：記錄時間, 登錄者, 商店, 品項, 日期, 外幣, 幣別, 匯率, 原台幣, 手續費, 總計, 備註, UID
                        rows.append([
                            now, final_user, r['商店'], r['品項'], r['日期'],
                            r['金額'], r['幣別'], round(rate, 3), base_twd,
                            total_twd - base_twd, total_twd, "", r['UID']
                        ])
                if rows:
                    sh.append_rows(rows, value_input_option='USER_ENTERED')
                    st.success(f"✅ 成功同步 {len(rows)} 筆！")
                    st.session_state['data'] = []
                else: st.warning("無新資料或 UID 重複。")

with tab_reg:
    st.header("🛠️ 建立新專案步驟")
    st.markdown(f"""
    1. **複製範本**：[點此複製考察記帳範本]({TEMPLATE_URL})
    2. **取得 ID**：複製新試算表網址中 `/d/` 與 `/edit` 之間的那串字元。
    3. **完成註冊**：在下方填寫專案名稱與 ID 並提交。
    """)
    reg_name = st.text_input("專案名稱")
    reg_id = st.text_input("試算表 ID")
    if st.button("✅ 提交註冊"):
        if reg_name and reg_id:
            try:
                gc = get_gc()
                reg_sh = gc.open_by_key(REGISTRY_ID).get_worksheet(0)
                reg_sh.append_row([datetime.now().strftime("%Y/%m/%d"), reg_name, reg_id, "TRUE"])
                st.success("註冊成功！請刷新頁面選取。")
            except Exception as e: st.error(f"註冊失敗: {e}")