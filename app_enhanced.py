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
# I. 基礎設施：中央註冊表與權限驗證
# ==========================================

REGISTRY_ID = "1rPQlGHtvx6M630vnZ_FANMRyR_EnMrzje85V3mZ2H0M"
TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit"

def get_gc():
    """使用 Service Account 權限連線 Google Sheets"""
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=60)
def load_central_registry():
    """載入註冊表資訊與 Auth 密碼"""
    try:
        gc = get_gc()
        sh = gc.open_by_key(REGISTRY_ID)
        # 1. 抓取 Auth 密碼 (分頁 Auth!A1)
        pwd = str(sh.worksheet("Auth").acell('A1').value).strip()
        # 2. 抓取專案清單 (四欄位格式)
        reg_sheet = sh.get_worksheet(0)
        data = reg_sheet.get_all_records()
        valid_p = {
            r["專案名稱"]: r["試算表 ID"] 
            for r in data if str(r.get("啟用狀態(請選TRUE)", "")).upper() == "TRUE"
        }
        return pwd, valid_p
    except Exception as e:
        st.error(f"❌ 中央註冊表讀取失敗: {e}")
        return "ADMIN", {}

# ==========================================
# II. 核心 VLM 辨識：利用「國家錨定」提升精準度
# ==========================================

def run_vlm_anchor_analysis(api_key, image_bytes, year, region, country):
    """將區域與國家作為 Prompt 錨定點"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        
        prompt = f"""
        你是一個財務審計專家。請分析這張來自 【{region} - {country}】 的收據。
        1. 基準年份: {year}。
        2. 請根據所在地點精確判定幣別(例如 EUR, JPY, USD)。
        3. 請回傳純 JSON 格式: 
           {{"shop": "商店名稱", "amount": 0.0, "date": "YYYY-MM-DD", "currency": "幣別", "items": "品項摘要"}}
        注意: 處理歐陸格式時，將逗點(,)視為小數點。
        """
        img = Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, img])
        return json.loads(response.text.replace('```json', '').replace('```', '').strip())
    except: return None

# ==========================================
# III. UI 佈局 (完全復刻業務邏輯)
# ==========================================

st.set_page_config(page_title="考察支出登錄系統 v4.6.3", layout="wide")
if 'data' not in st.session_state: st.session_state['data'] = []

# --- 側邊欄：專案選擇與授權 ---
admin_pwd, project_list = load_central_registry()

with st.sidebar:
    st.title("🛡️ 系統控制中心")
    # 1. 專案選擇 (讀取自註冊表)
    sel_p = st.selectbox("📁 選擇執行專案", list(project_list.keys()) + ["+ 註冊新專案"])
    
    st.divider()
    # 2. 授權門檻
    auth_method = st.radio("🔑 辨識彈藥來源", ["開發者 API (需密碼)", "自備個人 API KEY"])
    active_key = None
    if auth_method == "開發者 API (需密碼)":
        if st.text_input("輸入 A1 授權碼", type="password") == admin_pwd:
            st.success("✅ 授權通過")
            active_key = st.secrets["gemini_api_key"]
    else:
        user_key = st.text_input("貼上 Gemini API KEY")
        if user_key: active_key = user_key

    st.divider()
    target_year = st.number_input("📅 基準年度", value=2026)
    if st.button("🗑️ 清空所有辨識紀錄"):
        st.session_state['data'] = []; st.rerun()

# --- 主畫面：功能頁籤 ---
tab_main, tab_reg = st.tabs(["🚀 辨識與同步", "🆕 新增專案註冊"])

with tab_main:
    if sel_p == "+ 註冊新專案":
        st.warning("請切換至『新增專案註冊』分頁。")
    else:
        tid = project_list[sel_p]
        # 動態載入該專案的「人員名單」
        try:
            gc = get_gc()
            p_sh = gc.open_by_key(tid)
            u_names = p_sh.worksheet("人員名單").col_values(1)[1:]
        except: u_names = []

        # 頂部選擇區 (錨定資訊)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            sel_user = st.selectbox("登錄者姓名", u_names + ["其他人員"])
            final_user = st.text_input("請輸入姓名", value="") if sel_user == "其他人員" else sel_user
        with c2:
            sel_region = st.selectbox("前往區域", ["歐洲", "亞洲", "美洲", "其他"])
            sel_country = st.text_input("前往國家", value="德國")
        with c3:
            fee_rate = st.number_input("手續費率 (%)", value=1.5) / 100
        with c4:
            st.write("🔗 快速連結")
            st.link_button("開啟專案試算表", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

        st.divider()
        files = st.file_uploader("批次上傳單據影像", accept_multiple_files=True, type=['jpg','png','jpeg'])

        if st.button("⚡ 啟動 AI 自動偵察", type="primary"):
            if not active_key:
                st.error("❌ 尚未取得 API 授權，請檢查側邊欄。")
            elif files:
                with st.spinner("AI 正根據區域錨定資訊進行分析..."):
                    for f in files:
                        content = f.read()
                        # 核心辨識：傳入區域與國家資訊
                        res = run_vlm_anchor_analysis(active_key, content, target_year, sel_region, sel_country)
                        if res:
                            uid = hashlib.md5(content + final_user.encode()).hexdigest()[:12]
                            st.session_state['data'].append({
                                "UID": uid, "商店名稱": res['shop'], "消費日期": res['date'], 
                                "外幣金額": res['amount'], "幣別": res['currency'], 
                                "品項摘要": res['items'], "備註": ""
                            })
                st.rerun()

    # --- 關鍵：資料預覽與編輯表格 ---
    if st.session_state['data']:
        st.subheader("📝 辨識結果預覽 (可直接修改內容)")
        df = pd.DataFrame(st.session_state['data'])
        # 恢復表格編輯功能
        edf = st.data_editor(df, use_container_width=True, num_rows="dynamic")
        
        if st.button("📤 確認無誤，同步至專案試算表"):
            with st.spinner("同步中..."):
                gc = get_gc()
                target_wks = gc.open_by_key(tid).get_worksheet(0)
                existing_uids = target_wks.col_values(13)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                to_append = []
                for _, r in edf.iterrows():
                    if r['UID'] not in existing_uids:
                        # 自動抓取匯率
                        try:
                            rate = yf.Ticker(f"{r['幣別']}TWD=X").fast_info['lastPrice'] if r['幣別'] != "TWD" else 1.0
                        except: rate = 1.0 # 抓取失敗則預設 1.0
                        
                        base_twd = round(r['外幣金額'] * rate, 0)
                        total_twd = round(base_twd * (1 + fee_rate), 0)
                        
                        # A-M 欄位對齊
                        row = [
                            now, final_user, r['商店名稱'], r['品項摘要'], r['消費日期'],
                            r['外幣金額'], r['幣別'], round(rate, 3), base_twd,
                            total_twd - base_twd, total_twd, r['備註'], r['UID']
                        ]
                        to_append.append(row)
                
                if to_append:
                    target_wks.append_rows(to_append, value_input_option='USER_ENTERED')
                    st.success(f"🎉 同步完成！新增 {len(to_append)} 筆資料。")
                    st.session_state['data'] = []
                else:
                    st.warning("⚠️ 資料已存在，未重複同步。")

with tab_reg:
    st.header("🛠️ 專案註冊流程")
    st.markdown(f"1. [點此複製範本]({TEMPLATE_URL}) -> 檔案 -> 建立副本。\n2. 複製副本 URL 中的 ID。\n3. 在下方填寫並提交。")
    new_n = st.text_input("新專案名稱")
    new_id = st.text_input("新試算表 ID")
    if st.button("✅ 提交註冊"):
        if new_n and new_id:
            try:
                gc = get_gc()
                gc.open_by_key(REGISTRY_ID).get_worksheet(0).append_row([
                    datetime.now().strftime("%Y/%m/%d %H:%M"), new_n, new_id, "TRUE"
                ])
                st.success("註冊成功！")
            except Exception as e: st.error(f"失敗: {e}")