import hashlib
from datetime import datetime

import plotly.express as px
import streamlit as st

import core

st.set_page_config(page_title="GA4 数据看板", layout="wide", page_icon="📊")

ACCENT = "#E5484D"
UP_COLOR = "#2E9E6B"
MAX_HISTORY = 3

# 四个上传位：key, 显示名, 说明
SLOTS = [
    ("traffic", "用户获取", "用户获取情况 CSV"),
    ("download", "下载量", "download.csv"),
    ("buy", "购买量", "buy.csv"),
    ("seo", "网页和屏幕", "网页和屏幕 CSV"),
]

# ================= 样式 =================
st.markdown(f"""
<style>
.block-container {{ padding-top: 4.5rem; max-width: 1200px; }}
h1, h2, h3 {{ font-weight: 600; letter-spacing: -0.01em; }}
.page-title {{ font-size: 1.35rem; font-weight: 600; line-height: 1.5; margin: 0; padding: 0; }}
.page-sub {{ color: rgba(128,128,128,.95); font-size: .85rem; margin-top: 2px; }}
.pill {{ display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: .8rem; margin-right: 8px; }}
.pill.blue {{ background: rgba(56,139,253,.12); color: #3B82F6; }}
.pill.amber {{ background: rgba(245,158,11,.14); color: #D98E04; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 14px 0 22px; }}
.kpi {{ display: flex; justify-content: space-between; align-items: center; padding: 14px 18px;
        border-radius: 10px; background: rgba(128,128,128,.08); }}
.kpi .kl {{ font-size: .8rem; color: rgba(128,128,128,.95); }}
.kpi .kv {{ font-size: 1.55rem; font-weight: 600; line-height: 1.3; }}
.kpi .kv.na {{ font-size: 1rem; font-weight: 400; color: rgba(128,128,128,.8); }}
.kpi .kd {{ font-size: .85rem; font-weight: 500; }}
.kpi .kd.up {{ color: {UP_COLOR}; }}
.kpi .kd.dn {{ color: {ACCENT}; }}
.kpi .kd.fl {{ color: rgba(128,128,128,.95); }}
.kpi .kp {{ font-size: .72rem; color: rgba(128,128,128,.85); text-align: right; margin-top: 2px; }}
button[data-baseweb="tab"][aria-selected="true"] {{ color: {ACCENT}; }}
div[data-baseweb="tab-highlight"] {{ background-color: {ACCENT}; }}
section[data-testid="stSidebar"] .stFileUploader label p {{ font-weight: 500; font-size: .85rem; }}
.side-label {{ font-size: .72rem; letter-spacing: .04em; color: rgba(128,128,128,.9); margin: 14px 0 4px; }}
.hint {{ font-size: .8rem; padding: 8px 10px; border-radius: 8px; background: rgba(46,158,107,.12); color: {UP_COLOR}; }}
</style>
""", unsafe_allow_html=True)


# ================= 小工具：兼容不同 Streamlit 版本 =================
def show_df(df, **kwargs):
    try:
        st.dataframe(df, hide_index=True, width="stretch", **kwargs)
    except Exception:
        st.dataframe(df, hide_index=True, use_container_width=True, **kwargs)


def show_chart(fig):
    try:
        st.plotly_chart(fig, width="stretch")
    except Exception:
        st.plotly_chart(fig, use_container_width=True)


def style_fig(fig, title):
    fig.update_layout(
        title=dict(text=title, font=dict(size=14), x=0, xanchor="left", y=0.97, yanchor="top"),
        template="plotly_white", margin=dict(l=10, r=10, t=90, b=10),
        legend=dict(orientation="h", x=0, xanchor="left", y=1.02, yanchor="bottom", title=None),
        xaxis_title=None, yaxis_title=None,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,.18)")
    return fig


# ================= Session：历史记录 / 上传版本 =================
st.session_state.setdefault("history", [])      # 最多 3 条 {"id","time","label","sig","state"}
st.session_state.setdefault("view", "live")     # "live" 或 历史记录 id
st.session_state.setdefault("uploader_ver", 0)  # 递增 = 清空全部上传位


def cb_save(state, sig):
    hist = st.session_state.history
    if any(h["sig"] == sig for h in hist):
        return
    hist.insert(0, {"id": sig, "sig": sig, "time": datetime.now().strftime("%m-%d %H:%M"),
                    "label": core.state_label(state), "state": state})
    del hist[MAX_HISTORY:]


def cb_view(hid):
    st.session_state.view = hid


def cb_clear_history():
    st.session_state.history = []
    st.session_state.view = "live"


def cb_clear_uploads():
    st.session_state.uploader_ver += 1
    st.session_state.view = "live"


# ================= 侧边栏：数据上传 =================
texts = {}
with st.sidebar:
    st.markdown("### 数据上传")
    st.caption("四个文件相互独立，上传哪个就生成哪部分，缺少的会自动跳过。")
    ver = st.session_state.uploader_ver
    for key, name, hint in SLOTS:
        f = st.file_uploader(name, type=["csv"], key=f"up_{key}_{ver}", help=hint)
        texts[key] = f.getvalue().decode("utf-8", errors="ignore") if f else None

    n_loaded = sum(1 for v in texts.values() if v)
    if n_loaded:
        st.markdown(f'<div class="hint">已识别 {n_loaded} / {len(SLOTS)} 个文件</div>', unsafe_allow_html=True)
        st.button("清空已上传文件", on_click=cb_clear_uploads, key="btn_clear_up")

live_state = core.build_state(texts)
live_sig = hashlib.md5("".join(texts[k] or "-" for k in core.SLOT_KEYS).encode("utf-8")).hexdigest()[:12] if live_state else None

# 当前展示的快照
state = live_state
viewing_history = False
if st.session_state.view != "live":
    hit = next((h for h in st.session_state.history if h["id"] == st.session_state.view), None)
    if hit:
        state, viewing_history = hit["state"], True
    else:
        st.session_state.view = "live"

with st.sidebar:
    st.markdown('<div class="side-label">历史记录（最多保存 3 次）</div>', unsafe_allow_html=True)
    if live_state is not None and not viewing_history:
        st.button("保存当前分析到历史", on_click=cb_save, args=(live_state, live_sig), key="btn_save")
    if not st.session_state.history:
        st.caption("暂无记录。上传文件后点击上方按钮保存。")
    for h in st.session_state.history:
        active = viewing_history and h["id"] == st.session_state.view
        st.button(f"{h['label']}　{h['time']}", key=f"hist_{h['id']}", on_click=cb_view, args=(h["id"],),
                  type="primary" if active else "secondary")
    if st.session_state.history:
        st.button("清空历史记录", on_click=cb_clear_history, key="btn_clear_hist")

# ================= 主区域 =================
if state is None:
    st.markdown('<p class="page-title">GA4 数据看板</p>', unsafe_allow_html=True)
    st.info("从左侧上传 GA4 导出的 CSV 开始。四个文件可以只传其中一部分。")
    st.stop()

date_info = state["date"]
cur_label, prev_label = date_info["cur_label"], date_info["prev_label"]
report = core.build_report(state)

head_l, head_r = st.columns([5, 3], vertical_alignment="center")
with head_l:
    st.markdown('<p class="page-title">核心指标概览</p>'
                f'<p class="page-sub">{cur_label} vs {prev_label}</p>', unsafe_allow_html=True)
with head_r:
    b1, b2, b3 = st.columns(3)
    b1.download_button("PDF", core.export_pdf(report), core.export_filename(state, "pdf"),
                       mime="application/pdf", key="dl_pdf")
    b2.download_button("XLSX", core.export_xlsx(report), core.export_filename(state, "xlsx"),
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_xlsx")
    b3.download_button("CSV", core.export_csv(report), core.export_filename(state, "csv"),
                       mime="text/csv", key="dl_csv")

if viewing_history:
    c1, c2 = st.columns([6, 2], vertical_alignment="center")
    c1.info("正在查看历史记录。")
    c2.button("返回当前上传", on_click=cb_view, args=("live",), key="btn_back")

ip_value, ip_label = core.daily_ip(state)
pills = f'<span class="pill blue">负责站点 · {state["site"]}</span>'
if ip_value is not None:
    pills += f'<span class="pill amber">{ip_label} · {ip_value:,}</span>'
st.markdown(pills, unsafe_allow_html=True)

# ---- 指标卡：三行两列 ----
cards = []
for k in core.compute_kpis(state):
    if k["cur"] is None:
        cards.append(f'<div class="kpi"><div><div class="kl">{k["label"]}</div>'
                     f'<div class="kv na">暂无数据</div></div></div>')
        continue
    cls = "up" if k["dir"] > 0 else ("dn" if k["dir"] < 0 else "fl")
    arrow = "▲" if k["dir"] > 0 else ("▼" if k["dir"] < 0 else "–")
    cards.append(
        f'<div class="kpi"><div><div class="kl">{k["label"]}</div><div class="kv">{k["cur_s"]}</div></div>'
        f'<div style="text-align:right"><div class="kd {cls}">{arrow} {k["change_s"].lstrip("+-")}</div>'
        f'<div class="kp">上期 {k["prev_s"]}</div></div></div>')
st.markdown(f'<div class="kpi-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


# ---- 通用板块渲染：柱状 / 折线 / 明细表 ----
def render_block(df, label_col, key, title, colors, top_n=None):
    cur_col, prev_col = df.columns[1], df.columns[2]
    view = st.radio("展现方式", ["柱状对比", "折线趋势", "明细表"], horizontal=True,
                    key=key, label_visibility="collapsed", index=2 if key == "seo_view" else 0)
    shown = df.head(top_n) if top_n else df
    if view == "柱状对比":
        fig = px.bar(shown, x=label_col, y=[cur_col, prev_col], barmode="group", color_discrete_sequence=colors)
        fig.update_layout(xaxis_tickangle=-35)
        show_chart(style_fig(fig, title))
    elif view == "折线趋势":
        fig = px.line(shown, x=label_col, y=[cur_col, prev_col], markers=True, color_discrete_sequence=colors)
        fig.update_layout(xaxis_tickangle=-35)
        show_chart(style_fig(fig, title))
    else:
        vmax = int(max(shown[cur_col].max(), 1))
        show_df(shown, column_config={
            cur_col: st.column_config.ProgressColumn(cur_col, format="%d", min_value=0, max_value=vmax),
        })


COLORS = [ACCENT, "#C9CED6"]

tab_dl, tab_buy, tab_tf, tab_seo = st.tabs(["客户端下载量", "购买量", "渠道流量", "SEO 唯一 PV 分析"])

with tab_dl:
    df = core.download_df(state)
    if df is None:
        st.info("未上传 download.csv，这一部分已跳过。")
    elif df.empty:
        st.info("download.csv 中没有匹配到产品包。")
    else:
        render_block(df, "产品包", "dl_view", "各客户端下载量对比", COLORS)

with tab_buy:
    if state["buy"] is None:
        st.info("未上传 buy.csv，这一部分已跳过。")
    else:
        plan_label = st.radio("套餐周期", list(core.PLAN_CHOICES), horizontal=True, key="plan_choice")
        df = core.buy_df(state, core.PLAN_CHOICES[plan_label])
        if df is None or df.empty:
            st.info(f"暂无 {plan_label} 维度的购买数据。")
        else:
            render_block(df, "产品类型", "buy_view", f"{plan_label} 购买量对比", COLORS)

with tab_tf:
    df = core.traffic_df(state)
    if df is None:
        st.info("未上传用户获取情况 CSV，这一部分已跳过。")
    elif df.empty:
        st.info("用户获取文件中没有读到渠道数据。")
    else:
        render_block(df, "渠道", "tf_view", "各渠道用户获取量对比", COLORS)

with tab_seo:
    df = core.seo_df(state)
    if df is None:
        st.info("未上传网页和屏幕 CSV，这一部分已跳过。")
    elif df.empty:
        st.info("网页和屏幕文件中没有读到页面数据。")
    else:
        top_label = st.selectbox("筛选排名范围", ["TOP 10 页面", "TOP 20 页面", "TOP 50 页面", "全部页面"], index=0)
        limit = {"TOP 10 页面": 10, "TOP 20 页面": 20, "TOP 50 页面": 50, "全部页面": len(df)}[top_label]
        render_block(df, "网页路径", "seo_view", f"页面唯一 PV · {top_label}", COLORS, top_n=limit)
