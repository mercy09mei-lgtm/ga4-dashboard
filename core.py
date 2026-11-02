"""GA4 看板核心逻辑：解析 CSV、生成报表数据、导出 CSV / XLSX / PDF。

app.py 只负责界面，所有数据处理都在这里，方便单独测试。
"""
import csv
import io
import re
from datetime import datetime

import pandas as pd

# ================= 业务映射规则 =================
DOWNLOAD_RULES = [
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

PRODUCT_MAP = {
    "sp": "spotify", "spotify": "spotify",
    "all-in-one": "musicOne", "all_in_one": "musicOne", "musicone": "musicOne",
    "az": "amazC", "amazon": "amazC",
    "tidal": "Tidal", "td": "Tidal",
    "apc": "Apple", "apple": "Apple",
    "youtube": "YouTube", "yt": "YouTube",
    "deezer": "Deezer", "dz": "Deezer",
}

PLAN_MAP = {
    "lifetime": "lifetime",
    "one_year": "12M", "12m": "12M", "1_year": "12M",
    "3m": "3M", "three_month": "3M",
    "1m": "1M", "one_month": "1M",
}

# 套餐显示名 -> 内部 key（修复旧版 3M / 1M 被 .lower() 后取不到数据的问题）
PLAN_CHOICES = {"Lifetime": "lifetime", "12M": "12M", "3M": "3M", "1M": "1M"}

UNKNOWN_DATE = {"type": "unknown", "days": 7, "file_tag": "UNKNOWN",
                "cur_label": "本期", "prev_label": "上期"}

EXPORT_SEO_TOP = 50  # 导出时 SEO 页面取前 N 条


# ================= 日期 / 站点 =================
def analyze_date_info_from_text(text):
    for line in text.splitlines()[:15]:
        match = re.search(r'#\s*(\d{8})-(\d{8})\s+(\d{8})-(\d{8})', line)
        if match:
            c_start, c_end, p_start, p_end = match.groups()
            return _build_date_info(c_start, c_end, p_start, p_end)
    return dict(UNKNOWN_DATE)


def analyze_date_info_from_header_dates(text):
    """备用：用户获取 / 网页 CSV 里的“开始日期：/ 结束日期：”两组日期。"""
    starts = re.findall(r'开始日期[：:]\s*(\d{8})', text)
    ends = re.findall(r'结束日期[：:]\s*(\d{8})', text)
    if len(starts) >= 2 and len(ends) >= 2:
        return _build_date_info(starts[0], ends[0], starts[1], ends[1])
    return dict(UNKNOWN_DATE)


def _build_date_info(c_start, c_end, p_start, p_end):
    d_c_start = datetime.strptime(c_start, "%Y%m%d")
    d_c_end = datetime.strptime(c_end, "%Y%m%d")
    d_p_start = datetime.strptime(p_start, "%Y%m%d")
    d_p_end = datetime.strptime(p_end, "%Y%m%d")
    days_span = (d_c_end - d_c_start).days + 1
    if days_span >= 20:
        return {"type": "month", "days": days_span,
                "file_tag": d_c_start.strftime("%Y%m"),
                "cur_label": f"{d_c_start.month}月",
                "prev_label": f"{d_p_start.month}月"}
    return {"type": "week", "days": days_span,
            "file_tag": f"{c_start}-{c_end}",
            "cur_label": f"{d_c_start.month}.{d_c_start.day}-{d_c_end.month}.{d_c_end.day}",
            "prev_label": f"{d_p_start.month}.{d_p_start.day}-{d_p_end.month}.{d_p_end.day}"}


def parse_site_name_from_traffic(text):
    for line in text.splitlines()[:10]:
        match = re.search(r'https?://(?:www\.)?([^/\s\-]+)', line)
        if match:
            raw_host = match.group(1)
            parts = raw_host.split('.')
            if len(parts) >= 2:
                return f"{parts[0].capitalize()}.{parts[1]}"
            return raw_host.capitalize()
    return "Sidify.de"


# ================= CSV 解析（逻辑沿用旧版） =================
def parse_download_csv_content(text):
    rows = list(csv.reader(text.splitlines()))
    desktop_idx, mobile_idx, tablet_idx, totals_idx = 2, 3, None, -1
    for row in rows[:15]:
        if any("设备类别" in str(cell) for cell in row):
            for idx, val in enumerate(row):
                v_clean = val.strip().lower()
                if v_clean == "desktop":
                    desktop_idx = idx
                elif v_clean == "mobile":
                    mobile_idx = idx
                elif v_clean == "tablet":
                    tablet_idx = idx
                elif v_clean in ["totals", "合计"]:
                    totals_idx = idx
            break

    def safe_parse_val(raw_str):
        if not raw_str or raw_str.strip() in ['', '-', 'None']:
            return 0.0
        return float(raw_str.replace(',', '').strip())

    cur_period_label, prev_period_label = None, None
    current_key, label = None, None
    data = {}
    ga4_totals = {"cur": 0, "prev": 0}

    for row in rows:
        if not row:
            continue
        first_col = row[0].strip()
        if first_col.startswith('http') or any(first_col.endswith(ext) for ext in ['.exe', '.dmg', '.apk']):
            current_key = first_col
        if len(row) > 1 and " - " in row[1] and "变动百分比" not in row[1]:
            label = row[1].strip()
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label
            if not current_key and first_col == "":
                tot_val = safe_parse_val(row[totals_idx]) if len(row) > abs(totals_idx) else 0.0
                if label == cur_period_label:
                    ga4_totals["cur"] = int(tot_val)
                else:
                    ga4_totals["prev"] = int(tot_val)
            if current_key:
                target_col = None
                for pat, col_name in DOWNLOAD_RULES:
                    if re.search(pat, current_key, re.IGNORECASE):
                        target_col = col_name
                        break
                if target_col:
                    if target_col == "spotify安卓":
                        m_val = safe_parse_val(row[mobile_idx]) if len(row) > mobile_idx else 0.0
                        t_val = safe_parse_val(row[tablet_idx]) if tablet_idx is not None and len(row) > tablet_idx else 0.0
                        val = m_val + t_val
                    else:
                        val = safe_parse_val(row[desktop_idx]) if len(row) > desktop_idx else 0.0
                    if target_col not in data:
                        data[target_col] = {"cur": 0, "prev": 0}
                    if label == cur_period_label:
                        data[target_col]["cur"] += int(val)
                    else:
                        data[target_col]["prev"] += int(val)
    return data, ga4_totals


def parse_buy_csv_content(text):
    rows = list(csv.reader(text.splitlines()))
    cur_period_label, prev_period_label = None, None
    current_key, label = None, None
    data = {"lifetime": {}, "12M": {}, "3M": {}, "1M": {}}
    ga4_totals = {"cur": 0, "prev": 0}

    for row in rows:
        if not row:
            continue
        first_col = row[0].strip()
        if first_col.startswith("buy_"):
            current_key = first_col
        if len(row) > 1 and " - " in row[1] and "变动百分比" not in row[1]:
            label = row[1].strip()
            raw_v = row[2] if len(row) > 2 else "0"
            val = float(raw_v.replace(',', '')) if raw_v not in ['', '-', 'None'] else 0.0
            if not cur_period_label:
                cur_period_label = label
            elif not prev_period_label and label != cur_period_label:
                prev_period_label = label
            if not current_key and first_col == "":
                if label == cur_period_label:
                    ga4_totals["cur"] = int(val)
                else:
                    ga4_totals["prev"] = int(val)
            if current_key:
                parts = current_key.split('_')
                if len(parts) == 3:
                    raw_prod, raw_os, raw_plan = parts[1].lower(), "win", parts[2].lower()
                elif len(parts) >= 4:
                    raw_prod = parts[1].lower()
                    if parts[2].lower() in ["win", "mac", "android"]:
                        raw_os = parts[2].lower()
                        raw_plan = "_".join(parts[3:]).lower()
                    else:
                        raw_os = "win"
                        raw_plan = "_".join(parts[2:]).lower()
                else:
                    continue
                norm_prod = PRODUCT_MAP.get(raw_prod, raw_prod)
                norm_plan = PLAN_MAP.get(raw_plan, raw_plan)
                target_col = f"{norm_prod} {raw_os}"
                if norm_plan in data:
                    if target_col not in data[norm_plan]:
                        data[norm_plan][target_col] = {"cur": 0, "prev": 0}
                    if label == cur_period_label:
                        data[norm_plan][target_col]["cur"] += int(val)
                    else:
                        data[norm_plan][target_col]["prev"] += int(val)
    return data, ga4_totals


def parse_traffic_csv_content(text):
    traffic = {"cur": {}, "prev": {}}
    current_period = None
    for line in text.splitlines():
        line_str = line.strip()
        if not line_str:
            continue
        if line_str.startswith("# 开始日期："):
            if current_period is None:
                current_period = "cur"
            elif current_period == "cur":
                current_period = "prev"
            continue
        if line_str.startswith('#') or "带来用户首次互动的主渠道组" in line_str:
            continue
        if current_period in ["cur", "prev"]:
            cols = [c.strip() for c in line_str.split(',')]
            if cols and cols[0]:
                try:
                    users = int(cols[1].replace(',', ''))
                except (ValueError, IndexError):
                    users = 0
                traffic[current_period][cols[0]] = users
    return traffic


def parse_seo_pages_csv_content(text):
    """解析《网页和屏幕_网页路径和屏幕类.csv》，提取 SEO 唯一 PV（活跃用户数）。"""
    pages_data = {"cur": {}, "prev": {}}
    current_period = None
    for line in text.splitlines():
        line_str = line.strip()
        if not line_str:
            continue
        if line_str.startswith("# 开始日期："):
            if current_period is None:
                current_period = "cur"
            elif current_period == "cur":
                current_period = "prev"
            continue
        if line_str.startswith('#') or "网页路径和屏幕类" in line_str:
            continue
        if current_period in ["cur", "prev"]:
            cols = [c.strip() for c in line_str.split(',')]
            if cols and cols[0]:
                try:
                    unique_pv = int(cols[2].replace(',', ''))
                except (ValueError, IndexError):
                    unique_pv = 0
                pages_data[current_period][cols[0]] = unique_pv
    return pages_data


# ================= 状态：把已上传的文件合成一份可保存的快照 =================
SLOT_KEYS = ("traffic", "download", "buy", "seo")


def build_state(texts):
    """texts: {"traffic"/"download"/"buy"/"seo": str | None}。没有任何文件时返回 None。"""
    texts = {k: texts.get(k) for k in SLOT_KEYS}
    if not any(texts.values()):
        return None

    date_info = dict(UNKNOWN_DATE)
    for key in ("download", "buy", "traffic", "seo"):
        if not texts[key]:
            continue
        di = analyze_date_info_from_text(texts[key])
        if di["type"] == "unknown":
            di = analyze_date_info_from_header_dates(texts[key])
        if di["type"] != "unknown":
            date_info = di
            break

    state = {
        "date": date_info,
        "site": parse_site_name_from_traffic(texts["traffic"]) if texts["traffic"] else "Sidify.de",
        "dl": None, "dl_tot": None, "buy": None, "buy_tot": None, "tf": None, "seo": None,
        "sources": [k for k in SLOT_KEYS if texts[k]],
    }
    if texts["download"]:
        state["dl"], state["dl_tot"] = parse_download_csv_content(texts["download"])
    if texts["buy"]:
        state["buy"], state["buy_tot"] = parse_buy_csv_content(texts["buy"])
    if texts["traffic"]:
        state["tf"] = parse_traffic_csv_content(texts["traffic"])
    if texts["seo"]:
        state["seo"] = parse_seo_pages_csv_content(texts["seo"])
    return state


def state_label(state):
    d = state["date"]
    return f"{d['cur_label']} vs {d['prev_label']}"


# ================= 指标计算 =================
def _ratio(c, p):
    return (c - p) / p if p else 0.0


def _status(diff, threshold):
    if abs(diff) < threshold:
        return "正常"
    return "上涨" if diff > 0 else "下降"


def dl_totals(state):
    t, d = state["dl_tot"], state["dl"]
    cur = t["cur"] if t["cur"] > 0 else sum(v["cur"] for v in d.values())
    prev = t["prev"] if t["prev"] > 0 else sum(v["prev"] for v in d.values())
    return cur, prev


def buy_totals(state):
    t, b = state["buy_tot"], state["buy"]
    cur = t["cur"] if t["cur"] > 0 else sum(p["cur"] for plan in b.values() for p in plan.values())
    prev = t["prev"] if t["prev"] > 0 else sum(p["prev"] for plan in b.values() for p in plan.values())
    return cur, prev


def daily_ip(state):
    """日均 IP = 本期总流量 / 天数（月报按实际天数，周报按 7 天）。"""
    if not state["tf"]:
        return None, "日均 IP"
    d = state["date"]
    days = d["days"] if d["type"] == "month" else 7
    total = sum(state["tf"]["cur"].values())
    label = "月日均 IP" if d["type"] == "month" else "周日均 IP"
    return (int(round(total / days)) if days > 0 else 0), label


def compute_kpis(state):
    """按界面顺序返回 6 个指标：总流量、自然+引荐 / 下载量、购买事件数 / 下载转化率、购买转化率。
    缺少对应数据文件的指标 cur 为 None。"""
    tf = state["tf"]
    total = nat = dlt = buyt = None
    if tf:
        total = (sum(tf["cur"].values()), sum(tf["prev"].values()))
        nat = (tf["cur"].get("Organic Search", 0) + tf["cur"].get("Referral", 0),
               tf["prev"].get("Organic Search", 0) + tf["prev"].get("Referral", 0))
    if state["dl"] is not None:
        dlt = dl_totals(state)
    if state["buy"] is not None:
        buyt = buy_totals(state)

    dl_rate = buy_rate = None
    if dlt and nat:
        dl_rate = (dlt[0] / nat[0] if nat[0] else 0.0, dlt[1] / nat[1] if nat[1] else 0.0)
    if buyt and dlt:
        buy_rate = (buyt[0] / dlt[0] if dlt[0] else 0.0, buyt[1] / dlt[1] if dlt[1] else 0.0)

    specs = [("总流量", total, "count"), ("自然 + 引荐", nat, "count"),
             ("下载量", dlt, "count"), ("购买事件数", buyt, "count"),
             ("下载转化率", dl_rate, "rate"), ("购买转化率", buy_rate, "rate")]
    kpis = []
    for label, pair, kind in specs:
        if pair is None:
            kpis.append({"label": label, "cur": None, "prev": None, "kind": kind,
                         "cur_s": "暂无数据", "prev_s": "—", "change_s": "", "status": "", "dir": 0})
            continue
        cur, prev = pair
        if kind == "count":
            change = _ratio(cur, prev)
            cur_s, prev_s = f"{cur:,}", f"{prev:,}"
            change_s = f"{change * 100:+.2f}%"
            status = _status(change, 0.005)
        else:
            change = cur - prev
            cur_s, prev_s = f"{cur * 100:.2f}%", f"{prev * 100:.2f}%"
            change_s = f"{change * 100:+.2f}pp"
            status = _status(change, 0.0005)
        kpis.append({"label": label, "cur": cur, "prev": prev, "kind": kind,
                     "cur_s": cur_s, "prev_s": prev_s, "change_s": change_s,
                     "status": status, "dir": 1 if status == "上涨" else (-1 if status == "下降" else 0)})
    return kpis


# ================= 各板块 DataFrame =================
def _cols(state):
    d = state["date"]
    return f"本期 ({d['cur_label']})", f"上期 ({d['prev_label']})"


def _finish(df, cur_col, prev_col):
    """补充 差额 / 环比变动 两列。"""
    df["差额"] = df[cur_col] - df[prev_col]
    df["环比变动"] = df.apply(
        lambda r: f"{(r['差额'] / r[prev_col] * 100):.2f}%" if r[prev_col] != 0 else "-", axis=1)
    return df


def overview_df(state):
    d = state["date"]
    rows = []
    for k in compute_kpis(state):
        rows.append({"指标": k["label"], d["cur_label"]: k["cur_s"], d["prev_label"]: k["prev_s"],
                     "环比": k["change_s"] or "-", "状态": k["status"] or "-"})
    return pd.DataFrame(rows)


def download_df(state):
    if state["dl"] is None:
        return None
    cur_col, prev_col = _cols(state)
    df = pd.DataFrame([{"产品包": k, cur_col: v["cur"], prev_col: v["prev"]} for k, v in state["dl"].items()])
    if df.empty:
        return df
    df = df.sort_values(cur_col, ascending=False).reset_index(drop=True)
    return _finish(df, cur_col, prev_col)


def buy_df(state, plan_key=None):
    """plan_key=None 时返回全部套餐（带“套餐”列），否则只返回该套餐。"""
    if state["buy"] is None:
        return None
    cur_col, prev_col = _cols(state)
    rows = []
    for plan, items in state["buy"].items():
        if plan_key and plan != plan_key:
            continue
        for k, v in items.items():
            row = {} if plan_key else {"套餐": plan}
            row.update({"产品类型": k, cur_col: v["cur"], prev_col: v["prev"]})
            rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return _finish(df, cur_col, prev_col)


def traffic_df(state):
    if state["tf"] is None:
        return None
    tf = state["tf"]
    cur_col, prev_col = _cols(state)
    channels = list(dict.fromkeys(list(tf["cur"]) + list(tf["prev"])))
    df = pd.DataFrame([{"渠道": c, cur_col: tf["cur"].get(c, 0), prev_col: tf["prev"].get(c, 0)} for c in channels])
    if df.empty:
        return df
    df = df.sort_values(cur_col, ascending=False).reset_index(drop=True)
    return _finish(df, cur_col, prev_col)


def seo_df(state, top=None):
    if state["seo"] is None:
        return None
    seo = state["seo"]
    d = state["date"]
    cur_col, prev_col = f"本期唯一PV ({d['cur_label']})", f"上期唯一PV ({d['prev_label']})"
    paths = list(dict.fromkeys(list(seo["cur"]) + list(seo["prev"])))
    df = pd.DataFrame([{"网页路径": p, cur_col: seo["cur"].get(p, 0), prev_col: seo["prev"].get(p, 0)} for p in paths])
    if df.empty:
        return df
    df = df.sort_values(cur_col, ascending=False).reset_index(drop=True)
    df = _finish(df, cur_col, prev_col)
    return df.head(top) if top else df


def build_report(state):
    """导出用：按 核心指标 → 下载 → 购买 → 渠道 → SEO 的固定顺序返回板块列表，缺数据的板块自动跳过。"""
    sections = [("核心指标概览", overview_df(state))]
    for title, df in [("客户端下载量", download_df(state)),
                      ("购买量", buy_df(state)),
                      ("渠道流量", traffic_df(state)),
                      (f"SEO 唯一 PV（活跃用户）分析 · TOP {EXPORT_SEO_TOP}", seo_df(state, EXPORT_SEO_TOP))]:
        if df is not None and not df.empty:
            sections.append((title, df))
    return {"title": f"GA4 数据报表 · {state['site']} · {state_label(state)}", "sections": sections}


# ================= 导出 =================
def export_filename(state, ext):
    return f"GA4_Report_{state['date']['file_tag']}.{ext}"


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
    return buf.getvalue().encode("utf-8-sig")  # BOM，Excel 打开不乱码


def export_xlsx(report):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "GA4报表"
    ws.sheet_view.showGridLines = False
    accent = "E5484D"
    thin = Side(style="thin", color="E5E7EB")

    ws.cell(1, 1, report["title"]).font = Font(size=14, bold=True)
    r = 3
    max_cols = 1
    widths = {}
    for title, df in report["sections"]:
        ws.cell(r, 1, title).font = Font(size=12, bold=True, color=accent)
        r += 1
        for c, name in enumerate(df.columns, start=1):
            cell = ws.cell(r, c, name)
            cell.font = Font(bold=True, color="374151")
            cell.fill = PatternFill("solid", fgColor="F3F4F6")
            cell.border = Border(bottom=thin)
            cell.alignment = Alignment(horizontal="left" if c == 1 else "right")
            widths[c] = max(widths.get(c, 0), len(str(name)) * 2)
        r += 1
        for row in df.itertuples(index=False):
            for c, val in enumerate(row, start=1):
                if hasattr(val, "item"):
                    val = val.item()
                cell = ws.cell(r, c, val)
                cell.border = Border(bottom=thin)
                cell.alignment = Alignment(horizontal="left" if c == 1 else "right")
                if isinstance(val, int):
                    cell.number_format = "#,##0"
                widths[c] = max(widths.get(c, 0), min(len(str(val)) * 1.2, 60))
            r += 1
        max_cols = max(max_cols, len(df.columns))
        r += 2
    for c in range(1, max_cols + 1):
        ws.column_dimensions[chr(64 + c)].width = max(12, min(widths.get(c, 12) + 2, 60))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))  # 内置中文字体，无需额外字体文件
    font = "STSong-Light"
    accent = colors.HexColor("#E5484D")
    grey = colors.HexColor("#6B7280")
    line = colors.HexColor("#E5E7EB")

    h1 = ParagraphStyle("h1", fontName=font, fontSize=16, leading=22, textColor=colors.HexColor("#111827"))
    h2 = ParagraphStyle("h2", fontName=font, fontSize=12, leading=16, textColor=accent, spaceBefore=14, spaceAfter=6)
    cell = ParagraphStyle("cell", fontName=font, fontSize=8.5, leading=11, textColor=colors.HexColor("#111827"))
    head = ParagraphStyle("head", parent=cell, textColor=grey)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=report["title"])
    width = A4[0] - 32 * mm
    story = [Paragraph(report["title"], h1), Spacer(1, 4)]

    def esc(v):
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    for title, df in report["sections"]:
        story.append(Paragraph(esc(title), h2))
        data = [[Paragraph(esc(c), head) for c in df.columns]]
        for row in df.itertuples(index=False):
            cells = []
            for i, v in enumerate(row):
                if isinstance(v, (int,)) or hasattr(v, "item") and isinstance(v.item(), int):
                    v = f"{int(v):,}"
                cells.append(Paragraph(esc(v), cell))
            data.append(cells)
        lens = [max(6, min(48, max(len(str(c)) * 1.6, *(len(str(x)) for x in df[c])))) for c in df.columns]
        total = sum(lens)
        table = Table(data, colWidths=[width * x / total for x in lens], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, line),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(table)
    doc.build(story)
    return buf.getvalue()
