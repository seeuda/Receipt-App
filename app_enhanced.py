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
PREFERRED_MODELS = ["models/gemini-2.5-flash-lite", "models/gemini-2.5-flash"]

def normalize_items(items_value):
    """
    將 VLM 回傳的品項資料轉為可寫入 Google Sheets 的純文字。
    支援：string, list, dict 等格式
    """
    # 如果已經是字串，直接返回
    if isinstance(items_value, str):
        return items_value.strip() or "無"
    
    # 如果是列表
    if isinstance(items_value, list):
        normalized_items = []
        for item in items_value:
            if isinstance(item, str):
                # 純字串項目
                text = item.strip()
                if text:
                    normalized_items.append(text)
            elif isinstance(item, dict):
                # 字典項目（包含描述、數量、價格等）
                desc = str(item.get("description", "")).strip()
                qty = item.get("quantity")
                price = item.get("price")
                unit_price = item.get("unit_price")
                total_price = item.get("total_price")
                
                # 組合成可讀文字
                parts = []
                if desc:
                    parts.append(desc)
                if qty not in (None, ""):
                    parts.append(f"x{qty}")
                if price not in (None, ""):
                    parts.append(f"€{price}")
                elif unit_price not in (None, ""):
                    parts.append(f"單價:€{unit_price}")
                if total_price not in (None, "") and total_price != price:
                    parts.append(f"小計:€{total_price}")
                
                if parts:
                    normalized_items.append(" ".join(parts))
        
        # 用分號連接所有項目
        return "; ".join(normalized_items) if normalized_items else "無"
    
    # 如果是字典（單一品項）
    if isinstance(items_value, dict):
        desc = str(items_value.get("description", "")).strip()
        qty = items_value.get("quantity")
        price = items_value.get("price")
        
        parts = []
        if desc:
            parts.append(desc)
        if qty not in (None, ""):
            parts.append(f"x{qty}")
        if price not in (None, ""):
            parts.append(f"€{price}")
        
        return " ".join(parts) if parts else "無"
    
    # 其他類型：轉字串
    try:
        return str(items_value).strip() or "無"
    except:
        return "無"

def normalize_receipt_date(date_value, fallback_year=None):
    """將模型回傳日期正規化為 YYYY-MM-DD，失敗時回傳 None。"""
    if date_value is None:
        return None

    def _safe_date(y, m, d):
        try:
            return datetime(int(y), int(m), int(d)).strftime("%Y-%m-%d")
        except:
            return None

    if isinstance(date_value, datetime):
        return date_value.strftime("%Y-%m-%d")

    if hasattr(date_value, "strftime"):
        try:
            return date_value.strftime("%Y-%m-%d")
        except:
            pass

    text = str(date_value).strip()
    if not text:
        return None

    candidate_formats = [
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
        "%d-%m-%y", "%d/%m/%y", "%d.%m.%y",
        "%m-%d-%Y", "%m/%d/%Y", "%m.%d.%Y",
        "%m-%d-%y", "%m/%d/%y", "%m.%d.%y",
        "%y-%m-%d", "%y/%m/%d", "%y.%m.%d",
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
    ]
    for fmt in candidate_formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%Y-%m-%d")
        except:
            continue

    # 2) 混雜字串解析（例如 Receipt Date: 25/03/14 或 2025年3月14日）
    matches = list(re.finditer(r"\d+", text))
    if len(matches) < 2:
        return None

    nums = [int(m.group()) for m in matches]
    raw_tokens = [m.group() for m in matches]
    fallback_year = int(fallback_year) if fallback_year is not None else None

    year_idx = None
    # 先找四位數年份（優先）
    for i, token in enumerate(raw_tokens):
        if len(token) == 4 and 2000 <= int(token) <= 2100:
            year_idx = i
            break

    # 若沒有四位數年份，才使用 sidebar 年份當 anchor
    if year_idx is None and fallback_year is not None:
        yy = fallback_year % 100
        for i, n in enumerate(nums):
            if n == fallback_year:
                year_idx = i
                break

        # 兩位數年份 anchor：僅限明顯 3 段日期結構（例如 25/03/14）
        looks_like_triplet_date = bool(re.search(r"\b\d{1,2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}\b", text))
        if year_idx is None and looks_like_triplet_date and len(nums) == 3:
            for i, token in enumerate(raw_tokens):
                if len(token) <= 2 and int(token) == yy:
                    year_idx = i
                    break

    # 若字串內有明確四位數年份，也可直接採用
    if year_idx is None:
        for i, token in enumerate(raw_tokens):
            if len(token) == 4 and 2000 <= int(token) <= 2100:
                year_idx = i
                break

    # 2-1) 找到年份位置：依位置推斷 YYYY-MM-DD 或 DD-MM-YYYY
    if year_idx is not None:
        year_token = nums[year_idx]
        if year_token < 100:
            year_token = (2000 + year_token) if year_token <= 69 else (1900 + year_token)

        # 年在前 -> 年月日
        if year_idx + 2 < len(nums):
            candidate = _safe_date(year_token, nums[year_idx + 1], nums[year_idx + 2])
            if candidate:
                return candidate

        # 年在後 -> 日月年
        if year_idx - 2 >= 0:
            candidate = _safe_date(year_token, nums[year_idx - 1], nums[year_idx - 2])
            if candidate:
                return candidate

        # 年在中間 -> 月年日 或 日年月，嘗試兩種
        if 0 < year_idx < len(nums) - 1:
            candidate = _safe_date(year_token, nums[year_idx - 1], nums[year_idx + 1])
            if candidate:
                return candidate
            candidate = _safe_date(year_token, nums[year_idx + 1], nums[year_idx - 1])
            if candidate:
                return candidate

    if fallback_year is not None:
        # 僅在有明確分隔符的雙段日期時才使用 fallback_year，避免誤抓雜訊數字
        pair_match = re.search(r"\b(\d{1,2})\s*[-/.]\s*(\d{1,2})\b", text)
        if pair_match:
            a, b = int(pair_match.group(1)), int(pair_match.group(2))

            if a > 12 and b <= 12:
                candidate = _safe_date(fallback_year, b, a)
                if candidate:
                    return candidate
            if b > 12 and a <= 12:
                candidate = _safe_date(fallback_year, a, b)
                if candidate:
                    return candidate

            candidate = _safe_date(fallback_year, a, b)
            if candidate:
                return candidate
            candidate = _safe_date(fallback_year, b, a)
            if candidate:
                return candidate

    return None


def get_ambiguous_date_options(date_value, fallback_year=None):
    """若為歧義日期（如 03/04/2025），回傳兩個候選 YYYY-MM-DD。"""
    if date_value is None:
        return []

    text = str(date_value).strip()
    if not text:
        return []

    # 只針對 3 段數字日期，避免把 invoice 編號誤當日期
    m = re.search(r"\b(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{2,4})\b", text)
    if not m:
        return []

    first, second, third = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if first > 12 or second > 12:
        return []

    if third < 100:
        if fallback_year is not None and third == int(fallback_year) % 100:
            year = int(fallback_year)
        else:
            year = 2000 + third if third <= 69 else 1900 + third
    else:
        year = third

    dmy = None
    mdy = None
    try:
        dmy = datetime(year, second, first).strftime("%Y-%m-%d")
    except:
        pass
    try:
        mdy = datetime(year, first, second).strftime("%Y-%m-%d")
    except:
        pass

    options = []
    if dmy:
        options.append((dmy, f"{dmy}（以日月年解讀）"))
    if mdy and mdy != dmy:
        options.append((mdy, f"{mdy}（以月日年解讀）"))
    return options

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

@st.cache_data(ttl=600)
def resolve_vlm_model(api_key):
    """依可用模型清單自動選擇可用且相對低成本的 VLM 模型。"""
    try:
        genai.configure(api_key=api_key)
        models = [m for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        names = [m.name for m in models]

        # 優先使用預設偏好的低成本模型
        for preferred in PREFERRED_MODELS:
            if preferred in names:
                return preferred

        # 後備：任一 2.5 flash 模型
        for name in names:
            if '2.5-flash' in name:
                return name

        # 再後備：任一 flash 模型
        for name in names:
            if 'flash' in name:
                return name

        return None
    except:
        return None

def run_vlm_scan(api_key, image_bytes, year, country_info):
    """VLM 辨識：自動選用可用 Flash 模型，避免不可用模型造成失敗。"""
    try:
        genai.configure(api_key=api_key)

        selected_model = resolve_vlm_model(api_key) or "models/gemini-2.5-flash"

        # 使用可用模型 + 移除 system_instruction 控制 tokens
        model = genai.GenerativeModel(
            model_name=selected_model
            # 完全不設定 system_instruction
        )
        
        # === 優化 2: 壓縮圖片以降低 Visual Token 消耗 ===
        img = Image.open(io.BytesIO(image_bytes))
        
        # 計算縮放比例（保持長寬比）
        max_dimension = 1600  # 對 OCR 來說 1600px 已足夠
        if max(img.size) > max_dimension:
            ratio = max_dimension / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # === 優化 3: 超極簡 Prompt - 只給必要上下文 ===
        # 移除所有可能觸發 Thinking 的詞彙：
        # ❌ "Extract", "Return", "Country", "Decimal" 等
        # ✅ 只給幣別和年份
        prompt = f"""{country_info['currency']} {year}
JSON: shop, amount, YYYY-MM-DD, currency, items"""
        
        response = model.generate_content([prompt, img])
        
        raw = response.text.replace('```json', '').replace('```', '').strip()
        result = json.loads(raw)
        
        # 後處理：驗證日期合理性
        try:
            raw_date = result.get("date")

            normalized_date = normalize_receipt_date(raw_date, fallback_year=year)
            date_candidates = get_ambiguous_date_options(raw_date, fallback_year=year)

            if normalized_date:
                result["date"] = normalized_date
                
            result["日期歧義候選"] = date_candidates
            
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
        resolved_model = resolve_vlm_model(active_key)
        st.caption(f"💡 目前辨識模型：{resolved_model or 'models/gemini-2.5-flash'}")
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
        if 'uploader_key' in st.session_state:
            st.session_state['uploader_key'] += 1  # 重置檔案上傳器
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
            # 支付方式選擇
            payment_method = st.selectbox("💳 支付方式", ["信用卡", "現金"])
            
            if payment_method == "信用卡":
                fee_rate = st.number_input("手續費率 (%)", value=1.5, min_value=0.0, max_value=10.0, step=0.1) / 100
                st.caption("💡 信用卡海外交易手續費")
            else:  # 現金
                fee_rate = 0.0  # 現金無手續費
                
                # 現金模式：提供預設匯率輸入
                # 使用 session_state 保存，頁面重整前有效
                if 'default_cash_rate' not in st.session_state:
                    st.session_state['default_cash_rate'] = 0.0
                
                default_rate = st.number_input(
                    "💵 您的現金兌換匯率", 
                    value=st.session_state['default_cash_rate'],
                    min_value=0.0,
                    format="%.4f",
                    step=0.0001,
                    help="輸入實際兌換匯率，辨識時會自動套用"
                )
                
                # 儲存到 session_state
                st.session_state['default_cash_rate'] = default_rate
                
                if default_rate > 0:
                    st.caption(f"✓ 已設定預設匯率：{default_rate:.4f}")
                else:
                    st.caption("⚠️ 請先輸入兌換匯率")

        st.divider()
        
        # 批次上傳（使用動態 key 來支援清空）
        if 'uploader_key' not in st.session_state:
            st.session_state['uploader_key'] = 0
        
        files = st.file_uploader(
            "批次上傳單據照片", 
            accept_multiple_files=True, 
            type=['jpg','png','jpeg'],
            key=f"file_uploader_{st.session_state['uploader_key']}"
        )

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
                            # 防呆：模型可能回傳非 dict，避免 .get / [] 觸發 AttributeError
                            if not isinstance(res, dict):
                                st.warning(f"⚠️ 第 {idx+1} 張辨識結果格式異常，已略過。")
                                continue

                            # 欄位防呆：缺少核心欄位時略過，避免後續 KeyError
                            required_keys = {'shop', 'amount', 'currency'}
                            if not required_keys.issubset(res.keys()):
                                st.warning(f"⚠️ 第 {idx+1} 張辨識欄位不完整，已略過。")
                                continue

                            # UID 只依據圖片內容（不包含使用者名稱）
                            # 確保同一張照片無論誰上傳都是相同 UID
                            uid = hashlib.md5(img_bytes).hexdigest()[:12]
                            
                            # 確保日期格式正確
                            raw_date = res.get('date')
                            receipt_date = normalize_receipt_date(raw_date, fallback_year=target_year)
                            date_candidates = get_ambiguous_date_options(raw_date, fallback_year=target_year)
                            receipt_date = normalize_receipt_date(res.get('date'), fallback_year=target_year)
                            if not receipt_date:
                                # 如果格式錯誤，使用當天日期
                                receipt_date = datetime.now().strftime("%Y-%m-%d")
                                st.warning(f"⚠️ 收據日期格式錯誤，已設為今天：{receipt_date}")
                            
                            # 查詢匯率（根據支付方式）
                            if payment_method == "現金" and st.session_state.get('default_cash_rate', 0) > 0:
                                # 現金模式且有設定預設匯率 → 使用預設匯率
                                exchange_rate = st.session_state['default_cash_rate']
                            else:
                                # 信用卡模式或現金無預設 → 自動查詢
                                exchange_rate = get_rate_by_date(res['currency'], receipt_date)
                            
                            twd_amount = round(res['amount'] * exchange_rate, 0)
                            
                            batch.append({
                                "UID": uid,
                                "商店名稱": res['shop'],
                                "日期": receipt_date,  # 使用驗證過的日期
                                "日期原始": str(raw_date) if raw_date is not None else "",
                                "日期歧義候選": date_candidates,
                                "外幣金額": res['amount'],
                                "幣別": res['currency'],
                                "匯率": round(exchange_rate, 4) if payment_method == "現金" else round(exchange_rate, 3),
                                "台幣金額": twd_amount,
                                "品項摘要": normalize_items(res.get('items', '無')),  # 使用 normalize_items 處理
                                "備註": "",
                                "支付方式": payment_method  # 加入支付方式
                            })
                            
                            # 儲存圖片
                            images[uid] = base64.b64encode(img_bytes).decode()
                    
                    if batch:
                        # === 步驟 1: 批次內部去重（同一次上傳選到重複照片）===
                        batch_uids = {}
                        deduplicated_batch = []
                        duplicate_count = 0
                        
                        for record in batch:
                            uid = record['UID']
                            if uid not in batch_uids:
                                batch_uids[uid] = record
                                deduplicated_batch.append(record)
                            else:
                                # 同一次上傳內就有重複
                                duplicate_count += 1
                        
                        if duplicate_count > 0:
                            st.info(f"ℹ️ 過濾掉 {duplicate_count} 張重複照片（同一次上傳內）")
                        
                        # === 步驟 2: 與暫存區去重 ===
                        existing_uids = {record['UID'] for record in st.session_state['data']}
                        new_records = [record for record in deduplicated_batch if record['UID'] not in existing_uids]
                        
                        # === 步驟 3: 更新或覆蓋暫存區 ===
                        if new_records:
                            # 附加新資料
                            st.session_state['data'].extend(new_records)
                            # 更新圖片（使用 update 會覆蓋同 UID 的舊圖）
                            st.session_state['uploaded_images'].update(images)
                            st.success(f"✅ 新增 {len(new_records)} 筆辨識結果")
                        else:
                            st.warning("⚠️ 所有收據都已存在，未新增任何資料")
                        
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
                        
                        # 日期處理：加入錯誤處理
                        try:
                            if isinstance(record['日期'], str):
                                date_value = datetime.strptime(record['日期'], "%Y-%m-%d").date()
                            else:
                                date_value = record['日期']
                        except:
                            # 如果日期格式錯誤，使用當天日期
                            date_value = datetime.now().date()
                            st.warning(f"⚠️ 日期格式錯誤，已設為今天：{date_value}")
                        
                        raw_date = record.get('日期原始', '')
                        date_candidates = record.get('日期歧義候選', [])
                        if len(date_candidates) >= 2:
                            st.warning(f"⚠️ 偵測到日期歧義：{raw_date}")
                            labels = [label for _, label in date_candidates]
                            values = [value for value, _ in date_candidates]
                            current_value = record['日期'] if record['日期'] in values else values[0]
                            selected_label = st.radio(
                                "請確認日期解讀方式",
                                labels,
                                index=labels.index(next(label for value, label in date_candidates if value == current_value)),
                                key=f"ambiguous_date_{idx}"
                            )
                            selected_value = next(value for value, label in date_candidates if label == selected_label)
                            try:
                                date_value = datetime.strptime(selected_value, "%Y-%m-%d").date()
                            except:
                                pass

                        new_date = st.date_input("日期", value=date_value)
                        new_amount = st.number_input("外幣金額", value=float(record['外幣金額']), format="%.2f")
                        new_currency = st.text_input("幣別", value=record['幣別'])
                        
                        # 支付方式選擇
                        current_payment = record.get('支付方式', '信用卡')  # 預設信用卡
                        new_payment = st.selectbox(
                            "💳 支付方式", 
                            ["信用卡", "現金"],
                            index=0 if current_payment == "信用卡" else 1,
                            key=f"payment_{idx}"
                        )
                        
                        # 匯率輸入（現金可手動輸入，信用卡自動查詢）
                        if new_payment == "現金":
                            new_rate = st.number_input(
                                "💱 實際兌換匯率", 
                                value=float(record['匯率']), 
                                format="%.4f", 
                                step=0.0001,
                                help="現金兌換時的實際匯率（可手動輸入）"
                            )
                        else:
                            new_rate = st.number_input(
                                "💱 匯率", 
                                value=float(record['匯率']), 
                                format="%.3f", 
                                step=0.001,
                                help="信用卡匯率（可微調）"
                            )
                        
                        new_items = st.text_area("品項摘要", value=record['品項摘要'], height=100)
                        new_note = st.text_input("備註", value=record.get('備註', ''))
                        
                        # 即時計算台幣金額
                        calculated_twd = round(new_amount * new_rate, 0)
                        
                        # 顯示資訊（根據支付方式）
                        if new_payment == "現金":
                            st.info(f"💵 現金兌換 | 💰 台幣：NT$ {calculated_twd:,.0f}")
                        else:
                            st.info(f"💳 信用卡 | 💰 台幣：NT$ {calculated_twd:,.0f}")
                        
                        # 提交按鈕
                        submitted = st.form_submit_button("✅ 更新此筆資料", use_container_width=True)
                        
                        if submitted:
                            # 檢查是否有變更
                            date_changed = new_date.strftime("%Y-%m-%d") != record['日期']
                            amount_changed = new_amount != record['外幣金額']
                            currency_changed = new_currency != record['幣別']
                            rate_changed = new_rate != record['匯率']
                            payment_changed = new_payment != record.get('支付方式', '信用卡')
                            
                            # 更新資料
                            st.session_state['data'][idx]['商店名稱'] = new_shop
                            st.session_state['data'][idx]['日期'] = new_date.strftime("%Y-%m-%d")
                            st.session_state['data'][idx]['外幣金額'] = new_amount
                            st.session_state['data'][idx]['幣別'] = new_currency
                            st.session_state['data'][idx]['品項摘要'] = new_items
                            st.session_state['data'][idx]['備註'] = new_note
                            st.session_state['data'][idx]['支付方式'] = new_payment
                            
                            # 匯率邏輯：
                            # 1. 現金模式：使用者手動輸入匯率優先
                            # 2. 信用卡模式：如果使用者手動改匯率，優先使用；否則日期/幣別變更時自動查詢
                            if new_payment == "現金":
                                # 現金模式：直接使用手動輸入的匯率
                                st.session_state['data'][idx]['匯率'] = round(new_rate, 4)
                                st.session_state['data'][idx]['台幣金額'] = round(new_amount * new_rate, 0)
                            else:
                                # 信用卡模式
                                if rate_changed:
                                    # 使用者手動修改匯率
                                    st.session_state['data'][idx]['匯率'] = round(new_rate, 3)
                                    st.session_state['data'][idx]['台幣金額'] = round(new_amount * new_rate, 0)
                                elif date_changed or currency_changed:
                                    # 日期或幣別變更，自動查詢新匯率
                                    auto_rate = get_rate_by_date(new_currency, new_date.strftime("%Y-%m-%d"))
                                    st.session_state['data'][idx]['匯率'] = round(auto_rate, 3)
                                    st.session_state['data'][idx]['台幣金額'] = round(new_amount * auto_rate, 0)
                                elif amount_changed:
                                    # 只修改金額，保持原匯率
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
                    all_rows = wks.get_all_values()
                    uid_to_row = {}
                    for row_idx, row in enumerate(all_rows, start=1):
                        uid = str(row[12]).strip() if len(row) >= 13 else ""
                        if uid:
                            uid_to_row[uid] = row_idx

                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    updates = []         # 要覆蓋的
                    rows_to_append = []  # 要新增的

                    # === 逐筆檢查 ===
                    for r in st.session_state['data']:
                        uid = str(r['UID']).strip()

                        t_base = int(r['台幣金額'])
                        t_total = round(t_base * (1 + fee_rate), 0)

                        # 準備要寫入的資料（A~L 欄）
                        # 注意：時間戳和使用者會更新為當前上傳者
                        row_values_A_to_L = [
                            now, f_user, r['商店名稱'], r['品項摘要'], r['日期'],
                            r['外幣金額'], r['幣別'], r['匯率'],
                            t_base, t_total - t_base, t_total, r['備註']
                        ]

                        if uid in uid_to_row:
                            # UID 已存在 → 覆蓋該列的 A~L 欄（保留 M 欄 UID）
                            # 效果：同一張收據的新辨識結果會覆蓋舊資料
                            rownum = uid_to_row[uid]
                            updates.append({
                                "range": f"A{rownum}:L{rownum}",
                                "values": [row_values_A_to_L]
                            })
                        else:
                            # UID 不存在 → 新增一列（包含 UID）
                            rows_to_append.append(row_values_A_to_L + [uid])

                    # === 批次執行 ===
                    if updates:
                        wks.batch_update(updates, value_input_option='USER_ENTERED')

                    if rows_to_append:
                        required_rows = len(all_rows) + len(rows_to_append)
                        if required_rows > wks.row_count:
                            wks.add_rows(required_rows - wks.row_count)
                        wks.append_rows(rows_to_append, value_input_option='USER_ENTERED')

                    msg = []
                    if updates:
                        msg.append(f"更新 {len(updates)} 筆")
                    if rows_to_append:
                        msg.append(f"新增 {len(rows_to_append)} 筆")

                    st.success(f"✅ 同步完成！{' / '.join(msg)}")
                    
                    # 同步成功後提示，但不自動清空（讓使用者決定）
                    st.info("💡 同步完成後，暫存區資料仍保留。如需清空，請點擊下方按鈕。")
                    
                except Exception as e:
                    st.error(f"同步錯誤: {e}")
        
        # 清空暫存區按鈕（獨立於同步）
        if st.session_state['data']:
            st.divider()
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🗑️ 清空暫存區", type="secondary", use_container_width=True):
                    st.session_state['data'] = []
                    st.session_state['uploaded_images'] = {}
                    st.session_state['uploader_key'] += 1  # 重置檔案上傳器
                    st.success("✅ 暫存區已清空（包含上傳的檔案）")
                    st.rerun()
            with col2:
                st.caption("⚠️ 清空前請確認已同步至雲端")

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
