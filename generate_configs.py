# Base Indent: 0 spaces
import json
import os
import logging
from typing import Dict, Any

# 配置日誌紀錄，確保產出過程透明可追蹤
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def generate_configs(master_data: Dict[str, Dict[str, Any]], output_dir: str = "configs") -> None:
    """
    將母字典資料轉化為獨立的 JSON 配置檔。
    此舉可達成架構解耦，讓主程式動態讀取各國規律。
    """
    base_path = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(base_path, output_dir)
    
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        logging.info(f"✨ 已建立配置目錄: {target_dir}")

    for iso_code, params in master_data.items():
        file_path = os.path.join(target_dir, f"{iso_code}_params.json")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(params, f, ensure_ascii=False, indent=2)
            logging.info(f"✅ 產出成功: {iso_code}_params.json")
        except Exception as e:
            logging.error(f"❌ 產出 {iso_code} 失敗: {e}")

    logging.info(f"\n🎉 全數完成！共計產出 {len(master_data)} 個在地化參數檔。")

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

# --- 40 國在地化母字典 (8 大分組優化版) ---
# 參數說明：
# - decimal_sep / thousand_sep: 小數點與千分位符號，解決歐洲與美台差異
# - date_order: 日期辨識權重 (YMD/DMY/MDY)，防止月日倒置
# - address_regex: 標靶排除地址、電話、網址，提升品項純淨度
# - header_skips: 排除收據頂部無意義標頭（如發票、收據等字樣）
# - tax_symbols: 排除品項旁的稅級雜訊（如 A, B, *）

MASTER_REGISTRY = {
    # === 區域 1: 亞洲 [東亞/東南亞] ===
    "tw": {
        "country": "台灣", "sub_region": "亞洲 [東亞/東南亞]", "priority": 0, "currency_code": "TWD",
        "decimal_sep": ".", "thousand_sep": ",", "date_order": "YMD",
        "address_regex": r"(路|街|巷|弄|號|樓|市|區)", "tax_symbols": r"(\*)",
        "header_skips": ["統一發票", "電子發票", "收銀機發票", "交易明細"],
        "keywords": ["總計", "合計", "實收"], "stop_keywords": ["統編", "謝謝"], "month_map": {"1月":"01", "2月":"02", "3月":"03", "4月":"04", "5月":"05", "6月":"06", "7月":"07", "8月":"08", "9月":"09", "10月":"10", "11月":"11", "12月":"12"}
    },
    "jp": {
        "country": "日本", "sub_region": "亞洲 [東亞/東南亞]", "priority": 1, "currency_code": "JPY",
        "decimal_sep": ".", "thousand_sep": ",", "date_order": "YMD",
        "address_regex": r"(丁目|番地|号|県|市|区|町|道)", "tax_symbols": r"(\*)",
        "header_skips": ["領収", "受領", "お買上"],
        "keywords": ["合計", "お預り"], "stop_keywords": ["有難う", "税"], "month_map": {"1月":"01"}
    },
    "kr": {
        "country": "韓國", "sub_region": "亞洲 [東亞/東南亞]", "priority": 1, "currency_code": "KRW",
        "decimal_sep": ".", "thousand_sep": ",", "date_order": "YMD",
        "address_regex": r"(동|로|길|구|시|도)", "header_skips": ["영수증", "신용카드"],
        "keywords": ["합계", "금액"], "stop_keywords": ["감사합니다", "부가세"], "month_map": {"1월":"01"}
    },
    "sg": {
        "country": "新加坡", "sub_region": "亞洲 [東亞/東南亞]", "priority": 10, "currency_code": "SGD",
        "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY",
        "address_regex": r"(\d{6})|(Street)|(Road)|(Ave)|(Lane)",
        "keywords": ["TOTAL", "NET"], "stop_keywords": ["THANK", "GST"], "month_map": {"Jan":"01"}
    },
    "vn": {
        "country": "越南", "sub_region": "亞洲 [東亞/東南亞]", "priority": 10, "currency_code": "VND",
        "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY",
        "address_regex": r"(Quận)|(Huyện)|(Phường)", "keywords": ["TỔNG", "THANH TOÁN"], "stop_keywords": ["CẢM", "VAT"], "month_map": {"Tháng 1":"01"}
    },
    "th": {
        "country": "泰國", "sub_region": "亞洲 [東亞/東南亞]", "priority": 10, "currency_code": "THB",
        "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY",
        "address_regex": r"(เขต)|(แขวง)|(จังหวัด)", "keywords": ["รวม", "ยอด"], "stop_keywords": ["ขอบ", "VAT"], "month_map": {"ม.ค.":"01"}
    },
    "my": {
        "country": "馬來西亞", "sub_region": "亞洲 [東亞/東南亞]", "priority": 10, "currency_code": "MYR",
        "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY",
        "address_regex": r"(\d{5})|(Jalan)|(Taman)", "keywords": ["TOTAL"], "stop_keywords": ["TERIMA"], "month_map": {"Jan":"01"}
    },
    "ph": {
        "country": "菲律賓", "sub_region": "亞洲 [東亞/東南亞]", "priority": 10, "currency_code": "PHP",
        "decimal_sep": ".", "thousand_sep": ",", "date_order": "MDY",
        "address_regex": r"(Brgy)|(St\.)|(City)", "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}
    },
    "id": {
        "country": "印尼", "sub_region": "亞洲 [東亞/東南亞]", "priority": 10, "currency_code": "IDR",
        "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY",
        "address_regex": r"(Jalan)|(Kec\.)|(Kab\.)", "keywords": ["TOTAL"], "stop_keywords": ["TERIMA"], "month_map": {"Jan":"01"}
    },

    # === 區域 2: 亞洲 [西南亞] ===
    "in": {"country": "印度", "sub_region": "亞洲 [西南亞]", "priority": 10, "currency_code": "INR", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"(\d{6})|(Plot)|(Sector)", "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}},
    "ae": {"country": "阿聯", "sub_region": "亞洲 [西南亞]", "priority": 10, "currency_code": "AED", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"(Dubai)|(Abu Dhabi)|(PO Box)", "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}},
    "il": {"country": "以色列", "sub_region": "亞洲 [西南亞]", "priority": 10, "currency_code": "ILS", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"(Street)|(St\.)", "keywords": ["סה\"כ"], "stop_keywords": ["תודה"], "month_map": {"ינו":"01"}},
    "sa": {"country": "沙烏地", "sub_region": "亞洲 [西南亞]", "priority": 10, "currency_code": "SAR", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"(District)|(St\.)", "keywords": ["الإجمالي"], "stop_keywords": ["شكرا"], "month_map": {"ينا":"01"}},

    # === 區域 3: 歐洲 [中歐] ===
    "de": {
        "country": "德國", "sub_region": "歐洲 [中歐]", "priority": 1, "currency_code": "EUR",
        "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY",
        "address_regex": r"(\d{5}\s+[A-Z])|(Str\.)|(Strasse)|(Ring)|(Platz)|(Gasse)|(Tel:)|(St\.-Nr)",
        "tax_symbols": r"([A-Z]\b)", "header_skips": ["RECHNUNG", "QUITTUNG", "BELEG", "KASSENBELEG"],
        "keywords": ["GESAMT", "SUMME", "TOTAL"], "stop_keywords": ["VIELEN DANK", "STEUER"], "month_map": {"Jan":"01"}
    },
    "at": {"country": "奧地利", "sub_region": "歐洲 [中歐]", "priority": 10, "currency_code": "EUR", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{4}\s+[A-Z])|(Gasse)|(Str\.)", "tax_symbols": r"([A-Z]\b)", "header_skips": ["RECHNUNG"], "keywords": ["GESAMT"], "stop_keywords": ["DANKE"], "month_map": {"Jan":"01"}},
    "ch": {"country": "瑞士", "sub_region": "歐洲 [中歐]", "priority": 10, "currency_code": "CHF", "decimal_sep": ".", "thousand_sep": "'", "date_order": "DMY", "address_regex": r"(\d{4}\s+[A-Z])|(Strasse)|(Rue)", "tax_symbols": r"(\*)", "header_skips": ["FACTURE"], "keywords": ["TOTAL"], "stop_keywords": ["MERCI"], "month_map": {"Jan":"01"}},
    "cz": {"country": "捷克", "sub_region": "歐洲 [中歐]", "priority": 10, "currency_code": "CZK", "decimal_sep": ",", "thousand_sep": " ", "date_order": "DMY", "address_regex": r"(\d{3}\s\d{2})|(Ulice)|(Náměstí)", "keywords": ["CELKEM"], "stop_keywords": ["DĚK"], "month_map": {"Led":"01"}},
    "pl": {"country": "波蘭", "sub_region": "歐洲 [中歐]", "priority": 10, "currency_code": "PLN", "decimal_sep": ",", "thousand_sep": " ", "date_order": "DMY", "address_regex": r"(\d{2}-\d{3})|(ul\.)", "keywords": ["SUMA"], "stop_keywords": ["DZIEK"], "month_map": {"Sty":"01"}},
    "tr": {"country": "土耳其", "sub_region": "歐洲 [中歐]", "priority": 10, "currency_code": "TRY", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(Caddesi)|(Sokak)|(Mah\.)", "keywords": ["TOPLAM"], "stop_keywords": ["TEŞE"], "month_map": {"Oca":"01"}},

    # === 區域 4: 歐洲 [西歐] ===
    "gb": {"country": "英國", "sub_region": "歐洲 [西歐]", "priority": 1, "currency_code": "GBP", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"([A-Z]{1,2}\d[A-Z\d]?\s\d[A-Z]{2})|(Street)|(Rd\.)", "header_skips": ["INVOICE"], "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}},
    "fr": {"country": "法國", "sub_region": "歐洲 [西歐]", "priority": 5, "currency_code": "EUR", "decimal_sep": ",", "thousand_sep": " ", "date_order": "DMY", "address_regex": r"(\d{5})|(Rue)|(Ave)|(Boulevard)", "tax_symbols": r"(\*)", "header_skips": ["FACTURE"], "keywords": ["TOTAL"], "stop_keywords": ["MERCI"], "month_map": {"Jan":"01"}},
    "nl": {"country": "荷蘭", "sub_region": "歐洲 [西歐]", "priority": 10, "currency_code": "EUR", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{4}\s?[A-Z]{2})|(Straat)", "keywords": ["TOTAAL"], "stop_keywords": ["BEDANKT"], "month_map": {"Jan":"01"}},
    "be": {"country": "比利時", "sub_region": "歐洲 [西歐]", "priority": 10, "currency_code": "EUR", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(Rue)|(Straat)", "keywords": ["TOTAL"], "stop_keywords": ["MERCI"], "month_map": {"Jan":"01"}},
    "ie": {"country": "愛爾蘭", "sub_region": "歐洲 [西歐]", "priority": 10, "currency_code": "EUR", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"([A-Z]\d{2}\s[A-Z\d]{4})|(Street)", "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}},

    # === 區域 5: 歐洲 [北歐] ===
    "dk": {"country": "丹麥", "sub_region": "歐洲 [北歐]", "priority": 5, "currency_code": "DKK", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{4})|(Gade)|(Vej)", "keywords": ["TOTAL"], "stop_keywords": ["KØB"], "month_map": {"Januar":"01"}},
    "no": {"country": "挪威", "sub_region": "歐洲 [北歐]", "priority": 10, "currency_code": "NOK", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{4})|(Gate)|(Vei)", "keywords": ["TOTAL"], "stop_keywords": ["TAK"], "month_map": {"Jan":"01"}},
    "se": {"country": "瑞典", "sub_region": "歐洲 [北歐]", "priority": 10, "currency_code": "SEK", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{3}\s\d{2})|(Gata)|(Väg)", "keywords": ["TOTAL"], "stop_keywords": ["TACK"], "month_map": {"Jan":"01"}},
    "fi": {"country": "芬蘭", "sub_region": "歐洲 [北歐]", "priority": 10, "currency_code": "EUR", "decimal_sep": ",", "thousand_sep": " ", "date_order": "DMY", "address_regex": r"(\d{5})|(Katu)|(Tie)", "keywords": ["YHTEENSÄ"], "stop_keywords": ["KIITOS"], "month_map": {"Tammi":"01"}},
    "is": {"country": "冰島", "sub_region": "歐洲 [北歐]", "priority": 10, "currency_code": "ISK", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{3})|(Gata)|(Vegur)", "keywords": ["SAMTALS"], "stop_keywords": ["TAKK"], "month_map": {"Jan":"01"}},

    # === 區域 6: 歐洲 [南歐] ===
    "it": {"country": "義大利", "sub_region": "歐洲 [南歐]", "priority": 10, "currency_code": "EUR", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{5})|(Via)|(Piazza)", "keywords": ["TOTALE"], "stop_keywords": ["GRAZIE"], "month_map": {"Gen":"01"}},
    "es": {"country": "西班牙", "sub_region": "歐洲 [南歐]", "priority": 10, "currency_code": "EUR", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{5})|(Calle)|(Avenida)", "keywords": ["TOTAL"], "stop_keywords": ["GRACIAS"], "month_map": {"Ene":"01"}},
    "pt": {"country": "葡萄牙", "sub_region": "歐洲 [南歐]", "priority": 10, "currency_code": "EUR", "decimal_sep": ",", "thousand_sep": " ", "date_order": "DMY", "address_regex": r"(\d{4}-\d{3})|(Rua)", "keywords": ["TOTAL"], "stop_keywords": ["OBRIGADO"], "month_map": {"Jan":"01"}},
    "gr": {"country": "希臘", "sub_region": "歐洲 [南歐]", "priority": 10, "currency_code": "EUR", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{5})|(Οδός)", "keywords": ["ΣΥΝΟΛΟ"], "stop_keywords": ["ΕΥΧ"], "month_map": {"Ιαν":"01"}},

    # === 區域 7: 美洲 ===
    "us": {"country": "美國", "sub_region": "美洲", "priority": 1, "currency_code": "USD", "decimal_sep": ".", "thousand_sep": ",", "date_order": "MDY", "address_regex": r"(\d{5})|(Ave\.)|(St\.)|(Road)", "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}},
    "ca": {"country": "加拿大", "sub_region": "美洲", "priority": 10, "currency_code": "CAD", "decimal_sep": ".", "thousand_sep": ",", "date_order": "MDY", "address_regex": r"([A-Z]\d[A-Z]\s\d[A-Z]\d)|(St\.)", "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}},
    "br": {"country": "巴西", "sub_region": "美洲", "priority": 10, "currency_code": "BRL", "decimal_sep": ",", "thousand_sep": ".", "date_order": "DMY", "address_regex": r"(\d{5}-\d{3})|(Rua)", "keywords": ["TOTAL"], "stop_keywords": ["OBRIGADO"], "month_map": {"Jan":"01"}},
    "mx": {"country": "墨西哥", "sub_region": "美洲", "priority": 10, "currency_code": "MXN", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"(\d{5})|(Calle)|(Av\.)", "keywords": ["TOTAL"], "stop_keywords": ["GRACIAS"], "month_map": {"Ene":"01"}},

    # === 區域 8: 其他區域 ===
    "au": {"country": "澳洲", "sub_region": "其他區域", "priority": 10, "currency_code": "AUD", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"(\d{4})|(St\.)|(Rd\.)", "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}},
    "nz": {"country": "紐西蘭", "sub_region": "其他區域", "priority": 10, "currency_code": "NZD", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"(\d{4})|(St\.)", "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}},
    "za": {"country": "南非", "sub_region": "其他區域", "priority": 10, "currency_code": "ZAR", "decimal_sep": ".", "thousand_sep": ",", "date_order": "DMY", "address_regex": r"(\d{4})|(St\.)", "keywords": ["TOTAL"], "stop_keywords": ["THANK"], "month_map": {"Jan":"01"}}
}

if __name__ == "__main__":
    generate_configs(MASTER_REGISTRY)
    
    # 額外產出區域排序配置檔
    base_path = os.path.dirname(os.path.abspath(__file__))
    region_order_path = os.path.join(base_path, "configs", "region_order.json")
    try:
        with open(region_order_path, "w", encoding="utf-8") as f:
            json.dump({"region_order": REGION_ORDER}, f, ensure_ascii=False, indent=2)
        logging.info(f"✅ 產出區域排序配置: region_order.json")
    except Exception as e:
        logging.error(f"❌ 產出區域排序失敗: {e}")