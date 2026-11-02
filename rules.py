"""业务映射规则配置文件：根据域名/站点动态选择匹配规则，方便扩展 Sidify, NoteBurner 等新产品。"""
import re

# ================= 1. Sidify 规则配置 =================
SIDIFY_DOWNLOAD_RULES = [
    (r"sidify-music-converter(-arm)?\.exe", "spotify win"),
    (r"sidify-music-converter(-arm)?\.dmg", "spotify mac"),
    (r"sidify.*\.apk", "spotify安卓"),
    (r"sidify-music-converter-free", "SD Free"),
    (r"sidify-all-in-one(-arm)?\.exe", "musicOne win"),
    (r"sidify-all-in-one(-arm)?\.dmg", "musicOne mac"),
    (r"(amazon-music-converter|sidify-amc)(-arm)?\.exe", "amazC win"),
    (r"amazon-music-converter(-arm)?\.dmg", "amazC mac"),
    (r"tidal-music-converter(-arm)?\.exe", "Tidal win"),
    (r"tidal-music-converter(-arm)?\.dmg", "Tidal mac"),
    (r"apple-music-converter(-arm)?\.exe", "Apple win"),
    (r"apple-music-converter(-arm)?\.dmg", "Apple mac"),
    (r"youtube-music-converter(-arm)?\.exe", "YouTube win"),
    (r"youtube-music-converter(-arm)?\.dmg", "YouTube mac"),
    (r"deezer-music-converter(-arm)?\.exe", "Deezer win"),
    (r"deezer-music-converter(-arm)?\.dmg", "Deezer mac"),
]

SIDIFY_PRODUCT_MAP = {
    "sp": "spotify", "spotify": "spotify",
    "all-in-one": "musicOne", "all_in_one": "musicOne", "musicone": "musicOne",
    "az": "amazC", "amazon": "amazC",
    "tidal": "Tidal", "td": "Tidal",
    "apc": "Apple", "apple": "Apple",
    "youtube": "YouTube", "yt": "YouTube",
    "deezer": "Deezer", "dz": "Deezer",
}

# ================= 2. NoteBurner 规则配置 =================
NOTEBURNER_DOWNLOAD_RULES = [
    (r"noteburner-spotify-music-converter(-arm)?\.exe", "spotify win"),
    (r"noteburner-spotify-music-converter(-arm)?\.dmg", "spotify mac"),
    (r"noteburner-youtube-video-downloader\.exe", "yt-dl win"),
]

NOTEBURNER_PRODUCT_MAP = {
    "spotify": "spotify",
    "youtube": "youtube",
}

# ================= 通用套餐映射 =================
PLAN_MAP = {
    "lifetime": "lifetime",
    "one_year": "12M", "12m": "12M", "1_year": "12M",
    "3m": "3M", "three_month": "3M",
    "1m": "1M", "one_month": "1M",
}

PLAN_CHOICES = {"Lifetime": "lifetime", "12M": "12M", "3M": "3M", "1M": "1M"}


def get_rules_for_site(site_domain: str):
    domain = site_domain.lower() if site_domain else ""
    if "noteburner" in domain:
        return {
            "download_rules": NOTEBURNER_DOWNLOAD_RULES,
            "product_map": NOTEBURNER_PRODUCT_MAP,
            "plan_map": PLAN_MAP,
        }
    return {
        "download_rules": SIDIFY_DOWNLOAD_RULES,
        "product_map": SIDIFY_PRODUCT_MAP,
        "plan_map": PLAN_MAP,
    }
