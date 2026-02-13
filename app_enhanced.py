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
# I. 基礎設施：中央控管與環境初始化
# ==========================================

REGISTRY_ID = "1rPQlGHtvx6M630vnZ_FANMRyR_EnMrzje85V3mZ2H0M"
TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit"

# 靜態國家錨定資料 (由 generate_configs.py 精煉)
COUNTRIES_MASTER = {
    "tw": {"name": "台灣", "currency": "TWD", "hint": "."},
    "jp": {"name": "日本", "currency": "JPY", "hint": "."},
    "de": {"name": "德國", "currency": "EUR", "hint": ","},
    "vn": {"name": "越南", "currency": "VND", "hint": ","},
    "us": {"name": "美國", "currency": "USD", "hint": "."},
    "fr": {"name": "法國", "currency": "EUR", "hint": ","},
    "gb": {"name": "英國", "currency": "GBP", "hint": "."}
}

def get_gc():
    """使用 Service Account 權限連線 Google Sheets"""
    try:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], 
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ GCP 授權失敗，請檢查 Secrets 設定: {e}")
        return None

@st.cache_data(ttl=60)
def load_bootstrap_config():
    """載入中央註冊表：專案清單與 Auth 密碼"""
    gc = get_gc()
    if not gc: return "ADMIN", {}
    try:
        sh = gc.open_by_key(REGISTRY_ID)
        # 1. 抓取 Auth!A1 密碼 (開發者 API 門檻)
        auth_pwd = str(sh.worksheet("Auth").acell('A1').value).strip()
        # 2. 抓取專案清單 (欄位：時間戳記, 專案名稱, 試算表 ID, 啟用狀態(請選TRUE))
        raw_reg = sh.get_worksheet(0).get_all_records()
        valid_p = {
            r["專案名稱"]: r["試算表 ID"] 
            for r in raw_reg if str(r.get("啟用狀態(請選TRUE)", "")).upper() == "TRUE"
        }
        return auth_pwd, valid_p
    except Exception as e:
        st.error(f"⚠️ 讀取中央註冊表失敗: {e}")
        return "ADMIN", {}

def run_vlm_engine(api_key, image_bytes, year, country_name):
    """Gemini 1.5 Flash 辨識引擎 (國家錨定版)"""
    try:
        genai.configure(api_key=api_key)
        # 顯性路徑修復 NotFound 錯誤
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        
        # 尋找國家對應的提示
        hint = "."
        for k, v in COUNTRIES_MASTER.items():
            if v['name'] == country_name:
                hint = v['hint']
                break

        prompt = f"""
        你是一位專業審計師。分析這張來自【{country_name}】的收據影像。
        1. 當前年份基準: {year}。
        2. 請根據所在地點判定正確幣別。
        3. 金額書寫習慣提示: 此國可能使用 '{hint}' 作為小數點。
        請回傳純 JSON 格式 (不要 Markdown): 
        {{"shop": "商店名稱", "amount": 0.0, "date": "YYYY-MM-DD", "currency": "幣別", "items": "品項摘要"}}
        """
        img = Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, img])
        # 清理 JSON 標籤
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_text)
    except Exception as e:
        st.error(f"⚠️ AI 辨識失敗: {e}")
        return None

# ==========================================
# II. UI 與 任務控制流 (MVC)
# ==========================================

st.set_page_config(page_title="考察支出登錄系統 v4.6.7", layout="wide")

if 'data' not in st.session_state: st.session_state['data'] = []

# 啟動系統加載
admin_pwd, p_dict = load_bootstrap_config()

with st.sidebar:
    st.title("🛡️ 系統指揮中心")
    # 1. 專案選擇 (優先權最高)
    sel_p = st.selectbox("🎯 選擇執行專案", list(p_dict.keys()) + ["+ 註冊新專案"])
    
    st.divider()
    # 2. 授權分流
    auth_mode = st.radio("🔑 API 來源", ["開發者配額 (需密碼)", "自備 API KEY"])
    active_api_key = None
    if auth_mode == "開發者配額 (需密碼)":
        pwd_input = st.text_input("輸入 Auth!A1 授權密碼", type="password")
        if pwd_input == admin_pwd:
            st.success("✅ 授權成功")
            active_api_key = st.secrets["gemini_api_key"]
    else:
        user_key = st.text_input("貼上個人 Gemini API KEY")
        if user_key: active_api_key = user_key

    st.divider()
    target_year = st.number_input("📅 基準年度", value=2026)
    if st.button("🗑️ 清空所有辨識紀錄"):
        st.session_state['data'] = []; st.rerun()

# --- 主畫面分流 ---
tab_main, tab_reg = st.tabs(["🚀 辨識同步任務", "🆕 專案註冊"])

with tab_main:
    if sel_p == "+ 註冊新專案":
        st.info("請切換至『專案註冊』分頁。")
    elif p_dict:
        tid = p_dict[sel_p]
        # 動態載入所選專案的人員名單
        try:
            gc = get_gc()
            p_sh = gc.open_by_key(tid)
            names = [n for n in p_sh.worksheet("人員名單").col_values(1)[1:] if n.strip()]
        except: names = []

        # UI 頂部：登錄者與國家錨定 (4 欄位)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            u_sel = st.selectbox("登錄者姓名", names + ["其他人員"])
            f_user = st.text_input("手動輸入姓名", value="") if u_sel == "其他人員" else u_sel
        with c2:
            # 國家錨定：提供常用選單並支援自定義
            common_countries = [v['name'] for v in COUNTRIES_MASTER.values()]
            f_country = st.selectbox("📍 單據國家 (AI 錨定)", common_countries + ["其他"])
            if f_country == "其他": f_country = st.text_input("請輸入國家名稱", value="德國")
        with c3:
            fee_rate = st.number_input("手續費率 (%)", value=1.5) / 100
        with c4:
            st.write("🔗 雲端試算表")
            st.link_button("開啟專案 Sheet", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

        st.divider()
        files = st.file_uploader("批次上傳單據照片 (多選)", accept_multiple_files=True, type=['jpg','png','jpeg'])

        if st.button("⚡ 啟動 AI 自動偵察", type="primary"):
            if not active_api_key:
                st.error("❌ 尚未取得 API 權限，請檢查密碼或 API KEY。")
            elif files:
                with st.spinner(f"正在針對 {f_country} 收據進行 VLM 語義分析..."):
                    new_batch = []
                    for f in files:
                        content = f.read()
                        res = run_vlm_engine(active_api_key, content, target_year, f_country)
                        if res:
                            uid = hashlib.md5(content + f_user.encode()).hexdigest()[:12]
                            new_batch.append({
                                "UID": uid, "商店名稱": res['shop'], "日期": res['date'], 
                                "外幣金額": res['amount'], "幣別": res['currency'], 
                                "品項摘要": res['items'], "備註": ""
                            })
                    st.session_state['data'] = new_batch
                    st.rerun()

    # --- 關鍵：數據編輯表格與 A-M 欄位同步 ---
    if st.session_state['data']:
        st.subheader("📝 辨識結果核對 (可直接點擊格子修改)")
        df = pd.DataFrame(st.session_state['data'])
        edf = st.data_editor(df, use_container_width=True, num_rows="dynamic")
        
        if st.button("📤 確認無誤，同步至雲端試算表"):
            with st.spinner("計算匯率並同步 A-M 欄位中..."):
                try:
                    gc = get_gc()
                    sh = gc.open_by_key(tid).get_worksheet(0)
                    existing_uids = sh.col_values(13)
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    rows_to_append = []
                    for _, r in edf.iterrows():
                        if r['UID'] not in existing_uids:
                            # 抓取收據當時匯率
                            try:
                                rate = yf.Ticker(f"{r['幣別']}TWD=X").fast_info['lastPrice'] if r['幣別'] != "TWD" else 1.0
                            except: rate = 1.0
                            
                            twd_base = round(r['外幣金額'] * rate, 0)
                            twd_total = round(twd_base * (1 + fee_rate), 0)
                            
                            # 嚴格對齊 A-M 欄位
                            rows_to_append.append([
                                now, f_user, r['商店名稱'], r['品項摘要'], r['日期'],
                                r['外幣金額'], r['幣別'], round(rate, 3), twd_base,
                                twd_total - twd_base, twd_total, r['備註'], r['UID']
                            ])
                    
                    if rows_to_append:
                        sh.append_rows(rows_to_append, value_input_option='USER_ENTERED')
                        st.success(f"🎉 同步完成！新增 {len(rows_to_append)} 筆資料至 {sel_p}。")
                        st.session_state['data'] = []
                    else:
                        st.warning("⚠️ 資料已同步或無新內容。")
                except Exception as e: st.error(f"同步過程中斷: {e}")

with tab_reg:
    st.header("🛠️ 專案註冊流程")
    st.markdown(f"""
    1. **建立副本**：[點此複製試算表範本]({TEMPLATE_URL})，選擇『檔案』>『建立副本』。
    2. **取得 ID**：複製網址中 `/d/` 之後的那串字串。
    3. **註冊**：在下方輸入專案名稱與 ID 並提交。
    """)
    reg_n = st.text_input("新專案名稱 (例如: 2026 德國考察)")
    reg_i = st.text_input("試算表 Spreadsheet ID")
    if st.button("✅ 提交註冊"):
        if reg_n and reg_i:
            try:
                gc = get_gc()
                gc.open_by_key(REGISTRY_ID).get_worksheet(0).append_row([
                    datetime.now().strftime("%Y/%m/%d %H:%M"), reg_n, reg_i, "TRUE"
                ])
                st.success("註冊成功！請刷新頁面選取新專案。")
            except Exception as e: st.error(f"註冊失敗: {e}")