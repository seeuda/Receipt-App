# Base Indent: 0 spaces
import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, json, hashlib, yfinance as yf
from datetime import datetime
import google.generativeai as genai
from PIL import Image

# ==========================================
# I. 基礎設施與金鑰初始化
# ==========================================

def init_gspread():
    """初始化 Google Sheets 授權"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ Google Sheets 授權失敗: {e}")
        return None

def init_gemini():
    """初始化 Gemini 1.5 Flash 引擎"""
    genai.configure(api_key=st.secrets["gemini_api_key"])
    return genai.GenerativeModel('models/gemini-1.5-flash')

def get_vlm_analysis(image_bytes: bytes, target_year: int):
    """VLM 語義辨識核心"""
    model = init_gemini()
    prompt = f"""
    你是一個財務審計專家。請分析此收據並提取資訊。時空背景為 {target_year} 年。
    請回傳純 JSON 格式：
    {{ "shop": "商店名", "amount": 0.0, "date": "YYYY-MM-DD", "currency": "幣別代碼", "items": "品項" }}
    注意：處理德文/歐陸格式時，請將逗點(,)視為小數點。
    """
    img = Image.open(io.BytesIO(image_bytes))
    response = model.generate_content([prompt, img])
    try:
        return json.loads(response.text.replace('```json', '').replace('```', '').strip())
    except:
        return {"shop": "辨識失敗", "amount": 0.0, "date": f"{target_year}-01-01", "currency": "TWD", "items": "N/A"}

# ==========================================
# II. UI 與 任務控制 (恢復舊版機制)
# ==========================================

st.set_page_config(page_title="Python 指揮官: 報帳系統 v4.5", layout="wide")

# Session State 初始化
if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.title("🛡️ 戰術控制中心")
    # 1. 案號與專案選擇 (恢復舊版功能)
    project_name = st.text_input("專案案號 / 名稱", value="Project_2026_Alpha")
    target_sheet_url = st.text_input("目標試算表 URL", value=st.secrets.get("default_sheet", ""))
    
    st.divider()
    
    # 2. 財務參數
    target_year = st.number_input("年份鎖定", value=2026)
    fee_rate = st.slider("跨國手續費 (%)", 0.0, 5.0, 1.5) / 100
    base_currency = st.selectbox("結算幣別", ["TWD", "USD", "HKD"])

    if st.button("🗑️ 清空暫存數據"):
        st.session_state['data'] = []
        st.rerun()

# 主介面
st.header(f"📋 當前專案：{project_name}")
files = st.file_uploader("上傳收據影像", accept_multiple_files=True, type=['jpg', 'png', 'jpeg'])

if st.button("🚀 執行深度辨識", type="primary"):
    if files:
        with st.spinner("Gemini 多模態偵察中..."):
            for f in files:
                content = f.read()
                # 影像與 UID 生成
                uid = hashlib.md5(content + project_name.encode()).hexdigest()[:12]
                
                # Gemini 辨識
                res = get_vlm_analysis(content, target_year)
                
                # 匯率換算
                rate = 1.0
                if res['currency'] != base_currency:
                    try:
                        rate = yf.Ticker(f"{res['currency']}{base_currency}=X").fast_info['lastPrice']
                    except: rate = 1.0
                
                final_amt = round(res['amount'] * rate * (1 + fee_rate), 0)
                
                # 寫入暫存 (等待使用者確認)
                st.session_state['data'].append({
                    "UID": uid,
                    "商店名稱": res['shop'],
                    "消費日期": res['date'],
                    "原始幣別": res['currency'],
                    "原始金額": res['amount'],
                    "匯率": round(rate, 4),
                    "換算金額": final_amt,
                    "摘要": res['items'],
                    "專案": project_name
                })
        st.success("✅ 辨識完成，請在下方確認數據。")

# ==========================================
# III. 數據確認與同步 (核心功能回歸)
# ==========================================

if st.session_state['data']:
    # 使用者可直接在表格內修改辨識錯誤的地方
    df = pd.DataFrame(st.session_state['data'])
    edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic")

    if st.button("📤 確認無誤，同步至 Google Sheets", type="secondary"):
        gc = init_gspread()
        if gc:
            try:
                sh = gc.open_by_url(target_sheet_url).get_worksheet(0)
                # 獲取現有 UID (第 13 欄) 以防重複同步
                existing_uids = sh.col_values(13)
                
                new_rows = []
                for _, row in edited_df.iterrows():
                    if row['UID'] not in existing_uids:
                        # 依照你原有的試算表格式排列欄位
                        new_rows.append([
                            row['消費日期'], row['商店名稱'], row['摘要'], 
                            row['原始金額'], row['原始幣別'], row['匯率'], 
                            row['換算金額'], row['專案'], "", "", "", "", row['UID']
                        ])
                
                if new_rows:
                    sh.append_rows(new_rows)
                    st.success(f"🎉 成功同步 {len(new_rows)} 筆新資料！")
                else:
                    st.warning("⚠️ 沒有新資料需要同步 (UID 已存在)。")
            except Exception as e:
                st.error(f"同步過程中出錯: {e}")