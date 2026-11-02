"""GA4 看板 Web 界面：支持批量上传识别、SEO 四栏看板与产品线对比图表。"""
import hashlib
from datetime import datetime
import plotly.express as px
import streamlit as st
import core

st.set_page_config(page_title="GA4 数据看板", layout="wide", page_icon="📊")

ACCENT = "#E5484D"
UP_COLOR = "#2E9E6B"
MAX_HISTORY = 3

st.markdown(f"""
<style>
.block-container {{ padding-top: 2rem; max-width: 1250px; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 500; }}
.badge-green {{ background: #DCFCE7; color: #166534; }}
.badge-gray {{ background: #F3F4F6; color: #4B5563; }}
.kpi-card {{ border-radius: 8px; background: #FAFAFA; padding: 12px 18px; border: 1px solid #F0F0F0; }}
.kpi-title {{ font-size: 0.85rem; color: #6B7280; }}
.kpi-value {{ font-size: 1.6rem; font-weight: 700; line-height: 1.2; margin-top: 4px; }}
.kpi-delta-up {{ color: {UP_COLOR}; font-size: 0.85rem; font-weight: 600; }}
.kpi-delta-down {{ color: {ACCENT}; font-size: 0.85rem; font-weight: 600; }}
button[data-baseweb="tab"][aria-selected="true"] {{ color: {ACCENT} !important; border-bottom-color: {ACCENT} !important; }}
</style>
""", unsafe_allow_html=True)

st.session_state.setdefault("history", [])
st.session_state.setdefault("view", "live")
st.session_state.setdefault("path_inc", "/guide/")
st.session_state.setdefault("path_exc", "/de/")

# ================= 侧边栏：批量/单份上传 =================
texts = {"traffic": None, "download": None, "buy": None, "seo": None, "seo_click": None, "seo_buy": None}
file_badges = []

with st.sidebar:
    st.markdown("### 数据上传（支持批量/单份）")
    uploaded_files = st.file_uploader(
        "一次拖入多个 CSV\n自动识别文件类型",
        type=["csv"],
        accept_multiple_files=True,
        help="支持同时拖入用户获取、download、buy、网页和屏幕、SEO-download、SEO-buy文件",
        key="batch_uploader"
    )

    if uploaded_files:
        for f in uploaded_files:
            content = f.getvalue().decode("utf-8", errors="ignore")
            slot_key, label_name = core.detect_file_type(content, f.name)
            if slot_key != "unknown":
                texts[slot_key] = content
                file_badges.append((f.name, label_name, "badge-green"))
            else:
                file_badges.append((f.name, "未识别", "badge-gray"))

        st.markdown("---")
        for fn, lbl, cls in file_badges:
            cols = st.columns([7, 3])
            cols[0].caption(fn[:16] + "..." if len(fn) > 16 else fn)
            cols[1].markdown(f'<span class="badge {cls}">{lbl}</span>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("##### 历史记录 (最多 3 条)")
    live_state = core.build_state(texts)
    live_sig = hashlib.md5("".join(texts[k] or "-" for k in core.SLOT_KEYS).encode("utf-8")).hexdigest()[:8] if live_state else None

    if live_state and st.session_state.view == "live":
        if st.button("保存当前分析到历史", key="btn_save"):
            if not any(h["id"] == live_sig for h in st.session_state.history):
                st.session_state.history.insert(0, {
                    "id": live_sig,
                    "date_lbl": live_state["date"]["cur_label"],
                    "state": live_state,
                    "time": datetime.now().strftime("%m/%d")
                })
                del st.session_state.history[MAX_HISTORY:]

    for h in st.session_state.history:
        btn_type = "primary" if st.session_state.view == h["id"] else "secondary"
        cols = st.columns([7, 3])
        if cols[0].button(h["date_lbl"], key=f"hist_{h['id']}", type=btn_type):
            st.session_state.view = h["id"]
        cols[1].caption(h["time"])

    if st.session_state.history:
        if st.button("清空历史记录", key="clear_hist"):
            st.session_state.history = []
            st.session_state.view = "live"

# ================= 确定当前展示 State =================
state = live_state
if st.session_state.view != "live":
    hit = next((h for h in st.session_state.history if h["id"] == st.session_state.view), None)
    if hit: state = hit["state"]

if state is None:
    st.title("GA4 转化与 SEO 看板")
    st.info("👈 请在左侧上传 GA4 导出的 CSV 文件。即使只传 1 份文件（如仅传 SEO 下载或购买数据）也能正常展示相应板块。")
    st.stop()

# ================= 顶栏 =================
col_title, col_export = st.columns([7, 3])
with col_title:
    st.markdown("### 核心指标概览")
    st.caption(f"🗓️ **{state['date']['detail_label']}** · 站点: {state['site']}")
with col_export:
    b1, b2, b3 = st.columns(3)
    rep = core.build_report(state)
    b1.download_button("PDF", core.export_pdf(rep), core.export_filename(state, "pdf"), mime="application/pdf")
    b2.download_button("XLSX", core.export_xlsx(rep), core.export_filename(state, "xlsx"))
    b3.download_button("CSV", core.export_csv(rep), core.export_filename(state, "csv"), mime="text/csv")

# ================= KPI 卡片 =================
kpis = core.compute_kpis(state)
kpi_cols = st.columns(len(kpis))
for i, k in enumerate(kpis):
    with kpi_cols[i]:
        if k["cur"] is None:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-title">{k['label']}</div>
                <div class="kpi-value" style="font-size:1.2rem;color:#9CA3AF;">待传数据</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            arrow = "▲" if k["dir"] >= 0 else "▼"
            color_cls = "kpi-delta-up" if k["dir"] >= 0 else "kpi-delta-down"
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-title">{k['label']}</div>
                <div class="kpi-value">{k['cur_s']} <span class="{color_cls}">{arrow} {k['change_s']}</span></div>
            </div>
            """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ================= 各板块 Tab =================
t_dl, t_buy, t_tf, t_seo = st.tabs(["客户端下载量", "购买量", "渠道流量", "SEO 页面分析"])

with t_dl:
    df_dl = core.download_df(state)
    if df_dl is not None and not df_dl.empty:
        st.dataframe(df_dl, hide_index=True, use_container_width=True)
    else:
        st.info("未上传客户端下载数据文件（download.csv）。")

with t_buy:
    df_buy = core.buy_df(state, "lifetime")
    if df_buy is not None and not df_buy.empty:
        st.dataframe(df_buy, hide_index=True, use_container_width=True)
    else:
        st.info("未上传主站购买量数据（buy.csv）。SEO 页面的购买点击请在「SEO 页面分析」选项卡中查看。")

with t_tf:
    df_tf = core.traffic_df(state)
    if df_tf is not None and not df_tf.empty:
        st.dataframe(df_tf, hide_index=True, use_container_width=True)
    else:
        st.info("未上传用户获取渠道数据文件。")

with t_seo:
    # 1. 筛选条件栏
    f_col1, f_col2, f_col3 = st.columns([3, 3, 4])
    with f_col1:
        inc_kw = st.text_input("路径包含：", value=st.session_state.path_inc, key="in_inc")
    with f_col2:
        exc_kw = st.text_input("排除：", value=st.session_state.path_exc, key="in_exc")
    with f_col3:
        st.write("快捷筛选：")
        q1, q2, q3 = st.columns(3)
        if q1.button("/guide/"):
            st.session_state.path_inc = "/guide/"
            st.rerun()
        if q2.button("/blog/"):
            st.session_state.path_inc = "/blog/"
            st.rerun()
        if q3.button("全部"):
            st.session_state.path_inc = ""
            st.session_state.path_exc = ""
            st.rerun()

    # 2. 四栏看板选择
    c_m1, c_m2 = st.columns([6, 4])
    with c_m1:
        view_mode = st.radio("看板栏目", ["唯一 PV", "下载转化", "购买转化", "合并看板"], horizontal=True, index=3)
    with c_m2:
        top_filter = st.selectbox("显示行数", ["TOP 10 页面", "TOP 20 页面", "TOP 50 页面", "全部"], index=0)

    # 3. 聚合数据计算
    df_seo, matched_pages, tot_pv, tot_dl, tot_buy_clicks, avg_combo_rate, has_pv = core.seo_combined_df(
        state, include_keyword=inc_kw, exclude_keyword=exc_kw, view_mode=view_mode
    )

    # 4. 缺 PV 友好黄色提示
    if not has_pv:
        st.warning("⚠️ 未检测到网页 PV 数据（未上传《网页和屏幕.csv》），转化率暂时无法计算，当前仅展示点击量。")

    # 5. 指标摘要栏
    pv_summary = f"唯一 PV 合计 <b>{tot_pv:,}</b>" if has_pv else "唯一 PV <b>未上传</b>"
    rate_summary = f"综合转化率 <b>{avg_combo_rate:.1f}%</b>" if has_pv else "转化率 <b>暂不可用</b>"
    st.markdown(f"""
    <div style="display:flex; gap:16px; margin: 10px 0; font-size:0.9rem; background:#F9FAFB; padding:8px 12px; border-radius:6px;">
        <span>匹配 <b>{matched_pages}</b> 个页面</span>
        <span>{pv_summary}</span>
        <span>下载点击合计 <b>{tot_dl:,}</b></span>
        <span>购买点击合计 <b>{tot_buy_clicks:,}</b></span>
        <span>{rate_summary}</span>
    </div>
    """, unsafe_allow_html=True)

    # 6. 表格渲染
    if not df_seo.empty:
        limit_map = {"TOP 10 页面": 10, "TOP 20 页面": 20, "TOP 50 页面": 50, "全部": len(df_seo)}
        st.dataframe(df_seo.head(limit_map[top_filter]), hide_index=True, use_container_width=True)
    else:
        st.info("未匹配到符合筛选条件的 SEO 页面数据。")

    # 7. 可视化图表拓展
    st.markdown("---")
    st.markdown("#### 📊 SEO 趋势与对比分析")
    chart_col1, chart_col2 = st.columns([5, 5])
    with chart_col1:
        dim_choice = st.radio("分析维度", ["方案 1：按 TOP N 页面横向对比", "方案 2：按产品线汇总对比"], horizontal=True)
    with chart_col2:
        chart_type = st.radio("图表类型", ["柱状图", "折线图"], horizontal=True)

    if dim_choice.startswith("方案 1"):
        if not df_seo.empty:
            show_n = 10 if top_filter == "TOP 10 页面" else (20 if top_filter == "TOP 20 页面" else 30)
            df_chart = df_seo.head(show_n).copy()
            # 缩短 URL 便于坐标轴展示
            df_chart["短路径"] = df_chart["网页路径"].apply(lambda x: x.split('/')[-1] or x)

            # 动态选择对比列
            y_cols = [c for c in ["本期PV", "本期下载", "本期购买", "本期下载点击", "本期购买点击"] if c in df_chart.columns]
            if y_cols:
                if chart_type == "柱状图":
                    fig = px.bar(df_chart, x="短路径", y=y_cols, barmode="group",
                                 title=f"页面指标横向对比 (TOP {show_n})", color_discrete_sequence=[ACCENT, "#3B82F6", "#10B981"])
                else:
                    fig = px.line(df_chart, x="短路径", y=y_cols, markers=True,
                                  title=f"页面指标趋势对比 (TOP {show_n})", color_discrete_sequence=[ACCENT, "#3B82F6", "#10B981"])
                fig.update_layout(xaxis_tickangle=-35, margin=dict(t=50, b=50))
                st.plotly_chart(fig, use_container_width=True)
    else:
        df_prod = core.seo_product_summary_df(state)
        if not df_prod.empty:
            if chart_type == "柱状图":
                fig = px.bar(df_prod, x="产品名称", y=["本期下载点击", "本期购买点击"], barmode="stack",
                             title="各产品线 SEO 点击分布（下载 + 购买）", color_discrete_sequence=["#3B82F6", ACCENT])
            else:
                fig = px.line(df_prod, x="产品名称", y=["本期下载点击", "本期购买点击"], markers=True,
                              title="各产品线 SEO 点击分布趋势", color_discrete_sequence=["#3B82F6", ACCENT])
            fig.update_layout(xaxis_tickangle=-25, margin=dict(t=50, b=50))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("暂无可聚合的产品线点击数据。")
