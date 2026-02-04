import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json, yfinance as yf
from datetime import datetime, timedelta
from google.cloud import vision

# --- 1. 核心解析邏輯 ---
def load_all_configs():
    configs = {}
    for f in glob.glob("configs/*.json"):
        if "users.json" in f: continue 
        name = os.path.splitext(os.path.basename(f))[0]
        with open(f, 'r', encoding='utf-8') as j:
            configs[name] = json.load(j)
    return configs

def load_users():
    user_file = "configs/users.json"
    try:
        if os.path.exists(user_file):
            with open(user_file, "r", encoding="utf-8") as f:
                return json.load(f).get("users", ["預設登錄員"])
        return ["預設登錄員"]
    except: return ["預設登錄員"]

def normalize_date_pro(text, month_map):
    temp_text = text.replace("'", "")
    for m_name, m_num in month_map.items():
        temp_text = re.sub(rf'\b{m_name}\b', m_num, temp_text, flags=re.IGNORECASE)
    patterns = [r'(\d{1,2})[./\s-](\d{1,2})[./\s-](\d{4})', r'(\d{1,2})[./\s-](\d{1,2})[./\s-](\d{2})']
    for p in patterns:
        for m in re.finditer(p, temp_text):
            g = m.groups()
            try:
                y = g[2] if len(g[2]) == 4 else f"20{g[2]}"
                v_dt = datetime(int(y), int(g[1]), int(g[0]))
                if 2020 <= v_dt.year <= 2026: return v_dt.date()
            except: continue
    return datetime.now().date()

def extract_data(text, params):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    vendor = lines[0] if lines else "未知商店"
    sep, curr = re.escape(params['decimal_separator']), params['currency_code'].upper()
    money_regex = rf'(\d+[{sep}]\d{{2}})[\s]*([A-Za-z]*)'
    candidates = []
    for i, line in enumerate(lines):
        for match in re.finditer(money_regex, line):
            val = float(match.group(1).replace(params['decimal_separator'], '.'))
            score = (200 if re.search(r'Visa|Dankort|Ialt|Total|Payment', line, re.I) else 0)
            if curr in line.upper(): score += 100
            if re.search(r'moms|tax|ant', line, re.I): score -= 180
            candidates.append({'val': val, 'score': score + (i/len(lines)*60)})
    return vendor, sorted(candidates, key=lambda x: x['score'], reverse=True)[0]['val'] if candidates else 0.0

# --- 2. Google Sheets 同步功能 (ID 精確版) ---
def sync_to_sheets(df, user_name, curr_code):
    try:
        creds_info = st.secrets["gcp_service_account"]
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=scopes)
        gc = gspread.authorize(creds)
        
        # 使用你指定的試算表 ID
        sh = gc.open_by_key("1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM")
        wks = sh.get_worksheet(0)
        
        output_data = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for _, row in df.iterrows():
            output_data.append([
                now_str, user_name, row["商店名稱"], row["參考品項"], str(row["消費日期"]),
                row["外幣金額"], curr_code, row["匯率"], row["原始台幣"], row["手續費"], 
                row["總計台幣"], row["備註"]
            ])
        
        wks.append_rows(output_data, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        if "200" in str(e): return True # 處理假報錯
        st.error(f"同步至雲端失敗：{e}")
        return False

# --- 3. UI 介面設計 ---
st.set_page_config(page_title="支出登錄統計系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")

# 初始化 Session State (跨頁面記憶數據)
if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.header("👤 登錄人員設定")
    user_options = load_users() + ["其他"]
    sel_user = st.selectbox("選擇登錄者", user_options)
    final_user = st.text_input("輸入姓名") if sel_user == "其他" else sel_user
    
    st.markdown("---")
    configs = load_all_configs()
    sel_config = st.selectbox("選擇考察國家", list(configs.keys()))
    p = configs[sel_config]
    manual_rate = st.number_input(f"當前匯率 ({p['currency_code']})", value=4.60, step=0.01)
    fee_pct = st.number_input("手續費率 (%)", value=1.5, step=0.1) / 100

    if st.button("🗑️ 清空所有辨識結果", help="重新開始新的一批辨識"):
        st.session_state['data'] = []
        st.rerun()

# --- 上傳與預覽區塊 ---
st.subheader("📸 收據處理")
files = st.file_uploader("批次上傳收據照片 (可多選)", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files:
    with st.expander("🖼️ 查看上傳收據預覽 (確認內容)", expanded=True):
        cols = st.columns(5) # 每行顯示 5 張縮圖
        for idx, f in enumerate(files):
            with cols[idx % 5]:
                st.image(f, use_container_width=True)
                st.caption(f"📄 {f.name[:10]}...")

    if st.button("🔍 開始執行 AI 批次辨識", type="primary", use_container_width=True):
        new_batch = []
        try:
            creds_info = st.secrets["gcp_service_account"]
            vision_creds = service_account.Credentials.from_service_account_info(creds_info)
            client = vision.ImageAnnotatorClient(credentials=vision_creds)
            
            prog = st.progress(0)
            for idx, f in enumerate(files):
                content = f.read()
                res = client.document_text_detection(image=vision.Image(content=content))
                v, a = extract_data(res.full_text_annotation.text, p)
                d = normalize_date_pro(res.full_text_annotation.text, p.get('month_map', {}))
                new_batch.append({
                    "商店名稱": v, "消費日期": d, "外幣金額": a, "匯率": manual_rate, 
                    "參考品項": "", "備註": ""
                })
                prog.progress((idx + 1) / len(files))
            
            st.session_state['data'] = new_batch # 覆蓋目前的 Session Data
            st.success(f"✅ 成功辨識 {len(new_batch)} 張收據！")
        except Exception as e: st.error(f"辨識出錯：{e}")

# --- 數據編輯與同步區塊 ---
if st.session_state['data']:
    st.markdown("---")
    df = pd.DataFrame(st.session_state['data'])
    
    # 確保欄位名稱正確 (防止 Key 錯誤)
    if "日期" in df.columns: df = df.rename(columns={"日期": "消費日期"})
    if "商店" in df.columns: df = df.rename(columns={"商店": "商店名稱"})
    
    df["消費日期"] = pd.to_datetime(df["消費日期"]).dt.date
    
    # 計算衍伸金額
    df["原始台幣"] = (df["外幣金額"] * df["匯率"]).round(0)
    df["手續費"] = (df["原始台幣"] * fee_pct).round(0)
    df["總計台幣"] = df["原始台幣"] + df["手續費"]
    
    # 偵測重複項
    is_dup = df.duplicated(subset=["商店名稱", "消費日期", "外幣金額"], keep=False)
    if is_dup.any():
        st.warning("⚠️ 偵測到「重複的收據內容」，請檢查下方表格。")

    with st.expander("📝 數據確認與編輯清單", expanded=True):
        col_order = ["商店名稱", "參考品項", "消費日期", "外幣金額", "匯率", "原始台幣", "手續費", "總計台幣", "備註"]
        edited_df = st.data_editor(
            df[col_order],
            column_config={
                "消費日期": st.column_config.DateColumn(),
                "外幣金額": st.column_config.NumberColumn(format="%.2f"),
                "原始台幣": st.column_config.NumberColumn(disabled=True),
                "總計台幣": st.column_config.NumberColumn(disabled=True),
            },
            num_rows="dynamic", use_container_width=True
        )
        
        c1, c2 = st.columns([1, 4])
        with c1:
            if st.button("📤 同步至 Google 試算表", type="primary"):
                if sync_to_sheets(edited_df, final_user, p['currency_code']):
                    st.toast("數據傳送成功！")
                    st.balloons()
                    st.session_state['data'] = [] # 同步成功後清空，防止重刷頁面造成重複寫入
                    st.rerun() # 強制刷新畫面回歸乾淨狀態
        with c2:
            st.info(f"💰 本批次合計：{int(edited_df['總計台幣'].sum())} TWD")