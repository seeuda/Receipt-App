# 排序配置集中管理方案

## 🎯 優化目標

將所有排序邏輯集中到 `generate_configs.py`，實現：
1. ✅ 區域排序配置化
2. ✅ 國家優先級配置化
3. ✅ 單一來源管理（Single Source of Truth）

---

## 📊 配置架構

### 配置檔案結構

```
configs/
├── tw_params.json          # 台灣參數
├── jp_params.json          # 日本參數
├── ...                     # 其他 40 國參數
└── region_order.json       # 區域排序配置（新增）
```

---

## 🔧 實作細節

### 1️⃣ generate_configs.py 修改

**新增區域排序配置：**
```python
# 區域排序配置（按使用頻率）
REGION_ORDER = [
    "亞洲 [東亞/東南亞]",
    "歐洲 [西歐]",
    "歐洲 [中歐]",
    "歐洲 [南歐]",
    "歐洲 [北歐]",
    "亞洲 [西南亞]",
    "美洲",
    "其他區域"
]
```

**生成配置檔：**
```python
if __name__ == "__main__":
    generate_configs(MASTER_REGISTRY)
    
    # 額外產出區域排序配置檔
    with open("configs/region_order.json", "w") as f:
        json.dump({"region_order": REGION_ORDER}, f, indent=2)
```

---

### 2️⃣ app_v2.py 修改

**新增讀取函數：**
```python
def load_region_order() -> List[str]:
    """讀取區域排序配置，若檔案不存在則使用預設值"""
    try:
        with open("configs/region_order.json", 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("region_order", [])
    except FileNotFoundError:
        # 預設排序（向下相容）
        return [
            "亞洲 [東亞/東南亞]",
            # ...
        ]
```

**使用讀取的配置：**
```python
# 讀取區域排序配置
region_order = load_region_order()
sorted_regions = [r for r in region_order if r in reg_map]
```

---

### 3️⃣ region_order.json 範例

**檔案內容：**
```json
{
  "region_order": [
    "亞洲 [東亞/東南亞]",
    "歐洲 [西歐]",
    "歐洲 [中歐]",
    "歐洲 [南歐]",
    "歐洲 [北歐]",
    "亞洲 [西南亞]",
    "美洲",
    "其他區域"
  ]
}
```

---

## 📋 使用方式

### 調整區域排序

**Step 1: 修改 generate_configs.py**
```python
REGION_ORDER = [
    "歐洲 [西歐]",         # 改為第一
    "亞洲 [東亞/東南亞]",  # 移到第二
    # ...
]
```

**Step 2: 重新生成配置**
```bash
python generate_configs.py
```

**Step 3: 重新部署**
```bash
git add configs/region_order.json
git commit -m "chore: Update region order"
git push
```

---

### 調整國家優先級

**Step 1: 修改 generate_configs.py**
```python
"sg": {
    "country": "新加坡",
    "priority": 1,  # 原本 10 → 改為 1
    # ...
}
```

**Step 2: 重新生成配置**
```bash
python generate_configs.py
```

**Step 3: 重新部署**
```bash
git add configs/sg_params.json
git commit -m "chore: Increase Singapore priority"
git push
```

---

## 🎯 優點分析

### 集中管理

**原版（分散）：**
```
generate_configs.py  → 國家 Priority
app_v2.py            → 區域排序
```

**優化版（集中）：**
```
generate_configs.py  → 國家 Priority + 區域排序
app_v2.py            → 僅讀取配置
```

---

### 向下相容

**檔案不存在時：**
- `load_region_order()` 會返回預設順序
- 系統正常運作，不會報錯

**檔案存在時：**
- 讀取 `region_order.json`
- 使用自訂順序

---

### 易於調整

**修改流程：**
```
1. 編輯 generate_configs.py
2. 執行 python generate_configs.py
3. 提交 configs/ 目錄
4. 部署完成
```

**無需：**
- ❌ 修改主程式邏輯
- ❌ 重新測試核心功能
- ❌ 擔心破壞現有程式碼

---

## 📊 配置完整性檢查

### 驗證腳本

可在 `generate_configs.py` 末尾加入驗證：

```python
def validate_configs():
    """驗證配置完整性"""
    # 檢查所有區域是否在 REGION_ORDER 中
    all_regions = set(cfg['sub_region'] for cfg in MASTER_REGISTRY.values())
    missing_regions = all_regions - set(REGION_ORDER)
    
    if missing_regions:
        logging.warning(f"⚠️ 以下區域未在 REGION_ORDER 中: {missing_regions}")
    
    # 檢查所有國家是否有 priority
    for iso, cfg in MASTER_REGISTRY.items():
        if 'priority' not in cfg:
            logging.warning(f"⚠️ {iso} 缺少 priority 欄位")

if __name__ == "__main__":
    generate_configs(MASTER_REGISTRY)
    
    # 產出區域排序配置
    with open("configs/region_order.json", "w") as f:
        json.dump({"region_order": REGION_ORDER}, f, indent=2)
    
    # 驗證配置
    validate_configs()
```

---

## 🔄 部署流程

### 首次部署

```bash
# 1. 生成配置檔（包含 region_order.json）
python generate_configs.py

# 2. 提交所有配置
git add configs/
git commit -m "feat: Centralize sorting configuration"

# 3. 推送
git push origin main

# 4. Streamlit Cloud 自動重新部署
```

---

### 後續調整

```bash
# 1. 修改 generate_configs.py 中的 REGION_ORDER 或 priority

# 2. 重新生成
python generate_configs.py

# 3. 提交變更
git add configs/
git commit -m "chore: Update region/country priority"

# 4. 推送
git push
```

---

## 🎨 進階配置（可選）

### 選項 A：新增區域權重

在 `region_order.json` 加入權重資訊：

```json
{
  "region_order": [
    {"name": "亞洲 [東亞/東南亞]", "weight": 100},
    {"name": "歐洲 [西歐]", "weight": 80},
    {"name": "歐洲 [中歐]", "weight": 70}
  ]
}
```

**用途：**
- 未來可根據權重調整 UI 顯示（如字體大小、顏色）
- 統計分析使用頻率

---

### 選項 B：多語言區域名稱

```json
{
  "region_order": [
    {
      "id": "asia_east_southeast",
      "name_zh": "亞洲 [東亞/東南亞]",
      "name_en": "Asia [East & Southeast]"
    }
  ]
}
```

**用途：**
- 國際化支援
- 未來新增英文介面

---

## 📝 維護建議

### 定期檢查

**每季檢視：**
1. 區域排序是否符合實際使用頻率
2. 國家優先級是否需調整
3. 是否有新增國家需求

### 文件更新

**修改配置時同步更新：**
- `README.md` - 排序邏輯說明
- `SORTING_OPTIMIZATION.md` - 技術細節
- 本文件 - 配置管理指南

---

## 🎯 總結

### 優化前
```
區域排序：硬編碼在 app_v2.py
國家排序：Priority 在 generate_configs.py
```

### 優化後
```
區域排序：REGION_ORDER 在 generate_configs.py
國家排序：Priority 在 generate_configs.py
主程式：僅讀取 configs/*.json
```

### 效果
- ✅ 單一來源管理
- ✅ 易於調整維護
- ✅ 向下相容安全
- ✅ 配置與程式解耦

---

**版本：** v2.2  
**更新日期：** 2025-02-12  
**核心改進：** 排序配置集中管理
