from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import jieba
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from wordcloud import WordCloud


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SAMPLE_PATH = ROOT / "research" / "pgy_note_detail_investigation_20261120" / "actual_samples.json"
FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
]
STOPWORDS = {
    "我们", "你们", "他们", "她们", "这个", "那个", "这些", "那些", "自己", "大家",
    "什么", "怎么", "已经", "还是", "因为", "所以", "如果", "但是", "而且", "然后",
    "可以", "应该", "可能", "一个", "一些", "没有", "不是", "就是", "非常", "真的",
    "感觉", "现在", "时候", "这里", "那里", "一起", "以上", "以下", "在此", "进行",
    "包含", "多个", "这种", "那种", "其中", "属于", "作为", "比较", "参考", "打工人",
}

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def present(value: Any) -> bool:
    return value is not None and str(value).strip() not in {"", "-", "—", "–"}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def seconds_from_duration(value: Any) -> int | None:
    match = re.search(r"(?P<minutes>\d{1,2}):(?P<seconds>\d{2})", str(value or ""))
    if not match:
        return None
    return int(match.group("minutes")) * 60 + int(match.group("seconds"))


def write_csv(frame: pd.DataFrame, filename: str) -> None:
    frame.to_csv(HERE / filename, index=False, encoding="utf-8-sig")


def read_db_counts() -> dict[str, Any]:
    db_path = ROOT / "instance" / "creatorscope.sqlite3"
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        counts: dict[str, Any] = {}
        for table in ("tasks", "batches", "creators", "notes", "records", "metric_values"):
            if table in tables:
                counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            else:
                counts[table] = "表不存在"
        counts["tables"] = sorted(tables)
        return counts
    finally:
        conn.close()


def load_sample() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    notes = payload.get("notes")
    if not isinstance(notes, list) or not notes:
        raise ValueError("真实样本 JSON 没有 notes 数组或数组为空")
    if len(notes) > 3:
        raise ValueError("本分析限定为调查目录中已有的 1–3 条真实笔记样本")
    return payload, notes[0], payload.get("creator", {})


def main() -> None:
    db_counts = read_db_counts()
    payload, note, creator = load_sample()
    pgy = note.get("pgy_detail", {})
    xhs = note.get("xhs_public_page", {})
    pgy_metrics = pgy.get("metrics", {})
    xhs_metrics = xhs.get("metrics", {})
    hashtags = xhs.get("hashtags", [])
    title = clean_text(xhs.get("title"))
    body = clean_text(xhs.get("body"))

    row = {
        "sample_id": "pgy_xhs_note_001",
        "note_url": note.get("note_url", ""),
        "creator_nickname": xhs.get("author_nickname", creator.get("nickname", "")),
        "creator_profile_url": xhs.get("author_profile_url", creator.get("profile_url", "")),
        "creator_city_displayed": creator.get("city_displayed", ""),
        "creator_category_tags": " | ".join(creator.get("category_tags", [])),
        "creator_followers_display": creator.get("followers", {}).get("display", ""),
        "creator_followers_approx": creator.get("followers", {}).get("approximate_value"),
        "creator_image_quote_cny": creator.get("quotes_rmb", {}).get("image_note_one_price"),
        "creator_video_quote_cny": creator.get("quotes_rmb", {}).get("video_note_one_price"),
        "creator_data_updated_date": creator.get("data_updated_date", ""),
        "title": title,
        "body": body,
        "topic_tags": " | ".join(hashtags),
        "topic_count": len(hashtags),
        "content_type_pgy": pgy.get("content_type", ""),
        "pgy_published_at": xhs.get("published_at_pgy_display", ""),
        "xhs_published_at_display": xhs.get("published_at_xhs_display", ""),
        "xhs_location_displayed": xhs.get("location_displayed", ""),
        "video_duration_pgy_display": pgy.get("video_duration_displayed", ""),
        "video_duration_pgy_seconds": seconds_from_duration(pgy.get("video_duration_displayed")),
        "video_duration_xhs_display": xhs.get("video_duration_displayed", ""),
        "video_duration_xhs_seconds": seconds_from_duration(xhs.get("video_duration_displayed")),
        "pgy_capture_date": pgy.get("capture_date", ""),
        "xhs_capture_date": xhs.get("capture_date", ""),
        "pgy_exposure": pgy_metrics.get("exposure"),
        "pgy_reads": pgy_metrics.get("reads"),
        "pgy_likes": pgy_metrics.get("likes"),
        "pgy_favorites": pgy_metrics.get("favorites"),
        "pgy_comments": pgy_metrics.get("comments"),
        "pgy_shares": pgy_metrics.get("shares"),
        "pgy_follows": pgy_metrics.get("follows"),
        "xhs_likes_initial_display": xhs_metrics.get("likes_initial_display"),
        "xhs_likes_later_display": xhs_metrics.get("likes_later_display"),
        "xhs_favorites": xhs_metrics.get("favorites"),
        "xhs_comments": xhs_metrics.get("comments"),
        "xhs_shares": xhs_metrics.get("shares"),
        "xhs_shares_value_displayed": xhs_metrics.get("shares_value_displayed"),
    }
    cleaned_frame = pd.DataFrame([row])
    write_csv(cleaned_frame, "cleaned_notes.csv")

    jieba.add_word("打工人")
    tokens = [
        word.strip()
        for word in jieba.lcut(body, HMM=False)
        if len(word.strip()) >= 2
        and word.strip() not in STOPWORDS
        and re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]+", word.strip())
        and not word.strip().isdigit()
    ]
    word_counts = Counter(tokens)
    word_frame = pd.DataFrame(
        [{"word": word, "count": count} for word, count in word_counts.most_common()],
        columns=["word", "count"],
    )
    write_csv(word_frame, "word_frequency.csv")

    topic_counts = Counter(clean_text(tag).lstrip("#") for tag in hashtags if clean_text(tag))
    topic_frame = pd.DataFrame(
        [{"topic": topic, "count": count, "raw_tag": f"#{topic}"} for topic, count in topic_counts.most_common()],
        columns=["topic", "count", "raw_tag"],
    )
    write_csv(topic_frame, "topic_frequency.csv")

    metric_specs = [
        ("曝光量", "exposure", "次"),
        ("阅读量", "reads", "次"),
        ("点赞量", "likes", "次"),
        ("收藏量", "favorites", "次"),
        ("评论量", "comments", "条"),
        ("分享量", "shares", "次"),
        ("新增关注", "follows", "人"),
    ]
    metric_rows: list[dict[str, Any]] = []
    for label, key, unit in metric_specs:
        raw = pgy_metrics.get(key)
        metric_rows.append({
            "source": "蒲公英笔记详情面板",
            "page": "蒲公英内部笔记详情",
            "capture_date": pgy.get("capture_date", ""),
            "metric": label,
            "raw_value": "" if raw is None else str(raw),
            "numeric_value": raw,
            "unit": unit,
            "displayed_period": "页面未显示独立统计周期；单条笔记累计值的具体口径未进一步确认",
            "availability": "页面取得真实数值" if raw is not None else "页面未提供数值",
        })
    xhs_spec = [
        ("曝光量", None, "次"),
        ("阅读量", None, "次"),
        ("点赞量", xhs_metrics.get("likes_later_display"), "次"),
        ("收藏量", xhs_metrics.get("favorites"), "次"),
        ("评论量", xhs_metrics.get("comments"), "条"),
        ("分享量", xhs_metrics.get("shares"), "次"),
        ("新增关注", None, "人"),
    ]
    for label, raw, unit in xhs_spec:
        metric_rows.append({
            "source": "小红书公开笔记页",
            "page": "笔记详情页可见互动区",
            "capture_date": xhs.get("capture_date", ""),
            "metric": label,
            "raw_value": "" if raw is None else str(raw),
            "numeric_value": raw,
            "unit": unit,
            "displayed_period": "实时页面快照；无统计周期标签",
            "availability": "页面取得真实数值" if raw is not None else "页面未显示，不按 0 处理",
        })
    source_metrics_frame = pd.DataFrame(metric_rows)
    write_csv(source_metrics_frame, "source_metrics.csv")

    common_metrics = [
        ("点赞量", pgy_metrics.get("likes"), xhs_metrics.get("likes_later_display")),
        ("收藏量", pgy_metrics.get("favorites"), xhs_metrics.get("favorites")),
        ("评论量", pgy_metrics.get("comments"), xhs_metrics.get("comments")),
    ]
    comparison_frame = pd.DataFrame([
        {
            "metric": label,
            "unit": "条" if label == "评论量" else "次",
            "pgy_value": pgy_value,
            "xhs_value": xhs_value,
            "xhs_minus_pgy": xhs_value - pgy_value if pgy_value is not None and xhs_value is not None else None,
            "comparison_scope": "同一笔记不同页面快照；页面口径与观察时点未证明完全一致",
        }
        for label, pgy_value, xhs_value in common_metrics
    ])
    write_csv(comparison_frame, "source_comparison.csv")

    font_path = next((str(path) for path in FONT_CANDIDATES if path.exists()), None)
    if font_path is None:
        raise FileNotFoundError("未找到可用中文字体：msyh.ttc 或 simhei.ttf")
    if word_counts:
        cloud = WordCloud(
            font_path=font_path,
            width=1600,
            height=1000,
            background_color="white",
            collocations=False,
            max_words=100,
            prefer_horizontal=0.92,
            random_state=42,
        ).generate_from_frequencies(word_counts)
        cloud.to_file(str(HERE / "note_body_wordcloud.png"))
    else:
        fig, ax = plt.subplots(figsize=(12, 7))
        ax.text(0.5, 0.5, "过滤后无可用词项", ha="center", va="center", fontsize=18)
        ax.axis("off")
        fig.savefig(HERE / "note_body_wordcloud.png", dpi=150, bbox_inches="tight")
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(13, 7))
    if not topic_frame.empty:
        plot_topics = topic_frame.sort_values("count", ascending=True)
        ax.barh(plot_topics["topic"], plot_topics["count"], color="#c58f63")
        for y, count in enumerate(plot_topics["count"]):
            ax.text(count + 0.025, y, str(count), va="center", fontsize=9)
        ax.set_xlim(0, max(1.35, float(plot_topics["count"].max()) * 1.35))
    else:
        ax.text(0.5, 0.5, "无话题标签", ha="center", va="center")
    ax.set_title("真实样本话题标签频次（单条笔记）")
    ax.set_xlabel("出现次数")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(HERE / "topic_frequency.png", dpi=160, bbox_inches="tight")
    plt.close(fig)

    interaction_keys = [
        ("点赞", pgy_metrics.get("likes")),
        ("收藏", pgy_metrics.get("favorites")),
        ("评论", pgy_metrics.get("comments")),
        ("分享", pgy_metrics.get("shares")),
        ("新增关注", pgy_metrics.get("follows")),
    ]
    fig, ax = plt.subplots(figsize=(10, 6))
    labels = [label for label, value in interaction_keys if value is not None]
    values = [value for label, value in interaction_keys if value is not None]
    bars = ax.bar(labels, values, color=["#b87556", "#ce9e68", "#859b83", "#829aa8", "#9b8eaf"][:len(values)])
    ax.bar_label(bars, padding=3, fmt="%d")
    ax.set_title("蒲公英内部详情的笔记互动相关计数（单条样本）")
    ax.set_ylabel("页面显示计数")
    ax.grid(axis="y", alpha=0.2)
    ax.margins(y=0.15)
    fig.tight_layout()
    fig.savefig(HERE / "note_interactions.png", dpi=160, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(common_metrics))
    width = 0.36
    pgy_values = [float(item[1]) for item in common_metrics]
    xhs_values = [float(item[2]) for item in common_metrics]
    bars_pgy = ax.bar(x - width / 2, pgy_values, width, label="蒲公英详情面板", color="#b87556")
    bars_xhs = ax.bar(x + width / 2, xhs_values, width, label="小红书公开页", color="#5e8d88")
    ax.bar_label(bars_pgy, padding=3, fmt="%d", fontsize=8)
    ax.bar_label(bars_xhs, padding=3, fmt="%d", fontsize=8)
    ax.set_xticks(x, [item[0] for item in common_metrics])
    ax.set_ylabel("页面显示计数")
    ax.set_title("同一笔记的跨来源页面计数对照")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.2)
    ax.margins(y=0.18)
    fig.tight_layout()
    fig.savefig(HERE / "source_metric_comparison.png", dpi=160, bbox_inches="tight")
    plt.close(fig)

    content_fields = {
        "标题": title,
        "正文": body,
        "话题标签": hashtags,
        "作者昵称": xhs.get("author_nickname"),
        "作者主页链接": xhs.get("author_profile_url"),
        "笔记类型": pgy.get("content_type"),
        "蒲公英发布时间": xhs.get("published_at_pgy_display"),
        "笔记链接": note.get("note_url"),
        "蒲公英视频时长": pgy.get("video_duration_displayed"),
        "小红书地点": xhs.get("location_displayed"),
    }
    filled_content = sum(bool(value) for value in content_fields.values())
    pgy_metric_count = sum(value is not None for _label, key, _unit in metric_specs if (value := pgy_metrics.get(key)) is not None)
    xhs_displayed_count = sum(value is not None for _label, value, _unit in xhs_spec)
    capture_date = payload.get("captured_on", "")
    report = f"""# CreatorScope R3.2 真实笔记数据分析报告

## 运行与来源

- 分析输入：`research/pgy_note_detail_investigation_20261120/actual_samples.json`（既有真实可见页面调查样本，未改写）。
- 样本标记日期：{capture_date}；调查样本共 {len(note.get('xhs_public_page', {}).get('comment_samples', []))} 条评论可见样本，本轮文本词频仅分析笔记正文，不分析评论。
- 有效独立笔记：{len(payload.get('notes', []))} 条；创作者：1 位；笔记类型：{pgy.get('content_type', '未记录')}。
- 运行环境：项目 `.venv` 中的 Python、pandas、jieba、Matplotlib、NumPy、WordCloud；未安装或升级依赖。
- 所有 CSV 以 UTF-8 BOM 写出；未对 SQLite 执行写入，也未调用 Flask 应用启动流程。

## 实际处理结果

- 内容字段（标题、正文、话题、作者、链接、类型、发布时间、时长、地点）已提取 {filled_content}/{len(content_fields)} 项。
- 正文清洗后 {len(body)} 个字符；jieba 分词并过滤停用词后 {len(tokens)} 个词项、{len(word_counts)} 个不同词项。
- 停用词表由分析脚本内置（按此单条正文语境筛选）：{'、'.join(sorted(STOPWORDS))}。
- 实际话题标签 {len(topic_counts)} 个；在单条笔记样本内各标签计数为 1。这是样本标签清单频次，不是总体流行度。
- 蒲公英内部详情取得 {pgy_metric_count}/{len(metric_specs)} 个所列指标数值；单位按页面指标含义记录为计数。
- 小红书页取得 {xhs_displayed_count}/{len(xhs_spec)} 项可比较计数；未显示项为空，未填 0。点赞值在调查记录中从 {xhs_metrics.get('likes_initial_display')} 变化至 {xhs_metrics.get('likes_later_display')}。
- 两个页面共同具有点赞、收藏、评论三项真实数值，见 `source_comparison.csv` 和对照图。由于页面口径、页面统计定义及精确观察时点未经证明完全一致，差值只作描述展示。
- 视频时长显示约为蒲公英 {pgy.get('video_duration_displayed', '')}（{seconds_from_duration(pgy.get('video_duration_displayed'))} 秒）与小红书 {xhs.get('video_duration_displayed', '')}（{seconds_from_duration(xhs.get('video_duration_displayed'))} 秒）；不下载媒体。
- 小红书发布时间只保留原页面相对时间“{xhs.get('published_at_xhs_display', '')}”；没有将相对时间推算为精确日期。蒲公英显示发布时间为“{xhs.get('published_at_pgy_display', '')}”。

## 字段来源与统计口径

| 输出字段/指标 | 来源页面与原字段 | 清洗/单位/口径 |
|---|---|---|
| 标题、正文、话题标签 | 小红书公开笔记页 `title`、`body`、`hashtags` | 正文只合并连续空白；标题和标签原样保留；词频只使用正文 |
| 作者与主页、笔记链接 | 小红书公开页 `author_nickname`、`author_profile_url`、原 `note_url` | 保留样本中已有真实值；本轮不重新访问 |
| 类型、发布时间、地点、时长 | 蒲公英笔记详情及小红书页面显示字段 | 类型为视频；相对发布时间不强行转日期；时长按 mm:ss 转秒 |
| 粉丝数 | 蒲公英博主资料 `50.1w` / `approximate_value` | 保留显示值和约值 501,000 人；“约”表示平台缩写显示精度 |
| 报价 | 蒲公英博主资料图文/视频一口价 | 120,000 / 135,000 CNY，未用于笔记互动图 |
| 蒲公英曝光/阅读/点赞/收藏/评论/分享/新增关注 | 蒲公英内部笔记详情 `metrics` | 原始值为计数；页面未给出独立统计周期，按页面快照记录，不假设周期 |
| 小红书点赞/收藏/评论/分享 | 小红书公开笔记互动区 `metrics` | 点赞采用晚些时候显示的 5,319；收藏 525、评论 576；分享未显示，保持空值 |
| 正文词频 | 小红书笔记正文 | jieba `HMM=False`；过滤本脚本内置停用词、单字、纯数字及非中英数标点；计数为词项出现次数 |
| 话题频次 | 小红书 `hashtags` 数组 | 去掉前置 `#` 后精确计数；本样本每个标签仅出现一次 |
| 跨来源差值 | 同一笔记两个页面共同显示的点赞/收藏/评论 | `小红书页数值 - 蒲公英页数值`；只描述页面显示差异，不作统计推断 |

## 数据清洗与可用性

- 清洗后主表 1 行，对应 1 条独立笔记；另输出来源指标长表与 3 行共同比较表。
- 数值字段保留整数；两个页面的计数分开保存，不互相覆盖或合并。
- 缺失的阅读量、曝光量、分享量（小红书页面）及任何未显示字段留空；不视作 0。
- 样本 JSON 有 `sources`、`creator`、`creator_performance`、`fan_analysis`、`notes` 和 `limitations`；被调查目录没有 `field_inventory.csv`，本报告基于 JSON 实际键名整理字段来源。

## 可支持与样本不足的分析

- **可运行但只作示范：**正文词频/词云、标签频次清单、单条笔记互动计数柱状图、同一笔记跨页面计数对照图。
- **样本不足：**跨笔记互动分布、箱线图、散点图、指标相关性、领域/创作者总体标签排行、内容发布趋势、重复快照历史趋势。当前只有 1 条独立笔记，不能形成这些分析的有效样本。
- **不在本轮结论内：**评论情感分析（调查样本仅有少量可见评论，未进行语义标注）、图像内容识别、视频内容理解、总体推断。

## 项目集成评估（只给方案，本轮未改前端）

1. 已有 ECharts 页面使用统一 `echarts.init` 实例和 `renderAnalytics` 数据渲染；可以复用现有组件封装、图表卡片布局及颜色规范。
2. 当前看板面向 SQLite 中分类型的博主记录、笔记指标及批次；R3.2 样本 JSON 是调查专用嵌套结构，当前文件上传解析器要求顶层含 `records[]`，因此不能直接上传这份原始 JSON。
3. 建议下一步在导入边界新增独立的调查样本适配器，将同一笔记转换成创作者、笔记正文/标签、来源快照三类字段；来源指标带 `source`、`captured_at`、`period`、`metric_key`，并保留“页面未显示”状态。适配器先对隔离副本验证，再由用户决定是否接入主数据库。
4. 当前 SQLite 文件具有 `tasks`、`batches`、`records`、`metric_values` 四个业务表（及 `sqlite_sequence`），共 {db_counts.get('tasks', '未核验')} 个任务、{db_counts.get('batches', '未核验')} 个批次、{db_counts.get('records', '未核验')} 条记录及 {db_counts.get('metric_values', '未核验')} 个指标；其物理结构缺少当前源码 schema 中的 `creators` 和 `notes` 表，且 `records` 缺少 `creator_id`/`note_id`，疑似早期 schema。为保持本轮只读，未启动会自动初始化数据库的 Flask 应用，也未以当前代码对该 DB 做写入测试。
5. 集成前应先提供只读/隔离数据库迁移检查，确定当前未提交代码所需 schema 与现存文件如何兼容；不要直接在现有数据库上试导入。

## 图表与文件

- `cleaned_notes.csv`：清洗后的单笔记宽表。
- `word_frequency.csv`：正文分词词频。
- `topic_frequency.csv`：话题标签频次。
- `source_metrics.csv`：两个页面按来源分开的指标和缺失状态。
- `source_comparison.csv`：点赞、收藏、评论的同笔记描述性对照。
- `note_body_wordcloud.png`、`topic_frequency.png`、`note_interactions.png`、`source_metric_comparison.png`：由以上真实字段生成。
- `analyze_real_samples.py`：可复现脚本；从既有调查 JSON 读取，只在本目录写输出。

## 结论

数据导入、清洗、文本分词、有限描述统计、图表和 Markdown 报告生成已在离线真实样本上跑通。该闭环证明的是**一条真实笔记样本的端到端离线处理**，不代表多样本统计有效，也没有验证样本可直接导入现有 Web/SQLite 看板。集成前须解决专用 JSON 到当前 records 模型的映射，以及旧 SQLite schema 与当前未提交源码预期结构之间的兼容性。
"""
    (HERE / "analysis_report.md").write_text(report, encoding="utf-8")

    print(json.dumps({
        "sample_path": str(SAMPLE_PATH),
        "note_rows": len(cleaned_frame),
        "cleaned_fields": len(cleaned_frame.columns),
        "body_characters": len(body),
        "tokens_after_filter": len(tokens),
        "unique_tokens": len(word_counts),
        "topic_count": len(topic_counts),
        "pgy_metric_values": pgy_metric_count,
        "xhs_metric_values": xhs_displayed_count,
        "output_files": sorted(path.name for path in HERE.iterdir() if path.is_file()),
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
