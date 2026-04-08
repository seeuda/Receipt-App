# Receipt-App｜考察支出登錄系統

Receipt-App 是一套以 **Streamlit + Google 生態系 API** 打造的收據登錄工具，協助使用者將海外收據快速完成：
**OCR / VLM 辨識 → 人工校對 → 匯率換算 → 寫入 Google Sheets**。

目前專案主線版本為：
- `app_enhanced.py`：**唯一維運版本**（Gemini VLM + Token 優化 + 同步診斷）

`app.py` 已進入封存狀態（Deprecated），不再提供功能更新或相容性維護。

---

## 主要功能

- 批次上傳收據圖片（JPG / JPEG / PNG）
- 自動抽取店名、日期、金額、品項等欄位
- 支援多國幣別與匯率轉換（`yfinance`）
- 以 Google 試算表作為專案資料庫與同步目標
- 可於 UI 中人工修正辨識結果後再同步
- 具備 UID 去重與覆寫更新機制，降低重複登錄風險

---

## 專案檔案說明

```text
.
├── app.py                        # 舊版封存（Deprecated，不維運）
├── app_enhanced.py               # 主線版本：Gemini VLM + API 預檢 + Token 優化
├── countries_master.json         # 國家/區域/幣別等主資料
├── requirements.txt              # Python 套件依賴
├── .streamlit/secrets.toml.example # Secrets 範本（可提交）
├── .gitignore                    # 忽略本機 secrets 與暫存檔
├── TESTING.md                    # 測試建議與案例
├── QUICKSTART.md                 # 精簡啟動說明
└── 版本更新說明文件（V4.8.0~V4.10.1）
```

## Debug Logs（新增）

系統會自動建立 `logs/` 目錄，協助追查辨識與同步問題：

- `logs/image/`：原始上傳圖片（以 UID 命名）
- `logs/events/YYYY-MM-DD.jsonl`：事件紀錄，包含
  - `ai_output`
  - `final_output`
  - `sheet_row`

可用來快速比對「模型輸出 → 最終寫入資料 → 寫入 Sheet payload」。

---

## 執行前準備

1. **Python 3.10+**
2. 建立 Google Cloud 專案並啟用相關 API（至少含 Sheets；主線 `app_enhanced.py` 需 Gemini API）
3. 準備 Service Account JSON 金鑰
4. （若使用 `app_enhanced.py`）準備 Gemini API Key

---

## 安裝

```bash
pip install -r requirements.txt
```

---

## 設定 Secrets（本機與部署皆適用）

請在專案根目錄建立 `.streamlit/secrets.toml`（本機）或在部署平台 Secrets UI 設定相同欄位。

- 請勿提交 `.streamlit/secrets.toml` 到版控。
- 可使用 `.streamlit/secrets.toml.example` 當範本。

最少建議包含：

```toml
admin_registry_id = "<管理總表 Google Sheet ID>" # app.py / app_enhanced.py 都會讀取
gemini_api_key = "<你的 Gemini API Key>" # 使用 app_enhanced.py 時需要

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
universe_domain = "googleapis.com"
```

> `app_enhanced.py` 會優先讀取 `admin_registry_id`；若未設定則回退為內建預設管理表並顯示提示。

---

## 啟動方式

### 主線版（Gemini VLM）

```bash
streamlit run app_enhanced.py
```

> `app.py` 已停止維護，僅作歷史參考用途。

---

## 使用流程（建議）

1. 選擇專案與登錄者
2. 選擇國家/區域與相關費率參數
3. 批次上傳收據圖片
4. 執行自動辨識
5. 在表格中人工校正欄位
6. 同步至 Google Sheets

---

## 版本文件

- `V4.8.0_FEATURES.md`
- `V4.9.0_OPTIMIZATION.md`
- `V4.9.1_UPDATE.md`
- `V4.9.2_TOKEN_OPTIMIZATION.md`
- `V4.9.3_BUGFIX.md`
- `V4.9.4_UID_FIX.md`
- `V4.10.0_PAYMENT_METHOD.md`
- `V4.10.1_UPDATE_SUMMARY.txt`
- `MODEL_UPDATE_GUIDE.md`
- `GEMINI_API_FIX.md`

若你要升級或排查 API / Token 相關問題，建議先讀 `V4.9.2_TOKEN_OPTIMIZATION.md` 與 `GEMINI_API_FIX.md`。

---

## 授權

本專案採用 MIT License，詳見 `LICENSE`。
