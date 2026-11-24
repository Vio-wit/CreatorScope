"""课程实训报告生成。

把本机 SQLite 里已保存的采集批次、字段覆盖、统计结果和评论分析整理成一份可直接
提交的 Markdown / Word 报告。报告只使用库里已有的原始值与清洗结果：缺字段时写明
缺口，不用 0 或推断值补齐，也不把描述统计写成总体结论。
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any

from .analytics import TYPE_LABELS, analytics_data, dashboard_data, notes_data, overview_data
from .db import connection
from .quality import batch_quality_report, coverage_by_field

REPORT_TITLE = "数据采集与处理综合实训报告"

SENTIMENT_LABELS = ("正向倾向", "中性 / 未命中词典", "负向倾向", "混合倾向", "未分类（空文本）")

# 字段观察状态的中文显示；原始状态名同时保留在数据里。
STATE_LABELS = {
    "captured": "已捕获",
    "not_displayed": "页面未展示",
    "not_loaded": "未加载",
    "access_denied": "访问受限",
    "parse_failed": "解析失败",
    "not_attempted": "未尝试",
    "not_applicable": "不适用",
}


def state_text(states: dict[str, Any] | None) -> str:
    return "、".join(f"{STATE_LABELS.get(key, key)} {value}" for key, value in (states or {}).items())


STATUS_LABELS = {
    "completed": "已完成",
    "running": "运行中",
    "queued": "排队中",
    "paused": "已暂停",
    "waiting_user": "等待处理",
    "stopping": "正在停止",
    "stopped": "已停止",
    "failed": "失败",
    "error": "失败",
}


def status_label(value: Any) -> str:
    return STATUS_LABELS.get(str(value or ""), str(value or "—"))


def coverage_by_type(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 dashboard 的逐记录字段覆盖按记录类型累加，便于在报告里排一张小表。"""
    grouped: dict[str, dict[str, Any]] = {}
    for item in records:
        key = str(item.get("type") or "")
        entry = grouped.setdefault(key, {
            "type": key,
            "label": type_label(key),
            "record_count": 0,
            "field_count": 0,
            "present_count": 0,
        })
        entry["record_count"] += 1
        entry["field_count"] += int(item.get("field_count") or 0)
        entry["present_count"] += int(item.get("present_count") or 0)
    for entry in grouped.values():
        entry["coverage_pct"] = (
            round(entry["present_count"] * 100 / entry["field_count"], 1) if entry["field_count"] else 0.0
        )
    return sorted(grouped.values(), key=lambda item: item["type"])

# analytics 里没有登记的中文名，只在报告里补显示用。
EXTRA_TYPE_LABELS = {
    "xhs_public_note": "小红书公开笔记",
    "comment_snapshot": "评论快照",
}

# 报告末尾固定声明的口径与合规边界。
BASE_LIMITATIONS = (
    "本报告只描述本机已保存的样本，样本量有限，不能代表平台整体或总体分布。",
    "缺失字段不填 0，非精确值（如“10+”“1.2w”）不折算成精确数值；原始显示值与清洗结果分开保存。",
    "数据包含本机保存的采集与导入记录；来源及采集方法应结合各批次原始记录核实。",
    "评论情感倾向由小型词典与规则给出，属于探索线索，没有人工标注金标准，不能据此判断个人真实情绪。",
    "各来源、各统计周期的指标口径不同，不跨来源合并计算，也不把时间先后解释为因果关系。",
)

AVAILABILITY_LABELS = {
    "history": "多批次趋势",
    "correlation": "指标相关性",
    "audience": "受众画像",
    "word_frequency": "词频",
    "read_rankings": "阅读量排行",
    "metric_trends": "笔记指标趋势",
    "metric_distributions": "指标分布",
    "interaction_boxplot": "互动箱线图",
}

# 受众画像按来源与统计周期分组，维度与 analytics 里的字段名一一对应。
AUDIENCE_DIMENSIONS = (
    ("性别", "gender_distribution"),
    ("年龄", "age_distribution"),
    ("地域", "regions"),
    ("兴趣", "interests"),
    ("设备", "devices"),
)


def type_label(key: str) -> str:
    return TYPE_LABELS.get(key) or EXTRA_TYPE_LABELS.get(key) or key


def _batch_info(db_path: Path, batch_id: str) -> dict[str, Any] | None:
    with connection(db_path) as conn:
        row = conn.execute(
            """SELECT batch_id, source, status, captured_at, started_at, completed_at,
                      error_count, record_count
               FROM batches WHERE batch_id = ?""",
            (batch_id,),
        ).fetchone()
    return dict(row) if row else None


def _comment_bundle(db_path: Path, batch_id: str | None) -> dict[str, Any] | None:
    """评论分析住在 research/ 包内，按需导入；没有评论时返回 None。"""
    try:
        from research.r4_3_comment_analysis.comment_analysis import fetch_comments, summarize
        from research.r4_3_comment_analysis.sentiment import analyze_comments
    except ImportError:  # pragma: no cover - 源码快照完整时不会走到这里
        return None
    filters = {"batch_id": batch_id} if batch_id else {}
    rows = fetch_comments(db_path, filters)
    if not rows:
        return None
    return {"summary": summarize(rows), "sentiment": analyze_comments(rows)}


def _limitations(analytics: dict[str, Any], notes: dict[str, Any], comments: dict[str, Any] | None) -> list[str]:
    """只把真正描述“当前数据不足”的可用性说明写进局限章节。"""
    items = list(BASE_LIMITATIONS)
    availability = analytics.get("availability") or {}
    note_availability = notes.get("availability") or {}
    for key in ("correlation", "history", "audience", "word_frequency"):
        text = str(availability.get(key) or "").strip()
        if text:
            items.append(f"{AVAILABILITY_LABELS.get(key, key)}：{text}")
    trend_text = str(note_availability.get("metric_trends") or "").strip()
    if trend_text:
        items.append(f"{AVAILABILITY_LABELS['metric_trends']}：{trend_text}")
    if comments is None:
        items.append("当前范围没有已保存的可见评论，评论分析章节为空。")
    return items


def audience_rows(bundle: dict[str, Any], *, per_dimension: int = 5) -> list[dict[str, Any]]:
    """把受众画像压平成可直接排表的行；只保留来源页面真的给出的分类值。"""
    rows: list[dict[str, Any]] = []
    for group in bundle["analytics"].get("charts", {}).get("audience_groups") or []:
        for profile in group.get("profiles") or []:
            for dimension, key in AUDIENCE_DIMENSIONS:
                for entry in (profile.get(key) or [])[:per_dimension]:
                    rows.append({
                        "source": group.get("source"),
                        "period": group.get("period"),
                        "creator": profile.get("creator_name"),
                        "dimension": dimension,
                        "label": entry.get("label"),
                        "value": entry.get("value"),
                        "raw": entry.get("raw_value"),
                    })
    return rows


def _batch_overview(db_path: Path, batch_id: str) -> dict[str, Any]:
    quality = batch_quality_report(db_path, batch_id)
    if quality is None:
        raise LookupError("采集批次不存在")
    counts = quality["counts"]
    with connection(db_path) as conn:
        sources = [dict(row) for row in conn.execute(
            """SELECT source, SUM(record_count) AS record_count, SUM(comment_count) AS comment_count
               FROM (SELECT COALESCE(NULLIF(r.source, ''), b.source) AS source, COUNT(*) AS record_count, 0 AS comment_count
                     FROM records r JOIN batches b ON b.batch_id=r.batch_id WHERE r.batch_id=? GROUP BY 1
                     UNION ALL
                     SELECT COALESCE(NULLIF(s.source, ''), b.source), 0, COUNT(*)
                     FROM comment_snapshots s JOIN batches b ON b.batch_id=s.batch_id WHERE s.batch_id=? GROUP BY 1)
               GROUP BY source ORDER BY (SUM(record_count)+SUM(comment_count)) DESC, source""",
            (batch_id, batch_id),
        ).fetchall()]
    for item in sources:
        item["total_count"] = item["record_count"] + item["comment_count"]
    total = sum(item["total_count"] for item in sources)
    for item in sources:
        item["share_pct"] = round(item["total_count"] * 100 / total, 1) if total else None
    return {"counts": {"creators": counts["creators"], "notes": counts["notes"], "comments": counts["comments"], "batches": 1}, "sources": sources}


def build_report(
    db_path: Path,
    *,
    batch_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """汇总一份报告所需的全部数据；不做任何写库操作。"""
    db_path = Path(db_path)
    generated = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    batch = _batch_info(db_path, batch_id) if batch_id else None
    if batch_id and batch is None:
        raise LookupError("采集批次不存在")
    overview = _batch_overview(db_path, batch_id) if batch_id else overview_data(db_path)
    dashboard = dashboard_data(db_path, batch_id=batch_id, all_batches=not batch_id)
    analytics = analytics_data(db_path, batch_id=batch_id)
    notes = notes_data(db_path, batch_id=batch_id)
    quality = batch_quality_report(db_path, batch_id) if batch_id else None
    coverage = [] if batch_id else coverage_by_field(db_path)
    comments = _comment_bundle(db_path, batch_id)
    type_summary = [
        {**item, "label": type_label(str(item.get("type")))}
        for item in (dashboard.get("type_summary") or [])
    ]
    observation_rows = [
        {**item, "record_label": type_label(str(item.get("record_type")))}
        for item in coverage
    ]
    if quality:
        quality["status_label"] = status_label(quality.get("status"))
        quality["task_status_label"] = status_label(quality.get("task_status"))
        for item in quality.get("field_coverage") or []:
            item["state_text"] = state_text(item.get("states"))
            if item.get("displayed_unavailable_count"):
                item["state_text"] += f"；页面显示缺失 {item['displayed_unavailable_count']}"
            item["record_label"] = type_label(str(item.get("record_type")))
    bundle = {
        "title": REPORT_TITLE,
        "generated_at": generated,
        "database_name": db_path.name,
        "batch_id": batch_id,
        "batch": batch,
        "scope": "当前批次" if batch_id else "全部历史快照",
        "overview": overview,
        "counts": dashboard.get("counts") or {},
        "kpis": dashboard.get("kpis") or {},
        "type_summary": type_summary,
        "field_coverage": coverage_by_type(dashboard.get("field_coverage") or []),
        "analytics": analytics,
        "notes": notes,
        "quality": quality,
        "coverage": observation_rows,
        "comments": comments,
        "limitations": _limitations(analytics, notes, comments),
    }
    bundle["audience_rows"] = audience_rows(bundle)
    return bundle


# ---------------------------------------------------------------- Markdown 渲染


def _fmt_number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".") if value != int(value) else f"{int(value):,}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _fmt_percent(value: Any) -> str:
    return "—" if value is None else f"{_fmt_number(value)}%"


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_当前范围没有可展示的记录。_\n"
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join([" --- " for _ in headers]) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt_number(cell) for cell in row) + " |")
    return "\n".join(lines) + "\n"


def _md_distribution(items: list[dict[str, Any]], name_key: str = "name", value_key: str = "value") -> list[list[Any]]:
    return [[item.get(name_key), item.get(value_key)] for item in items or []]


def _md_quality_section(bundle: dict[str, Any]) -> list[str]:
    quality = bundle.get("quality")
    parts = ["## 二、数据质量与字段覆盖\n"]
    if quality:
        counts = quality.get("counts") or {}
        parts.append(
            _md_table(
                ["项目", "数值"],
                [
                    ["批次", quality.get("batch_id")],
                    ["来源", quality.get("source")],
                    ["状态", quality.get("status")],
                    ["开始时间", quality.get("started_at")],
                    ["完成时间", quality.get("completed_at")],
                    ["耗时（秒）", quality.get("elapsed_seconds")],
                    ["记录行", counts.get("records")],
                    ["创作者", counts.get("creators")],
                    ["笔记", counts.get("notes")],
                    ["评论", counts.get("comments")],
                    ["重复记录", counts.get("duplicate_records")],
                    ["失败数", counts.get("failures")],
                    ["数值解析成功率（%）", (quality.get("metrics") or {}).get("parse_success_pct")],
                ],
            )
        )
        rows = [
            [
                type_label(str(item.get("record_type"))),
                item.get("field_name"),
                item.get("expected_records"),
                item.get("captured_records"),
                item.get("coverage_pct"),
                item.get("state_text") or state_text(item.get("states")),
            ]
            for item in (quality.get("field_coverage") or [])[:25]
        ]
        parts.append("### 2.1 字段覆盖率（前 25 个字段）\n")
        parts.append(_md_table(["记录类型", "字段", "应有记录", "已捕获", "覆盖率（%）", "字段状态分布"], rows))
        return parts

    rows = [
        [type_label(str(item.get("type"))), item.get("record_count"), item.get("field_count"), item.get("present_count"), item.get("coverage_pct")]
        for item in (bundle.get("field_coverage") or [])
    ]
    parts.append("### 2.1 按记录类型的字段覆盖\n")
    parts.append(_md_table(["记录类型", "记录数", "字段数", "非空字段数", "非空占比（%）"], rows))
    observation_rows = [
        [item.get("record_label") or type_label(str(item.get("record_type"))), item.get("field_name"), item.get("observed"), item.get("captured"), item.get("share_pct")]
        for item in (bundle.get("coverage") or [])[:25]
    ]
    if observation_rows:
        parts.append("### 2.2 字段状态观测汇总\n")
        parts.append(
            "分母是该字段实际产生过状态观察的记录数；不同批次的对象数不同，"
            "因此这里只给观测占比，不合并成单一覆盖率。\n"
        )
        parts.append(_md_table(["记录类型", "字段", "观测记录", "已捕获", "捕获占比（%）"], observation_rows))
    return parts


def _md_notes_sections(bundle: dict[str, Any]) -> list[str]:
    notes = bundle["notes"]
    parts: list[str] = []
    text_analysis = notes.get("text_analysis") or {}
    body = text_analysis.get("body") or {}
    frequencies = body.get("frequencies") or []
    parts.append("### 4.3 笔记正文词频\n")
    if frequencies:
        parts.append(f"按 {body.get('sample_count', 0)} 条可用正文分词，取前 15 个高频词（jieba 分词，已除停用词）。\n")
        parts.append(_md_table(["词项", "出现次数"], [[item.get("word"), item.get("count")] for item in frequencies[:15]]))
    else:
        parts.append("_当前范围没有可用的笔记正文，词频章节为空。_\n")

    rows: list[list[Any]] = []
    for group in notes.get("charts", {}).get("top_read") or []:
        for item in (group.get("items") or [])[:5]:
            rows.append([group.get("source"), item.get("label"), item.get("value")])
    parts.append("### 4.4 阅读量排行\n")
    parts.append(_md_table(["来源", "笔记", "阅读量"], rows) if rows else "_当前范围没有可比较的阅读量数值。_\n")
    return parts


def _md_comment_sections(bundle: dict[str, Any]) -> list[str]:
    comments = bundle["comments"]
    if not comments:
        return ["## 五、评论分析\n", "_当前范围没有已保存的可见评论。_\n"]
    summary = comments["summary"]["summary"]
    sentiment = comments["sentiment"]["summary"]
    parts = [
        "## 五、评论分析\n",
        "### 5.1 评论规模\n",
        _md_table(
            ["指标", "数值"],
            [
                ["已保存评论", summary.get("comment_count")],
                ["一级评论", summary.get("root_count")],
                ["回复", summary.get("reply_count")],
                ["涉及笔记", summary.get("note_count")],
                ["涉及创作者", summary.get("creator_count")],
                ["置顶评论", summary.get("pinned_count")],
                ["平均原文长度（字符）", summary.get("average_length")],
                ["可精确转换的点赞值", summary.get("like_count_available")],
                ["可精确转换的回复数", summary.get("reply_count_exact_available")],
            ],
        ),
        f"口径说明：{summary.get('scope_notice', '')}\n",
        "### 5.2 情感倾向（词典规则）\n",
        _md_table(
            ["倾向", "条数"],
            [[label, (sentiment.get("sentiment_counts") or {}).get(label, 0)] for label in SENTIMENT_LABELS],
        ),
        f"方法说明：{sentiment.get('method', '')}\n",
        "### 5.3 高频词与 TF-IDF 关键词\n",
    ]
    words = (comments["summary"].get("top_words") or [])[:15]
    parts.append(_md_table(["词项", "出现次数"], [[item.get("word"), item.get("count")] for item in words]) if words else "")
    tfidf = (sentiment.get("tfidf_keywords") or [])[:10]
    if tfidf:
        parts.append(_md_table(["关键词", "TF-IDF 分数"], [[item.get("word"), item.get("score")] for item in tfidf]))
    return parts


def render_markdown(bundle: dict[str, Any]) -> str:
    """把报告数据渲染成 Markdown 文本（可直接粘进 Word 或转 PDF）。"""
    overview = bundle["overview"]
    counts = overview.get("counts", {})
    analytics = bundle["analytics"]
    charts = analytics.get("charts", {})
    notes = bundle["notes"]

    lines: list[str] = [f"# {bundle['title']}\n"]
    lines.append(
        _md_table(
            ["项目", "内容"],
            [
                ["生成时间", bundle["generated_at"]],
                ["本地数据库", bundle["database_name"]],
                ["数据范围", bundle["scope"]],
                ["批次来源", (bundle.get("batch") or {}).get("source") or "全部批次"],
                ["创作者", counts.get("creators")],
                ["笔记", counts.get("notes")],
                ["评论", counts.get("comments")],
                ["采集批次", counts.get("batches")],
            ],
        )
    )

    lines.append("## 一、数据集与采集方法\n")
    lines.append(
        "本报告汇总本机已保存的记录。数据可能来自可见页面采集或历史导入；"
        "请结合下方来源及原始批次记录核实采集方法。\n"
    )
    lines.append("### 1.1 数据来源构成\n")
    lines.append(
        _md_table(
            ["来源", "已保存行数", "占比（%）"],
            [[item.get("source"), item.get("total_count"), item.get("share_pct")] for item in overview.get("sources") or []],
        )
    )
    lines.append("### 1.2 记录类型构成\n")
    lines.append(
        _md_table(
            ["记录类型", "行数"],
            [[type_label(str(item.get("type"))), item.get("count")] for item in bundle.get("type_summary") or []],
        )
    )

    lines.extend(_md_quality_section(bundle))

    lines.append("## 三、创作者与受众\n")
    lines.append("### 3.1 粉丝数分布\n")
    lines.append(_md_table(["粉丝区间", "创作者数"], _md_distribution(charts.get("follower_distribution"))))
    lines.append("### 3.2 商业报价类型\n")
    lines.append(_md_table(["报价类型", "条数"], _md_distribution(charts.get("quote_status_distribution"))))
    lines.append("### 3.3 创作者地域\n")
    lines.append(_md_table(["地域", "创作者数"], _md_distribution(charts.get("location_distribution"))))
    lines.append("### 3.4 受众画像\n")
    audience = bundle.get("audience_rows") or []
    if audience:
        lines.append(
            _md_table(
                ["来源", "统计周期", "创作者", "维度", "分类", "占比（%）"],
                [
                    [item["source"], item["period"], item["creator"], item["dimension"], item["label"], item["value"]]
                    for item in audience
                ],
            )
        )
    else:
        lines.append("_当前范围没有可用的受众画像字段。_\n")

    lines.append("## 四、笔记互动\n")
    summaries = notes.get("charts", {}).get("metric_distributions") or []
    lines.append("### 4.1 各来源指标样本量\n")
    lines.append(
        _md_table(
            ["来源", "指标", "精确样本数"],
            [[item.get("source"), item.get("metric"), item.get("sample_count")] for item in summaries],
        )
        if summaries
        else "_当前范围没有可用的精确指标数值。_\n"
    )
    lines.append("### 4.2 互动指标四分位\n")
    box_rows = [
        [item.get("source"), item.get("metric"), item.get("sample_count"), " / ".join(_fmt_number(value) for value in item.get("values") or [])]
        for item in notes.get("charts", {}).get("interaction_boxplot") or []
    ]
    lines.append(_md_table(["来源", "指标", "样本数", "最小 / Q1 / 中位 / Q3 / 最大"], box_rows) if box_rows else "_当前范围没有可用的互动数值。_\n")
    lines.extend(_md_notes_sections(bundle))

    lines.extend(_md_comment_sections(bundle))

    lines.append("## 六、结论与说明\n")
    lines.append(
        "以上图表与表格均为对本机已保存样本的描述统计：粉丝与报价分布描述样本结构，"
        "笔记指标描述当前可见数值，评论词频与情感倾向描述已保存评论的文本特征。"
        "任何跨来源、跨周期的比较都在相同口径内进行，样本不足时保留空白而不是补 0。\n"
    )

    lines.append("## 七、局限与合规声明\n")
    for item in bundle["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ 图表（PNG）


def _figure_png(build) -> bytes:
    """用 matplotlib 生成一张 PNG；调用方保证 build(fig, axes) 画图。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    figure, axes = plt.subplots(figsize=(6.4, 3.2))
    try:
        build(figure, axes)
        figure.tight_layout()
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=160)
        return buffer.getvalue()
    finally:
        plt.close(figure)


def _bar_png(labels: list[str], values: list[float], title: str, *, horizontal: bool = False) -> bytes:
    def build(figure, axes) -> None:
        color = "#4f9d7c"
        if horizontal:
            positions = list(range(len(labels)))
            axes.barh(positions, values, color=color)
            axes.set_yticks(positions)
            axes.set_yticklabels(labels, fontsize=8)
            axes.invert_yaxis()
            axes.grid(axis="x", linestyle=":", color="#dfe5e0")
        else:
            axes.bar(labels, values, color=color, width=0.55)
            axes.tick_params(axis="x", labelsize=8)
            axes.grid(axis="y", linestyle=":", color="#dfe5e0")
        axes.set_title(title, fontsize=10)
        axes.set_axisbelow(True)
        for spine in ("top", "right"):
            axes.spines[spine].set_visible(False)

    return _figure_png(build)


def report_charts(bundle: dict[str, Any]) -> list[tuple[str, bytes]]:
    """报告里嵌入的关键图；没有数据的图直接跳过。"""
    charts: list[tuple[str, bytes]] = []
    analytics_charts = bundle["analytics"].get("charts", {})
    followers = [item for item in analytics_charts.get("follower_distribution") or [] if item.get("value")]
    if followers:
        charts.append((
            "图 1 创作者粉丝数分布",
            _bar_png([str(item["name"]) for item in followers], [float(item["value"]) for item in followers], "创作者粉丝数分布（人）"),
        ))
    quotes = [item for item in analytics_charts.get("quote_status_distribution") or [] if item.get("value")]
    if quotes:
        charts.append((
            "图 2 商业报价类型构成",
            _bar_png([str(item["name"]) for item in quotes], [float(item["value"]) for item in quotes], "商业报价字段的解析类型（条）"),
        ))
    top_read = [group for group in bundle["notes"].get("charts", {}).get("top_read") or [] if group.get("items")]
    if top_read:
        group = max(top_read, key=lambda item: len(item.get("items") or []))
        items = group["items"][:8]
        charts.append((
            f"图 3 笔记阅读量排行（{group.get('source')}）",
            _bar_png(
                [str(item.get("label"))[:14] for item in items],
                [float(item.get("value") or 0) for item in items],
                f"笔记阅读量排行（{group.get('source')}）",
                horizontal=True,
            ),
        ))
    if bundle.get("comments"):
        counts = bundle["comments"]["sentiment"]["summary"].get("sentiment_counts") or {}
        labels = [label for label in SENTIMENT_LABELS if counts.get(label)]
        if labels:
            charts.append((
                "图 4 评论情感倾向分布",
                _bar_png(labels, [float(counts.get(label) or 0) for label in labels], "评论情感倾向（条，词典规则）"),
            ))
    return charts


# ------------------------------------------------------------------- Word 渲染


def _docx_table(document, headers: list[str], rows: list[list[Any]]):
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for index, text in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = text
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = _fmt_number(value)
    return table


def render_docx(bundle: dict[str, Any], output: Path) -> Path:
    """生成带图表与表格的 .docx；需要 python-docx（见 requirements.txt）。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)
    style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    heading = document.add_heading(bundle["title"], level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    counts = bundle["overview"].get("counts", {})
    _docx_table(
        document,
        ["项目", "内容"],
        [
            ["生成时间", bundle["generated_at"]],
            ["本地数据库", bundle["database_name"]],
            ["数据范围", bundle["scope"]],
            ["批次来源", (bundle.get("batch") or {}).get("source") or "全部批次"],
            ["创作者 / 笔记 / 评论 / 批次", f"{counts.get('creators', 0)} / {counts.get('notes', 0)} / {counts.get('comments', 0)} / {counts.get('batches', 0)}"],
        ],
    )

    charts = dict(report_charts(bundle))

    document.add_heading("一、数据集与采集方法", level=1)
    document.add_paragraph(
        "本报告汇总本机已保存的记录。数据可能来自可见页面采集或历史导入；请结合下方来源及原始批次记录核实采集方法。"
    )
    sources = bundle["overview"].get("sources") or []
    if sources:
        _docx_table(
            document,
            ["采集来源", "已保存行数", "占比（%）"],
            [[item.get("source"), item.get("total_count"), item.get("share_pct")] for item in sources],
        )
    if bundle.get("type_summary"):
        document.add_paragraph("记录类型构成：")
        _docx_table(
            document,
            ["记录类型", "行数"],
            [[type_label(str(item.get("type"))), item.get("count")] for item in bundle["type_summary"]],
        )

    analytics = bundle["analytics"]
    analytics_charts = analytics.get("charts", {})
    document.add_heading("二、数据质量与字段覆盖", level=1)
    quality = bundle.get("quality")
    if quality:
        coverage_rows = quality.get("field_coverage") or []
        captured = sum(int(item.get("captured_records") or 0) for item in coverage_rows)
        expected = sum(int(item.get("expected_records") or 0) for item in coverage_rows)
        document.add_paragraph(
            f"批次 {quality.get('batch_id')}（来源：{quality.get('source')}）共保存 "
            f"{(quality.get('counts') or {}).get('records', 0)} 条记录、"
            f"{(quality.get('counts') or {}).get('comments', 0)} 条评论；"
            f"字段捕获合计 {captured}/{expected} 次观察"
            f"（{_fmt_percent(round(captured * 100 / expected, 1) if expected else None)}），"
            f"数值解析成功率 {_fmt_percent((quality.get('metrics') or {}).get('parse_success_pct'))}。"
        )
        _docx_table(
            document,
            ["记录类型", "字段", "应有/已捕获", "覆盖率（%）", "状态分布"],
            [
                [type_label(str(item.get("record_type"))), item.get("field_name"),
                 f"{item.get('expected_records')}/{item.get('captured_records')}", item.get("coverage_pct"),
                 item.get("state_text") or state_text(item.get("states"))]
                for item in coverage_rows[:25]
            ],
        )
    else:
        document.add_paragraph(
            f"全部批次共保存 {len(bundle.get('coverage') or [])} 个字段维度的状态观测；"
            "选择具体批次后重新生成报告可获得该批次的完整覆盖率表。"
        )
        if bundle.get("field_coverage"):
            _docx_table(
                document,
                ["记录类型", "记录数", "字段数", "非空字段数", "非空占比（%）"],
                [
                    [type_label(str(item.get("type"))), item.get("record_count"), item.get("field_count"), item.get("present_count"), item.get("coverage_pct")]
                    for item in bundle["field_coverage"]
                ],
            )

    document.add_heading("三、创作者与受众", level=1)
    availability = analytics.get("availability", {})
    if availability.get("audience"):
        document.add_paragraph(str(availability["audience"]))
    if "图 1 创作者粉丝数分布" in charts:
        document.add_picture(io.BytesIO(charts["图 1 创作者粉丝数分布"]), width=Inches(5.7))
    if "图 2 商业报价类型构成" in charts:
        document.add_picture(io.BytesIO(charts["图 2 商业报价类型构成"]), width=Inches(5.7))
    quote_types = [item for item in analytics_charts.get("quote_status_distribution") or [] if item.get("value")]
    if quote_types:
        document.add_paragraph(
            "报价口径：" + "；".join(f"{item['name']} {item['value']} 条" for item in quote_types)
            + "。起价与区间不会被折算成精确金额。"
        )
    if bundle.get("audience_rows"):
        document.add_paragraph("受众画像（按来源与统计周期分组，只列页面给出的分类值）：")
        _docx_table(
            document,
            ["来源", "统计周期", "创作者", "维度", "分类", "占比（%）"],
            [
                [item["source"], item["period"], item["creator"], item["dimension"], item["label"], item["value"]]
                for item in bundle["audience_rows"]
            ],
        )

    document.add_heading("四、笔记互动", level=1)
    metric_summaries = bundle["notes"].get("charts", {}).get("metric_distributions") or []
    if metric_summaries:
        _docx_table(
            document,
            ["来源", "指标", "精确样本数"],
            [[item.get("source"), item.get("metric"), item.get("sample_count")] for item in metric_summaries],
        )
    note_chart = next((title for title in charts if title.startswith("图 3")), None)
    if note_chart:
        document.add_picture(io.BytesIO(charts[note_chart]), width=Inches(5.7))

    document.add_heading("五、评论分析", level=1)
    if bundle.get("comments"):
        summary = bundle["comments"]["summary"]["summary"]
        document.add_paragraph(
            f"已保存可见评论 {summary.get('comment_count', 0)} 条（一级 {summary.get('root_count', 0)} 条、"
            f"回复 {summary.get('reply_count', 0)} 条），涉及 {summary.get('note_count', 0)} 篇笔记、"
            f"{summary.get('creator_count', 0)} 位创作者；平均原文长度 {_fmt_number(summary.get('average_length'))} 字符。"
        )
        if "图 4 评论情感倾向分布" in charts:
            document.add_picture(io.BytesIO(charts["图 4 评论情感倾向分布"]), width=Inches(5.7))
        document.add_paragraph(f"情感倾向由词典规则给出：{bundle['comments']['sentiment']['summary'].get('method', '')}")
    else:
        document.add_paragraph("当前范围没有已保存的可见评论。")

    document.add_heading("六、结论与说明", level=1)
    document.add_paragraph(
        "以上内容均为对本机已保存样本的描述统计。粉丝与报价分布描述样本结构，笔记指标描述当前可见数值，"
        "评论词频与情感倾向描述已保存评论的文本特征；样本不足处保留空白而不是补 0。"
    )

    document.add_heading("七、局限与合规声明", level=1)
    for item in bundle["limitations"]:
        document.add_paragraph(item, style="List Bullet")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output))
    return output


def report_filename(bundle: dict[str, Any], suffix: str) -> str:
    stamp = datetime.fromisoformat(bundle["generated_at"]).strftime("%Y%m%d_%H%M")
    return f"creatorscope_report_{stamp}.{suffix}"
