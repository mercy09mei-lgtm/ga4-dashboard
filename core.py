"""GA4 看板核心逻辑：解析 CSV、规则匹配、计算指标、导出报表。"""
import csv
import io
import re
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
import rules

PLAN_CHOICES = rules.PLAN_CHOICES

UNKNOWN_DATE = {
    "type": "unknown", "cur_days": 0, "prev_days": 0, "file_tag": "UNKNOWN",
    "cur_label": "本期", "prev_label": "上期", "detail_label": "本期 vs 上期"
}

EXPORT_SEO_TOP = 50
SLOT_KEYS = ("traffic", "download", "buy", "seo", "seo_click", "seo_buy")


def format_pct_change(diff_pct):
    """全局格式化环比变动率：
    上升：▲ +X.X%
    下降：▼ -X.X%
    持平：+0.0%
    无基数：-
    """
    if diff_pct is None:
        return "-"
    if diff_pct > 0:
        return f"▲ +{diff_pct:.1f}%"
    elif diff_pct < 0:
        return f"▼ {diff_pct:.1f}%"
    else:
        return "+0.0%"


# ================= 日期与站点 =================
def analyze_date_info_from_text(text):
    if not text:
        return dict(UNKNOWN_DATE)
    for line in text.splitlines()[:15]:
        # 对比导出： # 20261001-20261031 20260901-20260930；单时间段： # 20261006-20261102
        match = re.search(r'#\s*(\d{8})-(\d{8})(?:\s+(\d{8})-(\d{8}))?', line)
        if match:
            c_start, c_end, p_start, p_end = match.groups()
            return _build_date_info(c_start, c_end, p_start, p_end)
    return analyze_date_info_from_header_dates(text)


def analyze_date_info_from_header_dates(text):
    if not text:
        return dict(UNKNOWN_DATE)
    starts = re.findall(r'开始日期[：:]\s*(\d{8})', text)
    ends = re.findall(r'结束日期[：:]\s*(\d{8})', text)
    if len(starts) >= 2 and len(ends) >= 2:
        return _build_date_info(starts[0], ends[0], starts[1], ends[1])
    if len(starts) == 1 and len(ends) == 1:
        return _build_date_info(starts[0], ends[0], None, None)
    return dict(UNKNOWN_DATE)


def _build_date_info(c_start, c_end, p_start=None, p_end=None):
    try:
        d_c_s = datetime.strptime(c_start, "%Y%m%d")
        d_c_e = datetime.strptime(c_end, "%Y%m%d")
        cur_days = (d_c_e - d_c_s).days + 1
        cur_lbl = f"{d_c_s.strftime('%m.%d')}-{d_c_e.strftime('%m.%d')}"

        if not p_start or not p_end:  # 只有一个时间段：没有对比期
            return {
                "type": "single", "cur_days": cur_days, "prev_days": 0,
                "file_tag": f"{c_start}_{c_end}", "cur_label": cur_lbl,
                "prev_label": "", "detail_label": f"{cur_lbl} ({cur_days}天) · 单一时间段，无对比期",
            }

        d_p_s = datetime.strptime(p_start, "%Y%m%d")
        d_p_e = datetime.strptime(p_end, "%Y%m%d")
        prev_days = (d_p_e - d_p_s).days + 1
        prev_lbl = f"{d_p_s.strftime('%m.%d')}-{d_p_e.strftime('%m.%d')}"
        detail_lbl = f"本期 {cur_lbl} ({cur_days}天) vs 上期 {prev_lbl} ({prev_days}天)"
        return {
            "type": "custom", "cur_days": cur_days, "prev_days": prev_days,
            "file_tag": f"{c_start}_{c_end}", "cur_label": cur_lbl,
            "prev_label": prev_lbl, "detail_label": detail_lbl,
        }
    except Exception:
        return dict(UNKNOWN_DATE)


def parse_site_domain(text):
    if not text:
        return "sidify.de"
    for line in text.splitlines()[:15]:
        match = re.search(r'https?://(?:www\.)?([^/\s\-]+(?:\.[a-z]+)+)', line)
        if match:
            return match.group(1).lower()
    return "sidify.de"


# ================= 文件自动探测 =================
def detect_file_type(content: str, filename: str = ""):
    head = "\n".join(content.splitlines()[:20]).lower()
    fn = filename.lower()
    if "seo转化计算-下载" in head or "seo-download" in fn:
        return "seo_click", "下载点击"
    if "seo转化计算-buy" in head or ("seo" in fn and "buy" in fn):
        return "seo_buy", "SEO购买点击"
    if "带来用户首次互动的主渠道组" in head or "用户获取" in head or "traffic" in fn:
        return "traffic", "用户获取"
    if "网页路径和屏幕类" in head or "网页和屏幕" in head:
        return "seo", "网页 PV"
    if "设备类别" in head and ("download" in fn or any(ext in content for ext in [".exe", ".dmg", ".apk"])):
        return "download", "下载量"
    if "buy_" in content or "buynow" in content:
        return "buy", "购买量"
    return "unknown", "未识别"


def safe_parse_val(raw_str):
    if not raw_str or str(raw_str).strip() in ['', '-', 'None']:
        return 0.0
    try:
        return float(str(raw_str).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _extract_date_label_and_col(row):
    """严格匹配对比期日期格式，排除掉 '# https://xxx.de - GA4' 等非日期干扰"""
    if not row or str(row[0]).strip().startswith('#'):
        return None, None
    for idx in range(min(4, len(row))):
        val = str(row[idx]).strip()
        if " - " in val and "变动百分比" not in val and re.search(r'\d+月|\d{4}年|\d{8}', val):
            return idx, val
    return None, None


# ================= CSV 解析函数 =================
_NUM_RE = re.compile(r"^-?[\d,]+(\.\d+)?$")


def _is_num(v):
    return bool(_NUM_RE.match(str(v).strip()))


def _last_num(row):
    """取一行里最后一个纯数字单元格（跳过“总计”等文字）。"""
    for cell in reversed(row):
        if _is_num(cell):
            return int(safe_parse_val(cell))
    return None


def _has_compare(rows):
    """文件是否带“日期比较”行（GA4 勾选了对比期）。单时间段导出没有。"""
    return any(_extract_date_label_and_col(r)[0] is not None for r in rows)


def _excluder(rule_bundle):
    pat = (rule_bundle or {}).get("exclude_pattern") or rules.EXCLUDE_URL_PATTERN
    return re.compile(pat, re.IGNORECASE)


def _note_excluded(excluded, kind, key, val):
    if excluded is not None:
        excluded.append({"类型": kind, "排除项": key, "本期数量": int(val or 0)})


def _note_unnamed(unnamed, kind, key, example, val):
    """数据已按原名计入，但产品名还没有映射成显示名。"""
    if unnamed is not None and val:
        unnamed.append({"类型": kind, "未映射项": key, "示例": example, "本期数量": int(val)})


def _note_unmatched(unmatched, kind, key, val):
    if unmatched is not None and val:
        unmatched.append({"类型": kind, "未映射项": key, "本期数量": int(val)})



def parse_download_csv_content(text, rule_bundle, unmatched=None, excluded=None):
    """解析客户端 download.csv（带对比期 / 单时间段两种布局都支持）"""
    if not text:
        return None, None
    download_rules = rule_bundle["download_rules"]
    rows = list(csv.reader(text.splitlines()))
    desktop_idx, mobile_idx, tablet_idx, totals_idx = 2, 3, None, -1
    for row in rows[:15]:
        if any("设备类别" in str(cell) for cell in row):
            for idx, val in enumerate(row):
                v = val.strip().lower()
                if v == "desktop": desktop_idx = idx
                elif v == "mobile": mobile_idx = idx
                elif v == "tablet": tablet_idx = idx
                elif v in ["totals", "合计"]: totals_idx = idx
            break

    compare = _has_compare(rows)
    excl = _excluder(rule_bundle)
    cur_skip = False
    excl_tot = {"cur": 0, "prev": 0}
    cur_period_label, prev_period_label = None, None
    current_key = None
    data, ga4_totals = {}, {"cur": 0, "prev": 0}

    for row in rows:
        if not row or row[0].strip().startswith('#'):
            continue
        first_col = row[0].strip()
        is_key = first_col.startswith('http') or any(first_col.lower().endswith(ext) for ext in ['.exe', '.dmg', '.apk'])
        if is_key:
            current_key = first_col
            cur_skip = bool(excl.search(first_col))

        if compare:
            date_col_idx, label = _extract_date_label_and_col(row)
            if date_col_idx is None:
                continue
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label
            p_key = "cur" if label == cur_period_label else "prev"
        else:  # 单时间段：数据直接在文件行里，总计行的第一列为空
            if not is_key and not (first_col == "" and current_key is None):
                continue
            p_key = "cur"

        if not current_key and first_col == "":
            tot_val = safe_parse_val(row[totals_idx]) if len(row) > abs(totals_idx) else 0.0
            ga4_totals[p_key] = int(tot_val)
            continue

        if current_key and cur_skip:  # 测试域名：不计入统计，并从总计里扣除
            tv = int(safe_parse_val(row[totals_idx])) if len(row) > abs(totals_idx) else 0
            excl_tot[p_key] += tv
            if p_key == "cur":
                _note_excluded(excluded, "客户端下载", current_key, tv)
            continue

        if current_key:
            target_col = None
            for pat, col_name in download_rules:
                if re.search(pat, current_key, re.IGNORECASE):
                    target_col = col_name
                    break
            if not target_col:
                if p_key == "cur" and len(row) > abs(totals_idx):
                    _note_unmatched(unmatched, "客户端下载", current_key, safe_parse_val(row[totals_idx]))
                continue
            if current_key.lower().endswith(".apk"):  # 安卓包：按 .apk 判断，取 mobile + tablet
                m = safe_parse_val(row[mobile_idx]) if len(row) > mobile_idx else 0.0
                t = safe_parse_val(row[tablet_idx]) if tablet_idx and len(row) > tablet_idx else 0.0
                val = m + t
            else:
                val = safe_parse_val(row[desktop_idx]) if len(row) > desktop_idx else 0.0
            data.setdefault(target_col, {"cur": 0, "prev": 0})[p_key] += int(val)
    for p in ("cur", "prev"):
        ga4_totals[p] = max(ga4_totals[p] - excl_tot[p], 0)
    return data, ga4_totals


def split_buy_key(key, rule_bundle):
    """buy_<产品>_<系统?>_<套餐> → (原始产品段, 系统或None, 套餐或None)。产品可含多段（itunes_audio）。"""
    plan_map = rule_bundle["plan_map"]
    tokens = [t.lower() for t in key.split("_")[1:] if t]
    plan = None
    for n in (2, 1):  # one_year 占两段
        if len(tokens) > n and "_".join(tokens[-n:]) in plan_map:
            plan = plan_map["_".join(tokens[-n:])]
            tokens = tokens[:-n]
            break
    os_label = None
    for i, t in enumerate(tokens):
        if t in rules.OS_TOKENS and i > 0:
            os_label = rules.OS_TOKENS[t]
            tokens.pop(i)
            break
    return "_".join(tokens), os_label, plan


def classify_buy_key(key, rule_bundle):
    """buy key → (展示名, 套餐)；套餐认不出时返回 (None, None)。系统可省略（按站点 default_os）。"""
    raw_prod, os_label, plan = split_buy_key(key, rule_bundle)
    if plan is None or not raw_prod:
        return None, None
    prod = rule_bundle["product_map"].get(raw_prod, raw_prod)
    return f"{prod} {os_label or rule_bundle.get('default_os', 'win')}", plan


def parse_buy_csv_content(text, rule_bundle, unmatched=None, unnamed=None):
    """解析真正的订单结算表 buy.csv（带对比期 / 单时间段两种布局都支持）"""
    if not text:
        return None, None
    rows = list(csv.reader(text.splitlines()))
    compare = _has_compare(rows)
    cur_period_label, prev_period_label = None, None
    current_key = None
    data = {"lifetime": {}, "12M": {}, "3M": {}, "1M": {}}
    ga4_totals = {"cur": 0, "prev": 0}

    for row in rows:
        if not row or row[0].strip().startswith('#'):
            continue
        first_col = row[0].strip()
        if first_col.startswith("buy_"):
            current_key = first_col

        if compare:
            date_col_idx, label = _extract_date_label_and_col(row)
            if date_col_idx is None:
                continue
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label
            p_key = "cur" if label == cur_period_label else "prev"
        else:  # 单时间段： [buy_key, 数量] ；总计行 ['', 数量, '总计']
            if not first_col.startswith("buy_") and not (first_col == "" and current_key is None):
                continue
            date_col_idx, p_key = 0, "cur"

        val = safe_parse_val(row[date_col_idx + 1]) if len(row) > date_col_idx + 1 else 0.0
        if not current_key and first_col == "":
            ga4_totals[p_key] = int(val)
            continue
        if current_key:
            target_col, norm_plan = classify_buy_key(current_key, rule_bundle)
            if not target_col or norm_plan not in data:
                if p_key == "cur":
                    _note_unmatched(unmatched, "购买量", current_key, val)
                continue
            data[norm_plan].setdefault(target_col, {"cur": 0, "prev": 0})[p_key] += int(val)
            raw_prod = split_buy_key(current_key, rule_bundle)[0]
            if p_key == "cur" and raw_prod not in rule_bundle["product_map"]:
                _note_unnamed(unnamed, "购买产品名", raw_prod, current_key, val)
    return data, ga4_totals


def parse_traffic_csv_content(text):
    """解析渠道流量 / 用户获取 CSV"""
    if not text:
        return None
    traffic = {"cur": {}, "prev": {}}
    current_period = None
    for line in text.splitlines():
        l_str = line.strip()
        if not l_str:
            continue
        if l_str.startswith("# 开始日期："):
            current_period = "cur" if current_period is None else "prev"
            continue
        if l_str.startswith('#') or "带来用户首次互动的主渠道组" in l_str:
            continue
        if current_period in ["cur", "prev"]:
            cols = [c.strip() for c in l_str.split(',')]
            if cols and cols[0]:
                traffic[current_period][cols[0]] = int(safe_parse_val(cols[1])) if len(cols) > 1 else 0
    return traffic


def parse_seo_pages_csv_content(text, rule_bundle=None, excluded=None):
    """解析网页和屏幕 CSV"""
    if not text:
        return None
    pages_data = {"cur": {}, "prev": {}}
    current_period = None
    for line in text.splitlines():
        l_str = line.strip()
        if not l_str:
            continue
        if l_str.startswith("# 开始日期："):
            current_period = "cur" if current_period is None else "prev"
            continue
        if l_str.startswith('#') or "网页路径和屏幕类" in l_str:
            continue
        if current_period in ["cur", "prev"]:
            cols = [c.strip() for c in l_str.split(',')]
            if cols and cols[0]:
                if _excluder(rule_bundle).search(cols[0]):  # 测试域名页面
                    if current_period == "cur":
                        _note_excluded(excluded, "网页PV", cols[0], int(safe_parse_val(cols[2])) if len(cols) > 2 else 0)
                    continue
                pages_data[current_period][cols[0]] = int(safe_parse_val(cols[2])) if len(cols) > 2 else 0
    return pages_data


def parse_seo_download_clicks_csv(text, rule_bundle, unmatched=None, excluded=None):
    """
    解析 seo-download.csv：
    1. 取最后一列【合计】数值，不单独区分 desktop/mobile
    2. 对单页面挂载的多个安装包下载量进行累加
    3. 带对比期 / 单时间段两种布局都支持
    """
    if not text:
        return None, None, None
    download_rules = rule_bundle["download_rules"]
    rows = list(csv.reader(text.splitlines()))
    compare = _has_compare(rows)
    excl = _excluder(rule_bundle)
    current_skip = False
    excl_tot = {"cur": 0, "prev": 0}
    cur_period_label, prev_period_label = None, None
    current_path, current_file = None, None
    clicks_data = {"cur": {}, "prev": {}}
    prod_clicks = {"cur": {}, "prev": {}}
    total_clicks = {"cur": 0, "prev": 0}

    for row in rows:
        if not row or row[0].strip().startswith('#'):
            continue
        if len(row) > 1 and row[0].startswith("http"):
            current_file = row[0].strip()
        if len(row) > 1 and row[1].startswith("http"):
            current_path = urlparse(row[1].strip()).path
        if len(row) > 1 and (row[0].startswith("http") or row[1].startswith("http")):
            current_skip = bool(excl.search(row[0]) or excl.search(row[1]))

        if compare:
            date_col_idx, label = _extract_date_label_and_col(row)
            if date_col_idx is None:
                continue
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label
            p_key = "cur" if label == cur_period_label else "prev"
            val = _last_num(row) or 0
        else:
            is_data = len(row) > 1 and row[0].startswith("http") and row[1].startswith("http")
            is_total = (not current_path and row[0].strip() in ("", "总计")
                        and (len(row) < 2 or row[1].strip() == "") and _last_num(row) is not None)
            if not (is_data or is_total):
                continue
            p_key, val = "cur", _last_num(row) or 0

        if current_skip and current_path:  # 测试域名：不计入统计，并从总计里扣除
            excl_tot[p_key] += val
            if p_key == "cur":
                _note_excluded(excluded, "SEO下载点击", current_file or "?", val)
            continue

        if not current_path and row[0].strip() in ("", "总计"):
            total_clicks[p_key] = val
        elif current_path:
            clicks_data[p_key][current_path] = clicks_data[p_key].get(current_path, 0) + val
            target_prod = None
            if current_file:
                for pat, prod_name in download_rules:
                    if re.search(pat, current_file, re.IGNORECASE):
                        target_prod = prod_name
                        break
            if not target_prod:
                target_prod = "Other"
                if p_key == "cur":
                    _note_unmatched(unmatched, "SEO下载点击", current_file or "?", val)
            prod_clicks[p_key][target_prod] = prod_clicks[p_key].get(target_prod, 0) + val

    for p in ("cur", "prev"):
        total_clicks[p] = max(total_clicks[p] - excl_tot[p], 0)
    return clicks_data, prod_clicks, total_clicks


def parse_seo_buy_clicks_csv(text, rule_bundle, unmatched=None, excluded=None):
    """解析 SEO-buy.csv 提取用户在 SEO 页面点击购买（click_buy）的数据；带对比期 / 单时间段都支持"""
    if not text:
        return None, None, None
    seo_buy_rules = rule_bundle["seo_buy_rules"]
    fallback = rule_bundle.get("seo_buy_fallback")
    rows = list(csv.reader(text.splitlines()))
    compare = _has_compare(rows)
    excl = _excluder(rule_bundle)
    current_skip = False
    excl_tot = {"cur": 0, "prev": 0}
    cur_period_label, prev_period_label = None, None
    current_path, current_buynow = None, None
    clicks_data = {"cur": {}, "prev": {}}
    prod_clicks = {"cur": {}, "prev": {}}
    total_clicks = {"cur": 0, "prev": 0}

    for row in rows:
        if not row or row[0].strip().startswith('#'):
            continue
        if len(row) > 1 and row[0].startswith("http"):
            current_buynow = row[0].strip()
        if len(row) > 1 and row[1].startswith("http"):
            current_path = urlparse(row[1].strip()).path
        if len(row) > 1 and (row[0].startswith("http") or row[1].startswith("http")):
            current_skip = bool(excl.search(row[0]) or excl.search(row[1]))

        if compare:
            date_col_idx, label = _extract_date_label_and_col(row)
            if date_col_idx is None:
                continue
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label
            p_key = "cur" if label == cur_period_label else "prev"
            val = int(safe_parse_val(row[date_col_idx + 1])) if len(row) > date_col_idx + 1 else 0
        else:
            is_data = len(row) > 1 and row[0].startswith("http") and row[1].startswith("http")
            is_total = (not current_path and row[0].strip() in ("", "总计")
                        and (len(row) < 2 or row[1].strip() == "") and _last_num(row) is not None)
            if not (is_data or is_total):
                continue
            p_key, val = "cur", _last_num(row) or 0

        if current_skip and current_path:  # 测试域名：不计入统计，并从总计里扣除
            excl_tot[p_key] += val
            if p_key == "cur":
                _note_excluded(excluded, "SEO购买点击", current_buynow or "?", val)
            continue

        if not current_path and row[0].strip() in ("", "总计"):
            total_clicks[p_key] = val
        elif current_path:
            clicks_data[p_key][current_path] = clicks_data[p_key].get(current_path, 0) + val
            target_prod = None
            if current_buynow:
                for pat, p_name in seo_buy_rules:
                    if re.search(pat, current_buynow, re.IGNORECASE):
                        target_prod = p_name
                        break
                if not target_prod and fallback:
                    target_prod = fallback(current_buynow)
            if not target_prod:
                target_prod = "Other Buynow"
                if p_key == "cur":
                    _note_unmatched(unmatched, "SEO购买点击", current_buynow or "?", val)
            prod_clicks[p_key][target_prod] = prod_clicks[p_key].get(target_prod, 0) + val

    for p in ("cur", "prev"):
        total_clicks[p] = max(total_clicks[p] - excl_tot[p], 0)
    return clicks_data, prod_clicks, total_clicks


# ================= 状态汇总 =================
def slot_has_compare(slot, text):
    """该文件是否带对比期。CSV 行布局看“日期比较”行；用户获取/网页 PV 看是否有两段开始日期。"""
    if not text:
        return False
    if slot in ("traffic", "seo"):
        return len(re.findall(r"开始日期[：:]", text)) >= 2
    return _has_compare(list(csv.reader(text.splitlines())))


def build_state(texts, extra_rules=None):
    if not any(texts.values()):
        return None

    date_info = dict(UNKNOWN_DATE)
    site_domain = None
    slot_dates = {}
    for k in SLOT_KEYS:
        txt = texts.get(k)
        if not txt:
            continue
        if not site_domain:
            site_domain = parse_site_domain(txt)
        d = analyze_date_info_from_text(txt)
        if d["type"] != "unknown":
            slot_dates[k] = d
            # 优先展示带对比期的时间段（单一时间段的文件排在后面）
            if date_info["type"] == "unknown" or (date_info["type"] == "single" and d["type"] == "custom"):
                date_info = d

    rule_bundle = merge_extra_rules(rules.get_rules_for_site(site_domain), extra_rules, site_domain)
    unmatched = []
    unnamed = []
    excluded = []

    # 各文件的本期时间段不一致时给出提示（例如 NB 的 buy 是 10.06-11.02，网页 PV 是 10.01-10.31）
    date_mismatch = {}
    if len({d["cur_label"] for d in slot_dates.values()}) > 1:
        date_mismatch = {k: d["cur_label"] for k, d in slot_dates.items()}

    state = {
        "date": date_info,
        "date_mismatch": date_mismatch,
        "has_cmp": {k: slot_has_compare(k, texts.get(k)) for k in SLOT_KEYS},
        "unmatched": unmatched,
        "unnamed": unnamed,
        "excluded": excluded,
        "site_domain": site_domain or "",
        "site": site_domain.capitalize() if site_domain else "Sidify.de",
        "dl": None, "dl_tot": None,
        "buy": None, "buy_tot": None,
        "tf": None,
        "seo": None,
        "seo_click": None, "seo_prod_click": None, "seo_click_tot": None,
        "seo_buy": None, "seo_buy_prod_click": None, "seo_buy_tot": None,
        "sources": [k for k in SLOT_KEYS if texts.get(k)],
    }

    if texts.get("download"):
        state["dl"], state["dl_tot"] = parse_download_csv_content(texts["download"], rule_bundle, unmatched, excluded)
    if texts.get("buy"):
        state["buy"], state["buy_tot"] = parse_buy_csv_content(texts["buy"], rule_bundle, unmatched, unnamed)
    if texts.get("traffic"):
        state["tf"] = parse_traffic_csv_content(texts["traffic"])
    if texts.get("seo"):
        state["seo"] = parse_seo_pages_csv_content(texts["seo"], rule_bundle, excluded)
    if texts.get("seo_click"):
        state["seo_click"], state["seo_prod_click"], state["seo_click_tot"] = parse_seo_download_clicks_csv(texts["seo_click"], rule_bundle, unmatched, excluded)
    if texts.get("seo_buy"):
        state["seo_buy"], state["seo_buy_prod_click"], state["seo_buy_tot"] = parse_seo_buy_clicks_csv(texts["seo_buy"], rule_bundle, unmatched, excluded)

    return state


def excluded_summary(state):
    """被排除的测试域名数据汇总：{类型: (条数, 本期数量)}"""
    out = {}
    for it in state.get("excluded") or []:
        n, v = out.get(it["类型"], (0, 0))
        out[it["类型"]] = (n + 1, v + it["本期数量"])
    return out


def unmatched_df(state):
    """未命中任何映射规则的项（合并同名项），用于页面提示。"""
    items = state.get("unmatched") or []
    if not items:
        return None
    df = pd.DataFrame(items).groupby(["类型", "未映射项"], as_index=False)["本期数量"].sum()
    return df.sort_values("本期数量", ascending=False).reset_index(drop=True)


def state_label(state):
    return state.get("date", {}).get("detail_label", "本期 vs 上期")


# ================= KPI 计算 =================
def dl_totals(state):
    if state.get("dl_tot") and state["dl_tot"].get("cur", 0) > 0:
        return state["dl_tot"]["cur"], state["dl_tot"]["prev"]
    if state.get("dl"):
        return sum(v["cur"] for v in state["dl"].values()), sum(v["prev"] for v in state["dl"].values())
    return None


def buy_totals(state):
    if state.get("buy_tot") and state["buy_tot"].get("cur", 0) > 0:
        return state["buy_tot"]["cur"], state["buy_tot"]["prev"]
    if state.get("buy"):
        cur = sum(p["cur"] for plan in state["buy"].values() for p in plan.values())
        prev = sum(p["prev"] for plan in state["buy"].values() for p in plan.values())
        return cur, prev
    if state.get("seo_buy_tot") and state["seo_buy_tot"].get("cur", 0) > 0:
        return state["seo_buy_tot"]["cur"], state["seo_buy_tot"]["prev"]
    return None


def compute_kpis(state):
    tf = state.get("tf")
    total_users = None
    if tf and tf.get("cur"):
        total_users = (sum(tf["cur"].values()), sum(tf["prev"].values()))

    dlt = dl_totals(state)
    buyt = buy_totals(state)

    dl_rate = None
    if dlt and total_users and total_users[0] > 0:
        dl_rate = (dlt[0] / total_users[0], dlt[1] / total_users[1] if total_users[1] else 0.0)

    hc = state.get("has_cmp", {})
    buy_src = "buy" if (state.get("buy_tot") or state.get("buy")) else "seo_buy"
    specs = [
        ("总流量", total_users, "count", hc.get("traffic", False)),
        ("下载量", dlt, "count", hc.get("download", False)),
        ("购买事件数", buyt, "count", hc.get(buy_src, False)),
        ("下载转化率", dl_rate, "rate", hc.get("download", False) and hc.get("traffic", False)),
    ]
    kpis = []
    for label, pair, kind, has_cmp in specs:
        if pair is None:
            kpis.append({"label": label, "cur": None, "prev": None, "kind": kind,
                         "cur_s": "暂无数据", "prev_s": "—", "change_s": "", "dir": 0})
            continue
        cur, prev = pair
        if not has_cmp:
            shown = f"{cur:,}" if kind == "count" else f"{cur * 100:.2f}%"
            kpis.append({"label": label, "cur": cur, "prev": None, "kind": kind,
                         "cur_s": shown, "prev_s": "—", "change_s": "", "dir": 0})
            continue
        diff = (cur - prev) / prev if prev else 0.0
        if kind == "count":
            kpis.append({"label": label, "cur": cur, "prev": prev, "kind": kind,
                         "cur_s": f"{cur:,}", "prev_s": f"{prev:,}",
                         "change_s": f"{abs(diff) * 100:.2f}%",
                         "dir": 1 if diff >= 0 else -1})
        else:
            diff_pp = (cur - prev) * 100
            kpis.append({"label": label, "cur": cur, "prev": prev, "kind": kind,
                         "cur_s": f"{cur * 100:.2f}%", "prev_s": f"{prev * 100:.2f}%",
                         "change_s": f"{abs(diff_pp):.2f}pp",
                         "dir": 1 if diff_pp >= 0 else -1})
    return kpis


# ================= SEO 页面分析计算 =================
def seo_combined_df(state, include_keyword="", exclude_keyword="", view_mode="合并看板"):
    """
    view_mode: "唯一 PV", "下载转化", "购买转化", "合并看板"
    综合转化率 = (本期下载点击 + 本期购买点击) / 本期唯一 PV * 100%
    """
    seo_pv = state.get("seo")
    seo_dl = state.get("seo_click")
    seo_buy = state.get("seo_buy")
    has_pv = seo_pv is not None and bool(seo_pv.get("cur"))

    all_paths = set()
    if seo_pv: all_paths.update(seo_pv["cur"].keys(), seo_pv["prev"].keys())
    if seo_dl: all_paths.update(seo_dl["cur"].keys(), seo_dl["prev"].keys())
    if seo_buy: all_paths.update(seo_buy["cur"].keys(), seo_buy["prev"].keys())

    filtered_paths = [
        p for p in all_paths
        if (not include_keyword or include_keyword in p) and (not exclude_keyword or exclude_keyword not in p)
    ]

    rows = []
    tot_pv_c, tot_dl_c, tot_buy_c = 0, 0, 0

    for p in filtered_paths:
        pv_c = seo_pv["cur"].get(p, 0) if seo_pv else 0
        pv_p = seo_pv["prev"].get(p, 0) if seo_pv else 0
        dl_c = seo_dl["cur"].get(p, 0) if seo_dl else 0
        dl_p = seo_dl["prev"].get(p, 0) if seo_dl else 0
        b_c = seo_buy["cur"].get(p, 0) if seo_buy else 0
        b_p = seo_buy["prev"].get(p, 0) if seo_buy else 0

        tot_pv_c += pv_c
        tot_dl_c += dl_c
        tot_buy_c += b_c

        pv_diff = ((pv_c - pv_p) / pv_p * 100) if pv_p else None
        dl_diff = ((dl_c - dl_p) / dl_p * 100) if dl_p else None
        b_diff = ((b_c - b_p) / b_p * 100) if b_p else None

        pv_change = format_pct_change(pv_diff)
        dl_change = format_pct_change(dl_diff)
        b_change = format_pct_change(b_diff)

        dl_rate = f"{(dl_c / pv_c * 100):.1f}%" if pv_c else "-"
        buy_rate = f"{(b_c / pv_c * 100):.1f}%" if pv_c else "-"
        combo_rate = f"{((dl_c + b_c) / pv_c * 100):.1f}%" if pv_c else "-"

        row = {"网页路径": p}
        if view_mode == "唯一 PV":
            row.update({"本期PV": pv_c, "上期PV": pv_p, "PV环比": pv_change})
        elif view_mode == "下载转化":
            row.update({"本期下载": dl_c, "上期下载": dl_p, "下载环比": dl_change})
            if has_pv: row["下载转化率"] = dl_rate
        elif view_mode == "购买转化":
            row.update({"本期购买": b_c, "上期购买": b_p, "购买环比": b_change})
            if has_pv: row["购买转化率"] = buy_rate
        else:  # 合并看板
            row.update({
                "本期PV": pv_c, "上期PV": pv_p, "PV环比": pv_change,
                "本期下载": dl_c, "上期下载": dl_p, "下载环比": dl_change,
                "本期购买": b_c, "上期购买": b_p, "购买环比": b_change,
            })
            if has_pv:
                row["下载转化率"] = dl_rate
                row["购买转化率"] = buy_rate
                row["综合转化率"] = combo_rate
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        sort_col = "本期PV" if "本期PV" in df.columns and has_pv else ("本期下载" if "本期下载" in df.columns else df.columns[1])
        df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)

    avg_ctr = ((tot_dl_c + tot_buy_c) / tot_pv_c * 100) if tot_pv_c else 0.0
    return df, len(filtered_paths), tot_pv_c, tot_dl_c, tot_buy_c, avg_ctr, has_pv


_PROD_CANON = {"spotify": "Spotify", "musicone": "MusicOne", "amazc": "Amazon", "amazon": "Amazon",
               "tidal": "Tidal", "apple": "Apple", "youtube": "YouTube", "deezer": "Deezer",
               "videoone": "VideoOne", "audiorecorder": "AudioRecorder"}
_OS_CANON = {"win": "Win", "mac": "Mac", "android": "Android"}


def normalize_seo_product(name):
    """把 'spotify win' / 'spotify安卓' / 'Spotify Win' 统一成 '产品 系统' 的同一写法。"""
    n = str(name).strip()
    if n.endswith("安卓"):
        n = n[:-2] + " android"
    parts = n.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].lower() in _OS_CANON:
        prod = _PROD_CANON.get(parts[0].lower(), parts[0])
        return f"{prod} {_OS_CANON[parts[1].lower()]}"
    return n


def _merge_prod(d):
    out = {"cur": {}, "prev": {}}
    for p in ("cur", "prev"):
        for k, v in (d or {}).get(p, {}).items():
            nk = normalize_seo_product(k)
            out[p][nk] = out[p].get(nk, 0) + v
    return out


def seo_product_summary_df(state):
    """产品线汇总分析（下载点击 + 购买点击按产品聚合）"""
    dl_prods = _merge_prod(state.get("seo_prod_click"))
    buy_prods = _merge_prod(state.get("seo_buy_prod_click"))

    all_p = set(dl_prods.get("cur", {}).keys()) | set(dl_prods.get("prev", {}).keys()) | \
            set(buy_prods.get("cur", {}).keys()) | set(buy_prods.get("prev", {}).keys())

    rows = []
    for p in sorted(all_p):
        d_c = dl_prods.get("cur", {}).get(p, 0)
        d_p = dl_prods.get("prev", {}).get(p, 0)
        b_c = buy_prods.get("cur", {}).get(p, 0)
        b_p = buy_prods.get("prev", {}).get(p, 0)
        rows.append({
            "产品名称": p,
            "本期下载点击": d_c, "上期下载点击": d_p,
            "本期购买点击": b_c, "上期购买点击": b_p,
            "总意向点击": d_c + b_c
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("总意向点击", ascending=False).reset_index(drop=True)
    return df


def buy_all_df(state):
    """汇总全部套餐维度的购买数据，用于多维可视化对比"""
    if not state.get("buy"):
        return None
    rows = []
    for plan, prods in state["buy"].items():
        for prod, vals in prods.items():
            if vals["cur"] > 0 or vals["prev"] > 0:
                rows.append({
                    "产品": prod,
                    "订阅套餐": plan,
                    "本期购买量": vals["cur"],
                    "上期购买量": vals["prev"]
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["差额"] = df["本期购买量"] - df["上期购买量"]
    df["环比变动"] = df.apply(lambda r: format_pct_change(r["差额"] / r["上期购买量"] * 100) if r["上期购买量"] else "-", axis=1)
    return df.sort_values("本期购买量", ascending=False).reset_index(drop=True)


def download_df(state):
    if not state.get("dl"):
        return None
    df = pd.DataFrame([{"产品包": k, "本期": v["cur"], "上期": v["prev"]} for k, v in state["dl"].items()])
    if df.empty:
        return df
    df["差额"] = df["本期"] - df["上期"]
    df["环比变动"] = df.apply(lambda r: format_pct_change((r['差额'] / r['上期'] * 100)) if r['上期'] else "-", axis=1)
    return df.sort_values("本期", ascending=False).reset_index(drop=True)


def buy_df(state, plan_key="lifetime"):
    if not state.get("buy") or plan_key not in state["buy"]:
        return None
    items = state["buy"][plan_key]
    df = pd.DataFrame([{"产品类型": k, "本期": v["cur"], "上期": v["prev"]} for k, v in items.items()])
    if df.empty:
        return df
    df["差额"] = df["本期"] - df["上期"]
    df["环比变动"] = df.apply(lambda r: format_pct_change((r['差额'] / r['上期'] * 100)) if r['上期'] else "-", axis=1)
    return df.sort_values("本期", ascending=False).reset_index(drop=True)


def traffic_df(state):
    if not state.get("tf"):
        return None
    tf = state["tf"]
    channels = list(dict.fromkeys(list(tf.get("cur", {})) + list(tf.get("prev", {}))))
    df = pd.DataFrame([{"渠道": c, "本期": tf["cur"].get(c, 0), "上期": tf["prev"].get(c, 0)} for c in channels])
    if df.empty:
        return df
    df["差额"] = df["本期"] - df["上期"]
    df["环比变动"] = df.apply(lambda r: format_pct_change((r['差额'] / r['上期'] * 100)) if r['上期'] else "-", axis=1)
    return df.sort_values("本期", ascending=False).reset_index(drop=True)


def build_report(state):
    sections = [("核心指标概览", pd.DataFrame([{"指标": k["label"], "本期": k["cur_s"], "上期": k["prev_s"], "环比": k["change_s"]} for k in compute_kpis(state)]))]
    df_dl = download_df(state)
    df_buy = buy_all_df(state)
    df_tf = traffic_df(state)
    df_seo, _, _, _, _, _, _ = seo_combined_df(state, view_mode="合并看板")

    for title, df in [("客户端下载量", df_dl), ("购买量", df_buy), ("渠道流量", df_tf), ("SEO 页面分析", df_seo.head(EXPORT_SEO_TOP))]:
        if df is not None and not df.empty:
            sections.append((title, df))
    return {"title": f"GA4 数据报表 · {state['site']} · {state_label(state)}", "sections": sections}


def export_filename(state, ext):
    tag = state.get("date", {}).get("file_tag", "REPORT")
    return f"GA4_Report_{tag}.{ext}"


def export_csv(report):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([report["title"]])
    for title, df in report["sections"]:
        w.writerow([])
        w.writerow([f"【{title}】"])
        w.writerow(list(df.columns))
        for row in df.itertuples(index=False):
            w.writerow(list(row))
    return buf.getvalue().encode("utf-8-sig")


def export_xlsx(report):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "GA4报表"
    r = 1
    ws.cell(r, 1, report["title"])
    r += 2
    for title, df in report["sections"]:
        ws.cell(r, 1, title)
        r += 1
        ws.append(list(df.columns))
        r += 1
        for row in df.itertuples(index=False):
            ws.append(list(row))
            r += 1
        r += 1
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_pdf(report):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
    from reportlab.lib.styles import ParagraphStyle
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    style = ParagraphStyle("style", fontName="STSong-Light", fontSize=9)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    story = [Paragraph(report["title"], ParagraphStyle("t", fontName="STSong-Light", fontSize=14)), Spacer(1, 10)]
    for title, df in report["sections"]:
        story.append(Paragraph(title, ParagraphStyle("h", fontName="STSong-Light", fontSize=11)))
        data = [[Paragraph(str(c), style) for c in df.columns]]
        for row in df.itertuples(index=False):
            data.append([Paragraph(str(x), style) for x in row])
        story.append(Table(data))
        story.append(Spacer(1, 10))
    doc.build(story)
    return buf.getvalue()


# ================= 规则建议：从未映射项生成建议、试用、生成 rules.py 代码 =================
_INSTALL_EXT = re.compile(r"\.(exe|dmg|apk|msi|pkg|zip)$", re.I)
_GENERIC_TAIL = {"setup", "installer", "install", "converter", "downloader", "recorder", "x64", "x86", "win", "mac"}


def _site_brand(domain):
    d = (domain or "").lower().replace("www.", "")
    return re.sub(r"[^a-z0-9].*$", "", d)  # tunepat.de → tunepat


def _installer_stem(url):
    name = urlparse(url).path.rsplit("/", 1)[-1].lower() or url.lower()
    name = _INSTALL_EXT.sub("", name)
    return re.sub(r"-arm$", "", name), url.lower().split("?")[0].endswith(".apk")


def _guess_product(stem, brand):
    toks = [t for t in stem.split("-") if t]
    while toks and toks[0] in {brand, "nb", "tp"}:
        toks = toks[1:]
    while toks and toks[-1] in _GENERIC_TAIL:
        toks = toks[:-1]
    return "-".join(toks) or stem


def _guess_os(text):
    t = text.lower()
    if "android" in t:
        return "Android"
    if "windows" in t or "-win" in t or "_win" in t:
        return "Win"
    if "mac" in t:
        return "Mac"
    return "未标注"


def _seo_buy_fragment(url):
    segs = [s for s in urlparse(url).path.split("/") if s]
    if not segs:
        return url
    last = re.sub(r"\.html?$", "", segs[-1], flags=re.I)
    if "buynow" in last.lower() and len(segs) >= 2:
        return segs[-2] + "/" + last
    return last


def _guess_buy_plan_token(key):
    toks = [t for t in key.lower().split("_")[1:] if t and t not in rules.OS_TOKENS]
    return "_".join(toks[-2:]) if len(toks) >= 3 else (toks[-1] if toks else key)


def build_rule_suggestions(state):
    """把未映射项整理成最多 4 张可编辑的建议表：installers / buy_products / buy_plans / seo_buy（没有的不出现）。"""
    brand = _site_brand(state.get("site_domain"))
    out = {}
    items = state.get("unmatched") or []

    inst = {}
    for it in items:
        if it["类型"] in ("客户端下载", "SEO下载点击") and str(it["未映射项"]).startswith("http"):
            stem, is_apk = _installer_stem(it["未映射项"])
            row = inst.setdefault(stem, {"文件名主干": stem, "示例文件": it["未映射项"], "本期次数": 0,
                                         "产品显示名": _guess_product(stem, brand), "含安卓包": False})
            row["本期次数"] += it["本期数量"]
            row["含安卓包"] = row["含安卓包"] or is_apk
    if inst:
        out["installers"] = pd.DataFrame(list(inst.values())).sort_values("本期次数", ascending=False).reset_index(drop=True)

    prods = {}
    for it in state.get("unnamed") or []:
        if it["类型"] == "购买产品名":
            row = prods.setdefault(it["未映射项"], {"产品段": it["未映射项"], "示例key": it["示例"],
                                                  "本期次数": 0, "产品显示名": it["未映射项"]})
            row["本期次数"] += it["本期数量"]
    if prods:
        out["buy_products"] = pd.DataFrame(list(prods.values())).sort_values("本期次数", ascending=False).reset_index(drop=True)

    plans = {}
    for it in items:
        if it["类型"] == "购买量":
            tok = _guess_buy_plan_token(it["未映射项"])
            row = plans.setdefault(tok, {"套餐标识": tok, "示例key": it["未映射项"], "本期次数": 0, "套餐": "12M"})
            row["本期次数"] += it["本期数量"]
    if plans:
        out["buy_plans"] = pd.DataFrame(list(plans.values())).sort_values("本期次数", ascending=False).reset_index(drop=True)

    sbuy = {}
    for it in items:
        if it["类型"] == "SEO购买点击" and str(it["未映射项"]).startswith("http"):
            frag = _seo_buy_fragment(it["未映射项"])
            words = re.sub(r"(buynow|windows|win|mac|android|converter|downloader|order|for|html)", " ", frag, flags=re.I)
            words = re.sub(r"[-_/]+", " ", words).split()
            prod = words[0].capitalize() if words else "Product"
            guess = prod + " " + _guess_os(it["未映射项"])
            row = sbuy.setdefault(frag, {"匹配片段": frag, "示例URL": it["未映射项"], "本期次数": 0, "产品+系统": guess})
            row["本期次数"] += it["本期数量"]
    if sbuy:
        out["seo_buy"] = pd.DataFrame(list(sbuy.values())).sort_values("本期次数", ascending=False).reset_index(drop=True)
    return out


def _rows(df, name_col):
    """只取填了名称的行。"""
    if df is None:
        return []
    return [r for _, r in df.iterrows() if str(r.get(name_col, "")).strip()]


def _regex_fragment(frag):
    return str(frag).replace(".", "\\.")


def edits_to_extra_rules(edits):
    """用户在表格里确认后的结果 → 额外规则（仅本次会话生效）。edits: {表名: DataFrame}"""
    extra = {"download": [], "product_map": {}, "plan_map": {}, "seo_buy": []}
    for r in _rows(edits.get("installers"), "产品显示名"):
        extra["download"] += rules.dl(str(r["文件名主干"]), str(r["产品显示名"]).strip(), apk=bool(r.get("含安卓包")))
    for r in _rows(edits.get("buy_products"), "产品显示名"):
        extra["product_map"][str(r["产品段"]).lower()] = str(r["产品显示名"]).strip()
    for r in _rows(edits.get("buy_plans"), "套餐"):
        extra["plan_map"][str(r["套餐标识"]).lower()] = str(r["套餐"]).strip()
    for r in _rows(edits.get("seo_buy"), "产品+系统"):
        extra["seo_buy"].append((_regex_fragment(r["匹配片段"]), str(r["产品+系统"]).strip()))
    return extra


def merge_extra_rules(bundle, extra, site_domain=None):
    """把试用规则并入站点规则包（试用规则优先）。未知品牌站点（非 sidify / noteburner）没写系统时标“未标注”。"""
    b = dict(bundle)
    d = (site_domain or "").lower()
    if d and "sidify" not in d and "noteburner" not in d:
        b["default_os"] = "未标注"
    if not extra:
        return b
    b["download_rules"] = list(extra.get("download", [])) + list(b["download_rules"])
    b["seo_buy_rules"] = list(extra.get("seo_buy", [])) + list(b["seo_buy_rules"])
    b["product_map"] = {**b["product_map"], **extra.get("product_map", {})}
    b["plan_map"] = {**b["plan_map"], **extra.get("plan_map", {})}
    return b


def generate_rules_code(site_domain, edits):
    """生成可复制进 rules.py 的代码：已有站点给“追加到哪个列表”的片段，新品牌站点给完整的新站点模板。"""
    d = (site_domain or "").lower()
    known = "NOTEBURNER" if "noteburner" in d else ("SIDIFY" if "sidify" in d else None)
    brand = _site_brand(d)
    prefix = known or (re.sub(r"[^A-Z0-9]", "", brand.upper()) or "NEWSITE")

    dl_lines = []
    for r in _rows(edits.get("installers"), "产品显示名"):
        apk = ", apk=True" if r.get("含安卓包") else ""
        dl_lines.append('dl("%s", "%s"%s)' % (r["文件名主干"], str(r["产品显示名"]).strip(), apk))
    pm_lines = ['"%s": "%s",' % (str(r["产品段"]).lower(), str(r["产品显示名"]).strip()) for r in _rows(edits.get("buy_products"), "产品显示名")]
    plan_lines = ['"%s": "%s",' % (str(r["套餐标识"]).lower(), str(r["套餐"]).strip()) for r in _rows(edits.get("buy_plans"), "套餐")]
    sb_lines = ['(r"%s", "%s"),' % (_regex_fragment(r["匹配片段"]), str(r["产品+系统"]).strip()) for r in _rows(edits.get("seo_buy"), "产品+系统")]

    if known:
        out = []
        if dl_lines:
            if known == "SIDIFY":
                out.append("# ① 加到 SIDIFY_DOWNLOAD_RULES 列表里（* 号会把 dl() 生成的规则展开）\n" + "\n".join("    *%s," % x for x in dl_lines))
            else:
                out.append("# ① 加到 NOTEBURNER_DOWNLOAD_RULES 末尾（括号内，用 + 连接）\n" + "\n".join("    + %s" % x for x in dl_lines))
        if pm_lines:
            out.append("# ② 加到 %s_PRODUCT_MAP 里\n" % known + "\n".join("    " + x for x in pm_lines))
        if plan_lines:
            out.append("# ③ 加到 PLAN_MAP 里（值只能是 lifetime / 12M / 3M / 1M）\n" + "\n".join("    " + x for x in plan_lines))
        if sb_lines:
            out.append("# ④ 加到 %s_SEO_BUY_RULES 里（靠前的规则优先匹配）\n" % known + "\n".join("    " + x for x in sb_lines))
        return "\n\n".join(out) if out else "# 暂无可生成的规则：请先在上面的表格里填写“产品显示名”等必填项。"

    # —— 新品牌站点：生成完整模板 ——
    if dl_lines:
        dl_block = "(\n    " + "\n    + ".join(dl_lines) + "\n)"
    else:
        dl_block = "[]"
    code = "# ===== %s 规则（新站点：整段加到 rules.py 里 get_rules_for_site 之前）=====\n" % prefix.title()
    code += "%s_DOWNLOAD_RULES = %s\n\n" % (prefix, dl_block)
    code += "%s_PRODUCT_MAP = {\n%s\n}\n\n" % (prefix, "\n".join("    " + x for x in pm_lines))
    code += "%s_SEO_BUY_RULES = [\n%s\n]\n" % (prefix, "\n".join("    " + x for x in sb_lines))
    if plan_lines:
        code += "\n# 另外在 PLAN_MAP 里追加（所有站点共用）：\n" + "\n".join("    " + x for x in plan_lines) + "\n"
    code += "\n# 然后在 get_rules_for_site() 里、`if \"noteburner\" in domain:` 之前加上这一段：\n"
    code += '    if "%s" in domain:\n' % (brand or prefix.lower())
    code += "        return {\n"
    code += '            "download_rules": %s_DOWNLOAD_RULES,\n' % prefix
    code += '            "seo_buy_rules": %s_SEO_BUY_RULES,\n' % prefix
    code += '            "seo_buy_fallback": None,\n'
    code += '            "product_map": %s_PRODUCT_MAP,\n' % prefix
    code += '            "plan_map": PLAN_MAP,\n'
    code += '            "default_os": "未标注",\n'
    code += '            "exclude_pattern": EXCLUDE_URL_PATTERN,\n'
    code += "        }\n"
    return code


def build_ai_prompt(state, suggestions):
    """可复制给任意 AI 的提示词：只含未映射的文件名 / key 和规则格式，不含任何业务数据。"""
    lines = ["站点域名：%s" % (state.get("site_domain") or "未知"), "", "以下是我的 GA4 看板没有识别的项目（括号内是本期次数，仅供判断主次）："]
    spec = [("installers", "安装包文件", "示例文件"), ("buy_products", "购买 key 的产品段", "示例key"),
            ("buy_plans", "购买 key 的套餐段", "示例key"), ("seo_buy", "SEO 购买页 URL", "示例URL")]
    for key, title, col in spec:
        df = suggestions.get(key)
        if df is None:
            continue
        lines.append("\n【%s】" % title)
        lines += ["- %s（%s）" % (r[col], r["本期次数"]) for _, r in df.iterrows()]
    lines += ["", "请按下面的格式，帮我生成 rules.py 的新增规则，并说明每个产品名的判断依据：",
              '1. 安装包：dl("文件名主干", "产品名")，.exe/.dmg/-arm 自动生成，有安卓包加 apk=True',
              '2. 购买 key（buy_产品_系统_套餐）：在 PRODUCT_MAP 里加 "产品段": "显示名"',
              '3. SEO 购买页：(r"URL 匹配片段", "产品 系统")，系统写 Win / Mac / Android',
              "拿不准的产品名请标出来让我确认，不要猜。"]
    return "\n".join(lines)
