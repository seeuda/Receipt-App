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

# 載入 Master Config (包含國家錨定)
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
    try:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], 
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ GCP 授權失敗: {e}")
        return None

@st.cache_data(ttl=60)
def load_bootstrap_config():
    gc = get_gc()
    if not gc: return "ADMIN", {}
    try:
        sh = gc.open_by_key(REGISTRY_ID)
        pwd = str(sh.worksheet("Auth").acell('A1').value).strip()
        reg_data = sh.get_worksheet(0).get_all_records()
        valid_p = {r["專案名稱"]: r["試算表 ID"] for r in reg_data if str(r.get("啟用狀態(請選TRUE)", "")).upper() == "TRUE"}
        return pwd, valid_p
    except: return "ADMIN", {}

# ==========================================
# II. AI 辨識引擎 (強化錯誤留存)
# ==========================================

def run_vlm_engine(api_key, image_bytes, year, country_name):
    """執行辨識並確保錯誤可追蹤"""
    try:
        genai.configure(api_key=api_key)
        # 2026 標準模型識別碼
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        hint = "."
        for v in COUNTRIES_MASTER.values():
            if v['name'] == country_name: hint = v['hint']; break

        prompt = f"""
        你是財務審計專家。分析【{country_name}】收據影像。基準年份: {year}。
        請根據所在地判定幣別，注意歐陸數字格式(可能以'{hint}'為小數點)。
        回傳純 JSON: {{"shop": "商店", "amount": 0.0, "date": "YYYY-MM-DD", "currency": "幣別", "items": "品項"}}
        """
        img = Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, img])
        
        # 解析 JSON，處理 Markdown 標記
        raw_text = response.text.strip()
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()
            
        return json.loads(raw_text)
    except Exception as e:
        # 將錯誤存入 session_state 以防刷新消失
        st.session_state['last_error'] = str(e)
        return None

# ==========================================
# III. UI 控制區
# ==========================================

st.set_page_config(page_title="考察支出登錄系統 v4.6.8", layout="wide")

# 初始化狀態
if 'data' not in st.session_state: st.session_state['data'] = []
if 'last_error' not in st.session_state: st.session_state['last_error'] = None

admin_pwd, p_dict = load_bootstrap_config()

with st.sidebar:
    st.title("🛡️ 指揮官控制台")
    sel_p = st.selectbox("🎯 選擇執行專案", list(p_dict.keys()) + ["+ 註冊新專案"])
    
    st.divider()
    auth_mode = st.radio("🔑 API 來源", ["開發者配額 (需密碼)", "自備 API KEY"])
    active_key = None
    if auth_mode == "開發者配額 (需密碼)":
        pwd_input = st.text_input("輸入 Auth!A1 授權碼", type="password")
        if pwd_input == admin_pwd:
            active_key = st.secrets["gemini_api_key"]
            st.success("✅ 授權通過")
    else:
        user_key = st.text_input("貼上個人 Gemini API KEY")
        if user_key: active_key = user_key

    st.divider()
    target_year = st.number_input("📅 基準年度", value=2026)
    if st.button("🗑️ 清空辨識紀錄"):
        st.session_state['data'] = []; st.session_state['last_error'] = None; st.rerun()

# --- 主介面 ---
tab_main, tab_reg = st.tabs(["🚀 辨識同步", "🆕 專案註冊"])

with tab_main:
    # 錯誤顯示區 (不會消失)
    if st.session_state['last_error']:
        st.error(f"❌ AI 辨識失敗：{st.session_state['last_error']}")
        st.info("💡 建議：檢查 API Key 是否正確連結至 GCP 專案，且該專案已啟用 Generative Language API。")

    if sel_p == "+ 註冊新專案":
        st.info("請切換至『專案註冊』分頁。")
    elif p_dict:
        tid = p_dict[sel_p]
        try:
            gc = get_gc()
            p_sh = gc.open_by_key(tid)
            names = p_sh.worksheet("人員名單").col_values(1)[1:]
        except: names = []

        # UI 頂部：錨定資訊
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            u_sel = st.selectbox("登錄者姓名", names + ["其他"])
            f_user = st.text_input("姓名確認", value="") if u_sel == "其他" else u_sel
        with c2:
            f_country = st.selectbox("📍 單據國家", [v['name'] for v in COUNTRIES_MASTER.values()] + ["其他"])
            if f_country == "其他": f_country = st.text_input("輸入國家名稱", value="德國")
        with c3:
            fee_rate = st.number_input("手續費率 (%)", value=1.5) / 100
        with c4:
            st.link_button("開啟 Google Sheets", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

        st.divider()
        files = st.file_uploader("上傳收據 (JPG/PNG)", accept_multiple_files=True)

        if st.button("⚡ 啟動 AI 自動偵察", type="primary"):
            if not active_key:
                st.error("❌ 尚未取得 API 授權。")
            elif files:
                st.session_state['last_error'] = None # 重置錯誤
                with st.spinner("AI 偵察中..."):
                    new_batch = []
                    for f in files:
                        res = run_vlm_engine(active_key, f.read(), target_year, f_country)
                        if res:
                            uid = hashlib.md5(f.getvalue() + f_user.encode()).hexdigest()[:12]
                            new_batch.append({
                                "UID": uid, "商店名稱": res['shop'], "日期": res['date'], 
                                "外幣金額": res['amount'], "幣別": res['currency'], 
                                "品項摘要": res['items'], "備註": ""
                            })
                    if new_batch:
                        st.session_state['data'] = new_batch
                        st.rerun()

    # --- 數據表格與同步 ---
    if st.session_state['data']:
        st.subheader("📝 辨識結果核對")
        edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True, num_rows="dynamic")
        
        if st.button("📤 同步至雲端"):
            with st.spinner("同步中..."):
                try:
                    gc = get_gc()
                    sh = gc.open_by_key(tid).get_worksheet(0)
                    uids = sh.col_values(13)
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    rows = []
                    for _, r in edf.iterrows():
                        if r['UID'] not in uids:
                            try:
                                rate = yf.Ticker(f"{r['幣別']}TWD=X").fast_info['lastPrice'] if r['幣別'] != "TWD" else 1.0
                            except: rate = 1.0
                            t_base = round(r['外幣金額'] * rate, 0)
                            t_total = round(t_base * (1 + fee_rate), 0)
                            rows.append([now, f_user, r['商店名稱'], r['品項摘要'], r['日期'], r['外幣金額'], r['幣別'], round(rate,3), t_base, t_total - t_base, t_total, r['備註'], r['UID']])
                    
                    if rows:
                        sh.append_rows(rows, value_input_option='USER_ENTERED')
                        st.success(f"✅ 成功同步 {len(rows)} 筆。")
                        st.session_state['data'] = []
                    else: st.warning("無新資料。")
                except Exception as e: st.error(f"同步錯誤: {e}")

with tab_reg:
    st.header("🛠️ 專案註冊")
    st.markdown(f"1. [點此複製範本]({TEMPLATE_URL})\n2. 填寫名稱與 ID 提交。")
    reg_n = st.text_input("專案名稱")
    reg_id = st.text_input("試算表 ID")
    if st.button("✅ 提交"):
        if reg_n and reg_id:
            try:
                get_gc().open_by_key(REGISTRY_ID).get_worksheet(0).append_row([datetime.now().strftime("%Y/%m/%d %H:%M"), reg_n, reg_id, "TRUE"])
                st.success("註冊成功！")
            except Exception as e: st.error(f"註冊失敗: {e}")