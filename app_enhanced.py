# Base Indent: 0 spaces
import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, hashlib, numpy as np
from datetime import datetime, timedelta
from google.cloud import vision
from PIL import Image, ImageEnhance
import yfinance as yf
from typing import Dict, List, Tuple, Optional, Any

# ==========================================
# I. 基礎設施：數據中心、影像引擎與授權
# ==========================================

def ensure_configs_exist():
    """
    自動化防呆：若雲端環境缺少 JSON 配置，則呼叫腳本生成。
    """
    if not os.path.exists("configs") or not glob.glob("configs/*.json"):
        st.info("🔄 偵測到配置缺失，正在初始化 40 國在地化參數...")
        try:
            # 直接引用 generate_configs.py 的核心邏輯或執行該檔案
            import generate_configs
            generate_configs.main()
            st.success("✅ 配置初始化完成")
        except Exception as e:
            st.error(f"❌ 配置生成失敗: {e}")

def init_session() -> None:
    """
    初始化 Streamlit Session 狀態。
    確保 data, diagnostics 與認證狀態在頁面重整時不會丟失。
    """
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'diagnostics' not in st.session_state: 
        st.session_state['diagnostics'] = []
    if 'auth_active' not in st.session_state: 
        st.session_state['auth_active'] = False

def enhance_image(image_bytes: bytes) -> bytes:
    """
    自動影像增強模組。
    透過提升對比度與銳利度，減少 OCR 在處理反光或模糊收據時的誤差。
    """
    img = Image.open(io.BytesIO(image_bytes))
    # 提升對比度 (1.5倍)
    contrast_enhancer = ImageEnhance.Contrast(img)
    img = contrast_enhancer.enhance(1.5)
    # 提升銳利度 (2.0倍)
    sharpness_enhancer = ImageEnhance.Sharpness(img)
    img = sharpness_enhancer.enhance(2.0)
    
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

def calculate_salted_uid(file_content: bytes, user_name: str) -> str:
    """
    Salted UID 生成算法。
    結合檔案二進位 Hash 與使用者姓名進行二次 Hash，確保：
    1. 同一人上傳同一張圖 UID 固定。
    2. 不同人上傳同一張圖 UID 不同。
    """
    file_hash = hashlib.md5(file_content).hexdigest()
    salted_input = f"{file_hash}{user_name}"
    return hashlib.md5(salted_input.encode()).hexdigest()

def get_active_creds():
    """
    授權路由：根據目前 UI 選擇返回公共金鑰或自備金鑰。
    """
    if st.session_state.get('auth_mode') == "自備金鑰":
        if 'custom_creds' in st.session_state:
            return st.session_state['custom_creds']
    return st.secrets["gcp_service_account"]

def get_gspread_client():
    """
    建立 Google Sheets 安全授權客戶端。
    """
    try:
        creds_info = get_active_creds()
        scope = [
            "[https://www.googleapis.com/auth/spreadsheets](https://www.googleapis.com/auth/spreadsheets)",
            "[https://www.googleapis.com/auth/drive](https://www.googleapis.com/auth/drive)"
        ]
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Google Sheets 授權失敗: {str(e)}")
        return None

@st.cache_data(ttl=3600)
def get_rate_by_date(currency_code: str, target_date: datetime.date) -> float:
    """
    財務匯率引擎：抓取消費日期當下的匯率，失敗則返回預設值 35.0。
    """
    if currency_code == "TWD": 
        return 1.0
    try:
        ticker_symbol = f"{currency_code}TWD=X"
        ticker = yf.Ticker(ticker_symbol)
        # 抓取三日內數據以確保有值
        start_date = target_date
        end_date = target_date + timedelta(days=3)
        hist = ticker.history(start=start_date, end=end_date)
        if not hist.empty:
            return round(hist['Close'].iloc[0], 2)
        return 35.0
    except Exception:
        return 35.0

# ==========================================
# II. 業務邏輯：地址辨識與結構化分析
# ==========================================

def load_project_registry() -> Dict[str, str]:
    """
    從中央註冊表讀取啟用中的專案 ID。
    """
    try:
        gc = get_gspread_client()
        if not gc: return {}
        sh = gc.open_by_key(st.secrets["admin_registry_id"])
        wks = sh.get_worksheet(0)
        records = wks.get_all_records()
        headers = wks.row_values(1)
        
        # 動態偵測標題位置
        key_name = next(x for x in headers if "專案名稱" in x)
        key_id = next(x for x in headers if "試算表 ID" in x)
        key_status = next(x for x in headers if "啟用狀態" in x)
        
        return {r[key_name]: r[key_id] for r in records if str(r.get(key_status, "")).upper() == "TRUE"}
    except Exception:
        return {}

def is_address_feature(line: str, params: Dict) -> bool:
    """
    地址特徵辨識邏輯：利用郵編、數字密度判定，非關鍵字匹配。
    """
    # 1. Regex 關鍵字初步掃描
    addr_regex_str = params.get('address_regex', r'(Tel:)|(Fax:)')
    addr_re = re.compile(addr_regex_str, re.I)
    if addr_re.search(line):
        return True
    
    # 2. 郵遞區號偵測 (5位數或 3-4 位組合)
    if re.search(r'\b\d{5}\b', line) or re.search(r'\b\d{3}-\d{4}\b', line):
        return True
        
    # 3. 數字與符號密度判定 (門牌與街道特徵)
    if len(line) > 0:
        digit_count = sum(c.isdigit() for c in line)
        symbol_count = sum(c in ",-/#." for c in line)
        density = digit_count / len(line)
        if density > 0.3 and symbol_count >= 2:
            return True
            
    return False

def classify_diagnose(lines: List[str], params: Dict) -> List[Dict]:
    """
    語義診斷器：標記收據每一行的標籤。
    """
    results = []
    d_sep = params.get('decimal_sep', '.')
    t_sep = params.get('thousand_sep', ',')
    
    # 定義分類正則
    pay_re = re.compile(r'(VISA|MASTER|CASH|現金|卡號|CHANGE|TENDERED)', re.I)
    tax_re = re.compile(r'(TAX|VAT|GST|MWST|稅|税)', re.I)
    total_keywords = params.get('keywords', ['TOTAL', 'SUM'])
    total_re = re.compile(r'(' + '|'.join(total_keywords) + r')', re.I)
    
    for i, line in enumerate(lines):
        line = line.strip()
        label = "ITEM"
        # 價格特徵偵測
        prices = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', line)
        
        if is_address_feature(line, params):
            label = "ADDRESS"
        elif any(h.upper() in line.upper() for h in params.get('header_skips', [])):
            label = "HEADER"
        elif pay_re.search(line):
            label = "PAYMENT"
        elif tax_re.search(line):
            label = "TAX"
        elif total_re.search(line):
            label = "TOTAL"
        elif i == 0:
            label = "SHOP"
            
        results.append({
            "row": i, 
            "content": line, 
            "label": label, 
            "price_found": "Yes" if prices else "No"
        })
    return results

def extract_structured_data(text: str, params: Dict, target_year: int) -> Tuple[Dict, List]:
    """
    從 OCR 文字中提取結構化財務資訊。
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return {"shop": "未知", "amount": 0.0, "items": "無資料", "date": datetime.now().date()}, []
    
    tags = classify_diagnose(lines, params)
    d_sep, t_sep = params.get('decimal_sep', '.'), params.get('thousand_sep', ',')
    
    # 提取商店名 (標籤為 SHOP 的第一行)
    shop = next((t['content'] for t in tags if t['label'] == "SHOP"), "未知商店")
    
    # 提取總金額 (優先尋找 TOTAL 行，若無則抓取全域最大值)
    amount = 0.0
    total_rows = [t for t in tags if t['label'] == "TOTAL"]
    if total_rows:
        p_list = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', total_rows[0]['content'])
        if p_list:
            amount = float(p_list[-1].replace(t_sep, '').replace(d_sep, '.'))
            
    if amount == 0.0:
        all_prices = []
        for t in tags:
            p_list = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', t['content'])
            for p in p_list:
                all_prices.append(float(p.replace(t_sep, '').replace(d_sep, '.')))
        if all_prices:
            amount = max(all_prices)
            
    # 提取品項 (標籤為 ITEM 且長度大於 2 的行)
    item_list = [t['content'] for t in tags if t['label'] == "ITEM" and len(t['content']) > 2]
    items_summary = "、".join(list(dict.fromkeys(item_list))[:3])
    
    # 提取日期
    date_val = datetime(target_year, 1, 1).date()
    # 預先處理分隔符以利正則匹配
    clean_text = text.replace("/", " ").replace("-", " ").replace(".", " ")
    for line in clean_text.splitlines():
        match = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        if match:
            g1, g2, y_str = match[0]
            y = int(y_str) if len(y_str) == 4 else int(f"20{y_str}")
            if y == target_year:
                order = params.get('date_order', 'YMD')
                day, month = (int(g1), int(g2)) if order == "DMY" else (int(g2), int(g1))
                try:
                    date_val = datetime(y, month, day).date()
                    break
                except ValueError:
                    continue
                    
    return {"shop": shop, "amount": amount, "items": items_summary, "date": date_val}, tags

# ==========================================
# III. 財務運算與雲端同步 (Upsert 邏輯)
# ==========================================

def sync_to_sheets(df: pd.DataFrame, user_name: str, currency: str, tid: str, fee_rate: float) -> Tuple[int, int]:
    """
    同步資料至 Google Sheets。
    執行財務運算：$$Total = Base \times (1 + FeeRate)$$
    """
    try:
        gc = get_gspread_client()
        if not gc: return 0, 0
        sh = gc.open_by_key(tid)
        wks = sh.get_worksheet(0)
        
        # 抓取 M 欄 (UID 欄) 進行判重
        existing_uids = wks.col_values(13)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        append_list = []
        update_count = 0
        
        for _, row_data in df.iterrows():
            uid = row_data["UID"]
            # 財務計算
            base_twd = row_data["外幣金額"] * row_data["匯率"]
            fee_twd = base_twd * fee_rate
            total_twd = base_twd + fee_twd
            
            # 建立 A-M 欄位的完整資料行
            # A:時間, B:人員, C:商店, D:品項, E:日期, F:外幣, G:幣別, H:匯率, I:原台幣, J:手續費, K:總台幣, L:備註, M:UID
            final_row = [
                now_str, user_name, row_data["商店名稱"], row_data["參考品項"], 
                str(row_data["消費日期"]), row_data["外幣金額"], currency, 
                row_data["匯率"], round(base_twd, 0), round(fee_twd, 0), 
                round(total_twd, 0), row_data["備註"], uid
            ]
            
            if uid in existing_uids:
                # 執行覆蓋修正 (Upsert)
                row_idx = existing_uids.index(uid) + 1
                wks.update(f"A{row_idx}:M{row_idx}", [final_row], value_input_option='USER_ENTERED')
                update_count += 1
            else:
                append_list.append(final_row)
                
        if append_list:
            wks.append_rows(append_list, value_input_option='USER_ENTERED')
            
        return len(append_list), update_count
    except Exception as e:
        st.error(f"雲端同步發生錯誤: {e}")
        return 0, 0

# ==========================================
# IV. UI 主程式 (三段式側欄與主視窗)
# ==========================================

def main():
    st.set_page_config(page_title="考察支出登錄系統 (鋼鐵全量版)", layout="wide")
    init_session()
    
    # 載入設定
    all_configs = {}
    config_files = glob.glob("configs/*.json")
    for f in config_files:
        iso_code = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            all_configs[iso_code] = json.load(j)           
            
    project_registry = load_project_registry()

    # --- 1. 側欄佈局 ---
    with st.sidebar:
        # 第一段：專案選擇
        st.header("🏢 專案選擇")
        tid, user_list = None, []
        if project_registry:
            selected_p = st.selectbox("請選擇執行專案", list(project_registry.keys()))
            tid = project_registry[selected_p]
            try:
                gc = get_gspread_client()
                sh = gc.open_by_key(tid)
                user_list = [n for n in sh.worksheet("人員名單").col_values(1)[1:] if n.strip()]
            except:
                user_list = []
        else:
            st.warning("⚠️ 系統未偵測到任何啟用專案")

        st.markdown("---")
        # 第二段：授權模式與辨識控制
        st.header("🔐 授權模式與診斷")
        auth_choice = st.radio("認證來源", ["系統公共金鑰", "自備金鑰"])
        st.session_state['auth_mode'] = auth_choice
        
        if auth_choice == "系統公共金鑰":
            pwd_input = st.text_input("輸入授權密碼", type="password")
            if pwd_input == st.secrets.get("system_access_pwd"):
                st.success("✅ 公共授權已啟用")
                st.session_state['auth_active'] = True
            else:
                st.session_state['auth_active'] = False
        else:
            uploaded_json = st.file_uploader("上傳 JSON 金鑰檔案", type=['json'])
            if uploaded_json:
                st.session_state['custom_creds'] = json.load(uploaded_json)
                st.success("✅ 自備金鑰已載入")
                st.session_state['auth_active'] = True
            else:
                st.session_state['auth_active'] = False

        target_year = st.number_input("📅 年度鎖定", value=2026)
        debug_mode = st.checkbox("🔍 OCR 診斷模式")
        
        if st.button("清空目前快取列表", use_container_width=True):
            st.session_state['data'] = []
            st.session_state['diagnostics'] = []
            st.rerun()

        st.markdown("---")
        # 第三段：系統支援與回報
        st.header("🆕 系統支援")
        st.link_button("💬 小幫手問題回報中心", "[https://line.me/ti/g/twX_HfMGBd](https://line.me/ti/g/twX_HfMGBd)", use_container_width=True)

    # --- 2. 主畫面：參數輸入區 ---
    st.title("📸 考察支出 AI 辨識系統")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        selected_user = st.selectbox("報帳人員", user_list + ["其他"]) if user_list else st.text_input("報帳人員姓名")
        final_user_name = st.text_input("確認姓名 (用於 UID 加鹽)", value=selected_user) if selected_user == "其他" else selected_user
        
    with col2:
        # 二階區域選擇邏輯
        region_map = {}
        for iso, cfg in all_configs.items():
            region = cfg.get('sub_region', '其他')
            region_map.setdefault(region, []).append((cfg.get('country', iso), cfg))
            
        selected_region = st.selectbox("🌍 區域範圍", sorted(region_map.keys()))
        country_options = sorted(region_map[selected_region], key=lambda x: x[1].get('priority', 100))
        selected_country_name = st.selectbox("📍 記帳國家", [c[0] for c in country_options])
        selected_params = next(c[1] for c in country_options if c[0] == selected_country_name)
        
    with col3:
        initial_rate = get_rate_by_date(selected_params['currency_code'], datetime.now().date())
        final_rate = st.number_input(f"匯率 ({selected_params['currency_code']})", value=float(initial_rate), step=0.01)
        
    with col4:
        # 動態手續費輸入
        default_fee = 1.5 if selected_params['currency_code'] != "TWD" else 0.0
        final_fee_rate = st.number_input("動態手續費 (%)", value=default_fee, step=0.1) / 100
        if tid:
            st.link_button("📂 開啟專案試算表", f"[https://docs.google.com/spreadsheets/d/](https://docs.google.com/spreadsheets/d/){tid}/edit", use_container_width=True)

    st.markdown("---")
    
    # --- 3. 影像處理與辨識區 ---
    upload_files = st.file_uploader("📸 批次上傳收據 (支援多檔)", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if upload_files:
        with st.expander("🖼️ 影像預覽與辨識狀態", expanded=True):
            preview_cols = st.columns(5)
            for i, f in enumerate(upload_files):
                f.seek(0)
                file_bytes = f.read()
                uid_check = calculate_salted_uid(file_bytes, final_user_name)
                with preview_cols[i % 5]:
                    st.image(Image.open(io.BytesIO(file_bytes)), use_container_width=True)
                    is_processed = any(d['UID'] == uid_check for d in st.session_state['data'])
                    st.caption(f"#{i+1} {'⚠️ 已在清單' if is_processed else '🟢 待辨識'}")

    if upload_files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
        if not tid or not st.session_state.get('auth_active'):
            st.error("❌ 拒絕執行：請確認您已選擇專案並完成密碼授權。")
        else:
            try:
                # 初始化 Vision API 客戶端
                vision_creds = service_account.Credentials.from_service_account_info(get_active_creds())
                vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)
                
                st.session_state['data'] = []
                st.session_state['diagnostics'] = []
                
                progress_bar = st.progress(0)
                for i, f in enumerate(upload_files):
                    f.seek(0)
                    raw_content = f.read()
                    uid = calculate_salted_uid(raw_content, final_user_name)
                    
                    # 影像預處理：增強對比度與銳利度
                    processed_bytes = enhance_image(raw_content)
                    
                    # 執行 Vision OCR
                    image_obj = vision.Image(content=processed_bytes)
                    ocr_response = vision_client.document_text_detection(image=image_obj)
                    full_text = ocr_response.full_text_annotation.text
                    
                    # 結構化提取
                    result, diag_report = extract_structured_data(full_text, selected_params, target_year)
                    
                    # 存入 Session
                    st.session_state['data'].append({
                        "商店名稱": result['shop'],
                        "參考品項": result['items'],
                        "消費日期": result['date'],
                        "外幣金額": result['amount'],
                        "匯率": final_rate,
                        "備註": "",
                        "UID": uid
                    })
                    
                    if debug_mode:
                        st.session_state['diagnostics'].append({"file": f.name, "report": diag_report})
                    
                    progress_bar.progress((i + 1) / len(upload_files))
                
                st.success(f"✅ 成功辨識 {len(upload_files)} 張收據！")
                st.rerun()
            except Exception as e:
                st.error(f"辨識程序發生異常: {e}")

    # --- 4. 診斷儀表板呈現 ---
    if debug_mode and st.session_state.get('diagnostics'):
        st.subheader("🛠️ OCR 語義診斷儀表板")
        for diag in st.session_state['diagnostics']:
            with st.expander(f"文件分析報告: {diag['file']}"):
                st.table(diag['report'])

    # --- 5. 資料編輯與雲端同步 ---
    if st.session_state['data']:
        st.subheader("📝 辨識結果確認 (可手動修正)")
        editable_df = st.data_editor(
            pd.DataFrame(st.session_state['data']), 
            use_container_width=True,
            num_rows="dynamic"
        )
        
        if st.button("📤 確認並同步至雲端試算表", type="primary", use_container_width=True):
            with st.spinner("同步中..."):
                added, updated = sync_to_sheets(
                    editable_df, 
                    final_user_name, 
                    selected_params['currency_code'], 
                    tid, 
                    final_fee_rate
                )
                st.success(f"✅ 同步成功！(新增: {added} 筆 / 覆蓋修正: {updated} 筆)")
                # 清空已處理數據
                st.session_state['data'] = []
                st.rerun()

if __name__ == "__main__":
    main()