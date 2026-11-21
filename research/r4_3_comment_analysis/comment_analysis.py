from __future__ import annotations

import io
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import jieba
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from wordcloud import WordCloud


OUTPUT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = OUTPUT_DIR.parents[1]
DEFAULT_DB = PROJECT_ROOT / "instance" / "creatorscope_unified.sqlite3"
STOPWORDS_PATH = OUTPUT_DIR / "stopwords.txt"
CHARTS_DIR = OUTPUT_DIR / "charts"
EXPORTS_DIR = OUTPUT_DIR / "exports"
REPORTS_DIR = OUTPUT_DIR / "reports"
FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simsun.ttc"),
]
SHORT_MEANINGFUL = {"好", "贵", "真", "香", "爽", "爱", "美", "赞", "不", "没", "无", "很", "太", "差", "哭", "绝", "但", "还", "又", "给", "要", "想", "买"}
TOKEN_PATTERN = re.compile(r"^[\u3400-\u9fffA-Za-z0-9]+$")


def find_chinese_font() -> str:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    for entry in font_manager.fontManager.ttflist:
        if any(label in entry.name.lower() for label in ("simhei", "microsoft yahei", "noto sans cjk")):
            if Path(entry.fname).exists():
                return entry.fname
    raise FileNotFoundError("No usable Chinese font found; refusing to generate garbled Chinese charts")


def load_stopwords(path: Path = STOPWORDS_PATH) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")}


def tokenize(text: str, stopwords: set[str] | None = None) -> list[str]:
    stop = stopwords or set()
    tokens = []
    for raw in jieba.lcut(text, HMM=True):
        token = raw.strip().lower()
        if not token or token in stop or not TOKEN_PATTERN.fullmatch(token):
            continue
        if len(token) == 1 and token not in SHORT_MEANINGFUL:
            continue
        tokens.append(token)
    return tokens


def exact_integer(raw: Any) -> int | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return int(value) if re.fullmatch(r"\d+", value) else None


def _where(filters: dict[str, Any] | None = None) -> tuple[str, list[Any]]:
    filters = filters or {}
    clauses: list[str] = []
    params: list[Any] = []
    for name, column in (("batch_id", "s.batch_id"), ("note_id", "s.note_id"), ("creator_id", "n.creator_id")):
        value = str(filters.get(name) or "").strip()
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    level = str(filters.get("comment_level") or "").strip()
    if level in {"1", "2"}:
        clauses.append("s.comment_level=?")
        params.append(int(level))
    pinned = str(filters.get("is_pinned") or "").strip().lower()
    if pinned in {"true", "false", "1", "0"}:
        clauses.append("s.is_pinned=?")
        params.append(1 if pinned in {"true", "1"} else 0)
    query = str(filters.get("q") or "").strip()
    if query:
        clauses.append("(s.comment_text LIKE ? OR n.title LIKE ? OR CASE WHEN NULLIF(c.creator_name,'') IS NOT NULL AND c.creator_name<>c.pseudonym THEN c.creator_name WHEN NULLIF(c.platform_creator_id,'') IS NOT NULL THEN c.platform_creator_id ELSE '历史样本未保存原始名称' END LIKE ? OR COALESCE(s.raw_payload_json,'') LIKE ?)")
        params.extend([f"%{query}%"] * 4)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def fetch_comments(db_path: Path = DEFAULT_DB, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    where, params = _where(filters)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        rows = conn.execute(
            """SELECT s.batch_id, s.note_id, n.title AS note_title,
                      n.creator_id,
                      c.platform_creator_id AS creator_platform_id,
                      n.platform_note_id,
                      n.source_url AS note_source_url,
                      CASE WHEN NULLIF(c.creator_name,'') IS NOT NULL AND c.creator_name<>c.pseudonym THEN c.creator_name WHEN NULLIF(c.platform_creator_id,'') IS NOT NULL THEN c.platform_creator_id ELSE '历史样本未保存原始名称' END AS creator_name,
                      s.local_comment_id_hash,
                      s.parent_comment_id_hash,
                      s.comment_text, s.cleaned_text, s.displayed_time,
                      s.like_count_raw, s.like_count AS like_count_normalized,
                      s.reply_count_raw, s.is_pinned, s.comment_level, s.source,
                      s.captured_at, s.raw_payload_json AS raw_payload
               FROM comment_snapshots s
               JOIN notes n ON n.note_id=s.note_id
               JOIN creators c ON c.creator_id=n.creator_id"""
            + where
            + " ORDER BY s.note_id, s.comment_level, s.id",
            params,
        ).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row["is_pinned"] = None if row["is_pinned"] is None else bool(row["is_pinned"])
            row["raw_payload"] = json.loads(row["raw_payload"])
            payload = row["raw_payload"]
            row["comment_id"] = payload.get("comment_id")
            row["local_comment_id"] = payload.get("local_comment_id")
            row["parent_comment_id"] = payload.get("parent_comment_id")
            row["parent_local_comment_id"] = payload.get("parent_local_comment_id")
            row["local_comment_key"] = payload.get("local_comment_key")
            row["parent_local_comment_key"] = payload.get("parent_local_comment_key")
            row["local_comment_id_origin"] = payload.get("local_comment_id_origin", "legacy_unclassified")
            row["parent_comment_id_origin"] = payload.get("parent_comment_id_origin", "legacy_unclassified")
        return result
    finally:
        conn.close()


def filter_options(db_path: Path = DEFAULT_DB) -> dict[str, list[dict[str, Any]]]:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return {
            "batches": [dict(row) for row in conn.execute("SELECT batch_id, source, started_at, completed_at, record_count FROM collection_batches WHERE batch_id IN (SELECT DISTINCT batch_id FROM comment_snapshots) ORDER BY started_at DESC")],
            "creators": [dict(row) for row in conn.execute("SELECT c.creator_id, CASE WHEN NULLIF(c.creator_name,'') IS NOT NULL AND c.creator_name<>c.pseudonym THEN c.creator_name WHEN NULLIF(c.platform_creator_id,'') IS NOT NULL THEN c.platform_creator_id ELSE '历史样本未保存原始名称' END AS creator_name FROM creators c WHERE c.creator_id IN (SELECT DISTINCT n.creator_id FROM notes n JOIN comment_snapshots s ON s.note_id=n.note_id) ORDER BY creator_name")],
            "notes": [dict(row) for row in conn.execute("SELECT n.note_id, n.title, n.creator_id, CASE WHEN NULLIF(c.creator_name,'') IS NOT NULL AND c.creator_name<>c.pseudonym THEN c.creator_name WHEN NULLIF(c.platform_creator_id,'') IS NOT NULL THEN c.platform_creator_id ELSE '历史样本未保存原始名称' END AS creator_name FROM notes n JOIN creators c ON c.creator_id=n.creator_id WHERE n.note_id IN (SELECT DISTINCT note_id FROM comment_snapshots) ORDER BY creator_name, n.title")],
        }
    finally:
        conn.close()


def summarize(rows: list[dict[str, Any]], stopwords: set[str] | None = None) -> dict[str, Any]:
    stop = load_stopwords() if stopwords is None else stopwords
    note_counts = Counter(row["note_id"] for row in rows)
    note_labels = {row["note_id"]: f"{row['creator_name']} · {row['note_title']}" for row in rows}
    creator_counts = Counter(row["creator_id"] for row in rows)
    creator_labels = {row["creator_id"]: row["creator_name"] for row in rows}
    levels = Counter(row["comment_level"] for row in rows)
    lengths = [len(row["comment_text"]) for row in rows]
    likes = [int(row["like_count_normalized"]) for row in rows if row["like_count_normalized"] is not None]
    exact_replies = [count for row in rows if (count := exact_integer(row["reply_count_raw"])) is not None]
    tokens_by_note: dict[str, Counter[str]] = defaultdict(Counter)
    all_tokens: Counter[str] = Counter()
    for row in rows:
        tokens = tokenize(row["cleaned_text"], stop)
        all_tokens.update(tokens)
        tokens_by_note[row["note_id"]].update(tokens)
    sorted_words = sorted(all_tokens.items(), key=lambda item: (-item[1], item[0]))
    note_items = [{"note_id": note_id, "label": note_labels[note_id], "count": count} for note_id, count in sorted(note_counts.items(), key=lambda item: item[0])]
    length_edges = [(0, 10), (11, 20), (21, 40), (41, 80), (81, 160), (161, None)]
    length_hist = []
    for low, high in length_edges:
        label = f"{low}+" if high is None else f"{low}–{high}"
        count = sum(low <= length <= high if high is not None else length >= low for length in lengths)
        length_hist.append({"label": label, "count": count})
    like_counts = Counter(likes)
    like_hist = [{"label": str(value), "count": count} for value, count in sorted(like_counts.items())]
    reply_counts = Counter(exact_replies)
    reply_hist = [{"label": str(value), "count": count} for value, count in sorted(reply_counts.items())]
    length_boxplot = (
        [round(float(value), 2) for value in np.percentile(lengths, [0, 25, 50, 75, 100])]
        if lengths else []
    )
    note_top_words = []
    for note_id, counter in sorted(tokens_by_note.items()):
        for word, count in counter.most_common(12):
            note_top_words.append({"note_id": note_id, "note_label": note_labels[note_id], "word": word, "count": count})
    scatter = [
        {"length": len(row["comment_text"]), "likes": int(row["like_count_normalized"]), "note_title": row["note_title"]}
        for row in rows if row["like_count_normalized"] is not None
    ]
    status = "课程实验小样本，仅描述本批数据" if len(rows) < 30 else "描述统计；结论仅适用于已采样本"
    return {
        "summary": {
            "comment_count": len(rows),
            "root_count": levels.get(1, 0),
            "reply_count": levels.get(2, 0),
            "note_count": len(note_counts),
            "creator_count": len({row["creator_id"] for row in rows}),
            "average_length": round(float(np.mean(lengths)), 1) if lengths else None,
            "length_count": len(lengths),
            "like_count_available": len(likes),
            "reply_count_exact_available": len(exact_replies),
            "pinned_count": sum(row["is_pinned"] is True for row in rows),
            "unavailable_like_values": len(rows) - len(likes),
            "scope_notice": status,
        },
        "by_note": note_items,
        "by_creator": [
            {"creator_id": creator_id, "label": creator_labels[creator_id], "count": count}
            for creator_id, count in sorted(creator_counts.items(), key=lambda item: (-item[1], creator_labels[item[0]]))
        ],
        "length_histogram": length_hist,
        "length_boxplot": {"sample_count": len(lengths), "values": length_boxplot},
        "like_histogram": like_hist,
        "reply_histogram": reply_hist,
        "reply_histogram_sample_count": len(exact_replies),
        "reply_histogram_excluded_count": len(rows) - len(exact_replies),
        "top_words": [{"word": word, "count": count} for word, count in sorted_words[:20]],
        "word_frequencies": [{"word": word, "count": count} for word, count in sorted_words],
        "note_words": note_top_words,
        "scatter": scatter,
        "comments": rows,
    }


def compare_word_frequencies(
    baseline_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    stopwords: set[str] | None = None,
) -> dict[str, Any]:
    stop = load_stopwords() if stopwords is None else stopwords
    baseline_notes = {row["note_id"] for row in baseline_rows}
    comparison_notes = {row["note_id"] for row in comparison_rows}
    common_notes = baseline_notes & comparison_notes
    if not common_notes:
        return {"common_note_count": 0, "words": [], "message": "两个批次没有可直接对比的同一篇笔记"}
    baseline_sources = {row.get("source") for row in baseline_rows}
    comparison_sources = {row.get("source") for row in comparison_rows}
    common_sources = baseline_sources & comparison_sources
    if not common_sources:
        return {"common_note_count": len(common_notes), "words": [], "message": "两个批次没有相同的评论来源"}

    def count_words(rows: list[dict[str, Any]]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for row in rows:
            if row["note_id"] in common_notes and row.get("source") in common_sources:
                counts.update(tokenize(row.get("cleaned_text") or "", stop))
        return counts

    baseline_counts = count_words(baseline_rows)
    comparison_counts = count_words(comparison_rows)
    words = set(baseline_counts) | set(comparison_counts)
    ranked = sorted(words, key=lambda word: (-abs(comparison_counts[word] - baseline_counts[word]), -comparison_counts[word], word))[:20]
    return {
        "common_note_count": len(common_notes),
        "common_comment_sources": sorted(str(source) for source in common_sources),
        "baseline_comment_count": sum(1 for row in baseline_rows if row["note_id"] in common_notes and row.get("source") in common_sources),
        "comparison_comment_count": sum(1 for row in comparison_rows if row["note_id"] in common_notes and row.get("source") in common_sources),
        "words": [
            {"word": word, "baseline_count": baseline_counts[word], "comparison_count": comparison_counts[word], "delta": comparison_counts[word] - baseline_counts[word]}
            for word in ranked
        ],
        "message": "仅比较同一来源、共同笔记的已保存评论样本；词频变化不代表总体舆情变化",
    }


def _font_properties():
    font = find_chinese_font()
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return font


def _save_empty_or_plot(path: Path, title: str, message: str) -> None:
    font_path = _font_properties()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.axis("off")
    ax.set_title(title, fontproperties=font_manager.FontProperties(fname=font_path))
    ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes, fontproperties=font_manager.FontProperties(fname=font_path))
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def make_wordcloud_png(rows: list[dict[str, Any]], stopwords: set[str] | None = None) -> bytes:
    stop = load_stopwords() if stopwords is None else stopwords
    frequencies = Counter(token for row in rows for token in tokenize(row["cleaned_text"], stop))
    if not frequencies:
        _font_properties()
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.axis("off")
        ax.text(0.5, 0.5, "当前筛选下没有可用词频", ha="center", va="center")
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=140, bbox_inches="tight")
        plt.close(fig)
        return buffer.getvalue()
    cloud = WordCloud(
        font_path=find_chinese_font(), width=1400, height=820, max_words=120,
        background_color="white", collocations=False, random_state=37,
        prefer_horizontal=0.92, margin=8,
    ).generate_from_frequencies(frequencies)
    buffer = io.BytesIO()
    cloud.to_image().save(buffer, format="PNG")
    return buffer.getvalue()


def generate_charts(analysis: dict[str, Any], output_dir: Path = CHARTS_DIR) -> dict[str, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    font_path = _font_properties()
    font = font_manager.FontProperties(fname=font_path)
    chart_meta: dict[str, dict[str, Any]] = {}
    rows = analysis["comments"]
    words = analysis["word_frequencies"]

    if words:
        cloud = WordCloud(font_path=font_path, width=1400, height=820, max_words=120, background_color="white", collocations=False, random_state=37, margin=8)
        cloud.generate_from_frequencies({item["word"]: item["count"] for item in words})
        cloud.to_file(str(output_dir / "comment_wordcloud.png"))
    else:
        _save_empty_or_plot(output_dir / "comment_wordcloud.png", "真实评论词云", "当前没有可用词频")
    chart_meta["comment_wordcloud.png"] = {"records": len(rows), "top_words": words[:120], "font_path": font_path, "collocations": False}

    top = analysis["top_words"][:20]
    if top:
        fig, ax = plt.subplots(figsize=(9, 6))
        labels = [item["word"] for item in reversed(top)]
        values = [item["count"] for item in reversed(top)]
        ax.barh(labels, values, color="#5a8d72")
        ax.set_title("真实评论高频词 Top 20", fontproperties=font)
        ax.set_xlabel("词频", fontproperties=font)
        for label in ax.get_yticklabels(): label.set_fontproperties(font)
        fig.tight_layout()
        fig.savefig(output_dir / "top_words.png", dpi=160, bbox_inches="tight")
        plt.close(fig)
    else:
        _save_empty_or_plot(output_dir / "top_words.png", "高频词排行", "样本不足以形成词频")
    chart_meta["top_words.png"] = {"records": len(rows), "series": top}

    notes = analysis["by_note"]
    if notes:
        fig, ax = plt.subplots(figsize=(10, max(4.5, len(notes) * 1.3)))
        ax.barh([item["label"] for item in notes], [item["count"] for item in notes], color="#739b83")
        ax.set_title("不同笔记的可见评论数量", fontproperties=font)
        ax.set_xlabel("评论记录数（非评论区总量）", fontproperties=font)
        for label in ax.get_yticklabels(): label.set_fontproperties(font)
        fig.tight_layout()
        fig.savefig(output_dir / "comment_distribution.png", dpi=160, bbox_inches="tight")
        plt.close(fig)
    else:
        _save_empty_or_plot(output_dir / "comment_distribution.png", "笔记评论数量", "当前筛选没有记录")
    chart_meta["comment_distribution.png"] = {"records": len(rows), "series": notes}

    lengths = [len(row["comment_text"]) for row in rows]
    if lengths:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.hist(lengths, bins=[0, 10, 20, 40, 80, 160, max(161, max(lengths) + 1)], color="#799cbb", edgecolor="white")
        ax.set_title(f"评论长度分布（有效样本 n={len(lengths)}）", fontproperties=font)
        ax.set_xlabel("字符数（Unicode code point）", fontproperties=font)
        ax.set_ylabel("评论数", fontproperties=font)
        fig.tight_layout()
        fig.savefig(output_dir / "comment_length_distribution.png", dpi=160, bbox_inches="tight")
        plt.close(fig)
    else:
        _save_empty_or_plot(output_dir / "comment_length_distribution.png", "评论长度分布", "当前筛选没有记录")
    chart_meta["comment_length_distribution.png"] = {"records": len(lengths), "values": lengths, "bins": [0, 10, 20, 40, 80, 160], "length_basis": "原始评论文本字符数（含空白）"}

    boxplot = analysis["length_boxplot"]
    if boxplot["values"]:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.bxp([{"label": f"原始评论（n={boxplot['sample_count']}）", "whislo": boxplot["values"][0], "q1": boxplot["values"][1], "med": boxplot["values"][2], "q3": boxplot["values"][3], "whishi": boxplot["values"][4], "fliers": []}], showfliers=False, patch_artist=True, boxprops={"facecolor": "#dce9df", "edgecolor": "#5e9275"})
        ax.set_title(f"评论长度箱线图（有效样本 n={boxplot['sample_count']}）", fontproperties=font)
        ax.set_ylabel("原文字符数（含空白）", fontproperties=font)
        fig.tight_layout()
        fig.savefig(output_dir / "comment_length_boxplot.png", dpi=160, bbox_inches="tight")
        plt.close(fig)
    else:
        _save_empty_or_plot(output_dir / "comment_length_boxplot.png", "评论长度箱线图", "当前筛选没有记录")
    chart_meta["comment_length_boxplot.png"] = {"records": boxplot["sample_count"], "values": boxplot["values"], "quartiles": ["min", "q1", "median", "q3", "max"]}

    like_values = [int(row["like_count_normalized"]) for row in rows if row["like_count_normalized"] is not None]
    if len(like_values) >= 2:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.hist(like_values, bins=min(8, max(2, len(set(like_values)))), color="#e7a55d", edgecolor="white")
        ax.set_title(f"评论点赞数分布（有效样本 n={len(like_values)}）", fontproperties=font)
        ax.set_xlabel("点赞数；非精确显示值未参与", fontproperties=font)
        ax.set_ylabel("评论数", fontproperties=font)
        fig.tight_layout()
        fig.savefig(output_dir / "comment_like_distribution.png", dpi=160, bbox_inches="tight")
        plt.close(fig)
    else:
        _save_empty_or_plot(output_dir / "comment_like_distribution.png", "评论点赞数分布", f"有效精确数值样本 n={len(like_values)}；样本不足")
    chart_meta["comment_like_distribution.png"] = {"records": len(like_values), "values": like_values, "excluded_non_exact": len(rows) - len(like_values)}

    reply_hist = analysis["reply_histogram"]
    if reply_hist:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.bar([item["label"] for item in reply_hist], [item["count"] for item in reply_hist], color="#8b83ae", edgecolor="white")
        ax.set_title(f"精确回复数分布（有效样本 n={analysis['reply_histogram_sample_count']}）", fontproperties=font)
        ax.set_xlabel("页面显示的精确回复数；非精确值不参与", fontproperties=font)
        ax.set_ylabel("评论数", fontproperties=font)
        fig.tight_layout()
        fig.savefig(output_dir / "comment_reply_distribution.png", dpi=160, bbox_inches="tight")
        plt.close(fig)
    else:
        _save_empty_or_plot(output_dir / "comment_reply_distribution.png", "精确回复数分布", "当前筛选没有精确回复数")
    chart_meta["comment_reply_distribution.png"] = {"records": analysis["reply_histogram_sample_count"], "histogram": reply_hist, "excluded_non_exact": analysis["reply_histogram_excluded_count"]}

    note_ids = sorted({item["note_id"] for item in analysis["note_words"]})
    terms = sorted({item["word"] for item in analysis["note_words"]}, key=lambda word: (-sum(item["count"] for item in analysis["note_words"] if item["word"] == word), word))[:12]
    if note_ids and terms:
        fig, ax = plt.subplots(figsize=(11, 6))
        x = np.arange(len(terms))
        width = 0.8 / len(note_ids)
        note_label = {item["note_id"]: item["label"] for item in analysis["by_note"]}
        by_pair = {(item["note_id"], item["word"]): item["count"] for item in analysis["note_words"]}
        for index, note_id in enumerate(note_ids):
            offsets = x - 0.4 + width / 2 + index * width
            ax.bar(offsets, [by_pair.get((note_id, term), 0) for term in terms], width=width, label=note_label.get(note_id, note_id))
        ax.set_xticks(x, terms, rotation=25, ha="right", fontproperties=font)
        ax.set_title("不同笔记关键词频次对比", fontproperties=font)
        ax.set_ylabel("词频", fontproperties=font)
        ax.legend(prop=font, fontsize=8)
        fig.tight_layout()
        fig.savefig(output_dir / "note_keyword_comparison.png", dpi=160, bbox_inches="tight")
        plt.close(fig)
    else:
        _save_empty_or_plot(output_dir / "note_keyword_comparison.png", "笔记关键词对比", "当前筛选没有足够词频")
    chart_meta["note_keyword_comparison.png"] = {"records": len(rows), "notes": note_ids, "terms": terms}

    scatter = analysis["scatter"]
    if len(scatter) >= 2:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.scatter([item["length"] for item in scatter], [item["likes"] for item in scatter], color="#c87169", alpha=0.8)
        ax.set_title(f"评论长度与点赞数（有效样本 n={len(scatter)}）", fontproperties=font)
        ax.set_xlabel("原始评论字符数（含空白）", fontproperties=font)
        ax.set_ylabel("精确点赞数", fontproperties=font)
        fig.tight_layout()
        fig.savefig(output_dir / "comment_length_like_scatter.png", dpi=160, bbox_inches="tight")
        plt.close(fig)
    else:
        _save_empty_or_plot(output_dir / "comment_length_like_scatter.png", "评论长度与点赞数", f"有效点赞样本 n={len(scatter)}；样本不足")
    chart_meta["comment_length_like_scatter.png"] = {"records": len(scatter), "points": scatter}

    (output_dir / "chart_parameters.json").write_text(json.dumps(chart_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return chart_meta


def generate_analysis_report(analysis: dict[str, Any], output_dir: Path = REPORTS_DIR) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = analysis["summary"]
    freq_path = EXPORTS_DIR / "word_frequency.csv"
    frequency_rows = [{"word": item["word"], "frequency": item["count"]} for item in analysis["word_frequencies"]]
    pd.DataFrame(frequency_rows, columns=["word", "frequency"]).to_csv(freq_path, index=False, encoding="utf-8-sig")
    valid_likes = summary["like_count_available"]
    quality = {
        "source_record_count": len(analysis["comments"]),
        "filtered_record_count": summary["comment_count"],
        "root_comments": summary["root_count"],
        "replies": summary["reply_count"],
        "notes": summary["note_count"],
        "creators": summary["creator_count"],
        "original_text_nonempty_rate_percent": 100.0 if analysis["comments"] and all(row["comment_text"] for row in analysis["comments"]) else 0.0,
        "cleaned_text_nonempty_rate_percent": 100.0 if analysis["comments"] and all(row["cleaned_text"] for row in analysis["comments"]) else 0.0,
        "precise_like_values": valid_likes,
        "like_value_coverage_percent": round(valid_likes * 100 / summary["comment_count"], 1) if summary["comment_count"] else 0.0,
        "reply_count_exact_values": summary["reply_count_exact_available"],
        "sample_notice": summary["scope_notice"],
        "sentiment_classification": "deferred; no reliable ground truth and sample is small",
        "commenter_identity_fields_present": False,
    }
    (output_dir / "quality_report.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# R4.3 评论分析报告", "",
        f"- 实际数据：{summary['comment_count']} 条真实评论；一级 {summary['root_count']}，回复 {summary['reply_count']}。",
        f"- 涉及创作者 {summary['creator_count']} 位、笔记 {summary['note_count']} 篇；按 R4.2 实测质量记录保留创作者显示名并关联 note_id / title。",
        f"- 平均评论长度：{summary['average_length']} 字符；口径为折叠空白后的 Python Unicode 字符数。",
        f"- 精确点赞数可用 {summary['like_count_available']}/{summary['comment_count']}；非精确值（如 `10+`）不转整数。",
        f"- 评论长度与点赞数散点使用 {len(analysis['scatter'])} 条精确点赞记录；未估算缺失点赞数。",
        f"- 可精确解析的回复数 {summary['reply_count_exact_available']}/{summary['comment_count']}；纯操作词保持为空。",
        f"- 样本说明：{summary['scope_notice']}。评论数是 R4.2 实际取得的可见样本数，不等于评论区总量。", "",
        "## 高频词 Top 20", "", "| 词 | 次数 |", "|---|---:|",
    ]
    lines.extend(f"| {item['word']} | {item['count']} |" for item in analysis["top_words"])
    lines += ["", "## 笔记样本", "", "| 创作者 | 笔记 | 可见评论数 |", "|---|---|---:|"]
    lines.extend(f"| {item['label'].split(' · ',1)[0]} | {item['label'].split(' · ',1)[1]} | {item['count']} |" for item in analysis["by_note"])
    lines += [
        "", "## 口径与限制", "",
        "原始评论文本未脱敏、未改写；清洗字段仅折叠空白。`jieba` 词频排除配置停用词，保留否定词及配置的有意义单字。评论情感和主题分类暂缓，不把词频或情感规则解释成总体态度。点赞散点仅使用原始显示可精确转换的记录；未知值不当作 0。", "",
        "`local_comment_id` 只用于同批去重，不用于跨批匹配。按笔记的评论数和词频仅描述当前 3 篇笔记的小样本。", "",
    ]
    (output_dir / "analysis_report.md").write_text("\n".join(lines), encoding="utf-8")
    return quality
