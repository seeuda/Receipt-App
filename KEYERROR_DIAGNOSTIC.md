# KeyError 快速診斷指南

## 🚨 如果您看到這個錯誤

```
KeyError at line 625:
reg_map[rk].append((f"{cfg['emoji']} {cfg['country']}", cfg))
```

這表示某個配置檔缺少必要欄位。

---

## 🔍 診斷步驟

### Step 1: 檢查 configs/ 目錄是否存在

**GitHub 上檢查：**
1. 進入您的 repository
2. 確認有 `configs/` 目錄
3. 確認目錄內有 40+ 個 JSON 檔案

**應該看到：**
```
configs/
├── tw_params.json
├── jp_params.json
├── kr_params.json
├── ... (共 40 個)
└── region_order.json
```

**如果目錄不存在：**
```bash
# 本機執行
python generate_configs.py

# 提交至 GitHub
git add configs/
git commit -m "Add generated configs"
git push
```

---

### Step 2: 檢查配置檔格式

**隨機抽查一個檔案：**
```bash
cat configs/tw_params.json
```

**必須包含的欄位：**
```json
{
  "country": "台灣",          // ← 必須有
  "sub_region": "亞洲 [...]", // ← 必須有
  "priority": 0,
  "currency_code": "TWD",
  ...
}
```

**如果格式錯誤：**
```bash
# 重新生成
python generate_configs.py
git add configs/
git push
```

---

### Step 3: 檢查 Streamlit Cloud 日誌

**查看完整錯誤訊息：**
1. 點擊 Streamlit app 右下角 "Manage app"
2. 點擊 "Logs"
3. 查看完整錯誤堆疊

**可能看到的警告訊息：**
- `⚠️ tw_params.json 缺少 'country' 欄位，跳過`
- `❌ 找不到配置檔！請確認 configs/ 目錄存在`
- `❌ 沒有可用的國家配置！`

---

## 🛠️ 常見問題與解決方案

### 問題 1: configs/ 目錄不存在

**症狀：**
```
❌ 找不到配置檔！請確認 configs/ 目錄存在
```

**原因：**
- configs/ 目錄未提交至 GitHub
- 或 .gitignore 忽略了 configs/

**解決：**
```bash
# 檢查 .gitignore
cat .gitignore

# 如果 configs/ 被忽略，移除該行
vim .gitignore

# 提交 configs/
git add configs/
git commit -m "Add configs directory"
git push
```

---

### 問題 2: region_order.json 被誤讀

**症狀：**
```
⚠️ region_order.json 缺少 'country' 欄位，跳過
```

**原因：**
- `region_order.json` 是區域排序配置，不是國家參數
- 但被 `load_all_configs()` 誤讀了

**解決：**
- v2.2.1 已修正此問題
- 確認使用最新版 `app_v2.py`

---

### 問題 3: 某些配置檔損壞

**症狀：**
```
⚠️ 讀取 tw_params.json 失敗: Expecting property name enclosed in double quotes
```

**原因：**
- JSON 格式錯誤
- 可能手動編輯時出錯

**解決：**
```bash
# 重新生成所有配置
python generate_configs.py

# 檢查格式
cat configs/tw_params.json | python -m json.tool

# 提交
git add configs/
git push
```

---

### 問題 4: 配置檔缺少 emoji 欄位

**症狀：**
```
⚠️ 配置 tw 缺少必要欄位，跳過
```

**原因：**
- 配置檔是舊版格式
- 或手動建立時遺漏欄位

**解決：**
- emoji 欄位由 `load_all_configs()` 自動加入
- 如果仍出錯，檢查是否缺少 `country` 欄位

---

## ✅ 驗證清單

部署前確認：

- [ ] 本機執行 `python generate_configs.py` 成功
- [ ] `configs/` 目錄存在且包含 40+ 個 JSON 檔案
- [ ] `configs/region_order.json` 存在
- [ ] 隨機抽查 3 個配置檔，格式正確
- [ ] .gitignore 沒有忽略 configs/
- [ ] 已提交至 GitHub：`git add configs/` → `git push`
- [ ] GitHub 上可看到 configs/ 目錄

---

## 🔧 緊急修復

如果線上環境仍然出錯，臨時方案：

### 方案 A: 回滾到穩定版本

```bash
# 切換回 v1.0 或 v2.0
git checkout <穩定版本的 commit hash>
git push -f
```

### 方案 B: 移除排序優化

暫時使用簡化版排序：

```python
# app_v2.py L625 改為
reg_map[rk].append((f"{cfg.get('emoji', '🌐')} {cfg.get('country', '未知')}", cfg))
```

---

## 📊 v2.2.1 改進內容

本版本加入完整的錯誤處理：

### 1. load_all_configs() 強化

```python
✅ 檢查 configs/ 目錄是否存在
✅ 排除 region_order.json
✅ 驗證每個配置檔的必要欄位
✅ 捕捉並記錄個別檔案的錯誤
✅ 顯示友善的錯誤訊息
```

### 2. 區域國家對應表強化

```python
✅ 檢查 country 和 emoji 欄位
✅ 跳過格式錯誤的配置
✅ 驗證 reg_map 是否為空
✅ 驗證 sorted_regions 是否為空
✅ 及早停止執行（避免後續錯誤）
```

---

## 💡 預防措施

### 開發流程規範

```bash
# 1. 修改配置
vim generate_configs.py

# 2. 生成配置
python generate_configs.py

# 3. 驗證格式
ls configs/*.json | wc -l  # 應該 >= 41
cat configs/tw_params.json | python -m json.tool

# 4. 本機測試
streamlit run app_v2.py

# 5. 確認無誤後提交
git add configs/ generate_configs.py app_v2.py
git commit -m "Update configs"
git push
```

### CI/CD 建議（未來）

```yaml
# .github/workflows/validate.yml
name: Validate Configs
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Check configs exist
        run: |
          test -d configs
          test $(ls configs/*.json | wc -l) -ge 41
      - name: Validate JSON format
        run: |
          for f in configs/*.json; do
            python -m json.tool "$f" > /dev/null
          done
```

---

## 📞 仍然無法解決？

請提供以下資訊：

1. **Streamlit Cloud 完整日誌**（Manage app → Logs）
2. **GitHub configs/ 目錄截圖**
3. **configs/tw_params.json 內容**（可用 GitHub 查看）
4. **app_v2.py 版本**（檢查檔案開頭的版本號或最後修改日期）

透過 Line 回報中心聯繫：https://line.me/ti/g/twX_HfMGBd

---

**版本：** v2.2.1  
**修正日期：** 2025-02-12  
**核心改進：** 完整的配置檔錯誤處理與診斷
