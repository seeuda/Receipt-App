# [cite_start]OCR 語義優化報告 
- **特徵判定**：引入 `is_address_feature` 邏輯，利用數字密度與郵遞區號規律偵測地址，正確率提升。
- **影像預處理**：增加 Contrast 與 Sharpness 增強，解決手寫或模糊單據辨識困難。
- **判重更新**：採用 Salted UID，信任最新一次同步內容進行覆蓋。