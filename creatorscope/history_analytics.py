"""Read-only analysis helpers for the isolated historical evidence database."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from statistics import mean, median
from typing import Any

from .history_evidence import MARKER


class HistoryAnalyticsError(ValueError):
    """The configured historical evidence database is unavailable or invalid."""


SOURCE_GROUPS: dict[str, dict[str, str]] = {
    "EPT": {"label": "EPT 候选池", "where": "s.source_name = ?", "match": "EPT"},
    "MUSINSA": {"label": "MUSINSA 候选池", "where": "s.source_name = ?", "match": "MUSINSA"},
    "creator_detail": {
        "label": "达人详情样本",
        "where": "s.source_name LIKE ?",
        "match": "达人详情样本%",
    },
}

METRICS: tuple[tuple[str, str, str], ...] = (
    ("followers", "粉丝数", "人"),
    ("daily_read_median", "日常阅读中位数", "次"),
    ("daily_interaction_median", "日常互动中位数", "次"),
    ("likes_and_favorites", "获赞与收藏", "次"),
    ("published_notes", "发布笔记数", "篇"),
)
METRIC_KEYS = {field for field, _, _ in METRICS}
NOTE_SAMPLE_SHA256 = (
    "25a2efbb7169b79347e0bba2c44951d59f37859f9aa403f21a4eb65262fc4afa",
    "0c48d7ae2a0297920f0ffae5316fb920d7cc87a31a32bcf715d7f552a67d10f0",
    "47dcf757a3ab8b4aca582c1c9e907823891ad40394fdfcb99a22a1e291cc530c",
)


def _connection(path: Path | str | None) -> sqlite3.Connection:
    if path is None:
        raise HistoryAnalyticsError("历史样本库尚未配置")
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        raise HistoryAnalyticsError("本机没有可用的历史样本库")
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        marker = conn.execute("SELECT value FROM metadata WHERE key='application'").fetchone()
        if marker is None or marker["value"] != MARKER:
            conn.close()
            raise HistoryAnalyticsError("历史样本库格式不受支持")
        return conn
    except HistoryAnalyticsError:
        raise
    except sqlite3.Error as exc:
        if conn is not None:
            conn.close()
        raise HistoryAnalyticsError("无法读取历史样本库") from exc


def _group(key: str) -> tuple[str, str, str]:
    group = SOURCE_GROUPS.get(key)
    if group is None:
        raise HistoryAnalyticsError("请选择有效的数据来源")
    return key, group["where"], group["match"]


def _source_files(conn: sqlite3.Connection, where: str, match: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT s.source_id, s.source_name, s.sha256, s.source_capture_time,
               s.record_count, COUNT(DISTINCT sr.record_id) AS saved_records
        FROM sources s
        LEFT JOIN source_records sr ON sr.source_id=s.source_id
        WHERE {where}
        GROUP BY s.source_id
        ORDER BY s.source_name, s.sha256
        """,
        (match,),
    ).fetchall()
    return [dict(row) for row in rows]


def _eligible_value(row: sqlite3.Row) -> bool:
    try:
        value = float(row["numeric_value"])
    except (TypeError, ValueError):
        return False
    return row["parse_status"] == "parsed" and row["unit"] == "count" and math.isfinite(value) and value >= 0


def list_sources(path: Path | str | None) -> dict[str, Any]:
    conn = _connection(path)
    try:
        result = []
        for key, group in SOURCE_GROUPS.items():
            sources = _source_files(conn, group["where"], group["match"])
            if not sources:
                continue
            source_ids = [item["source_id"] for item in sources]
            marks = ",".join("?" for _ in source_ids)
            rows = conn.execute(
                f"""
                SELECT fe.target_field, fe.numeric_value, fe.unit, fe.parse_status
                FROM source_records sr
                JOIN field_evidence fe ON fe.record_id=sr.record_id
                WHERE sr.source_id IN ({marks})
                  AND fe.target_field IN ({','.join('?' for _ in METRIC_KEYS)})
                """,
                (*source_ids, *sorted(METRIC_KEYS)),
            ).fetchall()
            available = {row["target_field"] for row in rows if _eligible_value(row)}
            if not available:
                continue
            result.append({
                "key": key,
                "label": group["label"],
                "sample_count": sum(item["saved_records"] for item in sources),
                "source_file_count": len(sources),
                "available_metrics": [
                    {"key": field, "label": label}
                    for field, label, _ in METRICS if field in available
                ],
            })
        return {"sources": result}
    finally:
        conn.close()


def _summary(values: list[float], unit: str, total: int, periods: set[str | None]) -> dict[str, Any]:
    parsed = sorted(values)
    return {
        "valid_count": len(parsed),
        "total_count": total,
        "missing_count": max(total - len(parsed), 0),
        "unit": unit,
        "periods": sorted(period for period in periods if period),
        "average": round(mean(parsed), 1) if parsed else None,
        "median": round(median(parsed), 1) if parsed else None,
        "minimum": parsed[0] if parsed else None,
        "maximum": parsed[-1] if parsed else None,
    }


def _follower_buckets(values: list[float]) -> list[dict[str, Any]]:
    buckets = [
        (0, 10_000, "1 万以下"),
        (10_000, 50_000, "1–5 万"),
        (50_000, 100_000, "5–10 万"),
        (100_000, 500_000, "10–50 万"),
        (500_000, float("inf"), "50 万以上"),
    ]
    return [
        {"label": label, "count": sum(lower <= value < upper for value in values)}
        for lower, upper, label in buckets
    ]


def analysis_data(path: Path | str | None, source_key: str = "EPT") -> dict[str, Any]:
    _, where, match = _group(source_key)
    conn = _connection(path)
    try:
        sources = _source_files(conn, where, match)
        if not sources:
            raise HistoryAnalyticsError("所选数据来源暂时没有记录")
        source_ids = [item["source_id"] for item in sources]
        placeholders = ",".join("?" for _ in source_ids)
        records = conn.execute(
            f"""
            SELECT sr.record_id, sr.row_number, sr.capture_time,
                   s.source_name, s.sha256, fe.target_field, fe.numeric_value,
                   fe.unit, fe.period, fe.parse_status
            FROM source_records sr
            JOIN sources s ON s.source_id=sr.source_id
            LEFT JOIN field_evidence fe ON fe.record_id=sr.record_id
                AND fe.target_field IN ({','.join('?' for _ in METRIC_KEYS)})
            WHERE sr.source_id IN ({placeholders})
            ORDER BY s.source_name, sr.row_number, fe.target_field
            """,
            (*sorted(METRIC_KEYS), *source_ids),
        ).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        periods: dict[str, set[str | None]] = {field: set() for field, _, _ in METRICS}
        for row in records:
            item = grouped.setdefault(row["record_id"], {
                "row_number": row["row_number"], "source_name": row["source_name"],
                "sha256": row["sha256"], "capture_time": row["capture_time"],
                "metrics": {},
            })
            field = row["target_field"]
            if field not in METRIC_KEYS or field in item["metrics"]:
                continue
            periods[field].add(row["period"])
            if _eligible_value(row):
                item["metrics"][field] = float(row["numeric_value"])

        ordered = sorted(grouped.values(), key=lambda row: (row["source_name"], row["row_number"]))
        for index, row in enumerate(ordered, 1):
            row["sample"] = f"样本 {index:03d}"
            row["lineage"] = {
                "source_name": row.pop("source_name"),
                "sha256": row.pop("sha256"),
                "row_number": row["row_number"],
                "capture_time": row.pop("capture_time"),
            }

        total = len(ordered)
        field_summaries = {}
        units = {field: unit for field, _, unit in METRICS}
        for field, label, unit in METRICS:
            field_summaries[field] = {
                "label": label,
                **_summary(
                    [row["metrics"][field] for row in ordered if field in row["metrics"]],
                    unit,
                    total,
                    periods[field],
                ),
            }

        follower_values = [row["metrics"]["followers"] for row in ordered if "followers" in row["metrics"]]
        paired = [
            row for row in ordered
            if "daily_read_median" in row["metrics"] and "daily_interaction_median" in row["metrics"]
        ]
        return {
            "source": {
                "key": source_key,
                "label": SOURCE_GROUPS[source_key]["label"],
                "sample_count": total,
                "source_file_count": len(sources),
                "files": [
                    {
                        "name": source["source_name"],
                        "sha256": source["sha256"],
                        "capture_time": source["source_capture_time"],
                        "record_count": source["saved_records"],
                    }
                    for source in sources
                ],
            },
            "metrics": field_summaries,
            "follower_distribution": _follower_buckets(follower_values),
            "performance_pairs": [
                {
                    "sample": row["sample"],
                    "reading": row["metrics"]["daily_read_median"],
                    "interaction": row["metrics"]["daily_interaction_median"],
                    "lineage": row["lineage"],
                }
                for row in paired
            ],
            "records": [
                {
                    "sample": row["sample"],
                    **{field: row["metrics"].get(field) for field in METRIC_KEYS},
                    "lineage": row["lineage"],
                }
                for row in ordered
            ],
        }
    finally:
        conn.close()


def note_samples(path: Path | str | None) -> dict[str, Any]:
    conn = _connection(path)
    try:
        placeholders = ",".join("?" for _ in NOTE_SAMPLE_SHA256)
        case_order = " ".join(
            f"WHEN ? THEN {index}" for index, _ in enumerate(NOTE_SAMPLE_SHA256)
        )
        rows = conn.execute(
            f"""
            SELECT s.source_name, s.sha256, sr.row_number, sr.capture_time,
                   fe.target_field, fe.text_value
            FROM sources s
            JOIN source_records sr ON sr.source_id=s.source_id
            LEFT JOIN field_evidence fe ON fe.record_id=sr.record_id
                AND fe.target_field IN ('note_title','note_body_candidate','note_topics_candidate')
            WHERE s.sha256 IN ({placeholders})
            ORDER BY CASE s.sha256 {case_order} ELSE 999 END, sr.row_number
            """,
            (*NOTE_SAMPLE_SHA256, *NOTE_SAMPLE_SHA256),
        ).fetchall()
        samples: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row["sha256"]
            sample = samples.setdefault(key, {
                "title": "", "body": "", "topics": [],
                "lineage": {
                    "source_name": row["source_name"], "sha256": key,
                    "row_number": row["row_number"], "capture_time": row["capture_time"],
                },
            })
            field, value = row["target_field"], row["text_value"]
            if not value:
                continue
            if field == "note_title":
                sample["title"] = value
            elif field == "note_body_candidate":
                body = re.sub(r"https?://\S+", "", value)
                sample["body"] = re.sub(r"(?<!\w)@[^\s，。；;]+", "", body).strip()
            elif field == "note_topics_candidate":
                try:
                    topics = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    topics = re.findall(r"#([^\s#]+)", value)
                if isinstance(topics, list):
                    sample["topics"] = list(dict.fromkeys(str(topic).strip().lstrip("#") for topic in topics if str(topic).strip()))
        selected = [samples[digest] for digest in NOTE_SAMPLE_SHA256 if digest in samples and samples[digest]["title"] and samples[digest]["body"]]
        return {"sample_count": len(selected), "samples": selected}
    finally:
        conn.close()
