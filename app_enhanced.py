# Base Indent: 0 spaces
import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, json, hashlib, yfinance as yf
from datetime import datetime, timedelta
import google.generativeai as genai
from PIL import Image
import base64

# ==========================================
# I. 基礎設施與配置自動化
# ==========================================

REGISTRY_ID = "1rPQlGHtvx6M630vnZ_FANMRyR_EnMrzje85V3mZ2H0M"
TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit"

def get_rate_by_date(currency_code, target_date):
    """根據日期查詢匯率（週末自動往前抓週五）"""
    if currency_code == "TWD":
        return 1.0
    
    try:
        # 將字串日期轉為 datetime
        if isinstance(target_date, str):
            target_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        
        check_date = target_date
        max_attempts = 7  # 最多往前推 7 天
        
        for _ in range(max_attempts):
            # 如果是週末（5=六, 6=日），往前推
            while check_date.weekday() >= 5:
                check_date -= timedelta(days=1)
            
            # 嘗試獲取該日期的匯率
            ticker = yf.Ticker(f"{currency_code}TWD=X")
            hist = ticker.history(start=check_date, end=check_date + timedelta(days=1))
            
            if not hist.empty:
                return float(hist['Close'].iloc[0])
            
            # 如果該日無資料，繼續往前推一天
            check_date -= timedelta(days=1)
        
        # 如果都找不到，使用最新匯率
        return float(ticker.fast_info.get('lastPrice', 35.0))
    except:
        return 35.0  # 預設值

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
def load_bootstrap_data():
    gc = get_gc()
    if not gc: return "ADMIN", {}, {}
    try:
        sh = gc.open_by_key(REGISTRY_ID)
        pwd = str(sh.worksheet("Auth").acell('A1').value).strip()
        recs = sh.get_worksheet(0).get_all_records()
        valid_p = {r["專案名稱"]: r["試算表 ID"] for r in recs if str(r.get("啟用狀態(請選TRUE)", "")).upper() == "TRUE"}
        with open("countries_master.json", "r", encoding="utf-8") as f:
            c_master = json.load(f)
        return pwd, valid_p, c_master
    except Exception as e:
        st.error(f"⚠️ 初始化失敗: {e}")
        return "ADMIN", {}, {}

# ==========================================
# II. 核心偵察功能 (VLM 與 預檢)
# ==========================================

def test_api_connection(api_key):
    """【實施 API 預檢】確認金鑰是否具備模型存取權限"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        response = model.generate_content("ping")
        return True, "連線成功"
    except Exception as e:
        return False, str(e)

def run_vlm_scan(api_key, image_bytes, year, country_info):
    """VLM 辨識：結合座標錨定並捕捉錯誤"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        
        hint = country_info.get("decimal_hint", ".")
        prompt = f"""
        你是審計專家。分析【{country_info['name']}】收據影像。基準年份:{year}。預設幣別:{country_info['currency']}。
        小數點習慣:'{hint}'。回傳純 JSON: {{"shop":"","amount":0.0,"date":"YYYY-MM-DD","currency":"","items":""}}
        """
        img = Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, img])
        
        raw = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(raw)
    except Exception as e:
        st.session_state['vlm_error'] = f"{type(e).__name__}: {str(e)}"
        return None

# ==========================================
# III. UI 佈局 (MVC 架構)
# ==========================================

st.set_page_config(page_title="考察支出登錄系統 v4.8.0", layout="wide")

# 初始化 Session 狀態
if 'data' not in st.session_state: st.session_state['data'] = []
if 'vlm_error' not in st.session_state: st.session_state['vlm_error'] = None
if 'uploaded_images' not in st.session_state: st.session_state['uploaded_images'] = {}

# 載入外部參數與註冊資訊
admin_pwd, project_dict, c_master = load_bootstrap_data()

with st.sidebar:
    st.title("🛡️ 系統指揮中心")
    
    # 專案選擇
    sel_p_name = st.selectbox("🎯 選擇執行專案", list(project_dict.keys()) + ["+ 註冊新專案"])
    
    st.divider()
    
    # 權限分流與 API 預檢
    st.subheader("🔑 API 權限驗證")
    auth_mode = st.radio("模式", ["開發者配額 (需密碼)", "自備 API KEY"])
    active_key = None
    if auth_mode == "開發者配額 (需密碼)":
        if st.text_input("輸入 A1 授權碼", type="password") == admin_pwd:
            active_key = st.secrets["gemini_api_key"]
            st.success("✅ 授權代碼正確")
    else:
        u_key = st.text_input("輸入 Gemini API KEY")
        if u_key: active_key = u_key

    if active_key:
        if st.button("⚡ 測試 API 連線 (Ping)", use_container_width=True):
            with st.spinner("測試中..."):
                success, msg = test_api_connection(active_key)
                if success:
                    st.success("🚀 API 連線測試成功！")
                    st.session_state['vlm_error'] = None
                else:
                    st.error(f"❌ 連線失敗: {msg}")
                    st.session_state['vlm_error'] = msg

    st.divider()
    target_year = st.number_input("📅 基準年度", value=2026)
    if st.button("🗑️ 清空辨識紀錄", use_container_width=True):
        st.session_state['data'] = []
        st.session_state['uploaded_images'] = {}
        st.session_state['vlm_error'] = None
        st.rerun()

# --- 主畫面頁籤 ---
tab_main, tab_reg = st.tabs(["🚀 辨識同步任務", "🆕 專案快速註冊"])

with tab_main:
    if st.session_state['vlm_error']:
        with st.container():
            st.error("🚩 系統異常報告")
            st.code(st.session_state['vlm_error'], language="bash")
            st.info("💡 指揮官提示：請檢查側邊欄的 API 連線狀態，或確認是否已達到每分鐘流量限制。")

    if sel_p_name == "+ 註冊新專案":
        st.info("請前往『專案快速註冊』頁籤執行。")
    elif project_dict:
        tid = project_dict[sel_p_name]
        try:
            gc = get_gc()
            sh = gc.open_by_key(tid)
            names = [n for n in sh.worksheet("人員名單").col_values(1)[1:] if n.strip()]
        except: names = []

        # 頂部導航列
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            u_sel = st.selectbox("登錄者姓名", names + ["其他人員"])
            f_user = st.text_input("確認姓名", value="") if u_sel == "其他人員" else u_sel
        
        with c2:
            s_region = st.selectbox("🌍 選擇區域", c_master["region_order"])
            f_countries = {k: v for k, v in c_master["countries"].items() if v["region"] == s_region}
            s_country_keys = sorted(f_countries.keys(), key=lambda x: (f_countries[x]["priority"], f_countries[x]["name"]))
            
            sel_country_key = st.selectbox(
                "📍 選擇國家", s_country_keys, 
                format_func=lambda x: f_countries[x]["name"]
            )
            target_country = f_countries[sel_country_key]

        with c3:
            fee_rate = st.number_input("手續費率 (%)", value=1.5) / 100
        with c4:
            st.link_button("📂 開啟專案 Sheet", f"https://docs.google.com/spreadsheets/d/{tid}/edit", use_container_width=True)

        st.divider()
        files = st.file_uploader("批次上傳單據照片", accept_multiple_files=True, type=['jpg','png','jpeg'])

        if st.button("⚡ 啟動 AI 自動偵察", type="primary", use_container_width=True):
            if not active_key: 
                st.error("❌ 尚未取得 API 授權。")
            elif files:
                st.session_state['vlm_error'] = None
                with st.spinner(f"正在對 {target_country['name']} 收據進行 VLM 分析..."):
                    batch = []
                    images = {}
                    for f in files:
                        img_bytes = f.read()
                        res = run_vlm_scan(active_key, img_bytes, target_year, target_country)
                        if res:
                            uid = hashlib.md5(img_bytes + f_user.encode()).hexdigest()[:12]
                            
                            # 查詢匯率
                            receipt_date = res['date']
                            exchange_rate = get_rate_by_date(res['currency'], receipt_date)
                            twd_amount = round(res['amount'] * exchange_rate, 0)
                            
                            batch.append({
                                "UID": uid,
                                "商店名稱": res['shop'],
                                "日期": res['date'],
                                "外幣金額": res['amount'],
                                "幣別": res['currency'],
                                "匯率": round(exchange_rate, 3),
                                "台幣金額": twd_amount,
                                "品項摘要": res['items'],
                                "備註": ""
                            })
                            
                            # 儲存圖片
                            images[uid] = base64.b64encode(img_bytes).decode()
                    
                    if batch:
                        st.session_state['data'] = batch
                        st.session_state['uploaded_images'] = images
                        st.rerun()

    # --- 核對表格與預覽 ---
    if st.session_state['data']:
        st.subheader("📝 辨識結果核對")
        
        # 建立兩欄：左側表格，右側圖片預覽
        col_table, col_preview = st.columns([2, 1])
        
        with col_table:
            # 創建可編輯的 DataFrame
            df = pd.DataFrame(st.session_state['data'])
            
            # 使用 data_editor 並監聽變更
            edited_df = st.data_editor(
                df,
                use_container_width=True,
                num_rows="dynamic",
                column_config={
                    "外幣金額": st.column_config.NumberColumn(format="%.2f"),
                    "匯率": st.column_config.NumberColumn(format="%.3f"),
                    "台幣金額": st.column_config.NumberColumn(format="%.0f"),
                },
                key="receipt_editor"
            )
            
            # 偵測變更並重新計算台幣金額
            if not edited_df.equals(df):
                for idx, row in edited_df.iterrows():
                    # 如果日期或金額或幣別有變更，重新計算
                    old_row = df.loc[idx]
                    if (row['日期'] != old_row['日期'] or 
                        row['外幣金額'] != old_row['外幣金額'] or
                        row['幣別'] != old_row['幣別']):
                        
                        # 重新查詢匯率
                        new_rate = get_rate_by_date(row['幣別'], row['日期'])
                        edited_df.at[idx, '匯率'] = round(new_rate, 3)
                        edited_df.at[idx, '台幣金額'] = round(row['外幣金額'] * new_rate, 0)
                
                # 更新 session state
                st.session_state['data'] = edited_df.to_dict('records')
                st.rerun()
        
        with col_preview:
            st.markdown("### 📷 收據預覽")
            
            # 選擇要預覽的收據
            if len(edited_df) > 0:
                preview_options = [f"{row['商店名稱']} - {row['日期']}" for _, row in edited_df.iterrows()]
                selected_idx = st.selectbox("選擇收據", range(len(preview_options)), format_func=lambda x: preview_options[x])
                
                # 顯示圖片
                selected_uid = edited_df.iloc[selected_idx]['UID']
                if selected_uid in st.session_state['uploaded_images']:
                    img_data = base64.b64decode(st.session_state['uploaded_images'][selected_uid])
                    st.image(img_data, caption=f"收據 - {preview_options[selected_idx]}", use_container_width=True)
                else:
                    st.info("圖片預覽不可用")
        
        st.divider()
        
        # 同步按鈕
        if st.button("📤 同步至雲端", type="secondary", use_container_width=True):
            with st.spinner("同步 A-M 欄位中..."):
                try:
                    gc = get_gc()
                    wks = gc.open_by_key(tid).get_worksheet(0)
                    uids = wks.col_values(13)
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    rows = []
                    
                    for _, r in edited_df.iterrows():
                        if r['UID'] not in uids:
                            t_base = int(r['台幣金額'])
                            t_total = round(t_base * (1 + fee_rate), 0)
                            rows.append([
                                now, f_user, r['商店名稱'], r['品項摘要'], r['日期'],
                                r['外幣金額'], r['幣別'], r['匯率'],
                                t_base, t_total - t_base, t_total, r['備註'], r['UID']
                            ])
                    
                    if rows:
                        wks.append_rows(
                            rows,
                            value_input_option='USER_ENTERED',
                            insert_data_option='INSERT_ROWS',
                            table_range='A2:M2'
                        )
                        st.success(f"✅ 同步完成！新增 {len(rows)} 筆。")
                        st.session_state['data'] = []
                        st.session_state['uploaded_images'] = {}
                    else:
                        st.warning("無新資料。")
                except Exception as e:
                    st.error(f"同步錯誤: {e}")

with tab_reg:
    st.header("🛠️ 專案註冊")
    st.markdown(f"1. [點此複製範本]({TEMPLATE_URL})\n2. 複製 ID 提交。")
    rn, rid = st.text_input("專案名稱"), st.text_input("試算表 ID")
    if st.button("✅ 提交註冊", use_container_width=True):
        if rn and rid:
            try:
                get_gc().open_by_key(REGISTRY_ID).get_worksheet(0).append_row([datetime.now().strftime("%Y/%m/%d %H:%M"), rn, rid, "TRUE"])
                st.success("註冊成功！")
            except Exception as e:
                st.error(f"失敗: {e}")
