from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from wordcloud import WordCloud

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from creatorscope.news import load_news_items
from research.r4_3_comment_analysis.comment_analysis import load_stopwords, tokenize
from research.r4_3_comment_analysis.sentiment import analyze_comments


FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simsun.ttc"),
)
COLORS = ("#5e9275", "#7596bd", "#c77668", "#d3a65f", "#8b7cb4")


def _font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    for item in font_manager.fontManager.ttflist:
        if any(value in item.name.casefold() for value in ("simhei", "microsoft yahei", "noto sans cjk")):
            if Path(item.fname).is_file():
                return item.fname
    raise FileNotFoundError("未找到可显示中文的字体，停止生成以免图表乱码")


def _readonly_snapshot(database: Path) -> sqlite3.Connection:
    source_uri = database.resolve().as_uri() + "?mode=ro"
    source = sqlite3.connect(source_uri, uri=True, timeout=20)
    source.execute("PRAGMA query_only=ON")
    snapshot = sqlite3.connect(":memory:")
    source.backup(snapshot)
    source.close()
    snapshot.row_factory = sqlite3.Row
    return snapshot


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _save(fig: Any, output: Path, name: str) -> None:
    fig.tight_layout()
    fig.savefig(output / name, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _bar_chart(output: Path, name: str, title: str, labels: list[str], values: list[int], *, ylabel: str = "记录数") -> None:
    fig, ax = plt.subplots(figsize=(9.6, 5.4))
    bars = ax.bar(labels, values, color=[COLORS[index % len(COLORS)] for index in range(len(labels))])
    ax.bar_label(bars, padding=3, fontsize=9)
    ax.set_title(title, fontsize=14, pad=16)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.22)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="x", labelrotation=20)
    _save(fig, output, name)


def _wordcloud(output: Path, name: str, title: str, texts: list[str], font: str, stopwords: set[str]) -> int:
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(tokenize(text, stopwords))
    if not counts:
        return 0
    cloud = WordCloud(
        font_path=font,
        width=1400,
        height=780,
        background_color="white",
        max_words=100,
        collocations=False,
        prefer_horizontal=0.9,
        random_state=42,
    ).generate_from_frequencies(counts)
    fig, ax = plt.subplots(figsize=(11, 6.5))
    ax.imshow(cloud, interpolation="bilinear")
    ax.set_title(title, fontsize=14, pad=12)
    ax.axis("off")
    _save(fig, output, name)
    return sum(counts.values())


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def generate(database: Path, output: Path, news_jsonl: Path | None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    font = _font_path()
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=font).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    stopwords = load_stopwords()
    conn = _readonly_snapshot(database)
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        if integrity != "ok" or foreign_key_violations:
            raise RuntimeError(f"只读快照完整性失败: integrity={integrity}, foreign_key_violations={foreign_key_violations}")

        comments = _rows(conn, """
            SELECT s.comment_text,s.like_count,s.like_count_raw,s.reply_count_raw,s.comment_level,
                   s.note_id,n.title AS note_title,n.creator_id,c.creator_name,
                   c.platform_creator_id,c.pseudonym,s.source,s.batch_id
            FROM comment_snapshots s
            JOIN notes n ON n.note_id=s.note_id
            JOIN creators c ON c.creator_id=n.creator_id
            ORDER BY s.captured_at,s.id
        """)
        notes = _rows(conn, """
            SELECT note_id,title,body,topics_json,published_at,note_type,source,platform_note_id,source_url
            FROM notes ORDER BY published_at,note_id
        """)
        creators = _rows(conn, """
            SELECT creator_id,creator_name,pseudonym,platform_creator_id FROM creators ORDER BY creator_id
        """)
        sentiment = analyze_comments(comments)

        lengths = [len(str(item.get("comment_text") or "")) for item in comments]
        fig, ax = plt.subplots(figsize=(8.8, 5.2))
        ax.hist(lengths, bins=min(10, max(4, round(math.sqrt(len(lengths))))), color=COLORS[0], edgecolor="white")
        ax.set_title(f"评论原文长度分布（n={len(lengths)}）", fontsize=14, pad=16)
        ax.set_xlabel("Unicode 字符数")
        ax.set_ylabel("评论数")
        ax.grid(axis="y", alpha=0.22)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
        _save(fig, output, "comment_length_histogram.png")

        fig, ax = plt.subplots(figsize=(6.2, 5.0))
        if lengths:
            ax.boxplot(lengths, orientation="vertical", patch_artist=True, boxprops={"facecolor": "#dce9df", "color": COLORS[0]}, medianprops={"color": "#32473a", "linewidth": 2}, whiskerprops={"color": COLORS[0]}, capprops={"color": COLORS[0]})
        ax.set_title(f"评论原文长度箱线图（n={len(lengths)}）", fontsize=14, pad=16)
        ax.set_ylabel("Unicode 字符数")
        ax.set_xticks([1], ["实际评论文本"])
        ax.grid(axis="y", alpha=0.22)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
        _save(fig, output, "comment_length_boxplot.png")

        exact_likes = [int(item["like_count"]) for item in comments if item.get("like_count") is not None]
        fig, ax = plt.subplots(figsize=(8.8, 5.2))
        if exact_likes:
            bins = min(8, max(3, len(set(exact_likes))))
            ax.hist(exact_likes, bins=bins, color=COLORS[1], edgecolor="white")
        ax.set_title(f"评论精确点赞数分布（n={len(exact_likes)}）", fontsize=14, pad=16)
        ax.set_xlabel("页面保存的精确点赞数")
        ax.set_ylabel("评论数")
        ax.grid(axis="y", alpha=0.22)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
        _save(fig, output, "comment_exact_likes_histogram.png")

        scatter = [(len(str(item.get("comment_text") or "")), int(item["like_count"])) for item in comments if item.get("like_count") is not None]
        fig, ax = plt.subplots(figsize=(8.8, 5.2))
        if scatter:
            ax.scatter([item[0] for item in scatter], [item[1] for item in scatter], s=48, color=COLORS[2], alpha=0.8, edgecolors="white")
        ax.set_title(f"评论长度与精确点赞数（n={len(scatter)}）", fontsize=14, pad=16)
        ax.set_xlabel("评论原文 Unicode 字符数")
        ax.set_ylabel("精确点赞数")
        ax.grid(alpha=0.22)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
        _save(fig, output, "comment_length_likes_scatter.png")

        sentiment_counts = sentiment["summary"]["sentiment_counts"]
        sentiment_labels = [key for key, value in sentiment_counts.items() if value]
        sentiment_values = [sentiment_counts[key] for key in sentiment_labels]
        _bar_chart(output, "comment_sentiment_distribution.png", f"评论词典情感倾向探索（n={len(comments)}）", sentiment_labels, sentiment_values)

        tfidf = sentiment["summary"]["tfidf_keywords"][:12]
        fig, ax = plt.subplots(figsize=(9.2, 5.8))
        if tfidf:
            terms = list(reversed(tfidf))
            bars = ax.barh([item["word"] for item in terms], [item["score"] for item in terms], color=COLORS[1])
            ax.bar_label(bars, fmt="%.3f", padding=3, fontsize=8)
        ax.set_title(f"评论 TF-IDF 关键词（n={len(comments)}）", fontsize=14, pad=16)
        ax.set_xlabel("TF-IDF 汇总分（用于排序）")
        ax.grid(axis="x", alpha=0.22)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
        _save(fig, output, "comment_tfidf_keywords.png")

        title_texts = [str(item["title"]) for item in notes if item.get("title")]
        body_texts = [str(item["body"]) for item in notes if item.get("body")]
        topic_counter: Counter[str] = Counter()
        topic_tag_count = 0
        for item in notes:
            try:
                topics = json.loads(item.get("topics_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                topics = []
            if isinstance(topics, list):
                valid_topics = [str(topic) for topic in topics if str(topic).strip()]
                topic_counter.update(valid_topics)
                topic_tag_count += len(valid_topics)
        title_tokens = _wordcloud(output, "note_title_wordcloud.png", f"笔记标题词云（有标题 {len(title_texts)} 篇）", title_texts, font, stopwords)
        body_tokens = _wordcloud(output, "note_body_wordcloud.png", f"笔记正文词云（有正文 {len(body_texts)} 篇）", body_texts, font, stopwords)
        comment_tokens = _wordcloud(output, "comment_wordcloud.png", f"评论原文词云（n={len(comments)}）", [str(item.get("comment_text") or "") for item in comments], font, stopwords)
        _bar_chart(
            output,
            "note_topics_top20.png",
            f"笔记话题 Top 20（{len(topic_counter)} 个原始标签）",
            [item[0] for item in topic_counter.most_common(20)],
            [item[1] for item in topic_counter.most_common(20)],
            ylabel="出现次数",
        )

        creator_total = len(creators)
        creator_raw_names = sum(bool(str(row.get("creator_name") or "").strip()) and row["creator_name"] != row["pseudonym"] for row in creators)
        creator_raw_ids = sum(bool(str(row.get("platform_creator_id") or "").strip()) for row in creators)
        creator_legacy_aliases = sum(str(row.get("creator_name") or "") == str(row.get("pseudonym") or "") for row in creators)
        note_fields = {
            "标题": sum(bool(str(item.get("title") or "").strip()) for item in notes),
            "正文": sum(bool(str(item.get("body") or "").strip()) for item in notes),
            "话题标签": sum(bool(str(item.get("topics_json") or "").strip()) and item.get("topics_json") not in ("", "[]") for item in notes),
            "平台笔记 ID": sum(bool(str(item.get("platform_note_id") or "").strip()) for item in notes),
            "来源链接": sum(bool(str(item.get("source_url") or "").strip()) for item in notes),
        }
        _bar_chart(
            output,
            "creator_identity_coverage.png",
            f"创作者公开身份字段覆盖（总 n={creator_total}）",
            ["原始公开名称", "原始平台 ID", "历史占位字段"],
            [creator_raw_names, creator_raw_ids, creator_legacy_aliases],
        )
        _bar_chart(
            output,
            "note_field_coverage.png",
            f"笔记关键字段覆盖（总 n={len(notes)}）",
            list(note_fields),
            list(note_fields.values()),
        )

        news_summary: dict[str, Any] | None = None
        if news_jsonl and news_jsonl.is_file():
            news = load_news_items(news_jsonl)
            date_counts = Counter(str(item.get("published_at") or "")[:10] for item in news if item.get("published_at"))
            methods = Counter(str(item.get("published_at_method") or "missing") for item in news)
            if date_counts:
                ordered_dates = sorted(date_counts)
                _bar_chart(
                    output,
                    "news_archive_daily_counts.png",
                    f"新华网 RSS 历史归档：日期路径计数（n={len(news)}）",
                    ordered_dates,
                    [date_counts[item] for item in ordered_dates],
                )
            news_summary = {
                "items": len(news),
                "date_methods": dict(methods),
                "earliest_date": min(date_counts) if date_counts else None,
                "latest_date": max(date_counts) if date_counts else None,
            }

        summary = {
            "database_file": database.name,
            "database_sha256": _sha256(database),
            "database_integrity": integrity,
            "foreign_key_violations": foreign_key_violations,
            "counts": {
                "creators": creator_total,
                "creator_raw_names": creator_raw_names,
                "creator_raw_platform_ids": creator_raw_ids,
                "creator_legacy_alias_fields": creator_legacy_aliases,
                "notes": len(notes),
                "note_titles": len(title_texts),
                "note_bodies": len(body_texts),
                "note_topic_tags": topic_tag_count,
                "comments": len(comments),
                "comment_length_samples": len(lengths),
                "comment_exact_like_samples": len(exact_likes),
                "comment_length_like_scatter_samples": len(scatter),
                "comment_tfidf_keywords": len(tfidf),
                "wordcloud_tokens": {"note_title": title_tokens, "note_body": body_tokens, "comments": comment_tokens},
            },
            "sentiment": sentiment["summary"],
            "news": news_summary,
            "images": sorted(path.name for path in output.glob("*.png")),
        }
    finally:
        conn.close()

    manifest_path = output / "v1_2_chart_manifest.json"
    manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="从 SQLite 只读快照生成 CreatorScope V1.2 真实样本图表")
    parser.add_argument("--database", type=Path, required=True, help="正式库或 SQLite 审计副本；按只读方式打开")
    parser.add_argument("--output-dir", type=Path, default=Path("docs/assets/v1.2"))
    parser.add_argument("--news-jsonl", type=Path, default=None, help="可选的本地 RSS JSONL 来源")
    args = parser.parse_args()
    summary = generate(args.database, args.output_dir, args.news_jsonl)
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
