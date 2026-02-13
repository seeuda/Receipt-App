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
# I. 基礎設施與配置自動化
# ==========================================

REGISTRY_ID = "1rPQlGHtvx6M630vnZ_FANMRyR_EnMrzje85V3mZ2H0M"
TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit"

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
    """一次性載入註冊表與國家參數檔"""
    gc = get_gc()
    if not gc: return "ADMIN", {}, {}
    try:
        sh = gc.open_by_key(REGISTRY_ID)
        pwd = str(sh.worksheet("Auth").acell('A1').value).strip()
        recs = sh.get_worksheet(0).get_all_records()
        valid_p = {r["專案名稱"]: r["試算表 ID"] for r in recs if str(r.get("啟用狀態(請選TRUE)", "")).upper() == "TRUE"}
        
        # 動態讀取外部國家參數檔
        with open("countries_master.json", "r", encoding="utf-8") as f:
            c_master = json.load(f)
            
        return pwd, valid_p, c_master
    except Exception as e:
        st.error(f"⚠️ 初始化失敗: {e}")
        return "ADMIN", {}, {}

def run_vlm_scan(api_key, image_bytes, year, country_info):
    """VLM 辨識核心：由 country_info 全權導引"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        
        name = country_info["name"]
        curr = country_info["currency"]
        hint = country_info.get("decimal_hint", ".")

        prompt = f"""
        你是財務審計專家。請分析這張來自【{name}】的收據影像。
        1. 基準年份: {year}。
        2. 預設幣別: {curr}。
        3. 金額格式提示: 該國習慣以 '{hint}' 作為小數點 (若為逗號則需正確轉換)。
        回傳純 JSON (無 Markdown): 
        {{"shop": "商店名稱", "amount": 0.0, "date": "YYYY-MM-DD", "currency": "{curr}", "items": "摘要"}}
        """
        img = Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, img])
        # 強化 JSON 解析
        raw_text = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(raw_text)
    except Exception as e:
        st.session_state['vlm_error'] = str(e)
        return None

# ==========================================
# II. UI 與 任務控制 (Data-Driven MVC)
# ==========================================

st.set_page_config(page_title="考察支出登錄系統 v4.7.1", layout="wide")

if 'data' not in st.session_state: st.session_state['data'] = []
if 'vlm_error' not in st.session_state: st.session_state['vlm_error'] = None

# 加載外部參數與註冊資訊
admin_pwd, project_dict, c_master = load_bootstrap_data()

with st.sidebar:
    st.title("🛡️ 系統指揮中心")
    sel_project = st.selectbox("🎯 選擇執行專案", list(project_dict.keys()) + ["+ 註冊新專案"])
    
    st.divider()
    auth_mode = st.radio("🔑 API 來源", ["開發者配額 (需密碼)", "自備 API KEY"])
    active_key = None
    if auth_mode == "開發者配額 (需密碼)":
        if st.text_input("輸入 A1 授權碼", type="password") == admin_pwd:
            active_key = st.secrets["gemini_api_key"]
            st.success("✅ 授權成功")
    else:
        u_key = st.text_input("貼上 Gemini API KEY")
        if u_key: active_key = u_key

    st.divider()
    target_year = st.number_input("📅 基準年度", value=2026)
    if st.button("🗑️ 清空辨識紀錄"):
        st.session_state['data'] = []; st.session_state['vlm_error'] = None; st.rerun()

# --- 主畫面頁籤 ---
tab_main, tab_reg = st.tabs(["🚀 辨識同步任務", "🆕 專案快速註冊"])

with tab_main:
    # 錯誤訊息持久化顯示
    if st.session_state['vlm_error']:
        st.error(f"❌ 偵察失敗: {st.session_state['vlm_error']}")

    if sel_project == "+ 註冊新專案":
        st.info("請切換至『專案快速註冊』頁籤執行登錄。")
    elif project_dict:
        tid = project_dict[sel_project]
        # 動態抓取人員名單
        try:
            gc = get_gc()
            sh = gc.open_by_key(tid)
            names = [n for n in sh.worksheet("人員名單").col_values(1)[1:] if n.strip()]
        except: names = []

        # 頂部導航區：完全由 JSON 驅動的分層選單
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            u_sel = st.selectbox("登錄者姓名", names + ["其他人員"])
            f_user = st.text_input("確認姓名", value="") if u_sel == "其他人員" else u_sel
        
        with c2:
            # 第一層：區域 (由 JSON region_order 定義)
            s_region = st.selectbox("🌍 選擇區域", c_master["region_order"])
            
            # 第二層：國家 (過濾該區域並依 Priority 排序)
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
            st.write("🔗 快速連結")
            st.link_button("開啟專案 Sheet", f"https://docs.google.com/spreadsheets/d/{tid}/edit")

        st.divider()
        files = st.file_uploader("批次上傳單據照片", accept_multiple_files=True, type=['jpg','png','jpeg'])

        if st.button("⚡ 啟動 AI 自動偵察", type="primary"):
            if not active_key: st.error("❌ 尚未取得 API 授權。")
            elif files:
                st.session_state['vlm_error'] = None
                with st.spinner(f"正在對 {target_country['name']} 收據進行 VLM 分析..."):
                    batch = []
                    for f in files:
                        res = run_vlm_scan(active_key, f.read(), target_year, target_country)
                        if res:
                            uid = hashlib.md5(f.getvalue() + f_user.encode()).hexdigest()[:12]
                            batch.append({
                                "UID": uid, "商店名稱": res['shop'], "日期": res['date'], 
                                "外幣金額": res['amount'], "幣別": res['currency'], 
                                "品項摘要": res['items'], "備註": ""
                            })
                    if batch:
                        st.session_state['data'] = batch
                        st.rerun()

    # --- 核對表格與同步 ---
    if st.session_state['data']:
        st.subheader("📝 辨識結果核對")
        edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True, num_rows="dynamic")
        
        if st.button("📤 同步至雲端"):
            with st.spinner("同步 A-M 欄位中..."):
                try:
                    gc = get_gc()
                    wks = gc.open_by_key(tid).get_worksheet(0)
                    uids = wks.col_values(13)
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    rows = []
                    for _, r in edf.iterrows():
                        if r['UID'] not in uids:
                            rate = yf.Ticker(f"{r['幣別']}TWD=X").fast_info['lastPrice'] if r['幣別'] != "TWD" else 1.0
                            t_base = round(r['外幣金額'] * rate, 0)
                            t_total = round(t_base * (1 + fee_rate), 0)
                            rows.append([now, f_user, r['商店名稱'], r['品項摘要'], r['日期'], r['外幣金額'], r['幣別'], round(rate, 3), t_base, t_total - t_base, t_total, r['備註'], r['UID']])
                    if rows:
                        wks.append_rows(rows, value_input_option='USER_ENTERED')
                        st.success(f"✅ 同步完成！新增 {len(rows)} 筆。")
                        st.session_state['data'] = []
                    else: st.warning("無新資料或 UID 已重複。")
                except Exception as e: st.error(f"同步錯誤: {e}")

with tab_reg:
    st.header("🛠️ 專案註冊")
    st.markdown(f"1. [點此複製範本]({TEMPLATE_URL})\n2. 複製 ID 提交。")
    rn, rid = st.text_input("專案名稱"), st.text_input("試算表 ID")
    if st.button("✅ 提交"):
        if rn and rid:
            try:
                get_gc().open_by_key(REGISTRY_ID).get_worksheet(0).append_row([datetime.now().strftime("%Y/%m/%d %H:%M"), rn, rid, "TRUE"])
                st.success("註冊成功！")
            except Exception as e: st.error(f"失敗: {e}")