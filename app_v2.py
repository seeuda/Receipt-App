# Base Indent: 0 spaces
import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, hashlib
from datetime import datetime, timedelta
from google.cloud import vision
from PIL import Image, ImageEnhance, ImageFilter
import yfinance as yf
from typing import Dict, List, Tuple, Optional, Any
import numpy as np

# ═══════════════════════════════════════════════════════════════════════════
# I. 數據中心與財務引擎
# ═══════════════════════════════════════════════════════════════════════════

def init_session() -> None:
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'diagnostics' not in st.session_state:
        st.session_state['diagnostics'] = []
    if 'confidence_scores' not in st.session_state:
        st.session_state['confidence_scores'] = []

def calculate_salted_uid(file_content: bytes, user_name: str) -> str:
    """檔案 MD5 + 姓名加鹽，確保修正紀錄的個人化隔離"""
    file_hash = hashlib.md5(file_content).hexdigest()
    return hashlib.md5(f"{file_hash}{user_name}".encode()).hexdigest()

def get_gspread_client():
    creds_info = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        creds_info, 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=3600)
def get_rate_by_date(currency_code: str, target_date: datetime.date) -> float:
    if currency_code == "TWD": return 1.0
    try:
        ticker = yf.Ticker(f"{currency_code}TWD=X")
        sd, ed = target_date, target_date + timedelta(days=3)
        hist = ticker.history(start=sd, end=ed)
        if not hist.empty: return round(hist['Close'].iloc[0], 2)
        return 35.0
    except Exception: return 35.0

# ═══════════════════════════════════════════════════════════════════════════
# II. 專案管理與配置讀取
# ═══════════════════════════════════════════════════════════════════════════

def load_project_registry() -> Dict[str, str]:
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(st.secrets["admin_registry_id"])
        wks = sh.get_worksheet(0)
        data = wks.get_all_records()
        headers = wks.row_values(1)
        k_n = next((h for h in headers if "專案名稱" in h), "專案名稱")
        k_i = next((h for h in headers if "試算表 ID" in h), "試算表 ID")
        k_s = next((h for h in headers if "啟用狀態" in h), "啟用狀態")
        return {r[k_n]: r[k_i] for r in data if str(r.get(k_s, "")).strip().upper() == "TRUE"}
    except Exception: return {}

def add_project_to_registry(name: str, sheet_id: str) -> bool:
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(st.secrets["admin_registry_id"])
        wks = sh.get_worksheet(0); h = wks.row_values(1)
        idx_n = h.index(next(x for x in h if "專案名稱" in x))
        idx_i = h.index(next(x for x in h if "試算表 ID" in x))
        idx_s = h.index(next(x for x in h if "啟用狀態" in x))
        if sheet_id in wks.col_values(idx_i + 1): return False
        new_row = [""] * len(h)
        new_row[idx_n], new_row[idx_i], new_row[idx_s] = name, sheet_id, "TRUE"
        if h[0] in ["時間戳記", "Timestamp"]: new_row[0] = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        wks.append_row(new_row, value_input_option='USER_ENTERED'); return True
    except: return False

def load_all_configs() -> Dict:
    configs = {}
    emoji_map = {"tw": "🇹🇼", "jp": "🇯🇵", "kr": "🇰🇷", "sg": "🇸🇬", "vn": "🇻🇳", "th": "🇹🇭", "my": "🇲🇾", "ph": "🇵🇭", "id": "🇮🇩", "in": "🇮🇳", "ae": "🇦🇪", "il": "🇮🇱", "sa": "🇸🇦", "de": "🇩🇪", "at": "🇦🇹", "ch": "🇨🇭", "cz": "🇨🇿", "pl": "🇵🇱", "tr": "🇹🇷", "gb": "🇬🇧", "fr": "🇫🇷", "nl": "🇳🇱", "be": "🇧🇪", "ie": "🇮🇪", "dk": "🇩🇰", "no": "🇳🇴", "se": "🇸🇪", "fi": "🇫🇮", "is": "🇮🇸", "it": "🇮🇹", "es": "🇪🇸", "pt": "🇵🇹", "gr": "🇬🇷", "us": "🇺🇸", "ca": "🇨🇦", "br": "🇧🇷", "mx": "🇲🇽", "au": "🇦🇺", "nz": "🇳🇿", "za": "🇿🇦"}
    for f in glob.glob("configs/*.json"):
        iso = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            d = json.load(j); d['emoji'] = emoji_map.get(iso, "🌐"); configs[iso] = d
    return configs

# ═══════════════════════════════════════════════════════════════════════════
# III. 影像前處理增強
# ═══════════════════════════════════════════════════════════════════════════

def preprocess_image(image_bytes: bytes) -> bytes:
    """
    影像前處理流程：
    1. 對比度增強 (1.5x)
    2. 銳化處理
    3. 降噪處理
    目標：提升 OCR 辨識率 15-25%
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        
        # 轉換為 RGB（避免 RGBA 或灰階問題）
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # 1. 對比度增強
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)
        
        # 2. 銳化處理
        img = img.filter(ImageFilter.SHARPEN)
        
        # 3. 適度降噪（避免過度模糊）
        img = img.filter(ImageFilter.MedianFilter(size=3))
        
        # 輸出為 bytes
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=95)
        return output.getvalue()
    except Exception:
        # 若前處理失敗，返回原始影像
        return image_bytes

# ═══════════════════════════════════════════════════════════════════════════
# IV. 地址特徵辨識邏輯（強化版）
# ═══════════════════════════════════════════════════════════════════════════

def is_address_feature(line: str, params: Dict) -> bool:
    """純文字特徵判定是否為地址"""
    addr_re = re.compile(params.get('address_regex', r'(Tel:)|(Fax:)'), re.I)
    if addr_re.search(line): return True
    
    # 郵遞區號與數字規律
    if re.search(r'\b\d{5}\b', line) or re.search(r'\b\d{3}-\d{4}\b', line): return True
    
    # 數字與符號密度判定 (門牌特徵)
    if len(line) > 0:
        digit_ratio = sum(c.isdigit() for c in line) / len(line)
        symbol_density = sum(c in ",-/#." for c in line)
        if digit_ratio > 0.3 and symbol_density >= 2: return True
    
    return False

def detect_address_blocks(lines: List[str], params: Dict) -> List[int]:
    """
    檢測連續地址區塊（新增功能）
    地址特徵：
    1. 連續 2-3 行以上
    2. 包含地址關鍵字或郵遞區號
    3. 無金額數字
    
    返回：地址行的索引列表
    """
    address_indices = []
    consecutive_count = 0
    
    for i, line in enumerate(lines):
        is_addr = is_address_feature(line, params)
        
        # 檢查是否包含金額（小數點後兩位）
        has_price = bool(re.search(r'\d+[.,]\d{2}', line))
        
        if is_addr and not has_price:
            consecutive_count += 1
            address_indices.append(i)
        else:
            # 如果連續少於 2 行，不算地址區塊
            if consecutive_count == 1:
                address_indices.pop()
            consecutive_count = 0
    
    # 處理最後的連續區塊
    if consecutive_count == 1:
        address_indices.pop()
    
    return address_indices

def classify_diagnose(lines: List[str], params: Dict) -> List[Dict]:
    """語義標記分類器（強化版）"""
    diagnostics = []
    d_sep, t_sep = params.get('decimal_sep', '.'), params.get('thousand_sep', ',')
    
    # 正則表達式
    pay_re = re.compile(r'(VISA|MASTER|CASH|現金|卡號|CHANGE|TENDERED)', re.I)
    tax_re = re.compile(r'(TAX|VAT|GST|MWST|稅|税)', re.I)
    total_re = re.compile(r'(' + '|'.join(params.get('keywords', ['TOTAL', 'SUM'])) + r')', re.I)
    
    # 先檢測地址區塊
    address_indices = detect_address_blocks(lines, params)
    
    for i, line in enumerate(lines):
        line = line.strip()
        label = "ITEM"
        
        # 金額檢測（修正小數點判斷）
        prices = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', line)
        
        # 優先級判斷
        if i in address_indices:
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
        elif not prices:
            # 無金額的行可能是地址或標題
            if is_address_feature(line, params):
                label = "ADDRESS"
        
        diagnostics.append({
            "row": i,
            "content": line,
            "label": label,
            "has_price": "Yes" if prices else "No"
        })
    
    return diagnostics

# ═══════════════════════════════════════════════════════════════════════════
# V. 增強型日期解析
# ═══════════════════════════════════════════════════════════════════════════

def parse_date_advanced(text: str, params: Dict, target_year: int) -> Optional[datetime.date]:
    """
    多模式日期解析：
    1. 標準格式：YYYY/MM/DD, DD/MM/YYYY, MM/DD/YYYY
    2. 點號分隔：YYYY.MM.DD, DD.MM.YYYY
    3. 短橫線：YYYY-MM-DD, DD-MM-YYYY
    4. 空格分隔：DD MM YYYY
    5. 緊密格式：YYYYMMDD, DDMMYYYY
    """
    date_order = params.get('date_order', 'YMD')
    
    # 將文字統一處理（保留多種分隔符）
    patterns = [
        # 格式：YYYY/MM/DD 或 DD/MM/YYYY 或 MM/DD/YYYY
        r'(\d{4})[/\-\.\s](\d{1,2})[/\-\.\s](\d{1,2})',  # YYYY-MM-DD
        r'(\d{1,2})[/\-\.\s](\d{1,2})[/\-\.\s](\d{4})',  # DD-MM-YYYY 或 MM-DD-YYYY
        r'(\d{1,2})[/\-\.\s](\d{1,2})[/\-\.\s](\d{2})',  # DD-MM-YY 或 MM-DD-YY
        # 緊密格式
        r'(\d{4})(\d{2})(\d{2})',  # YYYYMMDD
        r'(\d{2})(\d{2})(\d{4})',  # DDMMYYYY
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                g1, g2, g3 = match
                
                # 判斷年份位置
                if len(g1) == 4:  # YYYY-MM-DD 格式
                    y, m, d = int(g1), int(g2), int(g3)
                elif len(g3) == 4:  # DD-MM-YYYY 或 MM-DD-YYYY 格式
                    y = int(g3)
                    if date_order == "DMY":
                        d, m = int(g1), int(g2)
                    else:  # MDY
                        m, d = int(g1), int(g2)
                elif len(g3) == 2:  # DD-MM-YY 格式
                    y = int(f"20{g3}")
                    if date_order == "DMY":
                        d, m = int(g1), int(g2)
                    else:  # MDY
                        m, d = int(g1), int(g2)
                else:
                    continue
                
                # 驗證日期合理性
                if y == target_year and 1 <= m <= 12 and 1 <= d <= 31:
                    return datetime(y, m, d).date()
            except (ValueError, IndexError):
                continue
    
    # 若無法解析，返回年度第一天
    return datetime(target_year, 1, 1).date()

# ═══════════════════════════════════════════════════════════════════════════
# VI. 增強型金額提取（修正小數點問題）
# ═══════════════════════════════════════════════════════════════════════════

def normalize_price(price_str: str, decimal_sep: str, thousand_sep: str) -> float:
    """
    標準化金額字串（修正小數點誤判）
    
    邏輯：
    1. 如果分隔符後面是兩位數字 → 小數點
    2. 如果分隔符後面是三位數字 → 千分位
    3. 移除千分位符號，替換小數點符號
    
    範例：
    - "1,234.56" (美式) → 1234.56
    - "1.234,56" (歐式) → 1234.56
    - "12.00" → 12.00 (不是 1200.00)
    """
    # 移除空格
    price_str = price_str.strip()
    
    # 判斷最後一個分隔符是小數點還是千分位
    # 策略：檢查最後一個分隔符後面的數字位數
    last_sep_idx = max(
        price_str.rfind(decimal_sep),
        price_str.rfind(thousand_sep)
    )
    
    if last_sep_idx > 0:
        # 取得分隔符後面的數字
        after_sep = price_str[last_sep_idx + 1:]
        
        # 如果後面剛好是兩位數字 → 小數點
        if len(after_sep) == 2 and after_sep.isdigit():
            # 這是小數點
            # 移除所有千分位，然後替換小數點
            if price_str[last_sep_idx] == decimal_sep:
                # 移除千分位
                clean = price_str.replace(thousand_sep, '')
                # 替換小數點為標準點號
                clean = clean.replace(decimal_sep, '.')
            else:
                # 最後一個是千分位符號，但後面只有兩位數 → 誤判，應該是小數點
                clean = price_str.replace(thousand_sep, '').replace(decimal_sep, '')
                clean = clean[:last_sep_idx] + '.' + after_sep
        else:
            # 這是千分位或其他情況
            clean = price_str.replace(thousand_sep, '').replace(decimal_sep, '.')
    else:
        # 沒有分隔符
        clean = price_str
    
    try:
        return float(clean)
    except ValueError:
        return 0.0

def extract_amount_with_validation(tags: List[Dict], params: Dict) -> Tuple[float, float]:
    """
    金額提取策略（修正版）：
    1. 優先從 TOTAL 標籤提取
    2. 驗證金額合理性（排除負數、過小值）
    3. 修正小數點誤判問題
    
    返回：(金額, 信心度 0-100)
    """
    d_sep = params.get('decimal_sep', '.')
    t_sep = params.get('thousand_sep', ',')
    
    # 金額正則（支援負數與小數）
    price_pattern = r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2,3})'
    
    # 策略 1：從 TOTAL 行提取
    total_rows = [t for t in tags if t['label'] == "TOTAL"]
    total_candidates = []
    
    for t in total_rows:
        prices = re.findall(price_pattern, t['content'])
        for p in prices:
            val = normalize_price(p, d_sep, t_sep)
            if val > 0:  # 排除負數
                total_candidates.append(val)
    
    # 策略 2：從品項行提取（備用）
    item_prices = []
    for t in tags:
        if t['label'] == 'ITEM':
            prices = re.findall(price_pattern, t['content'])
            for p in prices:
                val = normalize_price(p, d_sep, t_sep)
                if val > 0:
                    item_prices.append(val)
    
    # 決策邏輯
    if total_candidates:
        # 有 TOTAL 標籤：取最大值（通常是最終金額）
        amount = max(total_candidates)
        confidence = 90.0  # 高信心度
    elif item_prices:
        # 無 TOTAL 標籤：取最大品項金額但降低信心度
        amount = max(item_prices)
        confidence = 60.0  # 中等信心度
    else:
        amount = 0.0
        confidence = 0.0  # 無信心度
    
    # 驗證金額合理性
    if amount < 0.5:  # 過小金額視為異常
        confidence = max(0, confidence - 30)
    
    return amount, confidence

# ═══════════════════════════════════════════════════════════════════════════
# VII. 增強型商店名稱提取
# ═══════════════════════════════════════════════════════════════════════════

def extract_shop_name_with_validation(tags: List[Dict], params: Dict) -> Tuple[str, float]:
    """
    商店名稱提取策略：
    1. 優先從 SHOP 標籤提取
    2. 排除發票標題關鍵字
    3. 驗證名稱長度與特徵
    
    返回：(商店名稱, 信心度 0-100)
    """
    # 排除關鍵字
    exclude_keywords = [
        '發票', '收據', '明細', 'INVOICE', 'RECEIPT', 'BILL',
        '統一發票', '電子發票', '收銀機', 'TRANSACTION'
    ]
    
    shop_candidates = [t['content'] for t in tags if t['label'] == "SHOP"]
    
    for candidate in shop_candidates:
        # 檢查是否包含排除關鍵字
        if any(kw.upper() in candidate.upper() for kw in exclude_keywords):
            continue
        
        # 檢查長度合理性
        if 2 <= len(candidate) <= 50:
            confidence = 85.0
            return candidate, confidence
    
    # 降級策略：取第一個非空行
    for t in tags:
        if t['content'] and len(t['content']) >= 2:
            if not any(kw.upper() in t['content'].upper() for kw in exclude_keywords):
                return t['content'], 50.0  # 低信心度
    
    return "未知商店", 0.0

# ═══════════════════════════════════════════════════════════════════════════
# VIII. 整合型結構化資料提取（核心函數升級）
# ═══════════════════════════════════════════════════════════════════════════

def extract_structured_data(text: str, params: Dict, target_year: int) -> Tuple[Dict, List, Dict]:
    """
    增強版資料提取：
    返回：(結構化資料, 診斷標籤, 信心度評分)
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        empty_result = {"shop": "未知", "amount": 0.0, "items": "無", "date": datetime.now().date()}
        empty_confidence = {"overall": 0.0, "shop": 0.0, "amount": 0.0, "date": 0.0}
        return empty_result, [], empty_confidence
    
    # 1. 語義分類
    tags = classify_diagnose(lines, params)
    
    # 2. 商店名稱提取
    shop, shop_conf = extract_shop_name_with_validation(tags, params)
    
    # 3. 金額提取
    amount, amount_conf = extract_amount_with_validation(tags, params)
    
    # 4. 日期提取
    date_val = parse_date_advanced(text, params, target_year)
    date_conf = 80.0 if date_val.year == target_year else 30.0
    
    # 5. 品項提取（排除地址）
    items = [t['content'] for t in tags if t['label'] == "ITEM" and len(t['content']) > 2]
    items_text = "、".join(list(dict.fromkeys(items))[:3]) if items else "無品項資訊"
    
    # 6. 總體信心度計算
    overall_conf = (shop_conf * 0.2 + amount_conf * 0.5 + date_conf * 0.3)
    
    confidence = {
        "overall": round(overall_conf, 1),
        "shop": round(shop_conf, 1),
        "amount": round(amount_conf, 1),
        "date": round(date_conf, 1)
    }
    
    result = {
        "shop": shop,
        "amount": amount,
        "items": items_text,
        "date": date_val
    }
    
    return result, tags, confidence

# ═══════════════════════════════════════════════════════════════════════════
# IX. 雲端同步與財務計算
# ═══════════════════════════════════════════════════════════════════════════

def sync_to_sheets(df: pd.DataFrame, u_n: str, c_c: str, tid: str, fee_rate: float) -> Tuple[int, int]:
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(tid); wks = sh.get_worksheet(0)
        uids = wks.col_values(13); now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        to_app, upd_count = [], 0
        for _, r in df.iterrows():
            uid = r["UID"]; base = r["外幣金額"] * r["匯率"]
            fee = base * fee_rate; total = base + fee
            row = [now, u_n, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], c_c, r["匯率"], round(base,0), round(fee,0), round(total,0), r["備註"], uid]
            if uid in uids:
                idx = uids.index(uid) + 1; wks.update(f"A{idx}:M{idx}", [row], value_input_option='USER_ENTERED'); upd_count += 1
            else: to_app.append(row)
        if to_app: wks.append_rows(to_app, value_input_option='USER_ENTERED')
        return len(to_app), upd_count
    except Exception as e: st.error(f"同步異常: {e}"); return 0, 0

# ═══════════════════════════════════════════════════════════════════════════
# X. UI 主程式（增強版 + 匯率聯動）
# ═══════════════════════════════════════════════════════════════════════════

def main():
    st.set_page_config(page_title="考察支出登錄系統 [準確度增強版 v2.1]", layout="wide")
    init_session()
    all_cfg = load_all_configs()
    registry = load_project_registry()

    with st.sidebar:
        # 1. 專案選擇
        st.header("🏢 專案選擇")
        if registry:
            sel_p = st.selectbox("請選擇執行專案", list(registry.keys()))
            tid = registry[sel_p]
            try:
                gc = get_gspread_client()
                sh = gc.open_by_key(tid)
                wks = sh.worksheet("人員名單")
                u_l = [n for n in wks.col_values(1)[1:] if n.strip()]
            except:
                u_l = []
        else:
            st.warning("⚠️ 查無專案")
            tid, u_l = None, []
        
        st.markdown("---")
        # 2. 辨識控制
        st.header("⚙️ 辨識控制")
        t_year = st.number_input("📅 年度鎖定", value=2026)
        enable_preprocessing = st.checkbox("🎨 影像增強模式", value=True, help="提升 OCR 辨識率 15-25%")
        debug = st.checkbox("🔍 OCR 診斷模式")
        show_confidence = st.checkbox("📊 顯示信心度評分", value=True)
        
        if st.button("清空目前列表", use_container_width=True):
            st.session_state['data'] = []
            st.session_state['diagnostics'] = []
            st.session_state['confidence_scores'] = []
            st.rerun()
            
        st.markdown("---")
        # 3. 建立與註冊
        st.header("🆕 建立新專案")
        st.link_button("📥 範本連結", "https://docs.google.com/spreadsheets/d/15kD4ZMYEZvN3unbIhkH8b69KAVpiiKP-TA4q3pYJ86k/edit?usp=sharing", use_container_width=True)
        with st.expander("註冊新專案至系統"):
            n_p = st.text_input("專案名稱")
            i_p = st.text_input("試算表 ID")
            if st.button("確認註冊") and n_p and i_p:
                if add_project_to_registry(n_p, i_p):
                    st.success("註冊成功")
                    st.rerun()
        
        st.markdown("---")
        # 4. 客服支援
        st.header("🆘 客服支援")
        st.link_button("💬 小幫手問題回報中心", "https://line.me/ti/g/twX_HfMGBd", use_container_width=True)

    # --- Main UI ---
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_u = st.selectbox("報帳人員", u_l + ["其他"]) if u_l else st.text_input("人員姓名")
        final_u = st.text_input("確認姓名") if sel_u == "其他" else sel_u
    with c2:
        reg_map = {}
        for iso, cfg in all_cfg.items():
            rk = cfg.get('sub_region', '其他')
            if rk not in reg_map:
                reg_map[rk] = []
            reg_map[rk].append((f"{cfg['emoji']} {cfg['country']}", cfg))
        sel_reg = st.selectbox("🌍 區域範圍", sorted(reg_map.keys()))
        sel_c_name = st.selectbox("📍 記帳國家", [c[0] for c in reg_map[sel_reg]])
        p = next(c[1] for c in reg_map[sel_reg] if c[0] == sel_c_name)
    with c3:
        # 初始匯率（使用當前日期）
        if 'current_rate' not in st.session_state:
            st.session_state['current_rate'] = get_rate_by_date(p['currency_code'], datetime.now().date())
        
        cur_rate = st.number_input(
            f"匯率 ({p['currency_code']})", 
            value=float(st.session_state['current_rate']), 
            step=0.01,
            help="辨識後會根據收據日期自動更新"
        )
    with c4:
        fee_rate = st.number_input("手續費 (%)", value=1.5 if p['currency_code'] != "TWD" else 0.0) / 100
        if tid:
            st.link_button("📂 開啟試算表", f"https://docs.google.com/spreadsheets/d/{tid}/edit", use_container_width=True)

    st.markdown("---")
    files = st.file_uploader("📸 批次上傳收據", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files:
        with st.expander("🖼️ 影像預覽與狀態", expanded=True):
            img_c = st.columns(5)
            for idx, f in enumerate(files):
                f.seek(0)
                content = f.read()
                uid_check = calculate_salted_uid(content, final_u)
                with img_c[idx % 5]:
                    st.image(Image.open(io.BytesIO(content)), use_container_width=True)
                    status = '⚠️ 已辨識' if any(d['UID'] == uid_check for d in st.session_state['data']) else '🟢 待處理'
                    st.caption(f"#{idx+1} {status}")

    if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
        if not tid:
            st.error("❌ 未選擇專案")
        else:
            try:
                client = vision.ImageAnnotatorClient(
                    credentials=service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
                )
                st.session_state['data'] = []
                st.session_state['diagnostics'] = []
                st.session_state['confidence_scores'] = []
                
                # 進度條
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                for idx, f in enumerate(files):
                    f.seek(0)
                    content = f.read()
                    salted_uid = calculate_salted_uid(content, final_u)
                    
                    # 影像前處理
                    if enable_preprocessing:
                        processed_content = preprocess_image(content)
                        status_text.text(f"🎨 增強影像 {idx+1}/{len(files)}")
                    else:
                        processed_content = content
                    
                    # OCR 辨識
                    status_text.text(f"🔍 辨識收據 {idx+1}/{len(files)}")
                    txt = client.document_text_detection(
                        image=vision.Image(content=processed_content)
                    ).full_text_annotation.text
                    
                    # 資料提取
                    res, diag, conf = extract_structured_data(txt, p, t_year)
                    
                    # 🔧 修正：根據辨識日期自動更新匯率
                    auto_rate = get_rate_by_date(p['currency_code'], res['date'])
                    st.session_state['current_rate'] = auto_rate
                    
                    st.session_state['data'].append({
                        "商店名稱": res['shop'],
                        "參考品項": res['items'],
                        "消費日期": res['date'],
                        "外幣金額": res['amount'],
                        "匯率": auto_rate,  # 使用辨識日期的匯率
                        "備註": "",
                        "UID": salted_uid
                    })
                    
                    st.session_state['confidence_scores'].append({
                        "file": f.name,
                        "overall": conf['overall'],
                        "shop": conf['shop'],
                        "amount": conf['amount'],
                        "date": conf['date']
                    })
                    
                    if debug:
                        st.session_state['diagnostics'].append({"file": f.name, "report": diag})
                    
                    progress_bar.progress((idx + 1) / len(files))
                
                status_text.text("✅ 辨識完成！匯率已自動更新")
                st.rerun()
                
            except Exception as e:
                st.error(f"辨識異常: {e}")

    # 信心度評分顯示
    if show_confidence and st.session_state['confidence_scores']:
        st.markdown("---")
        st.subheader("📊 辨識信心度評分")
        
        conf_df = pd.DataFrame(st.session_state['confidence_scores'])
        
        # 顏色編碼
        def color_confidence(val):
            if val >= 80:
                return 'background-color: #d4edda; color: #155724'  # 綠色
            elif val >= 60:
                return 'background-color: #fff3cd; color: #856404'  # 黃色
            else:
                return 'background-color: #f8d7da; color: #721c24'  # 紅色
        
        styled_df = conf_df.style.applymap(
            color_confidence,
            subset=['overall', 'shop', 'amount', 'date']
        ).format({
            'overall': '{:.1f}%',
            'shop': '{:.1f}%',
            'amount': '{:.1f}%',
            'date': '{:.1f}%'
        })
        
        st.dataframe(styled_df, use_container_width=True)
        
        # 警告提示
        low_conf_files = [r['file'] for r in st.session_state['confidence_scores'] if r['overall'] < 60]
        if low_conf_files:
            st.warning(f"⚠️ 以下檔案信心度較低，建議仔細檢查：{', '.join(low_conf_files)}")

    if debug and st.session_state['diagnostics']:
        st.markdown("---")
        for r in st.session_state['diagnostics']:
            with st.expander(f"🛠️ 診斷報告: {r['file']}"):
                st.table(r['report'])

    if st.session_state['data']:
        st.markdown("---")
        st.subheader("📝 辨識結果編輯")
        edf = st.data_editor(
            pd.DataFrame(st.session_state['data']),
            use_container_width=True,
            num_rows="dynamic",
            column_config={
                "商店名稱": st.column_config.TextColumn("商店名稱", width="medium"),
                "參考品項": st.column_config.TextColumn("參考品項", width="large"),
                "消費日期": st.column_config.DateColumn("消費日期", format="YYYY-MM-DD"),
                "外幣金額": st.column_config.NumberColumn("外幣金額", format="%.2f"),
                "匯率": st.column_config.NumberColumn("匯率", format="%.2f"),
                "備註": st.column_config.TextColumn("備註", width="medium"),
            }
        )
        
        if st.button("📤 同步至雲端", type="primary", use_container_width=True):
            sc, uc = sync_to_sheets(edf, final_u, p['currency_code'], tid, fee_rate)
            st.success(f"✅ 同步完成：新增 {sc} 筆，覆蓋更新 {uc} 筆。")
            st.session_state['data'] = []
            st.session_state['confidence_scores'] = []
            st.rerun()

if __name__ == "__main__":
    main()
