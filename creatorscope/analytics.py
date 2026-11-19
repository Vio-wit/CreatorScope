from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jieba
import pandas as pd

from .db import connection


TYPE_LABELS = {
    "blogger_list_sample": "博主列表",
    "creator_profile_sample": "博主详情",
    "creator_content_performance": "内容表现",
    "fan_analysis": "粉丝分析",
    "note_detail": "笔记详情面板",
}


def _number(value: Any) -> float | None:
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "").replace("，", "")
    if cleaned in {"", "-", "—"}:
        return None
    factor = 1.0
    if cleaned.lower().endswith("w") or cleaned.endswith("万"):
        factor = 10000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("千"):
        factor = 1000.0
        cleaned = cleaned[:-1]
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    return float(match.group(0)) * factor


def _rate(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


def dashboard_data(
    db_path: Path,
    batch_id: str | None = None,
    record_type: str | None = None,
) -> dict[str, Any]:
    with connection(db_path) as conn:
        batches = [
            dict(row)
            for row in conn.execute(
                """
                SELECT batch_id, source, captured_at, record_count
                FROM batches ORDER BY captured_at DESC, rowid DESC
                """
            ).fetchall()
        ]
        tasks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT task_id, task_type, source, status, progress, message,
                       created_at, finished_at, batch_id
                FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT 12
                """
            ).fetchall()
        ]

    if batch_id and any(item["batch_id"] == batch_id for item in batches):
        selected_batch = batch_id
    else:
        selected_batch = batches[0]["batch_id"] if batches else None

    records: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    if selected_batch:
        with connection(db_path) as conn:
            sql = """
                SELECT record_id, record_type, captured_at, fields_json
                FROM records WHERE batch_id = ?
            """
            params: list[Any] = [selected_batch]
            if record_type:
                sql += " AND record_type = ?"
                params.append(record_type)
            sql += " ORDER BY record_id"
            for row in conn.execute(sql, params).fetchall():
                item = dict(row)
                item["fields"] = json.loads(item.pop("fields_json"))
                records.append(item)

            metric_sql = """
                SELECT r.record_type, m.metric_key, m.raw_value,
                       m.numeric_value, m.unit, m.period, m.parse_status
                FROM records r
                JOIN metric_values m ON m.record_id = r.record_id
                WHERE r.batch_id = ?
            """
            metric_params: list[Any] = [selected_batch]
            if record_type:
                metric_sql += " AND r.record_type = ?"
                metric_params.append(record_type)
            metric_rows = [
                dict(row) for row in conn.execute(metric_sql, metric_params).fetchall()
            ]

    latest_by_type: dict[str, dict[str, Any]] = {}
    for record in records:
        latest_by_type[record["record_type"]] = record

    fields_total = sum(len(record["fields"]) for record in records)
    fields_present = sum(
        value is not None and str(value).strip() not in {"", "-", "—"}
        for record in records
        for value in record["fields"].values()
    )
    quality = round(fields_present / fields_total * 100, 1) if fields_total else 0.0

    metric_frame = pd.DataFrame(metric_rows)
    parsed_metrics = 0
    if not metric_frame.empty:
        metric_frame["numeric_value"] = pd.to_numeric(
            metric_frame["numeric_value"], errors="coerce"
        )
        parsed_metrics = int(metric_frame["numeric_value"].notna().sum())

    type_counts: dict[str, int] = {}
    for record in records:
        type_counts[record["record_type"]] = type_counts.get(record["record_type"], 0) + 1
    type_summary = [
        {
            "type": record_type_key,
            "label": TYPE_LABELS.get(record_type_key, record_type_key),
            "count": count,
        }
        for record_type_key, count in type_counts.items()
    ]

    list_record = latest_by_type.get("blogger_list_sample")
    profile_record = latest_by_type.get("creator_profile_sample")
    performance_record = latest_by_type.get("creator_content_performance")
    fan_record = latest_by_type.get("fan_analysis")
    note_record = latest_by_type.get("note_detail")
    creator_fields = (
        profile_record["fields"] if profile_record else (list_record["fields"] if list_record else {})
    )
    performance_fields = performance_record["fields"] if performance_record else {}
    fan_fields = fan_record["fields"] if fan_record else {}
    note_fields = note_record["fields"] if note_record else {}

    note_metric_labels = ("曝光量", "阅读量", "点赞量")
    note_chart = [
        {"name": label, "value": _number(note_fields.get(label))}
        for label in note_metric_labels
        if _number(note_fields.get(label)) is not None
    ]
    fan_chart = [
        {"name": label, "value": _rate(fan_fields.get(label))}
        for label in (
            "活跃粉丝占比",
            "阅读粉丝占比",
            "互动粉丝占比",
            "下单粉丝占比",
        )
        if _rate(fan_fields.get(label)) is not None
    ]

    categories: list[dict[str, Any]] = []
    category_value = (profile_record or {}).get("fields", {}).get("内容类目", "")
    if category_value:
        parts = [
            part.strip()
            for part in re.split(r"[、,，/|]+", str(category_value))
            if part.strip()
        ]
        categories = [{"name": part, "value": 1} for part in parts]

    text_samples = []
    for record in records:
        for key in ("笔记标题", "笔记正文", "标题", "正文"):
            value = record["fields"].get(key)
            if isinstance(value, str) and value.strip():
                text_samples.append(value.strip())
    word_frequency: list[dict[str, Any]] = []
    if text_samples:
        words: list[str] = []
        for text in text_samples:
            words.extend(
                word.strip()
                for word in jieba.lcut(text)
                if len(word.strip()) > 1
            )
        if words:
            counts = pd.Series(words, dtype="string").value_counts().head(20)
            word_frequency = [
                {"word": str(word), "count": int(count)}
                for word, count in counts.items()
            ]

    note_detail_count = type_counts.get("note_detail", 0)
    if not text_samples:
        word_status = "当前样本未保存笔记标题或正文"
    else:
        word_status = "已从可用笔记文本分词"
    correlation_status = (
        "样本量不足：至少需要 3 条具备共同数值字段的笔记"
        if note_detail_count < 3
        else "可计算"
    )

    coverage = []
    for record in records:
        fields = record["fields"]
        present = sum(
            value is not None and str(value).strip() not in {"", "-", "—"}
            for value in fields.values()
        )
        total = len(fields)
        coverage.append(
            {
                "type": record["record_type"],
                "label": TYPE_LABELS.get(record["record_type"], record["record_type"]),
                "field_count": total,
                "present_count": present,
                "missing_count": total - present,
                "coverage_pct": round(present / total * 100, 1) if total else 0,
            }
        )

    record_rows = [
        {
            "record_id": record["record_id"],
            "type": record["record_type"],
            "label": TYPE_LABELS.get(record["record_type"], record["record_type"]),
            "captured_at": record["captured_at"],
            "field_count": len(record["fields"]),
            "fields": record["fields"],
        }
        for record in records
    ]

    return {
        "selected_batch_id": selected_batch,
        "batches": batches,
        "tasks": tasks,
        "counts": {
            "batches": len(batches),
            "records": len(records),
            "fields": fields_total,
            "present_fields": fields_present,
            "parsed_numeric_fields": parsed_metrics,
            "quality_pct": quality,
        },
        "kpis": {
            "followers_display": creator_fields.get("粉丝数"),
            "reading_median_display": performance_fields.get("阅读中位数")
            or (list_record or {}).get("fields", {}).get("阅读中位数（日常）"),
            "note_count_display": (profile_record or {}).get("fields", {}).get("发布笔记数"),
            "latest_note_published_at": note_fields.get("发布时间"),
        },
        "chart_data": {
            "note_metrics": note_chart,
            "fan_analysis": fan_chart,
            "categories": categories,
            "word_frequency": word_frequency,
        },
        "type_summary": type_summary,
        "availability": {
            "word_frequency": word_status,
            "correlation": correlation_status,
        },
        "field_coverage": coverage,
        "records": record_rows,
        "record_types": [{"value": "", "label": "全部数据"}]
        + [
            {"value": key, "label": label}
            for key, label in TYPE_LABELS.items()
        ],
    }
