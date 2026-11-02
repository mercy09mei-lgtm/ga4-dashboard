# ================= 修复后的 CSV 日期识别辅助函数 =================
def _extract_date_label_and_col(row):
    """严格匹配日期格式，过滤掉注释行中的' - '干扰"""
    if not row or str(row[0]).strip().startswith('#'):
        return None, None
    for idx in range(min(4, len(row))):
        val = str(row[idx]).strip()
        # 必须同时满足包含横杠且包含中文日期或年份，排除掉 "# xxx.de - GA4"
        if " - " in val and "变动百分比" not in val and re.search(r'\d+月|\d{4}年|\d{8}', val):
            return idx, val
    return None, None


# ================= 修复后的 SEO 下载点击解析 =================
def parse_seo_download_clicks_csv(text, rule_bundle):
    """
    解析 seo-download.csv：
    1. 取最后一列的【合计】值（不区分 desktop 和 mobile）
    2. 对同一页面的多个下载包进行汇总累加
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
        # 记录安装包 URL 和 网页位置 URL
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
            # 严格取最后一列【合计】数据，记该页面的下载总量
            val = int(safe_parse_val(row[-1])) if len(row) > 0 else 0

            if not current_path and row[0].strip() == "":
                total_clicks[p_key] = val
            elif current_path:
                clicks_data[p_key][current_path] = clicks_data[p_key].get(current_path, 0) + val
                # 产品线维度聚合
                target_prod = "Other"
                if current_file:
                    for pat, prod_name in download_rules:
                        if re.search(pat, current_file, re.IGNORECASE):
                            target_prod = prod_name
                            break
                prod_clicks[p_key][target_prod] = prod_clicks[p_key].get(target_prod, 0) + val

    return clicks_data, prod_clicks, total_clicks


# ================= 修复后的 SEO 购买点击解析 =================
def parse_seo_buy_clicks_csv(text, rule_bundle):
    """解析 SEO-buy.csv 提取购买点击"""
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
            # 购买活跃用户通常在日期列的下一列
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


# ================= 补全合并看板列并计算转化率 =================
def seo_combined_df(state, include_keyword="", exclude_keyword="", view_mode="合并看板"):
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

        pv_change = f"{((pv_c - pv_p) / pv_p * 100):+.1f}%" if pv_p else "-"
        dl_change = f"{((dl_c - dl_p) / dl_p * 100):+.1f}%" if dl_p else "-"
        b_change = f"{((b_c - b_p) / b_p * 100):+.1f}%" if b_p else "-"

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
