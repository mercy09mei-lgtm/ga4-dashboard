"""业务映射规则配置文件：根据域名/站点动态选择匹配规则。

新增一个站点/产品时只需要改这里：
  1. 下载包：在 *_DOWNLOAD_RULES 里加一行 dl("文件名主干", "产品名")，.exe/.dmg/-arm 自动生成
  2. 购买 key（buy_xxx_win_one_year）：在 *_PRODUCT_MAP 里加 "key 里的产品段": "显示名"
  3. SEO 购买页：Sidify 用 SIDIFY_SEO_BUY_RULES；NoteBurner 按 order-*.html 规律自动解析
没有命中规则的数据不会被悄悄丢掉，页面上会列出“未映射项”，照着上面补一行即可。
"""
import re


def dl(stem: str, product: str, apk: bool = False):
    """按文件名主干生成下载规则：.exe→win，.dmg→mac，(-arm) 自动兼容；apk=True 时追加安卓。"""
    rules = [
        (rf"{re.escape(stem)}(-arm)?\.exe", f"{product} win"),
        (rf"{re.escape(stem)}(-arm)?\.dmg", f"{product} mac"),
    ]
    if apk:
        rules.append((rf"{re.escape(stem)}\.apk", f"{product}安卓"))
    return rules


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

# SEO 购买点击映射（buynow URL -> 产品名称 + 操作系统）
SIDIFY_SEO_BUY_RULES = [
    (r"spotify-windows/buynow", "Spotify Win"),
    (r"spotify-android/buynow", "Spotify Android"),
    (r"spotify/buynow", "Spotify Mac"),
    (r"amazon-converter-windows/buynow", "Amazon Win"),
    (r"amazon-converter/buynow", "Amazon Mac"),
    (r"apple-music-windows/buynow", "Apple Win"),
    (r"apple-music/buynow", "Apple Mac"),
    (r"deezer-converter-windows/buynow", "Deezer Win"),
    (r"deezer-converter-mac/buynow", "Deezer Mac"),
    (r"tidal-downloader/buynow", "Tidal Win"),
    (r"youtube-music/buynow", "YouTube Win"),
    (r"all-in-one/buynow-windows", "MusicOne Win"),
    (r"all-in-one/buynow", "MusicOne Mac"),
]

# buy_<产品>_<系统>_<套餐> 里的“产品”段 -> 显示名
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
# 下载文件分布在 noteburner.com / noteburner-video.com / tunepat.com 多个域名，这里只按文件名主干匹配
NOTEBURNER_DOWNLOAD_RULES = (
    dl("noteburner-sp-music-converter", "spotify")
    + dl("nb-sp-downloader", "spotify", apk=True)
    + dl("noteburner-music-one", "musicOne")
    + dl("noteburner-youtube-music-converter", "YouTube")
    + dl("noteburner-amazon-music-recorder", "amazC")
    + dl("noteburner-deezer-music-converter", "Deezer")
    + dl("noteburner-tidal-music-converter", "Tidal")
    + dl("nb-audio-recorder", "AudioRecorder")
    # —— 视频产品（noteburner-video.com）——
    + dl("noteburner-video-downloader", "VideoDownloader")
    + dl("noteburner-netflix-video-downloader", "Netflix")
    + dl("noteburner-amazon-video-downloader", "AmazonVideo")
    + dl("nb-video-converter", "VideoConverter")
    # —— 其他品牌产品（tunepat.com）——
    + dl("any-audiobook-converter", "Audiobook(TunePat)")
)

# buy key：buy_sp_win_one_year / buy_itunes_audio_mac_lifetime / buy_spotify_lifetime（无系统）
NOTEBURNER_PRODUCT_MAP = {
    "sp": "spotify", "spotify": "spotify",
    "amazon": "amazC", "az": "amazC",
    "ap": "Apple", "itunes_audio": "Apple",
    "td": "Tidal", "tidal": "Tidal",
    "deezer": "Deezer",
    "musicone": "musicOne",
    "videoone": "videoOne",
    "youtube": "YouTube",
}

# NoteBurner 的 SEO 购买页：常规订单页由 noteburner_seo_buy_label() 自动解析，特殊 URL 可在这里补规则
NOTEBURNER_SEO_BUY_RULES = []

# SEO 购买点击：NoteBurner 的“文件名”是订单页 URL：order-[mac-]<产品slug>[-for-windows|-for-android].html
SEO_ORDER_SLUGS = [
    (r"netflix", "Netflix"),
    (r"amazon-video", "AmazonVideo"),
    (r"music-one", "MusicOne"),
    (r"video-one", "VideoOne"),
    (r"spotify", "Spotify"),
    (r"itunes|apple", "Apple"),
    (r"amazon", "Amazon"),
    (r"tidal", "Tidal"),
    (r"deezer", "Deezer"),
    (r"youtube", "YouTube"),
]


def noteburner_seo_buy_label(url: str):
    """order-mac-spotify-music-converter.html → 'Spotify Mac'；不是订单页格式返回 None（计入未映射项）。"""
    m = re.search(r"/order-(mac-)?(.+?)(-for-(windows|android))?\.html", (url or "").lower())
    if not m:
        return None
    mac, slug, _, suffix = m.groups()
    os_label = "Mac" if mac else {"windows": "Win", "android": "Android"}.get(suffix, "未标注")
    for kw, name in SEO_ORDER_SLUGS:
        if re.search(kw, slug):
            return f"{name} {os_label}"
    return f"{slug} {os_label}"  # 新产品：先用 slug 原样显示，不丢数据


# ================= 通用套餐映射 =================
PLAN_MAP = {
    "lifetime": "lifetime",
    "one_year": "12M", "12m": "12M", "1_year": "12M",
    "3m": "3M", "three_month": "3M",
    "1m": "1M", "one_month": "1M",
}

PLAN_CHOICES = {"Lifetime": "lifetime", "12M": "12M", "3M": "3M", "1M": "1M"}

# 测试域名（如 noteburner-dev.de / sidify-dev.de）的数据一律排除：文件名列、网页位置列、网页路径里出现即丢弃
EXCLUDE_URL_PATTERN = r"-dev\."

OS_TOKENS = {"win": "win", "windows": "win", "mac": "mac", "android": "android"}


def get_rules_for_site(site_domain: str):
    domain = site_domain.lower() if site_domain else ""
    if "noteburner" in domain:
        return {
            "download_rules": NOTEBURNER_DOWNLOAD_RULES,
            "seo_buy_rules": NOTEBURNER_SEO_BUY_RULES,
            "seo_buy_fallback": noteburner_seo_buy_label,
            "product_map": NOTEBURNER_PRODUCT_MAP,
            "plan_map": PLAN_MAP,
            "default_os": "未标注",   # buy key 里没写系统时，不擅自当成 win
            "exclude_pattern": EXCLUDE_URL_PATTERN,
        }
    return {
        "download_rules": SIDIFY_DOWNLOAD_RULES,
        "seo_buy_rules": SIDIFY_SEO_BUY_RULES,
        "seo_buy_fallback": None,
        "product_map": SIDIFY_PRODUCT_MAP,
        "plan_map": PLAN_MAP,
        "default_os": "win",         # 保持 Sidify 原有行为
        "exclude_pattern": EXCLUDE_URL_PATTERN,
    }
