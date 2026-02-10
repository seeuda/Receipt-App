# 考察支出登錄系統 (Receipt-App)

本專案是一款基於 **Python** 與 **Streamlit** 開發的 AI 自動化工具，專為跨國考察設計。透過 Google Cloud Vision AI 辨識多國收據資訊，自動換算匯率並同步至 Google Sheets，解決跨國報帳時格式不一、匯率計算繁瑣與人工輸入錯誤的痛點。

## 🌟 核心特色

* **多國在地化支援**：預設支援 40 國參數，自動處理各國小數點符號（如德國 `,`）、日期順序（DMY/YMD）與地址雜訊過濾。
* **配置驅動架構 (Decoupled)**：系統邏輯與國家參數完全解耦，透過 `configs/*.json` 即可擴充新國家的辨識規則，無需修改主程式。
* **精準語義提取**：具備標靶過濾機制，自動剔除收據中的商店地址、通訊資訊與無意義標頭。
* **動態專案管理**：支援即時註冊新專案試算表，自動辨識標題列索引，並相容 Google Form 的時間戳記格式。
* **可靠的判重機制**：透過商店、日期與金額生成唯一識別碼 (UID)，防止單據重複同步。

## 🛠️ 技術棧

* **Frontend**: Streamlit
* **OCR Engine**: Google Cloud Vision AI
* **Data Processing**: Pandas, Regex
* **Finance API**: yfinance (Real-time & Historical FX rates)
* **Storage**: Google Sheets API (gspread)

## 📂 專案結構

```text
.
├── app.py                # 主程式：UI 介面與核心執行邏輯
├── generate_configs.py   # 配置生成器：產出 40 國在地化 JSON 參數
├── configs/              # 自動生成：儲存各國參數檔 (JSON)
└── .streamlit/
    └── secrets.toml      # 敏感資訊：API Key 與試算表 ID (不進入 Git)

```

## 🚀 快速開始

### 1. 環境準備

* Python 3.10+
* 建立 Google Cloud 專案並啟用 Vision API 與 Sheets API。
* 取得 Service Account Key (JSON 格式)。

### 2. 安裝依賴

```bash
pip install streamlit pandas gspread google-cloud-vision yfinance Pillow

```

### 3. 配置秘密資訊

在 `.streamlit/secrets.toml` 中填入以下資訊：

```toml
admin_registry_id = "您的管理總表試算表ID"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "..."
client_email = "..."
# ...其餘 Service Account 欄位

```

### 4. 初始化配置

執行腳本產出各國參數檔：

```bash
python generate_configs.py

```

### 5. 啟動系統

```bash
streamlit run app.py

```

## 🔒 安全性規範

本專案嚴格遵守安全性標準：

* **禁止硬編碼**：所有 API Key 與私有路徑必須透過 `st.secrets` 或環境變數讀取。
* **版本控制排除**：請務必將 `secrets.toml` 加入 `.gitignore`，嚴禁上傳至公開倉庫。

## ⚖️ 授權協議 (License)

本專案採用 **MIT License** 授權。您可以自由地使用、修改及分發本程式碼，唯須保留原作者之版權聲明。

⚖️ License
This project is licensed under the MIT License - see the LICENSE file for details.

Copyright (c) 2026 JiunShiuan Wang
