# Base Indent: 0 spaces
import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
import io, os, json, hashlib
from datetime import datetime
import google.generativeai as genai
from PIL import Image
import yfinance as yf

# ==========================================
# I. 核心配置與 Gemini 初始化
# ==========================================

def init_gemini():
    """初始化 Gemini 1.5 Flash 引擎"""
    if 'gemini_api_key' not in st.secrets:
        st.error("❌ 找不到 gemini_api_key，請在 secrets.toml 中設定。")
        st.stop()
    genai.configure(api_key=st.secrets["gemini_api_key"])
    return genai.GenerativeModel('gemini-1.5-flash')

def get_vlm_analysis(image_bytes: bytes, target_year: int) -> dict:
    """使用多模態模型直接理解收據內容"""
    model = init_gemini()
    
    # 針對收據辨識的結構化提示詞
    prompt = f"""
    你是一個財務報帳專家。請分析這張收據影像並提取資訊。
    當前時空背景為 {target_year} 年。
    
    請務必回傳純 JSON 格式，包含以下欄位：
    - shop: 商店名稱 (String)
    - amount: 總消費金額 (Float, 僅數字)
    - date: 日期 (格式 YYYY-MM-DD)
    - items: 關鍵品項摘要 (String, 例如 '午餐', '文具', '車資')
    - currency: 幣別代碼 (String, 例如 'EUR', 'TWD', 'JPY')
    
    注意：
    1. 處理歐洲/德文收據時，請將逗點(,)正確解析為小數點。
    2. 若收據沒有年份，請預設為 {target_year}。
    3. 只回傳 JSON 字串，不要包含 markdown 標籤或解釋。
    """
    
    img = Image.open(io.BytesIO(image_bytes))
    response = model.generate_content([prompt, img])
    
    try:
        # 清理可能存在的 Markdown 標籤並解析
        raw_text = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(raw_text)
    except Exception as e:
        return {"shop": "解析錯誤", "amount": 0.0, "date": f"{target_year}-01-01", "items": "N/A", "currency": "TWD"}

# ==========================================
# II. UI 與 任務執行 (MVC 模型)
# ==========================================

st.set_page_config(page_title="Python 指揮官: 智慧報帳 v4.5", layout="wide")
st.title("📑 智慧收據偵察系統 (2026 加固版)")

# 初始化狀態
if 'data' not in st.session_state: st.session_state['data'] = []

with st.sidebar:
    st.header("⚙️ 偵察參數")
    target_year = st.number_input("時空鎖定年份", value=2026)
    fee_rate = st.number_input("跨國手續費率", value=0.015, step=0.005)
    target_currency = st.selectbox("換算目標幣別", ["TWD", "USD", "HKD"])

files = st.file_uploader("上傳收據 (德/英/日/中皆可)", accept_multiple_files=True, type=['jpg', 'jpeg', 'png', 'pdf'])

if st.button("🚀 啟動 VLM 深度辨識", type="primary"):
    if files:
        with st.spinner("Gemini 正在理解影像語義..."):
            for f in files:
                content = f.read()
                # 調用 Gemini 引擎
                res = get_vlm_analysis(content, target_year)
                
                # 計算 UID 防止重複
                uid = hashlib.md5(content + "Commander".encode()).hexdigest()[:12]
                
                # 獲取匯率 (yf 實時更新)
                rate = 1.0
                if res['currency'] != target_currency:
                    try:
                        ticker = f"{res['currency']}{target_currency}=X"
                        rate = yf.Ticker(ticker).fast_info['lastPrice']
                    except: rate = 1.0

                # 計算最終金額 (LaTeX 運算標準)
                # $Total = round(Amount \times Rate \times (1 + Fee), 0)$
                final_amt = round(res['amount'] * rate * (1 + fee_rate), 0)

                st.session_state['data'].append({
                    "商店名稱": res['shop'],
                    "消費日期": res['date'],
                    "幣別": res['currency'],
                    "原始金額": res['amount'],
                    "匯率": round(rate, 4),
                    "換算台幣": final_amt,
                    "品項摘要": res['items'],
                    "UID": uid
                })
        st.success("✅ 辨識完成！")

if st.session_state['data']:
    df = pd.DataFrame(st.session_state['data'])
    st.data_editor(df, use_container_width=True)