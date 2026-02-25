# 四個問題完整修正報告

## 🚨 問題四：成本問題（最嚴重）❌

### 帳單數據分析

```
2026/2/24 測試：
- 5 張收據
- 總費用：$6.40
- 每張成本：$1.28/張
- SKU：911A-8880-A243（Thinking Token）

對比預期：
- 預期（優化後）：$0.0001/張
- 實際：$1.28/張
- 差異：12,800 倍！❌

月度推算（1,000 張）：
- 實際成本：$1,280/月
- 完全無法接受 ❌❌❌
```

---

### 根本原因

**Gemini 2.5 Flash 的 Thinking 機制無法避免**

即使簡化 Prompt，Gemini 2.5 Flash 仍會：
1. 看到「JSON」→ 觸發結構化推理
2. 看到「date」→ 觸發日期格式推理
3. 看到圖片中的複雜文字 → 觸發 OCR 後處理推理
4. 自主決定進行深度分析

**結論：簡化 Prompt 無法解決問題** ❌

---

### ✅ 已實施：極簡版 Prompt

雖然無法完全解決，但已盡可能優化：

```python
# 完全移除 System Instruction
model = genai.GenerativeModel(
    model_name='models/gemini-2.5-flash'
    # 不設定 system_instruction
)

# 超極簡 Prompt
prompt = f"""{country_info['currency']} {year}
JSON: shop, amount, YYYY-MM-DD, currency, items"""
```

**預期效果：**
- 可能降低 30-50% 成本
- 但仍可能 $0.60-0.90/張
- 仍然太貴 ⚠️

---

### 🎯 真正的解決方案

#### 方案 A：Document AI（強烈推薦）⭐⭐⭐⭐⭐

**Google Cloud Document AI - Receipt Parser**

**定價：**
```
前 1,000 張/月：免費
1,001-1,000,000 張：$0.01/張
```

**與 Gemini 對比：**
```
              Gemini 2.5 Flash   Document AI
----------------------------------------------
測試成本      $1.28/張          免費（1K 內）
1,000 張/月   $1,280            免費
10,000 張/月  $12,800           $90
節省          -                 99.3%
```

**優點：**
- ✅ 成本極低且可預測
- ✅ 專門針對收據設計
- ✅ 精度極高（官方支援）
- ✅ 輸出結構化 JSON
- ✅ 支援多語言
- ✅ 包含欺詐檢測
- ✅ 穩定性最好

**缺點：**
- ⚠️ 需要設定 GCP 專案
- ⚠️ 需要啟用 Document AI API
- ⚠️ 整合較複雜（但一次性）

**實作範例：**
```python
from google.cloud import documentai_v1 as documentai

def process_receipt_with_documentai(image_bytes, processor_id):
    """使用 Document AI 處理收據"""
    client = documentai.DocumentProcessorServiceClient()
    
    # Receipt Parser Processor
    name = f"projects/YOUR_PROJECT/locations/us/processors/{processor_id}"
    
    raw_document = documentai.RawDocument(
        content=image_bytes,
        mime_type='image/jpeg'
    )
    
    request = documentai.ProcessRequest(
        name=name,
        raw_document=raw_document
    )
    
    result = client.process_document(request=request)
    
    # 提取結構化資料
    entities = {e.type_: e for e in result.document.entities}
    
    return {
        'shop': entities.get('supplier_name', {'mention_text': '未知'}).mention_text,
        'amount': float(entities.get('total_amount', {'normalized_value': {'money_value': {'units': 0}}}).normalized_value.money_value.units),
        'date': str(entities.get('invoice_date', {'normalized_value': {'date_value': ''}}).normalized_value.date_value),
        'currency': entities.get('currency', {'normalized_value': {'text': 'EUR'}}).normalized_value.text,
        'items': '; '.join([e.mention_text for e in result.document.entities if e.type_ == 'line_item'])
    }
```

---

#### 方案 B：Gemini 2.0 Flash Thinking（實驗性）⭐⭐⭐

**Google 剛推出的新模型**

可能的模型名稱：
- `models/gemini-2.0-flash-thinking`
- `models/gemini-2.0-flash-exp`

**特點：**
- 可能有「低思考」模式
- 成本可能介於 2.5 Flash 和無 Thinking 之間

**需要測試確認**

---

#### 方案 C：其他 LLM Provider ⭐⭐

**OpenAI GPT-4 Vision**
```
成本：$0.01/張（估計）
精度：高
問題：需要更換 API
```

**Anthropic Claude 3**
```
成本：$0.015/張（估計）
精度：高
問題：需要更換 API
```

---

### 📋 推薦行動計畫

#### 立即（今天）：

1. **✅ 部署極簡版 Prompt**（已完成）
   - 降低部分成本
   - 買時間做準備

2. **⚠️ 準備切換 Document AI**
   - 申請 GCP 專案（如果沒有）
   - 啟用 Document AI API
   - 建立 Receipt Parser Processor

3. **⚠️ 限制測試量**
   - 每次只測試 1-2 張
   - 直到切換完成

---

#### 短期（3 天內）：

4. **實作 Document AI 整合**
   - 參考上面的範例程式碼
   - 測試精度
   - 比較成本

5. **部署 Document AI 版本**
   - 完全替換 Gemini
   - 或提供選項讓使用者選擇

---

#### 驗證（7 天內）：

6. **監控成本**
   - 確認 Document AI 費用
   - 前 1,000 張應該免費
   - 之後 $0.01/張

7. **驗證精度**
   - 對比 Gemini 和 Document AI
   - Document AI 應該更準確

---

## ✅ 問題一：現金模式匯率連動（已修正）

### 問題描述

```
使用者操作：
1. 選擇「現金」
2. 輸入匯率：0.2185
3. 上傳收據並辨識

預期：辨識結果使用 0.2185
實際：辨識結果使用自動查詢的匯率 ❌
```

---

### 根本原因

**程式碼邏輯正確，但精度處理有問題**

```python
# 辨識時（Line 340-348）
if payment_method == "現金" and st.session_state.get('default_cash_rate', 0) > 0:
    exchange_rate = st.session_state['default_cash_rate']  # ✓ 有使用
else:
    exchange_rate = get_rate_by_date(...)

# 保存時（Line 356）
"匯率": round(exchange_rate, 3),  # ❌ 強制 3 位小數
```

**問題：**
- 現金模式輸入 4 位小數（0.2185）
- 但保存時強制 3 位（0.219）
- 看起來像沒有使用預設匯率

---

### ✅ 修正方案（已實施）

```python
# 根據支付方式決定精度
"匯率": round(exchange_rate, 4) if payment_method == "現金" else round(exchange_rate, 3),
```

**效果：**
- 現金模式：保留 4 位小數 ✓
- 信用卡模式：保留 3 位小數 ✓

---

## ✅ 問題二：清空功能未清除上傳檔案（已修正）

### 問題描述

```
使用者操作：
1. 上傳 5 張照片
2. 辨識完成
3. 點擊「清空暫存區」

預期：檔案上傳器清空
實際：檔案仍顯示在上傳器中 ❌
```

---

### 根本原因

**Streamlit file_uploader 的特性**

```python
# 當前做法（錯誤）
files = st.file_uploader(...)

st.session_state['data'] = []  # 清空資料 ✓
st.session_state['uploaded_images'] = {}  # 清空圖片 ✓

# 但 file_uploader 內部狀態不會被清空 ❌
```

**Streamlit 的 file_uploader 特性：**
- Widget 有自己的內部狀態
- 清空 session_state 不會影響 widget
- 需要使用 `key` 參數來重置

---

### ✅ 修正方案（已實施）

**方法：動態 key**

```python
# 初始化（Line 187 附近）
if 'uploader_key' not in st.session_state:
    st.session_state['uploader_key'] = 0

# 檔案上傳器（Line 305 附近）
files = st.file_uploader(
    "批次上傳單據照片",
    accept_multiple_files=True,
    type=['jpg','png','jpeg'],
    key=f"file_uploader_{st.session_state['uploader_key']}"  # 動態 key
)

# 清空時（Line 606 / 229）
st.session_state['data'] = []
st.session_state['uploaded_images'] = {}
st.session_state['uploader_key'] += 1  # 改變 key，強制重新建立 widget
st.rerun()
```

**原理：**
- 每次清空時，`uploader_key` +1
- Widget 的 key 改變
- Streamlit 會銷毀舊 widget，建立新的
- 新 widget 是空的 ✓

---

## ⚠️ 問題三：試算表同步覆蓋問題

### 問題描述

```
使用者操作：
1. 清空試算表
2. 上傳 5 張收據並同步（成功，列 2-6）
3. 再上傳 3 張新收據並同步

預期：新增到列 7-9
實際：覆蓋了列 2-4 ❌
```

---

### 可能原因分析

#### 原因 A：findall 結果順序問題

```python
# 當前邏輯（Line 525-545）
uid_cells = wks.findall(re.compile(r".+"), in_column=13)

for cell in uid_cells:
    uid_to_row[uid] = cell.row
    
# 問題：如果 Sheet 有格式問題
# findall 可能返回不完整或錯誤的結果
```

---

#### 原因 B：append_rows 的行為

```python
# 當前邏輯（Line 595）
wks.append_rows(rows_to_append, value_input_option='USER_ENTERED')

# append_rows 的行為：
# 1. 找到第一個完全空白的列
# 2. 從該列開始寫入

# 問題：如果 Sheet 有隱藏列或格式列
# append_rows 可能會寫入錯誤的位置
```

---

#### 原因 C：清空後的「幽靈」資料

```python
# 清空 Sheet 的方式可能有問題

# 錯誤方式 1：只刪除內容
wks.batch_clear(['A2:M1000'])  # 資料清空，但格式保留

# 錯誤方式 2：刪除列
wks.delete_rows(2, 1000)  # 可能留下空白列

# 正確方式：
# 1. 完全重建 Sheet
# 2. 或確保清空後沒有任何殘留
```

---

### 🔧 建議修正方案

#### 方案 A：改用確定性的列號（推薦）⭐⭐⭐⭐⭐

```python
# 不依賴 append_rows，改為確定性寫入

# 1. 獲取當前最後一列
all_values = wks.get_all_values()
last_row = len(all_values)  # 實際有資料的最後一列

# 2. 計算新資料的起始列
start_row = last_row + 1

# 3. 批次寫入到確定的位置
if rows_to_append:
    # 準備範圍
    end_row = start_row + len(rows_to_append) - 1
    range_str = f"A{start_row}:M{end_row}"
    
    # 批次寫入
    wks.update(range_str, rows_to_append, value_input_option='USER_ENTERED')
```

**優點：**
- ✅ 完全確定性（不依賴 append_rows）
- ✅ 不受 Sheet 格式影響
- ✅ 不會覆蓋現有資料

---

#### 方案 B：改進清空邏輯 ⭐⭐⭐

```python
def safe_clear_sheet(wks):
    """安全地清空 Sheet（保留標題列）"""
    
    # 1. 獲取當前行數
    all_values = wks.get_all_values()
    row_count = len(all_values)
    
    if row_count <= 1:
        return  # 只有標題列，不需清空
    
    # 2. 刪除所有資料列（從第 2 列開始）
    wks.delete_rows(2, row_count - 1)
    
    # 3. 驗證清空成功
    remaining = wks.get_all_values()
    assert len(remaining) == 1, "清空失敗"
```

---

#### 方案 C：使用交易式同步 ⭐⭐

```python
def transactional_sync(wks, updates, rows_to_append):
    """使用交易式邏輯確保資料完整性"""
    
    try:
        # 1. 讀取當前狀態（建立快照）
        snapshot = {
            'uid_to_row': build_uid_mapping(wks),
            'last_row': len(wks.get_all_values())
        }
        
        # 2. 執行更新
        if updates:
            wks.batch_update(updates)
        
        if rows_to_append:
            start_row = snapshot['last_row'] + 1
            range_str = f"A{start_row}:M{start_row + len(rows_to_append) - 1}"
            wks.update(range_str, rows_to_append)
        
        # 3. 驗證結果
        new_row_count = len(wks.get_all_values())
        expected = snapshot['last_row'] + len(rows_to_append)
        
        if new_row_count != expected:
            raise ValueError(f"同步失敗：預期 {expected} 列，實際 {new_row_count} 列")
        
        return True
        
    except Exception as e:
        # 回滾邏輯（如果可能）
        st.error(f"同步失敗：{e}")
        return False
```

---

### 📋 建議實施順序

1. **✅ 短期**（立即實施）：
   - 方案 A：改用確定性列號
   - 最簡單且最可靠

2. **⚠️ 中期**（1 週內）：
   - 方案 B：改進清空邏輯
   - 提供給使用者使用

3. **💡 長期**（可選）：
   - 方案 C：交易式同步
   - 如果需要更強的保證

---

## 🎯 總結與優先順序

### P0 - 立即執行（今天）

1. **✅ 部署極簡版 Prompt**（已完成）
2. **⚠️ 限制測試量**（等待 Document AI）
3. **⚠️ 申請 GCP 專案**（如果沒有）

---

### P1 - 短期（3 天內）

4. **實作 Document AI**（解決成本問題）
5. **測試精度**
6. **部署生產**

---

### P2 - 中期（1 週內）

7. **改進同步邏輯**（使用確定性列號）
8. **添加交易式保證**（可選）

---

### 成本對比（最終）

```
              當前 Gemini      Document AI      節省
------------------------------------------------------
5 張測試      $6.40           免費             100%
1,000 張/月   $1,280          免費             100%
10,000 張/月  $12,800         $90              99.3%
```

**結論：Document AI 是唯一可行的長期方案** ⭐⭐⭐⭐⭐

---

**報告版本：** Final  
**日期：** 2026-02-25  
**優先級：** 🔴 成本問題極度緊急
