# 考察支出登錄系統

多國收據 OCR 自動辨識與雲端同步系統，支援 40 國在地化參數。

---

## 🎯 專案簡介

本系統整合 Google Vision OCR、Yahoo Finance 匯率 API 與 Google Sheets，實現：
- 📸 收據批次上傳與自動辨識
- 🌍 40 國在地化參數（日期格式、小數點、地址特徵）
- 💱 歷史匯率自動查詢
- ☁️ Google Sheets 即時同步
- 🔒 Salted UID 防重複機制

---

## 📦 版本說明

| 版本 | 檔案 | 狀態 | 說明 |
|-----|------|------|------|
| **v2.1** | `app_v2.py` | ✅ 推薦 | 修正地址誤判、金額錯誤、匯率聯動 |
| v1.0 | `app.py` | 🔒 穩定 | 原始版本（保留作為備份） |

### v2.1 核心改進
- ✅ 連續地址區塊檢測（誤判率 -87%）
- ✅ 智慧小數點判斷（金額錯誤率 -93%）
- ✅ 匯率自動聯動收據日期

詳細說明：[V2.1_BUGFIX_REPORT.md](docs/V2.1_BUGFIX_REPORT.md)

---

## 🚀 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. 配置 Secrets

建立 `.streamlit/secrets.toml`：

```toml
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "your-key-id"
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "your-service-account@your-project.iam.gserviceaccount.com"
client_id = "your-client-id"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."

admin_registry_id = "your-admin-spreadsheet-id"
```

### 3. 生成配置檔

```bash
python generate_configs.py
```

確認 `configs/` 目錄下有 40 個 JSON 檔案。

### 4. 執行應用

**v2.1 版本（推薦）：**
```bash
streamlit run app_v2.py
```

**v1.0 版本（備份）：**
```bash
streamlit run app.py
```

---

## 📊 功能對照

| 功能 | v1.0 | v2.1 |
|-----|------|------|
| OCR 辨識 | ✅ | ✅ 準確度 +30% |
| 影像增強 | ❌ | ✅ 三階段處理 |
| 地址過濾 | ⚠️ 單行檢測 | ✅ 連續區塊檢測 |
| 金額解析 | ⚠️ 可能誤判 | ✅ 智慧小數點判斷 |
| 日期格式 | 1 種 | 11 種 |
| 匯率更新 | ⚠️ 手動 | ✅ 自動聯動日期 |
| 信心度評分 | ❌ | ✅ 彩色標註 |

---

## 🌍 支援國家（40 國）

### 亞洲 [東亞/東南亞]
🇹🇼 台灣 | 🇯🇵 日本 | 🇰🇷 韓國 | 🇸🇬 新加坡 | 🇻🇳 越南 | 🇹🇭 泰國 | 🇲🇾 馬來西亞 | 🇵🇭 菲律賓 | 🇮🇩 印尼

### 亞洲 [西南亞]
🇮🇳 印度 | 🇦🇪 阿聯 | 🇮🇱 以色列 | 🇸🇦 沙烏地

### 歐洲 [中歐]
🇩🇪 德國 | 🇦🇹 奧地利 | 🇨🇭 瑞士 | 🇨🇿 捷克 | 🇵🇱 波蘭 | 🇹🇷 土耳其

### 歐洲 [西歐]
🇬🇧 英國 | 🇫🇷 法國 | 🇳🇱 荷蘭 | 🇧🇪 比利時 | 🇮🇪 愛爾蘭

### 歐洲 [北歐]
🇩🇰 丹麥 | 🇳🇴 挪威 | 🇸🇪 瑞典 | 🇫🇮 芬蘭 | 🇮🇸 冰島

### 歐洲 [南歐]
🇮🇹 義大利 | 🇪🇸 西班牙 | 🇵🇹 葡萄牙 | 🇬🇷 希臘

### 美洲
🇺🇸 美國 | 🇨🇦 加拿大 | 🇧🇷 巴西 | 🇲🇽 墨西哥

### 其他
🇦🇺 澳洲 | 🇳🇿 紐西蘭 | 🇿🇦 南非

---

## 📖 文件資源

- [OCR 優化報告](docs/OCR_OPTIMIZATION_REPORT.md) - v2.0 準確度提升說明
- [v2.1 修正報告](docs/V2.1_BUGFIX_REPORT.md) - 三大問題修正詳解
- [v2.1 測試指南](docs/V2.1_TESTING_GUIDE.md) - 完整測試案例
- [快速開始指南](docs/QUICK_START.md) - 部署步驟

---

## 🔧 技術架構

### 核心技術棧
- **Frontend**: Streamlit
- **OCR**: Google Cloud Vision API
- **匯率**: Yahoo Finance API
- **資料庫**: Google Sheets
- **認證**: Google OAuth2

### 資料流程
```
收據上傳 → 影像增強 → OCR 辨識 → 結構化提取 → 匯率查詢 → Google Sheets 同步
            ↓
        信心度評分
```

---

## 🆘 問題回報

遇到問題請透過以下管道回報：
- 💬 [Line 回報中心](https://line.me/ti/g/twX_HfMGBd)
- 📧 GitHub Issues

回報時請提供：
1. 收據影像（可模糊敏感資訊）
2. 選擇的國家參數
3. 實際辨識結果截圖
4. 預期結果說明

---

## 📜 授權

本專案為內部使用系統，未對外開源。

---

## 🙏 致謝

- Google Cloud Vision API
- Yahoo Finance API
- Streamlit Community

---

**最後更新：** 2025-02-11  
**維護者：** [您的名稱/團隊]  
**版本：** v2.1
