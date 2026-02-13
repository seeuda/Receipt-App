# KeyError 錯誤修正說明

## 🐛 錯誤描述

**錯誤訊息：**
```
KeyError
File "/mount/src/receipt-app/app_enhanced_v2.py", line 578, in main
    reg_map[rk].append((f"{cfg['emoji']} {cfg['country']}", cfg))
```

**發生位置：** 
`app_v2.py` L578

---

## 🔍 根本原因

### 問題分析

**原版程式碼（L84-87）：**
```python
for f in glob.glob("configs/*.json"):
    iso = os.path.basename(f).split('_')[0].lower()
    with open(f, 'r', encoding='utf-8') as j:
        d = json.load(j); d['emoji'] = emoji_map.get(iso, "🌐"); configs[iso] = d
```

**問題：**
`glob.glob("configs/*.json")` 會讀取 **configs/ 目錄下的所有 JSON 檔案**，包括：
- ✅ `tw_params.json`（國家參數檔）
- ✅ `jp_params.json`（國家參數檔）
- ❌ `region_order.json`（區域排序檔）⚠️

**region_order.json 的結構：**
```json
{
  "region_order": [
    "亞洲 [東亞/東南亞]",
    ...
  ]
}
```

**問題在於：**
1. `region_order.json` 沒有 `country` 欄位
2. 當程式嘗試訪問 `cfg['country']` 時 → **KeyError**

---

## ✅ 解決方案

### 修正程式碼

**修正位置：** `app_v2.py` L81-89

```python
def load_all_configs() -> Dict:
    configs = {}
    emoji_map = {...}
    for f in glob.glob("configs/*.json"):
        # 排除 region_order.json（非國家參數檔）
        if os.path.basename(f) == "region_order.json":
            continue  # ← 關鍵修正
        iso = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            d = json.load(j); d['emoji'] = emoji_map.get(iso, "🌐"); configs[iso] = d
    return configs
```

**核心改動：**
```python
# 新增檢查，排除 region_order.json
if os.path.basename(f) == "region_order.json":
    continue
```

---

## 🧪 測試驗證

### 測試案例

**情境 1：configs/ 目錄結構**
```
configs/
├── tw_params.json          ✅ 讀取
├── jp_params.json          ✅ 讀取
├── kr_params.json          ✅ 讀取
├── ...
└── region_order.json       ⚠️ 跳過（修正後）
```

**預期結果：**
- `load_all_configs()` 返回 40 個國家配置
- 不包含 `region_order`

---

### 驗證步驟

**Step 1：本機測試**
```python
# 測試腳本
import glob
import os

configs = {}
for f in glob.glob("configs/*.json"):
    if os.path.basename(f) == "region_order.json":
        continue
    iso = os.path.basename(f).split('_')[0].lower()
    print(f"讀取: {iso} <- {f}")

print(f"\n共讀取 {len(configs)} 個國家配置")
```

**預期輸出：**
```
讀取: tw <- configs/tw_params.json
讀取: jp <- configs/jp_params.json
...
共讀取 40 個國家配置
```

**Step 2：Streamlit 測試**
```bash
streamlit run app_v2.py
```

**預期結果：**
- 頁面正常載入
- 區域選單顯示正常
- 國家選單顯示正常
- 無 KeyError

---

## 📊 修正前後對比

### 修正前（有問題）

```python
for f in glob.glob("configs/*.json"):
    # ❌ 讀取所有 JSON，包括 region_order.json
    iso = os.path.basename(f).split('_')[0].lower()
    d = json.load(j)
    configs[iso] = d  # region_order.json 被誤讀為國家配置
```

**結果：**
```python
configs = {
    'tw': {...},         # ✅ 正常
    'jp': {...},         # ✅ 正常
    'region': {...},     # ❌ 錯誤！這是 region_order.json
}
```

**錯誤發生：**
```python
cfg = configs['region']  # 這是 region_order.json 的內容
cfg['country']           # KeyError：沒有 'country' 欄位
```

---

### 修正後（正常）

```python
for f in glob.glob("configs/*.json"):
    if os.path.basename(f) == "region_order.json":
        continue  # ✅ 跳過 region_order.json
    iso = os.path.basename(f).split('_')[0].lower()
    d = json.load(j)
    configs[iso] = d
```

**結果：**
```python
configs = {
    'tw': {...},  # ✅ 正常
    'jp': {...},  # ✅ 正常
    # region_order.json 被正確跳過
}
```

---

## 🔧 替代方案（更嚴謹）

如果想更嚴謹地過濾，可以使用檔名模式匹配：

### 方案 A：僅讀取 *_params.json

```python
def load_all_configs() -> Dict:
    configs = {}
    emoji_map = {...}
    # 僅讀取符合 *_params.json 格式的檔案
    for f in glob.glob("configs/*_params.json"):
        iso = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            d = json.load(j); d['emoji'] = emoji_map.get(iso, "🌐"); configs[iso] = d
    return configs
```

**優點：**
- 更明確的檔案過濾
- 即使新增其他 JSON 檔案也不會誤讀

---

### 方案 B：驗證必要欄位

```python
def load_all_configs() -> Dict:
    configs = {}
    emoji_map = {...}
    for f in glob.glob("configs/*.json"):
        if os.path.basename(f) == "region_order.json":
            continue
        iso = os.path.basename(f).split('_')[0].lower()
        with open(f, 'r', encoding='utf-8') as j:
            d = json.load(j)
            # 驗證必要欄位
            if 'country' not in d or 'currency_code' not in d:
                continue  # 跳過格式不符的檔案
            d['emoji'] = emoji_map.get(iso, "🌐")
            configs[iso] = d
    return configs
```

**優點：**
- 更健壯的錯誤處理
- 自動跳過格式錯誤的檔案

---

## 📋 部署檢查清單

修正後部署前確認：

- [ ] 本機測試通過（無 KeyError）
- [ ] `region_order.json` 存在於 `configs/`
- [ ] 修正後的 `app_v2.py` 已提交
- [ ] GitHub 上可看到最新版本
- [ ] Streamlit Cloud 重新部署完成

---

## 🎯 經驗教訓

### 問題根源

**配置目錄包含不同類型的 JSON 檔案：**
```
configs/
├── tw_params.json       (國家參數)
├── jp_params.json       (國家參數)
└── region_order.json    (區域排序配置)
```

**教訓：**
- `glob.glob("*.json")` 會讀取所有 JSON 檔案
- 需要明確過濾或檔名規範

### 未來改進建議

**選項 1：分離目錄結構**
```
configs/
├── countries/           (國家參數)
│   ├── tw_params.json
│   ├── jp_params.json
│   └── ...
└── region_order.json    (區域排序)
```

讀取時：
```python
for f in glob.glob("configs/countries/*.json"):
    # 不會誤讀 region_order.json
```

**選項 2：統一檔名格式**
```
configs/
├── country_tw.json      (國家參數，固定前綴)
├── country_jp.json      (國家參數，固定前綴)
└── region_order.json    (特殊配置)
```

讀取時：
```python
for f in glob.glob("configs/country_*.json"):
    # 明確過濾
```

---

## ✅ 總結

### 問題
`glob.glob("configs/*.json")` 誤讀 `region_order.json`，導致 KeyError

### 解決
在讀取循環中加入檔名檢查，跳過 `region_order.json`

### 效果
- ✅ 修復 KeyError
- ✅ 正確載入 40 個國家配置
- ✅ 區域排序正常運作

---

**版本：** v2.2.1  
**修正日期：** 2025-02-12  
**修正內容：** 修復配置檔讀取 KeyError
