import streamlit as st
import os
import re
import csv
import io
import shutil
import tempfile
import openpyxl
from datetime import datetime
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# 页面基础配置
st.set_page_config(page_title="GA4 自动报表与看板系统", layout="wide", page_icon="📊")

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
    "tidal": "Tidal", "apc": "Apple", "apple": "Apple",
    "youtube": "YouTube", "yt": "YouTube", "deezer": "Deezer",
}

PLAN_MAP = {
    "lifetime": "lifetime",
    "one_year": "12M", "12m": "12M", "1_year": "12M",
    "3m": "3M", "three_month": "3M",
    "1m": "1M", "one_month": "1M",
}

TEMPLATE_FILE = os.path.join(os.path.dirname(__file__), "template.xlsx")

# ================= 数据解析函数 =================
def analyze_date_info_from_text(text):
    for line in text.splitlines()[:15]:
        match = re.search(r'#\s*(\d{8})-(\d{8})\s+(\d{8})-(\d{8})', line)
        if match:
            c_start, c_end, p_start, p_end = match.groups()
            d_c_start = datetime.strptime(c_start, "%Y%m%d")
            d_c_end = datetime.strptime(c_end, "%Y%m%d")
            d_p_start = datetime.strptime(p_start, "%Y%m%d")
            d_p_end = datetime.strptime(p_end, "%Y%m%d")
            days_span = (d_c_end - d_c_start).days + 1

            if days_span >= 20:
                report_type = "month"
                file_tag = f"{d_c_start.strftime('%Y%m')}"
                cur_label = f"{d_c_start.month}月"
                prev_label = f"{d_p_start.month}月"
            else:
                report_type = "week"
                file_tag = f"{c_start}-{c_end}"
                cur_label = f"{d_c_start.month}.{d_c_start.day}-{d_c_end.month}.{d_c_end.day}"
                prev_label = f"{d_p_start.month}.{d_p_start.day}-{d_p_end.month}.{d_p_end.day}"

            return {"type": report_type, "days": days_span, "file_tag": file_tag, "cur_label": cur_label, "prev_label": prev_label}
    return {"type": "unknown", "days": 7, "file_tag": "UNKNOWN", "cur_label": "本期", "prev_label": "上期"}

def parse_download_csv_content(text):
    rows = list(csv.reader(text.splitlines()))
    desktop_idx, mobile_idx = 2, 3
    for row in rows[:15]:
        if "设备类别" in row:
            for idx, val in enumerate(row):
                if val.strip().lower() == "desktop": desktop_idx = idx
                elif val.strip().lower() == "mobile": mobile_idx = idx
            break

    cur_period_label, prev_period_label = None, None
    current_key = None
    data = {}

    for row in rows:
        if not row: continue
        first_col = row[0].strip()
        if first_col.startswith('http') or any(first_col.endswith(ext) for ext in ['.exe', '.dmg', '.apk']):
            current_key = first_col

        if len(row) > 1 and " - " in row[1] and "变动百分比" not in row[1]:
            label = row[1].strip()
            if not cur_period_label: cur_period_label = label
            elif not prev_period_label and label != cur_period_label: prev_period_label = label

            if current_key:
                target_col = None
                for pat, col_name in DOWNLOAD_RULES:
                    if re.search(pat, current_key, re.IGNORECASE):
                        target_col = col_name
                        break
                if target_col:
                    col_to_pick = mobile_idx if target_col == "spotify安卓" else desktop_idx
                    raw_val = row[col_to_pick] if len(row) > col_to_pick else "0"
                    val = float(raw_val.replace(',', '')) if raw_val not in ['', '-'] else 0.0

                    if target_col not in data:
                        data[target_col] = {"cur": 0, "prev": 0}
                    if label == cur_period_label:
                        data[target_col]["cur"] += int(val)
                    else:
                        data[target_col]["prev"] += int(val)
    return data

def parse_buy_csv_content(text):
    rows = list(csv.reader(text.splitlines()))
    cur_period_label, prev_period_label = None, None
    current_key = None
    data = {"lifetime": {}, "12M": {}, "3M": {}, "1M": {}}

    for row in rows:
        if not row: continue
        first_col = row[0].strip()
        if first_col.startswith("buy_"):
            current_key = first_col

        if len(row) > 1 and " - " in row[1] and "变动百分比" not in row[1]:
            label = row[1].strip()
            val = float(row[2].replace(',', '')) if len(row) > 2 and row[2] not in ['', '-'] else 0.0

            if not cur_period_label: cur_period_label = label
            elif not prev_period_label and label != cur_period_label: prev_period_label = label

            if current_key:
                parts = current_key.split('_')
                if len(parts) >= 4:
                    raw_prod, raw_os = parts[1].lower(), parts[2].lower()
                    raw_plan = "_".join(parts[3:]).lower()
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
    return data

def parse_traffic_csv_content(text):
    lines = text.splitlines()
    traffic = {"cur": {}, "prev": {}}
    current_period = None

    for line in lines:
        line_str = line.strip()
        if not line_str: continue

        if line_str.startswith("# 开始日期："):
            if current_period is None: current_period = "cur"
            elif current_period == "cur": current_period = "prev"
            continue

        if line_str.startswith('#') or "带来用户首次互动的主渠道组" in line_str:
            continue

        if current_period in ["cur", "prev"]:
            cols = [c.strip() for c in line_str.split(',')]
            if cols and cols[0]:
                channel = cols[0]
                try: users = int(cols[1].replace(',', ''))
                except: users = 0
                traffic[current_period][channel] = users
    return traffic

def generate_report_excel(template_path, date_info, dl_data, buy_data, tf_data):
    """将数据填充回内置的 template.xlsx 并生成内存二进制流供下载"""
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_name = tmp.name

    shutil.copyfile(template_path, tmp_name)
    wb = openpyxl.load_workbook(tmp_name)
    ws = wb.active

    for r in [2, 8, 14, 20, 26]:
        ws.cell(r, 1, date_info["cur_label"])
        ws.cell(r + 1, 1, date_info["prev_label"])

    for c in range(2, 18):
        h = ws.cell(1, c).value
        if h:
            hc = " ".join(str(h).split())
            match = next((v for k, v in dl_data.items() if " ".join(k.split()) == hc), None)
            ws.cell(2, c, match["cur"] if match else 0)
            ws.cell(3, c, match["prev"] if match else 0)

    for plan_key, head_r, cur_r, prev_r in [("lifetime", 7, 8, 9), ("12M", 13, 14, 15), ("3M", 19, 20, 21), ("1M", 25, 26, 27)]:
        cur_plan = buy_data.get(plan_key, {})
        for c in range(2, 16):
            h = ws.cell(head_r, c).value
            if h:
                hc = " ".join(str(h).split())
                match = next((v for k, v in cur_plan.items() if " ".join(k.split()) == hc), None)
                ws.cell(cur_r, c, match["cur"] if match else 0)
                ws.cell(prev_r, c, match["prev"] if match else 0)

    if tf_data:
        for r in range(33, 40):
            ch = str(ws.cell(r, 1).value or "").strip()
            ws.cell(r, 2, tf_data["cur"].get(ch, 0))
            ws.cell(r, 3, tf_data["prev"].get(ch, 0))
        ws.cell(32, 2, sum(tf_data["cur"].values()))
        ws.cell(32, 3, sum(tf_data["prev"].values()))
        days = date_info["days"]
        div = days if date_info["type"] == "month" else 7
        ws.cell(32, 5, f"=B32/{div}")
        ws.cell(32, 6, f"=E32-(C32/{div})")

    wb.save(tmp_name)
    with open(tmp_name, "rb") as f:
        bytes_data = f.read()
    os.remove(tmp_name)
    return bytes_data


# ================= 网页 UI 布局 =================
st.title("📊 GA4 数据可视化看板与报表系统")

# 侧边栏：文件上传与模板状态
with st.sidebar:
    st.header("📂 数据上传区")
    uploaded_files = st.file_uploader(
        "上传 GA4 导出的 CSV 文件（支持多选拖拽）：",
        type=["csv"],
        accept_multiple_files=True
    )
    st.markdown("---")
    st.subheader("📑 报表模板设置")
    if os.path.exists(TEMPLATE_FILE):
        st.success("✅ 网站已预置默认 `template.xlsx` 模板")
    else:
        st.error("❌ 网站目录下未检测到 `template.xlsx`，请放置在同级目录！")

# 核心处理与呈现
if uploaded_files:
    # 自动识别上传的各个 CSV 文件
    dl_text, buy_text, tf_text = None, None, None
    for f in uploaded_files:
        name = f.name.lower()
        content = f.getvalue().decode('utf-8', errors='ignore')
        if "download" in name:
            dl_text = content
        elif "buy" in name:
            buy_text = content
        elif "用户获取" in name or "渠道" in name:
            tf_text = content

    if not dl_text or not buy_text:
        st.warning("请确保上传的文件中包含 `download.csv` 和 `buy.csv`！")
    else:
        # 数据解析
        date_info = analyze_date_info_from_text(dl_text)
        dl_data = parse_download_csv_content(dl_text)
        buy_data = parse_buy_csv_content(buy_text)
        tf_data = parse_traffic_csv_content(tf_text) if tf_text else None

        # 顶部周期信息提示条
        col_info1, col_info2, col_info3 = st.columns(3)
        col_info1.metric("周期类型", f"{date_info['type'].upper()}报")
        col_info2.metric("当前周期", date_info["cur_label"])
        col_info3.metric("对比周期", date_info["prev_label"])

        # 生成并提供 Excel 下载
        if os.path.exists(TEMPLATE_FILE):
            excel_bytes = generate_report_excel(TEMPLATE_FILE, date_info, dl_data, buy_data, tf_data)
            output_name = f"GA4_Report_{date_info['file_tag']}.xlsx"
            st.download_button(
                label=f"📥 导出并下载标准化报表 ({output_name})",
                data=excel_bytes,
                file_name=output_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )

        st.markdown("---")

        # 看板 Tab 切换
        tab1, tab2, tab3 = st.tabs(["📥 客户端下载量看板", "💰 购买量看板", "🌐 渠道流量看板"])

        # --- Tab 1: 下载量 ---
        with tab1:
            dl_df = pd.DataFrame([
                {"产品包": k, f"本期 ({date_info['cur_label']})": v["cur"], f"上期 ({date_info['prev_label']})": v["prev"]}
                for k, v in dl_data.items()
            ])
            dl_df["差额"] = dl_df.iloc[:, 1] - dl_df.iloc[:, 2]
            dl_df["环比变动"] = dl_df.apply(lambda r: f"{(r['差额'] / r.iloc[2] * 100):.2f}%" if r.iloc[2] != 0 else "-", axis=1)

            # 视图切换控件
            chart_type = st.radio("展现方式：", ["柱状对比图", "折线走势图", "纯数据表格"], horizontal=True, key="dl_view")

            if chart_type == "柱状对比图":
                fig = px.bar(
                    dl_df, x="产品包", y=[dl_df.columns[1], dl_df.columns[2]],
                    barmode="group", title="各客户端下载量对比",
                    color_discrete_sequence=["#1976D2", "#90CAF9"]
                )
                st.plotly_chart(fig, use_container_width=True)
            elif chart_type == "折线走势图":
                fig = px.line(
                    dl_df, x="产品包", y=[dl_df.columns[1], dl_df.columns[2]],
                    markers=True, title="各客户端下载量走势对比",
                    color_discrete_sequence=["#1976D2", "#FFA726"]
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.dataframe(dl_df, use_container_width=True)

        # --- Tab 2: 购买量 ---
        with tab2:
            plan_choice = st.selectbox("选择套餐周期：", ["Lifetime", "12M", "3M", "1M"])
            target_plan_data = buy_data.get(plan_choice.lower() if plan_choice != "12M" else "12M", {})

            buy_df = pd.DataFrame([
                {"产品类型": k, f"本期 ({date_info['cur_label']})": v["cur"], f"上期 ({date_info['prev_label']})": v["prev"]}
                for k, v in target_plan_data.items()
            ])
            if not buy_df.empty:
                buy_df["差额"] = buy_df.iloc[:, 1] - buy_df.iloc[:, 2]
                buy_df["环比变动"] = buy_df.apply(lambda r: f"{(r['差额'] / r.iloc[2] * 100):.2f}%" if r.iloc[2] != 0 else "-", axis=1)

                buy_chart_type = st.radio("展现方式：", ["柱状对比图", "折线走势图", "纯数据表格"], horizontal=True, key="buy_view")
                if buy_chart_type == "柱状对比图":
                    fig = px.bar(
                        buy_df, x="产品类型", y=[buy_df.columns[1], buy_df.columns[2]],
                        barmode="group", title=f"{plan_choice} 购买转化量对比",
                        color_discrete_sequence=["#4CAF50", "#A5D6A7"]
                    )
                    st.plotly_chart(fig, use_container_width=True)
                elif buy_chart_type == "折线走势图":
                    fig = px.line(
                        buy_df, x="产品类型", y=[buy_df.columns[1], buy_df.columns[2]],
                        markers=True, title=f"{plan_choice} 购买转化量对比",
                        color_discrete_sequence=["#4CAF50", "#FFB74D"]
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.dataframe(buy_df, use_container_width=True)
            else:
                st.info(f"暂无 {plan_choice} 维度的购买数据")

        # --- Tab 3: 渠道流量 ---
        with tab3:
            if tf_data:
                channels = list(set(list(tf_data["cur"].keys()) + list(tf_data["prev"].keys())))
                tf_df = pd.DataFrame([
                    {"渠道": c, f"本期 ({date_info['cur_label']})": tf_data["cur"].get(c, 0), f"上期 ({date_info['prev_label']})": tf_data["prev"].get(c, 0)}
                    for c in channels
                ])
                tf_df["差额"] = tf_df.iloc[:, 1] - tf_df.iloc[:, 2]
                tf_df["环比变动"] = tf_df.apply(lambda r: f"{(r['差额'] / r.iloc[2] * 100):.2f}%" if r.iloc[2] != 0 else "-", axis=1)

                tf_chart_type = st.radio("展现方式：", ["柱状对比图", "折线走势图", "纯数据表格"], horizontal=True, key="tf_view")
                if tf_chart_type == "柱状对比图":
                    fig = px.bar(
                        tf_df, x="渠道", y=[tf_df.columns[1], tf_df.columns[2]],
                        barmode="group", title="各渠道用户获取量对比",
                        color_discrete_sequence=["#9C27B0", "#CE93D8"]
                    )
                    st.plotly_chart(fig, use_container_width=True)
                elif tf_chart_type == "折线走势图":
                    fig = px.line(
                        tf_df, x="渠道", y=[tf_df.columns[1], tf_df.columns[2]],
                        markers=True, title="各渠道用户获取量对比",
                        color_discrete_sequence=["#9C27B0", "#FF7043"]
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.dataframe(tf_df, use_container_width=True)
            else:
                st.info("未检测到《用户获取情况》CSV 文件，暂不展示渠道数据。")
else:
    st.info("👈 请在左侧侧边栏上传你的 CSV 文件以启动数据看板。")