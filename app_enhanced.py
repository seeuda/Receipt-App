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

def get_gc():
    """使用 Service Account 權限連線 Google Sheets"""
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=60)
def load_bootstrap_config():
    """載入中央註冊表：時間戳記, 專案名稱, 試算表 ID, 啟用狀態"""
    try:
        gc = get_gc()
        sh = gc.open_by_key(REGISTRY_ID)
        # 1. 抓取 Auth!A1 密碼
        pwd = str(sh.worksheet("Auth").acell('A1').value).strip()
        # 2. 抓取專案清單
        reg_sheet = sh.get_worksheet(0)
        recs = reg_sheet.get_all_records()
        # 依據你的格式過濾
        p_map = {r["專案名稱"]: r["試算表 ID"] for r in recs if str(r.get("啟用狀態(請選TRUE)", "")).upper() == "TRUE"}
        return pwd, p_map
    except Exception as e:
        st.error(f"❌ 註冊表讀取失敗，請檢查 GCP 權限: {e}")
        return "ADMIN", {}

def run_vlm_engine(api_key, image_bytes, year, region, country):
    """VLM 辨識：結合國家錨定資訊"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        # 關鍵：將國家與區域資訊餵給 AI 以防幣別誤判
        prompt = f"""
        你是一位專業審計師。分析來自【{region} - {country}】的收據影像。
        基準年份: {year}。
        請根據所在地點判定正確幣別。若為歐陸國家，請注意其小數點常以逗號(,)表示。
        請回傳純 JSON 格式: 
        {{"shop": "商店名稱", "amount": 0.0, "date": "YYYY-MM-DD", "currency": "幣別代碼", "items": "品項摘要"}}
        不要包含 Markdown 標籤。
        """
        img = Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, img])
        return json.loads(response.text.replace('```json', '').replace('```', '').strip())
    except Exception as e:
        st.error(f"⚠️ 辨識過程出錯: {e}")
        return None

# ==========================================
# II. UI 流程控制 (解決偵察無反應問題)
# ==========================================

st.set_page_config(page_title="考察支出登錄系統 v4.6.4", layout="wide")

# Session 保持
if 'data' not in st.session_state: st.session_state['data'] = []
if 'auth_passed' not in st.session_state: st.session_state['auth_passed'] = False

# 1. 啟動加載
admin_pwd, p_list = load_bootstrap_config()

with st.sidebar:
    st.header("🏢 中央控管區")
    # 專案選擇 (Sidebar 最上方)
    sel_p = st.selectbox("🎯 選擇執行專案", list(p_list.keys()) + ["+ 註冊新專案"])
    
    st.divider()
    # 授權分流
    st.subheader("🔑 辨識彈藥庫")
    auth_mode = st.radio("模式", ["開發者配額 (需密碼)", "自備 API KEY"])
    active_key = None
    if auth_mode == "開發者配額 (需密碼)":
        pwd_input = st.text_input("輸入 Auth!A1 授權碼", type="password")
        if pwd_input == admin_pwd:
            st.success("✅ 已取得開發者配額")
            active_key = st.secrets["gemini_api_key"]
    else:
        u_key = st.text_input("輸入 Gemini API Key")
        if u_key: active_key = u_key

    st.divider()
    target_year = st.number_input("📅 基準年度", value=2026)
    if st.button("🗑️ 清除快取數據"):
        st.session_state['data'] = []; st.rerun()

# --- 主畫面佈局 ---
tab_recon, tab_reg = st.tabs(["🚀 AI 偵察與同步", "🆕 專案快速註冊"])

with tab_recon:
    if sel_p == "+ 註冊新專案":
        st.info("請前往『專案快速註冊』頁籤。")
    else:
        tid = p_list[sel_p]
        # 動態連動：從所選專案 Sheet 抓取人員名單
        try:
            gc = get_gc()
            p_sh = gc.open_by_key(tid)
            raw_names = p_sh.worksheet("人員名單").col_values(1)[1:]
            person_list = [n for n in raw_names if n.strip()]
        except: person_list = ["預設報帳員"]

        # 頂部錨定區 (4 欄位)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            sel_u = st.selectbox("登錄者", person_list + ["其他人員"])
            final_u = st.text_input("姓名確認", value="") if sel_u == "其他人員" else sel_u
        with c2:
            sel_reg = st.selectbox("區域錨定", ["歐洲", "亞洲", "美洲", "其他"])
            sel_cnt = st.text_input("國家錨定", value="德國")
        with c3:
            fee_rate = st.number_input("跨國手續費 (%)", value=1.5) / 100
        with c4:
            st.write("🔗 專案試算表")
            st.link_button("開啟 Google Sheets", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

        st.divider()
        files = st.file_uploader("批次上傳單據 (多選)", accept_multiple_files=True, type=['jpg','png','jpeg'])

        # [解決點] AI 自動偵察按鈕
        if st.button("⚡ 啟動 AI 自動偵察", type="primary"):
            if not active_key:
                st.error("❌ 拒絕執行：請先在側邊欄完成 API 授權或輸入密碼。")
            elif files:
                with st.spinner(f"正在針對 {sel_cnt} 進行 VLM 語義偵察..."):
                    for f in files:
                        content = f.read()
                        # 呼叫錨定辨識
                        res = run_vlm_engine(active_key, content, target_year, sel_reg, sel_cnt)
                        if res:
                            # UID 鎖定第 13 欄
                            uid = hashlib.md5(content + final_u.encode()).hexdigest()[:12]
                            st.session_state['data'].append({
                                "UID": uid, "商店名稱": res['shop'], "日期": res['date'],
                                "外幣": res['amount'], "幣別": res['currency'], 
                                "品項": res['items'], "備註": ""
                            })
                st.rerun() # 強制刷新 UI 以顯示表格

    # --- 預覽與編輯區 ---
    if st.session_state['data']:
        st.subheader("📝 辨識結果核對")
        df = pd.DataFrame(st.session_state['data'])
        edf = st.data_editor(df, use_container_width=True, num_rows="dynamic")
        
        if st.button("📤 確認無誤，發射至雲端", type="secondary"):
            with st.spinner("正在執行 A-M 欄位映射與 UID 判重..."):
                try:
                    gc = get_gc()
                    target_wks = gc.open_by_key(tid).get_worksheet(0)
                    existing_uids = target_wks.col_values(13)
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    batch_rows = []
                    for _, r in edf.iterrows():
                        if r['UID'] not in existing_uids:
                            # 抓取即時匯率
                            try:
                                rate_obj = yf.Ticker(f"{r['幣別']}TWD=X").fast_info
                                live_rate = rate_obj['lastPrice']
                            except: live_rate = 1.0 # 抓取失敗則 1.0
                            
                            base_twd = round(r['外幣'] * live_rate, 0)
                            total_twd = round(base_twd * (1 + fee_rate), 0)
                            
                            # A-M 欄位填充
                            batch_rows.append([
                                now, final_u, r['商店名稱'], r['品項'], r['日期'],
                                r['外幣'], r['幣別'], round(live_rate, 3), base_twd,
                                total_twd - base_twd, total_twd, r['備註'], r['UID']
                            ])
                    
                    if batch_rows:
                        target_wks.append_rows(batch_rows, value_input_option='USER_ENTERED')
                        st.success(f"🎉 同步完成！新增 {len(batch_rows)} 筆。")
                        st.session_state['data'] = []
                        st.rerun()
                    else:
                        st.warning("⚠️ 資料已同步或 UID 重複。")
                except Exception as e: st.error(f"同步失敗: {e}")

with tab_reg:
    st.header("🛠️ 專案註冊介面")
    st.markdown(f"""
    1. **範本副本**：[點此複製試算表範本]({TEMPLATE_URL})
    2. **取得 ID**：複製新試算表 URL 中的 ID 字串。
    3. **提交**：在下方填寫資訊以寫入中央註冊表。
    """)
    n_p = st.text_input("專案名稱")
    n_id = st.text_input("試算表 ID")
    if st.button("✅ 提交至註冊表"):
        if n_p and n_id:
            get_gc().open_by_key(REGISTRY_ID).get_worksheet(0).append_row([
                datetime.now().strftime("%Y/%m/%d %H:%M"), n_p, n_id, "TRUE"
            ])
            st.success("註冊成功！請刷新頁面選取新專案。")