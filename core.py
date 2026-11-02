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
SLOT_KEYS = ("traffic", "download", "buy", "seo", "seo_click")


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
            "type": "custom",
            "cur_days": cur_days,
            "prev_days": prev_days,
            "file_tag": f"{c_start}_{c_end}",
            "cur_label": cur_lbl,
            "prev_label": prev_lbl,
            "detail_label": detail_lbl,
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


# ================= 文件类型自动探测 =================
def detect_file_type(content: str, filename: str = ""):
    head = "\n".join(content.splitlines()[:20])
    fn = filename.lower()
    if "seo转化计算-下载" in head or "seo-download" in fn:
        return "seo_click", "下载点击"
    if "seo转化计算-buy" in head or ("seo" in fn and "buy" in fn):
        return "buy", "购买量"
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


# ================= CSV 解析函数（单文件全面兼容） =================
def parse_download_csv_content(text, rule_bundle):
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
        if not row: continue
        first_col = row[0].strip()
        if first_col.startswith('http') or any(first_col.endswith(ext) for ext in ['.exe', '.dmg', '.apk']):
            current_key = first_col

        # 动态探测日期列（兼容普通 download.csv 与带网页位置的 csv）
        date_col_idx = None
        for idx in range(min(4, len(row))):
            if " - " in row[idx] and "变动百分比" not in row[idx]:
                date_col_idx = idx
                break

        if date_col_idx is not None:
            label = row[date_col_idx].strip()
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
    """支持 buy.csv 和 SEO-buy.csv 解析"""
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
        if not row: continue
        first_col = row[0].strip()
        if first_col.startswith("buy_") or "buynow" in first_col:
            current_key = first_col

        date_col_idx = None
        for idx in range(min(4, len(row))):
            if " - " in row[idx] and "变动百分比" not in row[idx]:
                date_col_idx = idx
                break

        if date_col_idx is not None:
            label = row[date_col_idx].strip()
            # 活跃用户值通常跟在日期列之后
            val = safe_parse_val(row[date_col_idx + 1]) if len(row) > date_col_idx + 1 else 0.0

            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label

            if not current_key and first_col == "":
                ga4_totals["cur" if label == cur_period_label else "prev"] += int(val)

            if current_key:
                # 解析 URL 或 buy_ 事件名称
                if "buynow" in current_key:
                    # 从 URL 提取产品与系统: 如 /spotify-windows/buynow.html
                    matched_prod = "spotify"
                    matched_os = "win" if "windows" in current_key else ("mac" if "mac" in current_key else "android")
                    for p_k in prod_map:
                        if p_k in current_key.lower():
                            matched_prod = prod_map[p_k]
                            break
                    target_col = f"{matched_prod} {matched_os}"
                    norm_plan = "lifetime"
                else:
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
    if not text:
        return None
    traffic = {"cur": {}, "prev": {}}
    current_period = None
    for line in text.splitlines():
        l_str = line.strip()
        if not l_str: continue
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
    if not text:
        return None
    pages_data = {"cur": {}, "prev": {}}
    current_period = None
    for line in text.splitlines():
        l_str = line.strip()
        if not l_str: continue
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


def parse_seo_download_clicks_csv(text):
    if not text:
        return None, None
    rows = list(csv.reader(text.splitlines()))
    cur_period_label, prev_period_label = None, None
    current_path = None
    clicks_data = {"cur": {}, "prev": {}}
    total_clicks = {"cur": 0, "prev": 0}

    for row in rows:
        if not row: continue
        if len(row) > 1 and row[1].startswith("http"):
            current_path = urlparse(row[1].strip()).path

        date_col_idx = None
        for idx in range(min(4, len(row))):
            if " - " in row[idx] and "变动百分比" not in row[idx]:
                date_col_idx = idx
                break

        if date_col_idx is not None:
            label = row[date_col_idx].strip()
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label
            p_key = "cur" if label == cur_period_label else "prev"
            val = int(safe_parse_val(row[-1])) if len(row) > 0 else 0
            if not current_path and row[0].strip() == "":
                total_clicks[p_key] = val
            elif current_path:
                clicks_data[p_key][current_path] = clicks_data[p_key].get(current_path, 0) + val
    return clicks_data, total_clicks


# ================= 状态汇总 =================
def build_state(texts):
    if not any(texts.values()):
        return None

    date_info = dict(UNKNOWN_DATE)
    site_domain = None
    for k in SLOT_KEYS:
        txt = texts.get(k)
        if not txt: continue
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
        "seo_click": None, "seo_click_tot": None,
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
        state["seo_click"], state["seo_click_tot"] = parse_seo_download_clicks_csv(texts["seo_click"])

    return state


def state_label(state):
    return state.get("date", {}).get("detail_label", "本期 vs 上期")


# ================= 指标计算 =================
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


# ================= SEO 页面分析合并表 =================
def seo_combined_df(state, include_keyword="", exclude_keyword="", view_mode="合并查看"):
    seo_pv = state.get("seo")
    seo_click = state.get("seo_click")
    if not seo_pv and not seo_click:
        return pd.DataFrame(), 0, 0, 0, 0.0

    all_paths = set()
    if seo_pv:
        all_paths.update(seo_pv["cur"].keys(), seo_pv["prev"].keys())
    if seo_click:
        all_paths.update(seo_click["cur"].keys(), seo_click["prev"].keys())

    filtered_paths = []
    for p in all_paths:
        if include_keyword and include_keyword not in p:
            continue
        if exclude_keyword and exclude_keyword in p:
            continue
        filtered_paths.append(p)

    rows = []
    tot_pv_cur, tot_click_cur = 0, 0
    for p in filtered_paths:
        pv_c = seo_pv["cur"].get(p, 0) if seo_pv else 0
        pv_p = seo_pv["prev"].get(p, 0) if seo_pv else 0
        clk_c = seo_click["cur"].get(p, 0) if seo_click else 0
        clk_p = seo_click["prev"].get(p, 0) if seo_click else 0

        tot_pv_cur += pv_c
        tot_click_cur += clk_c

        pv_change = f"{((pv_c - pv_p) / pv_p * 100):+.1f}%" if pv_p else "-"
        clk_change = f"{((clk_c - clk_p) / clk_p * 100):+.1f}%" if clk_p else "-"
        ctr = f"{(clk_c / pv_c * 100):.1f}%" if pv_c else "0.0%"

        row = {"网页路径": p}
        if view_mode in ["仅唯一 PV", "合并查看"]:
            row.update({"本期PV": pv_c, "上期PV": pv_p, "PV环比": pv_change})
        if view_mode in ["仅下载点击", "合并查看"]:
            row.update({"本期点击": clk_c, "上期点击": clk_p, "点击环比": clk_change})
        if view_mode == "合并查看":
            row["点击率"] = ctr
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        sort_col = "本期PV" if "本期PV" in df.columns else "本期点击"
        df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)

    avg_ctr = (tot_click_cur / tot_pv_cur * 100) if tot_pv_cur else 0.0
    return df, len(filtered_paths), tot_pv_cur, tot_click_cur, avg_ctr


def download_df(state):
    if not state.get("dl"): return None
    df = pd.DataFrame([{"产品包": k, "本期": v["cur"], "上期": v["prev"]} for k, v in state["dl"].items()])
    if df.empty: return df
    df["差额"] = df["本期"] - df["上期"]
    df["环比变动"] = df.apply(lambda r: f"{(r['差额'] / r['上期'] * 100):.2f}%" if r["上期"] else "-", axis=1)
    return df.sort_values("本期", ascending=False).reset_index(drop=True)


def buy_df(state, plan_key="lifetime"):
    if not state.get("buy") or plan_key not in state["buy"]: return None
    items = state["buy"][plan_key]
    df = pd.DataFrame([{"产品类型": k, "本期": v["cur"], "上期": v["prev"]} for k, v in items.items()])
    if df.empty: return df
    df["差额"] = df["本期"] - df["上期"]
    df["环比变动"] = df.apply(lambda r: f"{(r['差额'] / r['上期'] * 100):.2f}%" if r["上期"] else "-", axis=1)
    return df.sort_values("本期", ascending=False).reset_index(drop=True)


def traffic_df(state):
    if not state.get("tf"): return None
    tf = state["tf"]
    channels = list(dict.fromkeys(list(tf.get("cur", {})) + list(tf.get("prev", {}))))
    df = pd.DataFrame([{"渠道": c, "本期": tf["cur"].get(c, 0), "上期": tf["prev"].get(c, 0)} for c in channels])
    if df.empty: return df
    df["差额"] = df["本期"] - df["上期"]
    df["环比变动"] = df.apply(lambda r: f"{(r['差额'] / r['上期'] * 100):.2f}%" if r["上期"] else "-", axis=1)
    return df.sort_values("本期", ascending=False).reset_index(drop=True)


def build_report(state):
    sections = [("核心指标概览", pd.DataFrame([{"指标": k["label"], "本期": k["cur_s"], "上期": k["prev_s"], "环比": k["change_s"]} for k in compute_kpis(state)]))]
    df_dl = download_df(state)
    df_buy = buy_df(state)
    df_tf = traffic_df(state)
    df_seo, _, _, _, _ = seo_combined_df(state, view_mode="合并查看")

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
