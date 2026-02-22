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
import re

# ==========================================
# I. 基礎設施與配置自動化
# ==========================================

REGISTRY_ID = "1rPQlGHtvx6M630vnZ_FANMRyR_EnMrzje85V3mZ2H0M"
TEMPLATE_URL = "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit"

def get_rate_by_date(currency_code, target_date):
    if currency_code in ("TWD", "NTD"):
        return 1.0

    try:
        if isinstance(target_date, str):
            target_date = datetime.strptime(target_date, "%Y-%m-%d").date()

        def fetch_pair(pair, d, max_days=7):
            ticker = yf.Ticker(pair)
            for i in range(max_days + 1):
                day = d - timedelta(days=i)
                start = day.strftime("%Y-%m-%d")
                end = (day + timedelta(days=1)).strftime("%Y-%m-%d")
                try:
                    hist = ticker.history(start=start, end=end)
                    if hist is not None and not hist.empty:
                        v = float(hist["Close"].iloc[-1])
                        if v > 0:
                            return v
                except:
                    pass

            try:
                fi = getattr(ticker, "fast_info", None)
                if fi and fi.get("lastPrice"):
                    v = float(fi["lastPrice"])
                    if v > 0:
                        return v
            except:
                pass

            return None

        check_pairs = [
            f"{currency_code}TWD=X",
            f"TWD{currency_code}=X",
            f"{currency_code}USD=X",
            f"USD{currency_code}=X"
        ]

        for pair in check_pairs:
            v = fetch_pair(pair, target_date)
            if v is not None and v > 0:
                if pair.startswith("TWD"):
                    return 1.0 / v if v != 0 else 35.0
                elif pair.endswith("USD=X"):
                    usdtwd = fetch_pair("USDTWD=X", target_date)
                    if usdtwd:
                        if pair.startswith("USD"):
                            return usdtwd / v if v != 0 else 35.0
                        else:
                            return v * usdtwd
                else:
                    return v

        return 35.0

    except Exception as e:
        return 35.0

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
        # 優化：使用 list_models 代替 generate_content，避免不必要的 token 消耗
        models = list(genai.list_models())
        if any('generateContent' in m.supported_generation_methods for m in models):
            return True, f"連線成功（可用模型數：{len(models)}）"
        else:
            return False, "API Key 有效但無可用模型"
    except Exception as e:
        return False, str(e)

def run_vlm_scan(api_key, image_bytes, year, country_info):
    """VLM 辨識：優化版 - 降低 Token 消耗"""
    try:
        genai.configure(api_key=api_key)
        
        # === 優化 1: 使用 System Instruction 減少重複 Prompt Token ===
        system_instruction = """你是專業審計師。任務：
1. 精確提取收據關鍵資訊
2. 回傳純 JSON（無 markdown 格式）
3. 日期格式必須是 YYYY-MM-DD

JSON 格式：{"shop":"店名","amount":數字,"date":"YYYY-MM-DD","currency":"幣別","items":"品項"}"""

        model = genai.GenerativeModel(
            model_name='models/gemini-2.5-flash',
            system_instruction=system_instruction
        )
        
        # === 優化 2: 壓縮圖片以降低 Visual Token 消耗 ===
        img = Image.open(io.BytesIO(image_bytes))
        
        # 計算縮放比例（保持長寬比）
        max_dimension = 1600  # 對 OCR 來說 1600px 已足夠
        if max(img.size) > max_dimension:
            ratio = max_dimension / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # === 優化 3: 簡化 Prompt（關鍵資訊已在 System Instruction）===
        hint = country_info.get("decimal_hint", ".")
        prompt = f"""收據分析參數：
- 年度：{year}（用於判斷日期格式，如 DD/MM/YY → YYYY-MM-DD）
- 國家：{country_info['name']}
- 預設幣別：{country_info['currency']}
- 小數點：'{hint}'

日期規則：若看到 "21/06/25" 且 21≤31，判斷為 DD/MM/YY，結合年度 {year} 推斷完整日期。"""
        
        response = model.generate_content([prompt, img])
        
        raw = response.text.replace('```json', '').replace('```', '').strip()
        result = json.loads(raw)
        
        # 後處理：驗證日期合理性
        try:
            date_str = result['date']
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            # 如果年份太舊或太新，嘗試修正
            if parsed_date.year < 2000 or parsed_date.year > year + 1:
                parts = date_str.split('-')
                if len(parts) == 3:
                    result['date'] = f"{year}-{parts[1]}-{parts[2]}"
        except:
            pass
        
        return result
    except Exception as e:
        st.session_state['vlm_error'] = f"{type(e).__name__}: {str(e)}"
        return None

# ==========================================
# III. UI 佈局 (MVC 架構)
# ==========================================

st.set_page_config(page_title="考察支出登錄系統 v4.9.0", layout="wide")

# 初始化 Session 狀態
if 'data' not in st.session_state: st.session_state['data'] = []
if 'vlm_error' not in st.session_state: st.session_state['vlm_error'] = None
if 'uploaded_images' not in st.session_state: st.session_state['uploaded_images'] = {}

# 載入外部參數與註冊資訊
admin_pwd, project_dict, c_master = load_bootstrap_data()

with st.sidebar:
    st.title("🛡️ 系統指揮中心")
    
    # 年度選擇（移到最上方）
    target_year = st.number_input("📅 單據年度（輔助辨識）", value=2026, min_value=2020, max_value=2030)
    
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
        c1, c2, c3 = st.columns(3)
        with c1:
            s_region = st.selectbox("🌍 選擇區域", c_master["region_order"])
            f_countries = {k: v for k, v in c_master["countries"].items() if v["region"] == s_region}
            s_country_keys = sorted(f_countries.keys(), key=lambda x: (f_countries[x]["priority"], f_countries[x]["name"]))
            
            sel_country_key = st.selectbox(
                "📍 選擇國家", s_country_keys, 
                format_func=lambda x: f_countries[x]["name"]
            )
            target_country = f_countries[sel_country_key]
        
        with c2:
            u_sel = st.selectbox("👤 登錄者姓名", names + ["其他人員"])
            f_user = st.text_input("確認姓名", value="") if u_sel == "其他人員" else u_sel

        with c3:
            fee_rate = st.number_input("手續費率 (%)", value=1.5) / 100

        st.divider()
        
        # 批次上傳
        files = st.file_uploader("批次上傳單據照片", accept_multiple_files=True, type=['jpg','png','jpeg'])

        # AI 辨識按鈕
        if st.button("⚡ 啟動 AI 自動偵察", type="primary", use_container_width=True):
            if not active_key: 
                st.error("❌ 尚未取得 API 授權。")
            elif files:
                st.session_state['vlm_error'] = None
                with st.spinner(f"正在對 {target_country['name']} 收據進行 VLM 分析..."):
                    batch = []
                    images = {}
                    for idx, f in enumerate(files):
                        img_bytes = f.read()
                        
                        # === 優化：在批次處理中加入延遲，避免 Rate Limit ===
                        if idx > 0:  # 第一張不用等
                            import time
                            time.sleep(1)  # 每張間隔 1 秒
                        
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
        
        # 開啟專案連結（移到辨識按鈕下方）
        st.link_button("📂 開啟專案 Sheet", f"https://docs.google.com/spreadsheets/d/{tid}/edit", use_container_width=True)

    # --- 核對表格：改為直式卡片顯示（手機友善） ---
    if st.session_state['data']:
        st.divider()
        st.subheader("📝 辨識結果核對")
        
        # 使用 tabs 或 expander 顯示每筆收據
        for idx, record in enumerate(st.session_state['data']):
            with st.expander(f"📄 收據 {idx+1}: {record['商店名稱']} - {record['日期']}", expanded=(idx==0)):
                col_img, col_form = st.columns([1, 1])
                
                # 左側：圖片預覽
                with col_img:
                    st.markdown("#### 📷 收據影像")
                    uid = record['UID']
                    if uid in st.session_state['uploaded_images']:
                        img_data = base64.b64decode(st.session_state['uploaded_images'][uid])
                        st.image(img_data, use_container_width=True)
                    else:
                        st.info("圖片預覽不可用")
                
                # 右側：編輯表單
                with col_form:
                    st.markdown("#### ✏️ 資料編輯")
                    
                    # 使用 form 避免每次輸入都觸發 rerun
                    # 使用 idx 確保 key 唯一性
                    with st.form(key=f"receipt_form_{idx}"):
                        new_shop = st.text_input("商店名稱", value=record['商店名稱'])
                        new_date = st.date_input("日期", value=datetime.strptime(record['日期'], "%Y-%m-%d").date())
                        new_amount = st.number_input("外幣金額", value=float(record['外幣金額']), format="%.2f")
                        new_currency = st.text_input("幣別", value=record['幣別'])
                        new_rate = st.number_input("💱 匯率", value=float(record['匯率']), format="%.3f", step=0.001, help="可手動修改匯率")
                        new_items = st.text_area("品項摘要", value=record['品項摘要'], height=100)
                        new_note = st.text_input("備註", value=record.get('備註', ''))
                        
                        # 即時計算台幣金額
                        calculated_twd = round(new_amount * new_rate, 0)
                        st.info(f"💰 台幣金額：NT$ {calculated_twd:,.0f}")
                        
                        # 提交按鈕
                        submitted = st.form_submit_button("✅ 更新此筆資料", use_container_width=True)
                        
                        if submitted:
                            # 檢查是否有變更
                            date_changed = new_date.strftime("%Y-%m-%d") != record['日期']
                            amount_changed = new_amount != record['外幣金額']
                            currency_changed = new_currency != record['幣別']
                            rate_changed = new_rate != record['匯率']
                            
                            # 更新資料
                            st.session_state['data'][idx]['商店名稱'] = new_shop
                            st.session_state['data'][idx]['日期'] = new_date.strftime("%Y-%m-%d")
                            st.session_state['data'][idx]['外幣金額'] = new_amount
                            st.session_state['data'][idx]['幣別'] = new_currency
                            st.session_state['data'][idx]['品項摘要'] = new_items
                            st.session_state['data'][idx]['備註'] = new_note
                            
                            # 匯率邏輯：
                            # 1. 如果使用者手動修改匯率，優先使用使用者的值
                            # 2. 如果只是修改日期/幣別，自動查詢新匯率
                            if rate_changed:
                                # 使用者手動修改匯率，使用使用者輸入的值
                                st.session_state['data'][idx]['匯率'] = round(new_rate, 3)
                                st.session_state['data'][idx]['台幣金額'] = round(new_amount * new_rate, 0)
                            elif date_changed or currency_changed:
                                # 日期或幣別變更，自動查詢新匯率
                                auto_rate = get_rate_by_date(new_currency, new_date.strftime("%Y-%m-%d"))
                                st.session_state['data'][idx]['匯率'] = round(auto_rate, 3)
                                st.session_state['data'][idx]['台幣金額'] = round(new_amount * auto_rate, 0)
                            elif amount_changed:
                                # 只修改金額，使用原匯率重新計算
                                st.session_state['data'][idx]['台幣金額'] = round(new_amount * new_rate, 0)
                            
                            st.success("✅ 資料已更新！")
                            st.rerun()
        
        st.divider()
        
        # 同步按鈕
        if st.button("📤 同步至雲端", type="secondary", use_container_width=True):
            with st.spinner("同步 A-M 欄位中..."):
                try:
                    gc = get_gc()
                    wks = gc.open_by_key(tid).get_worksheet(0)
        
                    # === 建立 雲端 UID -> 列號 對照表 ===
                    uid_col = wks.col_values(13)  # M 欄
                    uid_to_row = {}
                    for idx, uid in enumerate(uid_col, start=1):
                        uid = str(uid).strip()
                        if uid:
                            uid_to_row[uid] = idx

                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    updates = []         # 要覆蓋的
                    rows_to_append = []  # 要新增的

                    # === 逐筆檢查 ===
                    for r in st.session_state['data']:
                        uid = str(r['UID']).strip()

                        t_base = int(r['台幣金額'])
                        t_total = round(t_base * (1 + fee_rate), 0)

                        row_values_A_to_L = [
                            now, f_user, r['商店名稱'], r['品項摘要'], r['日期'],
                            r['外幣金額'], r['幣別'], r['匯率'],
                            t_base, t_total - t_base, t_total, r['備註']
                        ]

                        if uid in uid_to_row:
                            # 已存在 → 覆蓋 A~L（保留 UID）
                            rownum = uid_to_row[uid]
                            updates.append({
                                "range": f"A{rownum}:L{rownum}",
                                "values": [row_values_A_to_L]
                            })
                        else:
                            # 不存在 → 新增
                            rows_to_append.append(row_values_A_to_L + [uid])

                    # === 批次執行 ===
                    if updates:
                        wks.batch_update(updates, value_input_option='USER_ENTERED')

                    if rows_to_append:
                        wks.append_rows(rows_to_append, value_input_option='USER_ENTERED')

                    msg = []
                    if updates:
                        msg.append(f"更新 {len(updates)} 筆")
                    if rows_to_append:
                        msg.append(f"新增 {len(rows_to_append)} 筆")

                    st.success(f"✅ 同步完成！{' / '.join(msg)}")
                    st.session_state['data'] = []
                    st.session_state['uploaded_images'] = {}
                    st.rerun()

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
