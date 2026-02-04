import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, glob, re, json
from datetime import datetime
from google.cloud import vision

# --- 1. 核心邏輯 ---

# 建立國家名稱映射表 (若未來有新國家，在此新增即可)
COUNTRY_NAMES = {
    "denmark": "🇩🇰 丹麥",
    "japan": "🇯🇵 日本",
    "usa": "🇺🇸 美國",
    "germany": "🇩🇪 德國",
    "taiwan": "🇹🇼 台灣"
}

def load_all_configs():
    configs = {}
    for f in glob.glob("configs/*.json"):
        if "users.json" in f: continue 
        filename = os.path.splitext(os.path.basename(f))[0]
        # 優先顯示映射表中的中文，若無則顯示首字母大寫的檔名
        display_name = COUNTRY_NAMES.get(filename, filename.capitalize())
        with open(f, 'r', encoding='utf-8') as j:
            configs[display_name] = json.load(j)
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
    sep = re.escape(params['decimal_separator'])
    curr = params['currency_code'].upper()
    money_regex = rf'(\d+[{sep}]\d{{2}})[\s]*([A-Za-z]*)'
    candidates = []
    for i, line in enumerate(lines):
        for match in re.finditer(money_regex, line):
            val = float(match.group(1).replace(params['decimal_separator'], '.'))
            score = (200 if re.search(r'Visa|Dankort|Ialt|Total|Payment', line, re.I) else 0)
            if curr in line.upper(): score += 100
            candidates.append({'val': val, 'score': score + (i/len(lines)*60)})
    return vendor, sorted(candidates, key=lambda x: x['score'], reverse=True)[0]['val'] if candidates else 0.0

def sync_to_sheets(df, user_name, curr_code):
    try:
        creds_info = st.secrets["gcp_service_account"]
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = service_account.Credentials.from_service_account_info(creds_info, scopes=scopes)
        gc = gspread.authorize(creds)
        # 你的試算表 ID
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
        if "200" in str(e): return True
        st.error(f"同步失敗：{e}")
        return False

# --- 2. 網頁介面 ---
st.set_page_config(page_title="支出登錄系統", layout="wide")
st.title("📊 國外考察支出登錄統計系統")

if 'data' not in st.session_state: st.session_state['data'] = []

# --- 步驟 1：設定區 ---
with st.expander("👤 步驟 1：基本設定與結果查看", expanded=True):
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        user_options = load_users() + ["其他"]
        sel_user = st.selectbox("人員", user_options)
        final_user = st.text_input("手寫姓名") if sel_user == "其他" else sel_user
    with c2:
        configs = load_all_configs()
        # 此處會顯示「🇩🇰 丹麥」等中文名稱
        sel_display = st.selectbox("考察國家", list(configs.keys()))
        p = configs[sel_display]
        manual_rate = st.number_input(f"參考匯率 ({p['currency_code']})", value=4.60, step=0.01)
    with c3:
        fee_pct = st.number_input("手續費率 (%)", value=1.5, step=0.1) / 100
        # 新增：試算表連結按鈕
        sheet_url = "https://docs.google.com/spreadsheets/d/1Aw7ti3Yadw9SJ1n6_WoEFU1SQrmDfIGQw6O0oeO_gUM/edit"
        st.link_button("📂 打開試算表查看結果", sheet_url, use_container_width=True)

# --- 步驟 2：上傳區 ---
st.subheader("📸 步驟 2：上傳收據")
files = st.file_uploader("批次上傳", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'])

if files:
    with st.expander("🖼️ 預覽收據"):
        cols = st.columns(5)
        for idx, f in enumerate(files):
            cols[idx % 5].image(f, use_container_width=True)

    if st.button("🔍 執行 AI 辨識", type="primary", use_container_width=True):
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
                    "商店名稱": v, "參考品項": "", "消費日期": d, "外幣金額": a, "匯率": manual_rate, "備註": ""
                })
                prog.progress((idx + 1) / len(files))
            st.session_state['data'] = new_batch
            st.success("✅ 辨識完成！")
        except Exception as e: st.error(f"辨識出錯：{e}")

# --- 步驟 3：確認與同步 ---
if st.session_state['data']:
    st.markdown("---")
    st.subheader("📝 步驟 3：數據確認 (手動修正外幣或匯率，下方將即時更新)")
    
    df = pd.DataFrame(st.session_state['data'])
    df["消費日期"] = pd.to_datetime(df["消費日期"]).dt.date

    col_order = ["商店名稱", "參考品項", "消費日期", "外幣金額", "匯率", "備註"]
    
    edited_df = st.data_editor(
        df[col_order],
        column_config={
            "消費日期": st.column_config.DateColumn(),
            "外幣金額": st.column_config.NumberColumn(format="%.2f"),
        },
        num_rows="dynamic", use_container_width=True, key="editor"
    )

    # 即時計算
    edited_df["原始台幣"] = (edited_df["外幣金額"] * edited_df["匯率"]).round(0)
    edited_df["手續費"] = (edited_df["原始台幣"] * fee_pct).round(0)
    edited_df["總計台幣"] = edited_df["原始台幣"] + edited_df["手續費"]

    st.write("📊 **即時換算預覽：**")
    final_cols = ["商店名稱", "參考品項", "消費日期", "外幣金額", "匯率", "原始台幣", "手續費", "總計台幣", "備註"]
    st.dataframe(edited_df[final_cols], use_container_width=True)
    
    total_val = int(edited_df["總計台幣"].sum())
    st.metric("本批總支出金額 (TWD)", f"{total_val:,} 元")

    if st.button("📤 同步至雲端", type="primary", use_container_width=True):
        if sync_to_sheets(edited_df, final_user, p['currency_code']):
            st.toast("同步成功！")
            st.balloons()
            st.session_state['data'] = []
            st.rerun()