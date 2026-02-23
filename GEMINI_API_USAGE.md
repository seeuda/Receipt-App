# Gemini API 使用時機與流程說明

## 🎯 Gemini API 的兩個使用時機

目前系統中，Gemini API 只在以下**兩個時機**被呼叫：

### 時機 1: 測試 API 連線（可選）⚠️
**觸發條件：** 使用者點擊 Sidebar 的「⚡ 測試 API 連線 (Ping)」按鈕

**呼叫函數：** `test_api_connection(api_key)`

**API 呼叫：**
```python
genai.configure(api_key=api_key)
models = list(genai.list_models())  # 只列出模型，不生成內容
```

**Token 消耗：** 
- ✅ **0 tokens**（只列出模型，不呼叫 generation API）

**用途：**
- 驗證 API Key 是否有效
- 檢查可用的模型數量

**是否必須：**
- ❌ **非必須**
- 使用者可以跳過測試，直接上傳收據

---

### 時機 2: AI 自動辨識收據（核心功能）⭐
**觸發條件：** 使用者點擊「⚡ 啟動 AI 自動偵察」按鈕

**呼叫函數：** `run_vlm_scan(api_key, image_bytes, year, country_info)`

**API 呼叫：**
```python
genai.configure(api_key=api_key)
model = genai.GenerativeModel(
    model_name='models/gemini-2.5-flash',
    system_instruction=system_instruction
)
response = model.generate_content([prompt, img])  # 這裡才真正消耗 tokens
```

**Token 消耗：**
- **Input Tokens:**
  - System Instruction: ~50 tokens（一次性）
  - Prompt: ~40 tokens/張
  - Visual Tokens: ~500 tokens/張（1600px 壓縮後）
- **Output Tokens:** ~50 tokens/張

**每張收據總計：** ~590 tokens

**用途：**
- 辨識商店名稱
- 辨識消費日期
- 辨識金額
- 辨識幣別
- 辨識品項摘要

**是否必須：**
- ✅ **必須**
- 這是系統的核心功能

---

## 📊 完整使用流程圖

```
使用者開啟 App
    ↓
[Sidebar] 輸入 API Key
    ↓
【可選】點擊「測試 API 連線」
    ↓
    ├─→ 呼叫 test_api_connection()
    │   └─→ genai.list_models()  ← API 呼叫 #1（0 tokens）
    ↓
選擇國家 / 年度
    ↓
上傳收據照片（可多張）
    ↓
點擊「啟動 AI 自動偵察」
    ↓
    ├─→ 迴圈處理每張收據
    │   └─→ 呼叫 run_vlm_scan()
    │       └─→ model.generate_content()  ← API 呼叫 #2（~590 tokens/張）
    ↓
顯示辨識結果（暫存區）
    ↓
使用者確認 / 修正
    ↓
同步至 Google Sheets  ← 不呼叫 Gemini API
    ↓
完成
```

---

## 🔍 詳細分析：時機 2（核心功能）

### 處理流程

```python
# 使用者上傳 3 張收據照片
files = [receipt_1.jpg, receipt_2.jpg, receipt_3.jpg]

# 點擊「啟動 AI 自動偵察」後：
for idx, f in enumerate(files):
    img_bytes = f.read()
    
    # 每張間隔 1 秒（避免 Rate Limit）
    if idx > 0:
        time.sleep(1)
    
    # 呼叫 Gemini API 辨識
    res = run_vlm_scan(active_key, img_bytes, target_year, target_country)
    # ↑ 這裡會呼叫 model.generate_content()
    
    # 處理辨識結果
    if res:
        uid = hashlib.md5(img_bytes).hexdigest()[:12]
        batch.append({
            "UID": uid,
            "商店名稱": res['shop'],      # 來自 AI
            "日期": res['date'],          # 來自 AI
            "外幣金額": res['amount'],    # 來自 AI
            "幣別": res['currency'],      # 來自 AI
            "品項摘要": res['items'],     # 來自 AI
            # ... 其他欄位
        })
```

---

### Token 消耗細節

**單張收據（1600px 壓縮後）：**

| 類型 | 數量 | 說明 |
|-----|------|------|
| System Instruction | 50 tokens | 只計算一次 |
| Prompt (Text) | 40 tokens | 每張 |
| Visual Tokens | ~500 tokens | 每張 |
| Output | ~50 tokens | 每張 |
| **小計** | **~590 tokens** | 每張 |

**批次處理範例：**

```
上傳 10 張收據：
- System Instruction: 50 tokens（只算一次）
- Input（每張）: (40 + 500) × 10 = 5,400 tokens
- Output（每張）: 50 × 10 = 500 tokens
- 總計: 5,950 tokens

成本（Gemini 2.5 Flash）:
- Input: 5,450 × $0.075 / 1M = $0.00041
- Output: 500 × $0.30 / 1M = $0.00015
- 總計: $0.00056（10 張收據）
```

---

## ⚙️ 不使用 Gemini API 的操作

以下操作**完全不需要** Gemini API：

### 1. 匯率查詢
```python
# 使用 yfinance 查詢匯率
exchange_rate = get_rate_by_date(currency, date)
# ↑ 呼叫 Yahoo Finance API（免費）
```

### 2. 手動修正資料
```python
# 使用者在表單中修改
new_shop = st.text_input("商店名稱", value=record['商店名稱'])
new_date = st.date_input("日期", value=...)
new_amount = st.number_input("外幣金額", value=...)
# ↑ 純前端操作，不呼叫任何 API
```

### 3. 台幣金額計算
```python
# 本地計算
twd_amount = round(foreign_amount * exchange_rate, 0)
# ↑ 不需要 API
```

### 4. 同步至 Google Sheets
```python
# 使用 gspread 直接寫入
wks.append_rows(rows, value_input_option='USER_ENTERED')
# ↑ 呼叫 Google Sheets API（不是 Gemini）
```

### 5. UID 生成
```python
# 本地計算 MD5
uid = hashlib.md5(img_bytes).hexdigest()[:12]
# ↑ 不需要 API
```

### 6. 去重檢查
```python
# 本地比對
existing_uids = {record['UID'] for record in st.session_state['data']}
new_records = [r for r in batch if r['UID'] not in existing_uids]
# ↑ 不需要 API
```

---

## 💰 成本控制建議

### 目前的優化措施

1. ✅ **圖片壓縮**
   - 限制最大邊長 1600px
   - 節省 ~80% Visual Tokens

2. ✅ **System Instruction**
   - 固定內容只算一次
   - 節省 ~82% Prompt Tokens

3. ✅ **API 測試優化**
   - 使用 list_models（0 tokens）
   - 不使用 generate_content

4. ✅ **批次延遲**
   - 每張間隔 1 秒
   - 避免 Rate Limit

---

### 進一步節省成本

#### 選項 1: 避免重複辨識

**問題：**
使用者誤上傳同一張照片兩次

**目前處理：**
```
第一次：辨識 → 消耗 590 tokens
第二次：去重 → 不辨識 → 0 tokens ✅
```

**優化效果：** 已優化 ✓

---

#### 選項 2: 快取辨識結果

**概念：**
```python
# 在辨識前檢查是否已辨識過
if uid in cache:
    # 使用快取結果
    result = cache[uid]
else:
    # 呼叫 API 辨識
    result = run_vlm_scan(...)
    cache[uid] = result
```

**優點：**
- 相同照片只辨識一次
- 即使不同使用者上傳

**缺點：**
- 需要建立快取機制
- 快取可能過期

**建議：** 可選功能，看需求

---

#### 選項 3: 使用者確認再辨識

**目前流程：**
```
上傳 → 立即辨識 → 顯示結果
```

**替代流程：**
```
上傳 → 預覽圖片 → 使用者確認 → 辨識
```

**優點：**
- 避免誤傳照片浪費 tokens
- 使用者可以先移除不需要的

**缺點：**
- 多一個步驟
- 使用體驗較差

**建議：** 不推薦（體驗優先）

---

## 📊 實際成本範例

### 使用情境：每月 1000 張收據

**Token 消耗：**
```
System Instructions: 50 tokens（全月只算一次）
Input Tokens: (40 + 500) × 1000 = 540,000 tokens
Output Tokens: 50 × 1000 = 50,000 tokens
總計: ~590,000 tokens
```

**成本（Gemini 2.5 Flash）：**
```
Input: 540,050 × $0.075 / 1,000,000 = $0.041
Output: 50,000 × $0.30 / 1,000,000 = $0.015
總計: $0.056/月

年度成本: $0.67
```

**相當於：**
- 每張收據 $0.000056（0.0056 分）
- 一杯咖啡的價格可以辨識 ~53,000 張收據

---

## ⚠️ Rate Limit 注意事項

### Gemini 2.5 Flash 限制

**免費版：**
- 15 RPM（每分鐘請求數）
- 1 million TPM（每分鐘 tokens）
- 1,500 RPD（每天請求數）

**付費版：**
- 更高的配額
- 詳見 Google AI Studio

---

### 目前的保護機制

```python
# 每張間隔 1 秒
if idx > 0:
    time.sleep(1)

# 效果：
# 60 張/分鐘 = 1 張/秒 = 1 RPM（遠低於 15 RPM 限制）✓
```

---

## 🎉 總結

### Gemini API 使用時機

| 操作 | 是否使用 | Token 消耗 |
|-----|---------|-----------|
| 測試連線 | 可選 | 0 tokens |
| **AI 辨識收據** | **必須** | **~590 tokens/張** |
| 匯率查詢 | ❌ | 0（用 yfinance） |
| 手動修正 | ❌ | 0（前端操作） |
| 計算台幣 | ❌ | 0（本地計算） |
| 同步 Sheets | ❌ | 0（用 gspread） |
| 去重檢查 | ❌ | 0（本地比對） |

---

### 成本估算

**每月 1000 張收據：**
- Token 消耗：~590,000
- 金額：$0.056
- 每張：$0.000056

**非常划算！** ✨

---

**文件版本：** v1.0  
**更新日期：** 2025-02-13  
**適用版本：** v4.9.4+
