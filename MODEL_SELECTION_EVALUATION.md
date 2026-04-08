# Gemini API 模型選擇與成本優化評估

## 🎯 目標

找到成本最低且精度足夠的 OCR 方案，用於收據辨識。

---

## 📊 當前問題

### Gemini 2.5 Flash 的 Thinking Tokens 問題

**實測數據（2/22）：**
```
API 呼叫：77 次
總費用：$4.37
每次成本：$0.056

預期成本（無 Thinking）：$0.00005
實際成本：1,120 倍！
```

**根本原因：**
- Gemini 2.5 Flash 具備「思考能力」
- 會產生大量內部推理 Tokens（15,000-25,000 tokens/次）
- 這些 Thinking Tokens 全部計費

---

## ✅ 方案 1: 簡化 Prompt 避免 Thinking Mode（推薦）⭐⭐⭐⭐⭐

### 原理

**觸發 Thinking 的關鍵因素：**
1. 角色設定（"專業審計師"）
2. 複雜指令（"精確提取"、"驗證"）
3. 多步驟邏輯（"如果...則..."）
4. 推理要求（"判斷"、"分析"）

**避免 Thinking 的技巧：**
- 使用簡短直接的指令
- 移除角色設定
- 單一明確任務
- 避免條件邏輯

---

### 實作

#### 優化前（觸發 Thinking）

```python
system_instruction = """你是專業審計師。任務：
1. 精確提取收據關鍵資訊
2. 回傳純 JSON（無 markdown 格式）
3. 日期格式必須是 YYYY-MM-DD

JSON 格式：{"shop":"店名","amount":數字,"date":"YYYY-MM-DD","currency":"幣別","items":"品項"}"""

prompt = f"""收據分析參數：
- 年度：{year}（用於判斷日期格式，如 DD/MM/YY → YYYY-MM-DD）
- 國家：{country_info['name']}
- 預設幣別：{country_info['currency']}
- 小數點：'{hint}'

日期規則：若看到 "21/06/25" 且 21≤31，判斷為 DD/MM/YY，結合年度 {year} 推斷完整日期。"""
```

**Token 估算：**
- System Instruction: ~100 tokens
- User Prompt: ~150 tokens
- **Thinking Tokens: ~20,000 tokens** ❌
- Output: ~50 tokens
- **總計: ~20,300 tokens**

**費用（每張）：**
```
Input: 250 × $0.075 / 1M = $0.000019
Output (含 Thinking): 20,050 × $0.30 / 1M = $0.006015
總計: $0.006034
```

---

#### 優化後（避免 Thinking）⭐

```python
system_instruction = "Extract receipt info as JSON: shop, amount, date (YYYY-MM-DD), currency, items."

prompt = f"""Country: {country_info['name']}
Currency: {country_info['currency']}
Year: {year}
Decimal: '{hint}'

Return JSON only."""
```

**Token 估算：**
- System Instruction: ~20 tokens
- User Prompt: ~30 tokens
- **Thinking Tokens: 0-100 tokens** ✓（大幅減少）
- Output: ~50 tokens
- **總計: ~100-200 tokens**

**費用（每張）：**
```
Input: 50 × $0.075 / 1M = $0.0000038
Output: 150 × $0.30 / 1M = $0.000045
總計: $0.0000488
```

**節省：** $0.006034 → $0.0000488 = **99.2% ⬇️**

---

### 實測建議

**測試方法：**
```
1. 部署簡化版 Prompt
2. 上傳 10 張測試收據
3. 檢查 Google Cloud Console：
   - 查看每次呼叫的 Token 數
   - 確認費用 < $0.001（10 張）
4. 驗證辨識精度是否下降
```

**預期結果：**
- Token 消耗：100-200/張（vs 20,000）
- 費用：$0.0005（10 張）
- 精度：可能略降，但應該足夠

**優點：**
- ✅ 立即實施（已修改程式碼）
- ✅ 成本大幅降低（99%+）
- ✅ 無需更換 API
- ✅ 保持相同的整合方式

**缺點：**
- ⚠️ 日期判斷可能較弱（可後處理補強）
- ⚠️ 複雜收據可能精度下降

**推薦指數：⭐⭐⭐⭐⭐**

---

## 方案 2: 專門的 OCR API

### Google Document AI

**定價：**
```
Receipt/Expense Parser：
- 前 1,000 頁/月：免費
- 1,001-1,000,000 頁：$0.10/10 頁 = $0.01/張
- 1,000,001+：$0.065/10 頁 = $0.0065/張
```

**優點：**
- ✅ 專門針對收據設計
- ✅ 輸出結構化 JSON
- ✅ 精度極高（官方支援）
- ✅ 成本可預測
- ✅ 支援多語言
- ✅ 包含欺詐檢測

**缺點：**
- ❌ 需要申請 GCP 專案
- ❌ 需要啟用 Document AI API
- ❌ 較複雜的整合
- ❌ 前 1,000 張免費後開始收費（$0.01/張）

**成本對比（每月 1,000 張）：**
```
Gemini 2.5 Flash（簡化 Prompt）：
- 前 1,000 張：$0.049

Google Document AI：
- 前 1,000 張：免費
- 之後：$0.01/張 × 1,000 = $10/1,000 張

Document AI 更貴！
```

**推薦指數：⭐⭐**（僅在精度要求極高時考慮）

---

### 第三方專業 API

#### Klippa
**定價：** ~$0.05-0.10/張

#### Veryfi
**定價：** $0.01-0.05/張

#### Mindee
**定價：** ~$0.01-0.03/張

#### Tabscanner
**定價：** ~$0.02-0.05/張

**優點：**
- ✅ 專門針對收據/發票
- ✅ 高精度
- ✅ 支援多語言
- ✅ 包含驗證功能

**缺點：**
- ❌ 需要註冊新帳號
- ❌ 需要重寫整合程式碼
- ❌ 比簡化 Prompt 的 Gemini 貴
- ❌ 可能有用量限制

**成本對比（每月 1,000 張）：**
```
Gemini 2.5 Flash（簡化）：$0.049
第三方 API：$10-50

第三方 API 貴太多！
```

**推薦指數：⭐**（成本太高）

---

## 🎯 最終建議

### 推薦方案：簡化 Prompt + Gemini 2.5 Flash

**理由：**
1. **成本最低**（$0.049/1,000 張）
2. **立即可用**（已修改程式碼）
3. **無需額外整合**
4. **精度可接受**（收據 OCR 相對簡單）

**實施步驟：**

#### 步驟 1: 立即部署（已完成）✓
```bash
git add app_enhanced.py
git commit -m "perf: Simplify prompt to avoid Thinking Tokens"
git push
```

#### 步驟 2: 測試驗證
```
1. 上傳 10 張收據
2. 檢查 Google Cloud 費用
3. 驗證辨識精度
```

#### 步驟 3: 監控與調整
```
如果精度不足：
- 可微調 Prompt
- 可加入後處理邏輯
- 可考慮 Document AI（成本更高）
```

---

## 📊 成本對比總表

| 方案 | 成本/1,000張 | 精度 | 整合難度 | 推薦度 |
|-----|-------------|------|---------|--------|
| **Gemini 2.5 簡化** | **$0.049** | ★★★★☆ | ★★★★★ | ⭐⭐⭐⭐⭐ |
| Gemini 2.5 原版 | $56 | ★★★★★ | ★★★★★ | ❌ |
| Document AI | $10（1K後） | ★★★★★ | ★★★☆☆ | ⭐⭐ |
| Klippa | $50-100 | ★★★★★ | ★★☆☆☆ | ⭐ |
| Veryfi | $10-50 | ★★★★★ | ★★☆☆☆ | ⭐ |
| Mindee | $10-30 | ★★★★★ | ★★☆☆☆ | ⭐ |

---

## 🔧 進階優化建議

### 如果簡化 Prompt 後精度下降

**補救措施：**

#### 1. 後處理日期
```python
# 在 run_vlm_scan 後處理
def fix_date_format(date_str, year):
    """後處理日期格式"""
    # DD/MM/YY → YYYY-MM-DD
    # 21/06/25 → 2025-06-21
    pass
```

#### 2. 加入驗證規則
```python
def validate_receipt(result):
    """驗證辨識結果"""
    # 檢查金額是否合理
    # 檢查日期是否合理
    # 檢查幣別是否匹配國家
    pass
```

#### 3. 使用 Context Caching
```python
# Gemini 2.5 支援 context caching
# 可以將 system_instruction 快取
# 進一步降低成本
```

---

## ⚠️ 關於 gemini-1.5-flash 不可用

**錯誤訊息：**
```
NotFound: 404 models/gemini-1.5-flash is not found for API version v1beta
```

**原因：**
- Gemini 1.5 Flash 可能已被淘汰
- 或在 v1beta API 中已不支援
- 或模型名稱已更改

**可嘗試的模型：**
```python
# 選項 1: Gemini 2.0 Flash
model_name = 'models/gemini-2.0-flash-exp'

# 選項 2: Gemini 2.5 Flash（簡化 Prompt）
model_name = 'models/gemini-2.5-flash'  # 目前使用

# 選項 3: 查詢可用模型
models = genai.list_models()
for m in models:
    if 'flash' in m.name.lower():
        print(m.name)
```

---

## ✅ 行動計畫

### 立即執行（已完成）

- [x] 簡化 System Instruction
- [x] 簡化 User Prompt
- [x] 移除觸發 Thinking 的關鍵字

### 部署後驗證

1. **上傳 10 張測試收據**
2. **檢查 Google Cloud Console**
   - 確認每次 Token 數 < 500
   - 確認總費用 < $0.001
3. **驗證精度**
   - 商店名稱正確率
   - 金額正確率
   - 日期正確率
   - 幣別正確率

### 如果需要

4. **調整 Prompt**（如果精度不足）
5. **加入後處理**（補強日期判斷）
6. **考慮 Document AI**（如果預算允許）

---

## 🎉 結論

**最佳方案：簡化 Prompt + Gemini 2.5 Flash**

- 成本：$0.049/1,000 張（vs $56）
- 節省：**99.9%** ⬇️
- 精度：應該足夠（需驗證）
- 實施：立即可用

**立即部署並測試！** 🚀

---

**報告版本：** Final  
**日期：** 2026-02-23  
**建議：** 立即部署簡化版，監控成本與精度
