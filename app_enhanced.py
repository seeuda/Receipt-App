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
# I. 核心基礎設施：配置自動化、影像增強與授權
# ==========================================

def ensure_configs_exist():
    """自動化配置防呆：確保 40 國 JSON 參數在雲端環境就緒"""
    if not os.path.exists("configs") or not glob.glob("configs/*.json"):
        try:
            import generate_configs
            generate_configs.main()
        except: pass

def init_session() -> None:
    """初始化 Streamlit Session 工作區"""
    if 'data' not in st.session_state: 
        st.session_state['data'] = []
    if 'diagnostics' not in st.session_state: 
        st.session_state['diagnostics'] = []
    if 'auth_active' not in st.session_state: 
        st.session_state['auth_active'] = False

def enhance_image(image_bytes: bytes) -> bytes:
    """影像預處理：提升對比度 1.5x 與銳利度 2.0x"""
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageEnhance.Contrast(img).enhance(1.5)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

def calculate_salted_uid(file_content: bytes, user_name: str) -> str:
    """Salted UID：MD5(檔案二進位指紋 + 使用者姓名)，確保修正紀錄不落地覆蓋"""
    file_hash = hashlib.md5(file_content).hexdigest()
    return hashlib.md5(f"{file_hash}{user_name}".encode()).hexdigest()

def get_active_creds():
    """獲取當前運算憑證 (自備金鑰模式具備最高優先權)"""
    if st.session_state.get('auth_mode') == "自備金鑰" and 'custom_creds' in st.session_state:
        return st.session_state['custom_creds']
    return st.secrets["gcp_service_account"]

def get_gspread_client(creds_info=None):
    """建立 Google Sheets API 客戶端"""
    if creds_info is None:
        creds_info = get_active_creds()
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=scope)
    return gspread.authorize(creds)

# ==========================================
# II. 專案管理與地址特徵辨識邏輯
# ==========================================

def load_project_registry() -> Dict[str, str]:
    """
    讀取中央註冊表。
    修正點：強化欄位名稱匹配 (去空白) 與 啟用狀態判斷 (支援 TRUE 字符串)。
    """
    try:
        # 強制使用系統金鑰讀取管理表
        gc = get_gspread_client(st.secrets["gcp_service_account"])
        sh = gc.open_by_key(st.secrets["admin_registry_id"])
        wks = sh.get_worksheet(0)
        data = wks.get_all_records()
        if not data: return {}
        
        # 抓取標題列並清理空白
        headers = [str(h).strip() for h in wks.row_values(1)]
        k_n = next(x for x in headers if "專案名稱" in x)
        k_i = next(x for x in headers if "試算表 ID" in x)
        k_s = next(x for x in headers if "啟用狀態" in x)
        
        # 轉換資料並過濾啟用專案
        return {r[k_n]: r[k_i] for r in data if str(r.get(k_s, "")).strip().upper() == "TRUE"}
    except Exception as e:
        # 偵錯訊息：將錯誤呈現在側欄以便排查
        st.sidebar.error(f"⚠️ 註冊表讀取異常: {e}")
        return {}

def is_address_feature(line: str, params: Dict) -> bool:
    """不依賴標題字眼，利用「郵編、數字符號密度」判定是否為地址雜訊"""
    addr_re = re.compile(params.get('address_regex', r'(Tel:)|(Fax:)'), re.I)
    if addr_re.search(line): return True
    # 偵測各國 5 位數或 3-4 位組合郵編
    if re.search(r'\b\d{5}\b', line) or re.search(r'\b\d{3}-\d{4}\b', line): return True
    # 門牌與街道特徵：數字密度 > 30% 且具備分隔符
    if len(line) > 0:
        digit_ratio = sum(c.isdigit() for c in line) / len(line)
        symbol_count = sum(c in ",-/#." for c in line)
        if digit_ratio > 0.3 and symbol_count >= 2: return True
    return False

def classify_diagnose(lines: List[str], params: Dict) -> List[Dict]:
    """語義標記分類器：SHOP, ADDRESS, HEADER, ITEM, TOTAL, TAX, PAYMENT"""
    results = []
    d_sep, t_sep = params.get('decimal_sep', '.'), params.get('thousand_sep', ',')
    pay_re = re.compile(r'(VISA|MASTER|CASH|現金|卡號|CHANGE|TENDERED)', re.I)
    tax_re = re.compile(r'(TAX|VAT|GST|MWST|稅|税)', re.I)
    total_re = re.compile(r'(' + '|'.join(params.get('keywords', ['TOTAL', 'SUM'])) + r')', re.I)
    
    for i, line in enumerate(lines):
        line = line.strip(); label = "ITEM"
        prices = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', line)
        if is_address_feature(line, params): label = "ADDRESS"
        elif any(h.upper() in line.upper() for h in params.get('header_skips', [])): label = "HEADER"
        elif pay_re.search(line): label = "PAYMENT"
        elif tax_re.search(line): label = "TAX"
        elif total_re.search(line): label = "TOTAL"
        elif i == 0: label = "SHOP"
        results.append({"row": i, "content": line, "label": label, "has_price": "Yes" if prices else "No"})
    return results

def extract_structured_data(text: str, params: Dict, target_year: int) -> Tuple[Dict, List]:
    """結構化屬性提取核心"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines: return {"shop": "未知", "amount": 0.0, "items": "無", "date": datetime.now().date()}, []
    tags = classify_diagnose(lines, params); d_sep, t_sep = params.get('decimal_sep', '.'), params.get('thousand_sep', ',')
    shop = next((t['content'] for t in tags if t['label'] == "SHOP"), "未知商店")
    
    # 總金額提取邏輯
    amount = 0.0; total_rows = [t for t in tags if t['label'] == "TOTAL"]
    if total_rows:
        p_l = re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', total_rows[0]['content'])
        if p_l: amount = float(p_l[-1].replace(t_sep, '').replace(d_sep, '.'))
    if amount == 0.0:
        all_p = [float(p.replace(t_sep, '').replace(d_sep, '.')) for t in tags for p in re.findall(r'(-?\d+[' + re.escape(t_sep + d_sep) + r']\d{2})', t['content']) if p]
        if all_p: amount = max(all_p)
    
    items = [t['content'] for t in tags if t['label'] == "ITEM" and len(t['content']) > 2]
    d_val = datetime(target_year, 1, 1).date(); t_c = text.replace("/", " ").replace("-", " ").replace(".", " ")
    for line in t_c.splitlines():
        fm = re.findall(r'(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})', line)
        if fm:
            g1, g2, y_s = fm[0]; y = int(y_s) if len(y_s) == 4 else int(f"20{y_s}")
            if y == target_year:
                order = params.get('date_order', 'YMD'); d, m = (int(g1), int(g2)) if order == "DMY" else (int(g2), int(g1))
                try: d_val = datetime(y, m, d).date(); break
                except: continue
    return {"shop": shop, "amount": amount, "items": "、".join(list(dict.fromkeys(items))[:3]), "date": d_val}, tags

# ==========================================
# III. 財務運算與 Google Sheets Upsert 同步
# ==========================================

def sync_to_sheets(df: pd.DataFrame, u_n: str, currency: str, tid: str, fee_rate: float) -> Tuple[int, int]:
    """同步資料並執行財務演算：Total = Base * (1 + FeeRate)"""
    try:
        gc = get_gspread_client(); sh = gc.open_by_key(tid); wks = sh.get_worksheet(0)
        uids = wks.col_values(13); now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        to_append, upd_count = [], 0
        for _, r in df.iterrows():
            uid = r["UID"]; base = r["外幣金額"] * r["匯率"]; fee = base * fee_rate; total = base + fee
            # 建立 A-M 欄位行
            row = [now, u_n, r["商店名稱"], r["參考品項"], str(r["消費日期"]), r["外幣金額"], currency, r["匯率"], round(base,0), round(fee,0), round(total,0), r["備註"], uid]
            if uid in uids:
                idx = uids.index(uid) + 1; wks.update(f"A{idx}:M{idx}", [row], value_input_option='USER_ENTERED'); upd_count += 1
            else: to_append.append(row)
        if to_append: wks.append_rows(to_append, value_input_option='USER_ENTERED')
        return len(to_append), upd_count
    except Exception as e:
        st.error(f"同步失敗: {e}"); return 0, 0

# ==========================================
# IV. UI 主程式 (v2)
# ==========================================

def main():
    st.set_page_config(page_title="考察支出登錄系統 v2", layout="wide"); ensure_configs_exist(); init_session()
    
    # 載入 40 國配置
    all_configs = {os.path.basename(f).split('_')[0]: json.load(open(f, 'r', encoding='utf-8')) for f in glob.glob("configs/*.json")}
    project_registry = load_project_registry()

    with st.sidebar:
        # 1. 專案選擇
        st.header("🏢 專案選擇")
        tid, user_list = None, []
        if project_registry:
            sel_p = st.selectbox("選擇執行專案", list(project_registry.keys())); tid = project_registry[sel_p]
            try:
                # 取得該專案之人員名單
                gc_sys = get_gspread_client(st.secrets["gcp_service_account"])
                user_list = [n for n in gc_sys.open_by_key(tid).worksheet("人員名單").col_values(1)[1:] if n.strip()]
            except: user_list = []
        else:
            st.warning("⚠️ 系統未偵測到任何啟用專案")

        st.markdown("---")
        # 2. 授權模式：遠端密碼驗證 vs 自備金鑰
        st.header("🔐 授權與診斷")
        auth_mode = st.radio("認證來源", ["系統公共金鑰", "自備金鑰"])
        st.session_state['auth_mode'] = auth_mode
        
        if auth_mode == "系統公共金鑰":
            try:
                # 讀取遠端 Auth 試算表 A1 儲存格 
                auth_gc = get_gspread_client(st.secrets["gcp_service_account"])
                auth_sh = auth_gc.open_by_key("1rPQlGHtvx6M630vnZ_FANMRyR_EnMrzje85V3mZ2H0M")
                remote_pwd = str(auth_sh.worksheet("Auth").acell('A1').value).strip()
                
                pwd_input = st.text_input("輸入授權密碼", type="password")
                # 即時比對，相符即啟用，不需送出按鈕
                if pwd_input.strip() == remote_pwd:
                    st.success("✅ 公共授權成功")
                    st.session_state['auth_active'] = True
                else:
                    st.session_state['auth_active'] = False
            except Exception as e:
                st.error(f"遠端授權連線失敗: {e}")
        else:
            # 自備金鑰模式：上傳 JSON
            uploaded_json = st.file_uploader("上傳 JSON 金鑰檔案", type=['json'])
            if uploaded_json:
                st.session_state['custom_creds'] = json.load(uploaded_json)
                st.success("✅ 自備金鑰已載入")
                st.session_state['auth_active'] = True
            else:
                st.session_state['auth_active'] = False

        target_year = st.number_input("📅 年度鎖定", value=2026); debug_mode = st.checkbox("🔍 OCR 診斷模式")
        if st.button("清空列表快取", use_container_width=True):
            st.session_state['data'] = []; st.session_state['diagnostics'] = []; st.rerun()

        st.markdown("---")
        st.link_button("💬 小幫手問題回報中心", "https://line.me/ti/g/twX_HfMGBd", use_container_width=True)

    # --- 主畫面區 ---
    st.title("📸 考察支出登錄系統 v2")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_u = st.selectbox("報帳人員", user_list + ["其他"]) if user_list else st.text_input("人員姓名")
        final_u = st.text_input("確認姓名 (UID 鹽值)", value=sel_u) if sel_u == "其他" else sel_u
    with c2:
        reg_map = {}
        for iso, cfg in all_configs.items():
            rk = cfg.get('sub_region', '其他'); reg_map.setdefault(rk, []).append((cfg.get('country', iso), cfg))
        sel_reg = st.selectbox("🌍 區域", sorted(reg_map.keys()))
        countries = sorted(reg_map[sel_reg], key=lambda x: x[1].get('priority', 100))
        sel_c_name = st.selectbox("📍 國家", [c[0] for c in countries]); p = next(c[1] for c in countries if c[0] == sel_c_name)
    with c3:
        rate = get_rate_by_date(p['currency_code'], datetime.now().date())
        f_rate = st.number_input(f"匯率 ({p['currency_code']})", value=float(rate), step=0.01)
    with c4:
        # 動態手續費輸入，預設 1.5% 
        fee_rate = st.number_input("手續費 (%)", value=1.5 if p['currency_code'] != "TWD" else 0.0, step=0.1) / 100
        if tid: st.link_button("📂 開啟專案", f"https://docs.google.com/spreadsheets/d/{tid}/edit", use_container_width=True)

    st.markdown("---")
    files = st.file_uploader("📸 批次上傳收據", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

    if files:
        with st.expander("🖼️ 影像預覽", expanded=True):
            img_c = st.columns(5)
            for idx, f in enumerate(files):
                f.seek(0); content = f.read(); uid = calculate_salted_uid(content, final_u)
                with img_c[idx % 5]:
                    st.image(Image.open(io.BytesIO(content)), use_container_width=True)
                    st.caption(f"#{idx+1} {'⚠️ 已辨識' if any(d['UID'] == uid for d in st.session_state['data']) else '🟢 待處理'}")

    if files and st.button("🚀 執行 AI 自動辨識", type="primary", use_container_width=True):
        if not tid or not st.session_state.get('auth_active'): st.error("❌ 拒絕執行：請確認專案已選擇且完成授權密碼。")
        else:
            try:
                client = vision.ImageAnnotatorClient(credentials=service_account.Credentials.from_service_account_info(get_active_creds()))
                st.session_state['data'], st.session_state['diagnostics'] = [], []
                for f in files:
                    f.seek(0); content = f.read(); processed = enhance_image(content); uid = calculate_salted_uid(content, final_u)
                    txt = client.document_text_detection(image=vision.Image(content=processed)).full_text_annotation.text
                    res, diag = extract_structured_data(txt, p, target_year)
                    st.session_state['data'].append({"商店名稱": res['shop'], "參考品項": res['items'], "消費日期": res['date'], "外幣金額": res['amount'], "匯率": f_rate, "備註": "", "UID": uid})
                    if debug_mode: st.session_state['diagnostics'].append({"file": f.name, "report": diag})
                st.rerun()
            except Exception as e: st.error(f"辨識程序失敗: {e}")

    if debug_mode and st.session_state.get('diagnostics'):
        for r in st.session_state['diagnostics']:
            with st.expander(f"🛠️ 語義診斷: {r['file']}"): st.table(r['report'])

    if st.session_state['data']:
        edf = st.data_editor(pd.DataFrame(st.session_state['data']), use_container_width=True)
        if st.button("📤 同步至雲端", type="primary", use_container_width=True):
            a, u = sync_to_sheets(edf, final_u, p['currency_code'], tid, fee_rate)
            st.success(f"✅ 同步達成！(新增: {a} / 更新: {u})"); st.session_state['data'] = []; st.rerun()

if __name__ == "__main__": main()