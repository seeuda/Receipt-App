# GitHub + Streamlit 部署檢查清單

## 📋 部署前準備

### ✅ 檔案清單確認

**必要檔案：**
- [ ] `app.py` - v1.0 原始版本（保留）
- [ ] `app_v2.py` - v2.1 優化版本（新增）
- [ ] `requirements.txt` - 已更新（含 numpy）
- [ ] `generate_configs.py` - 配置生成腳本
- [ ] `configs/` - 40 國參數檔（已生成）

**文件檔案：**
- [ ] `README.md` - 專案說明
- [ ] `docs/OCR_OPTIMIZATION_REPORT.md`
- [ ] `docs/V2.1_BUGFIX_REPORT.md`
- [ ] `docs/V2.1_TESTING_GUIDE.md`
- [ ] `docs/QUICK_START.md`

---

## 🔧 GitHub 上傳步驟

### Step 1: 檔案重命名

在本地專案目錄：

```bash
# 確保舊版本已命名為 app.py
# 將優化版本重命名為 app_v2.py
mv app_enhanced_v2.py app_v2.py
```

---

### Step 2: 建立 docs 目錄

```bash
# 建立文件目錄
mkdir -p docs

# 移動文件檔案
mv OCR_OPTIMIZATION_REPORT.md docs/
mv V2.1_BUGFIX_REPORT.md docs/
mv V2.1_TESTING_GUIDE.md docs/
mv QUICK_START.md docs/
mv TESTING_GUIDE.md docs/
```

---

### Step 3: Git 操作

```bash
# 1. 檢查狀態
git status

# 2. 新增檔案
git add app_v2.py
git add requirements.txt
git add docs/
git add README.md
git add configs/  # 如果還沒上傳

# 3. 確認變更
git status

# 4. 提交
git commit -m "feat: Add v2.1 - Fix address filtering, amount parsing, and exchange rate auto-update

- Add detect_address_blocks() for consecutive address detection
- Add normalize_price() for decimal point disambiguation  
- Add auto exchange rate update based on receipt date
- Improve OCR accuracy by 25-40%
- Reduce manual correction from 40% to 15-20%"

# 5. 推送
git push origin main
```

---

### Step 4: 確認上傳

前往 GitHub 確認：
- [ ] `app_v2.py` 已出現
- [ ] `README.md` 已更新
- [ ] `docs/` 目錄已建立
- [ ] `requirements.txt` 包含 numpy

---

## ☁️ Streamlit Cloud 部署

### 方案 A：建立新 App（推薦）

#### Step 1: 建立新 App

1. 登入 [Streamlit Cloud](https://streamlit.io/cloud)
2. 點擊「New app」
3. 填寫設定：

| 欄位 | 設定值 | 說明 |
|-----|--------|------|
| **Repository** | your-username/your-repo | 選擇您的 repo |
| **Branch** | main | 主分支 |
| **Main file path** | `app_v2.py` | ⭐ 關鍵設定 |
| **App URL** | `expense-tracker-v2` | 自訂網址 |

4. 點擊「Deploy!」

---

#### Step 2: 設定 Secrets

部署後：
1. 進入新 app 的 Settings → Secrets
2. 複製舊 app 的 secrets 內容
3. 貼上並儲存

**Secrets 範本：**
```toml
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "your-service-account@..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."

admin_registry_id = "your-spreadsheet-id"
```

---

#### Step 3: 等待部署完成

- 觀察部署日誌
- 確認無錯誤訊息
- 測試基本功能

---

### 方案 B：修改現有 App

#### Step 1: 修改設定

1. 進入現有 app 的 Settings
2. General → Main file path
3. 修改：`app.py` → `app_v2.py`
4. Save

#### Step 2: 重啟 App

1. Settings → Reboot app
2. 等待重啟完成

---

## 🧪 部署後測試

### 必測項目

- [ ] **基本功能**
  - [ ] 專案選擇正常
  - [ ] 人員名單正確讀取
  - [ ] 國家選單顯示正常

- [ ] **檔案上傳**
  - [ ] 影像預覽正常
  - [ ] 批次上傳功能正常

- [ ] **OCR 辨識**
  - [ ] 影像增強開關有效
  - [ ] 進度條顯示正常
  - [ ] 辨識結果正確

- [ ] **v2.1 核心修正**
  - [ ] 地址未出現在品項欄位
  - [ ] 金額未放大 100 倍
  - [ ] 匯率自動更新（非當前日期）

- [ ] **信心度評分**
  - [ ] 評分表正常顯示
  - [ ] 顏色編碼正確（綠/黃/紅）
  - [ ] 低信心度警告出現

- [ ] **雲端同步**
  - [ ] 新增資料成功
  - [ ] 覆蓋更新成功
  - [ ] Google Sheets 資料正確

---

## 🔄 版本切換策略

### 情境 A：v2.1 穩定運行

**建議：**
1. 保留兩個 app 運行 1-2 週
2. 觀察使用者回饋
3. 確認無重大問題後，逐步遷移所有使用者至 v2.1
4. v1.0 app 保留作為緊急備份

---

### 情境 B：v2.1 發現問題

**快速回滾：**

**方案 A：新 App 模式**
- 直接引導使用者回到舊 app URL
- 無需修改程式碼

**方案 B：修改設定模式**
1. Settings → Main file path
2. 改回 `app_v2.py` → `app.py`
3. Reboot app

---

## 📊 使用者遷移計畫

### 階段 1：內部測試（1 週）

- [ ] 系統管理員測試 v2.1
- [ ] 收集關鍵問題
- [ ] 修正明顯 bug

### 階段 2：小範圍試用（1 週）

- [ ] 邀請 5-10 位使用者試用 v2.1
- [ ] 收集回饋與問題
- [ ] 優化使用者體驗

### 階段 3：全面推廣（2 週）

- [ ] 公告 v2.1 上線
- [ ] 提供教學文件
- [ ] 引導使用者遷移

### 階段 4：穩定運行（持續）

- [ ] 監控錯誤率
- [ ] 持續優化
- [ ] v1.0 降級為備份

---

## 🎯 成功指標

### 技術指標

- [ ] OCR 準確率 > 85%
- [ ] 地址誤判率 < 5%
- [ ] 金額錯誤率 < 1%
- [ ] 匯率自動更新成功率 > 95%

### 使用者指標

- [ ] 手動修正比例 < 20%
- [ ] 使用者滿意度 > 80%
- [ ] 問題回報數 < 舊版 50%

---

## 📝 部署記錄範本

```markdown
# 部署記錄

**部署日期：** 2025-02-11  
**執行人員：** ___________  
**部署版本：** v2.1

## 部署步驟

- [x] GitHub 檔案上傳
- [x] Streamlit Cloud 部署
- [x] Secrets 設定
- [x] 基本功能測試
- [x] v2.1 核心功能驗證

## 測試結果

| 項目 | 狀態 | 備註 |
|-----|------|------|
| 基本功能 | ✅ | 正常 |
| 地址過濾 | ✅ | 正常 |
| 金額解析 | ✅ | 正常 |
| 匯率更新 | ✅ | 正常 |

## App URL

- v2.1: https://expense-tracker-v2.streamlit.app
- v1.0: https://expense-tracker.streamlit.app (備份)

## 問題記錄

[無 / 列出發現的問題]

## 後續行動

- [ ] 通知使用者新版本上線
- [ ] 收集使用者回饋
- [ ] 監控錯誤率
```

---

## 🆘 常見問題

### Q1: 部署失敗，顯示 "ModuleNotFoundError: No module named 'numpy'"

**解決：**
確認 `requirements.txt` 包含 `numpy` 並已推送至 GitHub。

---

### Q2: Secrets 設定後仍無法連接 Google Sheets

**解決：**
1. 檢查 Secrets 格式（TOML 格式）
2. 確認 Service Account 權限
3. 檢查 Spreadsheet ID 正確性

---

### Q3: 如何同時維護兩個版本？

**建議：**
- v1.0：僅修復嚴重 bug，不再添加新功能
- v2.1：持續優化與新功能開發

---

## ✅ 最終檢查清單

部署前最後確認：

- [ ] 所有檔案已推送至 GitHub
- [ ] `app_v2.py` 可在本地正常運行
- [ ] Secrets 已備份
- [ ] 測試案例已準備
- [ ] 使用者已通知（如需要）
- [ ] 回滾計畫已準備

---

**文件版本：** v1.0  
**更新日期：** 2025-02-11  
**建議部署時間：** 非高峰時段（避免影響使用者）
