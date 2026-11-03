"""GA4 看板核心逻辑：解析 CSV、规则匹配、计算指标、导出报表。"""
import csv
import io
import re
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
import rules

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
        match = re.search(r'#\s*(\d{8})-(\d{8})\s+(\d{8})-(\d{8})', line)
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
    return dict(UNKNOWN_DATE)


def _build_date_info(c_start, c_end, p_start, p_end):
    try:
        d_c_s = datetime.strptime(c_start, "%Y%m%d")
        d_c_e = datetime.strptime(c_end, "%Y%m%d")
        d_p_s = datetime.strptime(p_start, "%Y%m%d")
        d_p_e = datetime.strptime(p_end, "%Y%m%d")
        cur_days = (d_c_e - d_c_s).days + 1
        prev_days = (d_p_e - d_p_s).days + 1

        cur_lbl = f"{d_c_s.strftime('%m.%d')}-{d_c_e.strftime('%m.%d')}"
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
    head = "\n".join(content.splitlines()[:20])
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
def parse_download_csv_content(text, rule_bundle):
    """解析客户端 download.csv"""
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

    cur_period_label, prev_period_label = None, None
    current_key = None
    data, ga4_totals = {}, {"cur": 0, "prev": 0}

    for row in rows:
        if not row:
            continue
        first_col = row[0].strip()
        if first_col.startswith('http') or any(first_col.endswith(ext) for ext in ['.exe', '.dmg', '.apk']):
            current_key = first_col

        date_col_idx, label = _extract_date_label_and_col(row)
        if date_col_idx is not None:
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label

            if not current_key and first_col == "":
                tot_val = safe_parse_val(row[totals_idx]) if len(row) > abs(totals_idx) else 0.0
                ga4_totals["cur" if label == cur_period_label else "prev"] = int(tot_val)

            if current_key:
                target_col = None
                for pat, col_name in download_rules:
                    if re.search(pat, current_key, re.IGNORECASE):
                        target_col = col_name
                        break
                if target_col:
                    if target_col == "spotify安卓":
                        m = safe_parse_val(row[mobile_idx]) if len(row) > mobile_idx else 0.0
                        t = safe_parse_val(row[tablet_idx]) if tablet_idx and len(row) > tablet_idx else 0.0
                        val = m + t
                    else:
                        val = safe_parse_val(row[desktop_idx]) if len(row) > desktop_idx else 0.0
                    if target_col not in data:
                        data[target_col] = {"cur": 0, "prev": 0}
                    data[target_col]["cur" if label == cur_period_label else "prev"] += int(val)
    return data, ga4_totals


def parse_buy_csv_content(text, rule_bundle):
    """解析真正的订单结算表 buy.csv"""
    if not text:
        return None, None
    prod_map = rule_bundle["product_map"]
    plan_map = rule_bundle["plan_map"]
    rows = list(csv.reader(text.splitlines()))
    cur_period_label, prev_period_label = None, None
    current_key = None
    data = {"lifetime": {}, "12M": {}, "3M": {}, "1M": {}}
    ga4_totals = {"cur": 0, "prev": 0}

    for row in rows:
        if not row:
            continue
        first_col = row[0].strip()
        if first_col.startswith("buy_"):
            current_key = first_col

        date_col_idx, label = _extract_date_label_and_col(row)
        if date_col_idx is not None:
            val = safe_parse_val(row[date_col_idx + 1]) if len(row) > date_col_idx + 1 else 0.0
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label

            if not current_key and first_col == "":
                ga4_totals["cur" if label == cur_period_label else "prev"] = int(val)
            if current_key:
                parts = current_key.split('_')
                if len(parts) >= 3:
                    raw_prod = parts[1].lower()
                    raw_os = parts[2].lower() if len(parts) >= 4 and parts[2].lower() in ["win", "mac", "android"] else "win"
                    raw_plan = "_".join(parts[3:] if raw_os != "win" else parts[2:]).lower()
                else:
                    raw_prod, raw_os, raw_plan = "spotify", "win", "lifetime"
                target_col = f"{prod_map.get(raw_prod, raw_prod)} {raw_os}"
                norm_plan = plan_map.get(raw_plan, "lifetime")
                if norm_plan in data:
                    if target_col not in data[norm_plan]:
                        data[norm_plan][target_col] = {"cur": 0, "prev": 0}
                    data[norm_plan][target_col]["cur" if label == cur_period_label else "prev"] += int(val)
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


def parse_seo_pages_csv_content(text):
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
                pages_data[current_period][cols[0]] = int(safe_parse_val(cols[2])) if len(cols) > 2 else 0
    return pages_data


def parse_seo_download_clicks_csv(text, rule_bundle):
    """
    解析 seo-download.csv：
    1. 取最后一列【合计】数值，不单独区分 desktop/mobile
    2. 对单页面挂载的多个安装包下载量进行累加
    """
    if not text:
        return None, None, None
    download_rules = rule_bundle["download_rules"]
    rows = list(csv.reader(text.splitlines()))
    cur_period_label, prev_period_label = None, None
    current_path, current_file = None, None
    clicks_data = {"cur": {}, "prev": {}}
    prod_clicks = {"cur": {}, "prev": {}}
    total_clicks = {"cur": 0, "prev": 0}

    for row in rows:
        if not row:
            continue
        if len(row) > 1 and row[0].startswith("http"):
            current_file = row[0].strip()
        if len(row) > 1 and row[1].startswith("http"):
            current_path = urlparse(row[1].strip()).path

        date_col_idx, label = _extract_date_label_and_col(row)
        if date_col_idx is not None:
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label

            p_key = "cur" if label == cur_period_label else "prev"
            # 严格读取最后一列【合计】
            val = int(safe_parse_val(row[-1])) if len(row) > 0 else 0

            if not current_path and row[0].strip() == "":
                total_clicks[p_key] = val
            elif current_path:
                clicks_data[p_key][current_path] = clicks_data[p_key].get(current_path, 0) + val
                target_prod = "Other"
                if current_file:
                    for pat, prod_name in download_rules:
                        if re.search(pat, current_file, re.IGNORECASE):
                            target_prod = prod_name
                            break
                prod_clicks[p_key][target_prod] = prod_clicks[p_key].get(target_prod, 0) + val

    return clicks_data, prod_clicks, total_clicks


def parse_seo_buy_clicks_csv(text, rule_bundle):
    """解析 SEO-buy.csv 提取用户在 SEO 页面点击购买（click_buy）的数据"""
    if not text:
        return None, None, None
    seo_buy_rules = rule_bundle["seo_buy_rules"]
    rows = list(csv.reader(text.splitlines()))
    cur_period_label, prev_period_label = None, None
    current_path, current_buynow = None, None
    clicks_data = {"cur": {}, "prev": {}}
    prod_clicks = {"cur": {}, "prev": {}}
    total_clicks = {"cur": 0, "prev": 0}

    for row in rows:
        if not row:
            continue
        if len(row) > 1 and row[0].startswith("http"):
            current_buynow = row[0].strip()
        if len(row) > 1 and row[1].startswith("http"):
            current_path = urlparse(row[1].strip()).path

        date_col_idx, label = _extract_date_label_and_col(row)
        if date_col_idx is not None:
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label

            p_key = "cur" if label == cur_period_label else "prev"
            val = int(safe_parse_val(row[date_col_idx + 1])) if len(row) > date_col_idx + 1 else 0

            if not current_path and row[0].strip() == "":
                total_clicks[p_key] = val
            elif current_path:
                clicks_data[p_key][current_path] = clicks_data[p_key].get(current_path, 0) + val
                target_prod = "Other Buynow"
                if current_buynow:
                    for pat, p_name in seo_buy_rules:
                        if re.search(pat, current_buynow, re.IGNORECASE):
                            target_prod = p_name
                            break
                prod_clicks[p_key][target_prod] = prod_clicks[p_key].get(target_prod, 0) + val

    return clicks_data, prod_clicks, total_clicks


# ================= 状态汇总 =================
def build_state(texts):
    if not any(texts.values()):
        return None

    date_info = dict(UNKNOWN_DATE)
    site_domain = None
    for k in SLOT_KEYS:
        txt = texts.get(k)
        if not txt:
            continue
        if not site_domain:
            site_domain = parse_site_domain(txt)
        if date_info["type"] == "unknown":
            date_info = analyze_date_info_from_text(txt)

    rule_bundle = rules.get_rules_for_site(site_domain)

    state = {
        "date": date_info,
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
        state["dl"], state["dl_tot"] = parse_download_csv_content(texts["download"], rule_bundle)
    if texts.get("buy"):
        state["buy"], state["buy_tot"] = parse_buy_csv_content(texts["buy"], rule_bundle)
    if texts.get("traffic"):
        state["tf"] = parse_traffic_csv_content(texts["traffic"])
    if texts.get("seo"):
        state["seo"] = parse_seo_pages_csv_content(texts["seo"])
    if texts.get("seo_click"):
        state["seo_click"], state["seo_prod_click"], state["seo_click_tot"] = parse_seo_download_clicks_csv(texts["seo_click"], rule_bundle)
    if texts.get("seo_buy"):
        state["seo_buy"], state["seo_buy_prod_click"], state["seo_buy_tot"] = parse_seo_buy_clicks_csv(texts["seo_buy"], rule_bundle)

    return state


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

    specs = [
        ("总流量", total_users, "count"),
        ("下载量", dlt, "count"),
        ("购买事件数", buyt, "count"),
        ("下载转化率", dl_rate, "rate"),
    ]
    kpis = []
    for label, pair, kind in specs:
        if pair is None:
            kpis.append({"label": label, "cur": None, "prev": None, "kind": kind,
                         "cur_s": "暂无数据", "prev_s": "—", "change_s": "", "dir": 0})
            continue
        cur, prev = pair
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


def seo_product_summary_df(state):
    """产品线汇总分析（下载点击 + 购买点击按产品聚合）"""
    dl_prods = state.get("seo_prod_click") or {"cur": {}, "prev": {}}
    buy_prods = state.get("seo_buy_prod_click") or {"cur": {}, "prev": {}}

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
    df_buy = buy_df(state)
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