from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import jieba
import pandas as pd

from .db import connection
from .ingest import normalize_value


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
    *,
    all_batches: bool = False,
    source: str = "",
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
        source_rows = conn.execute(
            """SELECT source FROM (
                   SELECT COALESCE(NULLIF(r.source,''),b.source) AS source FROM records r JOIN batches b ON b.batch_id=r.batch_id
                   UNION SELECT COALESCE(NULLIF(s.source,''),b.source) AS source FROM comment_snapshots s JOIN batches b ON b.batch_id=s.batch_id
               ) WHERE source IS NOT NULL AND TRIM(source)<>'' ORDER BY source"""
        ).fetchall()
        sources = [str(row["source"]) for row in source_rows]
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

    if all_batches:
        selected_batch = None
    elif batch_id and any(item["batch_id"] == batch_id for item in batches):
        selected_batch = batch_id
    else:
        selected_batch = batches[0]["batch_id"] if batches else None

    records: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    if selected_batch or all_batches:
        with connection(db_path) as conn:
            sql = """
                SELECT r.record_id, r.record_type, r.captured_at, r.fields_json
                FROM records r JOIN batches b ON b.batch_id=r.batch_id
            """
            params: list[Any] = []
            clauses: list[str] = []
            if selected_batch:
                clauses.append("r.batch_id = ?")
                params.append(selected_batch)
            if record_type:
                clauses.append("r.record_type = ?")
                params.append(record_type)
            if source:
                clauses.append("COALESCE(NULLIF(r.source,''),b.source)=?")
                params.append(source)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY record_id"
            for row in conn.execute(sql, params).fetchall():
                item = dict(row)
                item["fields"] = json.loads(item.pop("fields_json"))
                records.append(item)

            metric_sql = """
                SELECT r.record_type, m.metric_key, m.raw_value,
                       m.numeric_value, m.unit, m.period, m.parse_status
                FROM records r
                JOIN batches b ON b.batch_id=r.batch_id
                JOIN metric_values m ON m.record_id = r.record_id
            """
            metric_params: list[Any] = []
            metric_clauses: list[str] = []
            if selected_batch:
                metric_clauses.append("r.batch_id = ?")
                metric_params.append(selected_batch)
            if record_type:
                metric_clauses.append("r.record_type = ?")
                metric_params.append(record_type)
            if source:
                metric_clauses.append("COALESCE(NULLIF(r.source,''),b.source)=?")
                metric_params.append(source)
            if metric_clauses:
                metric_sql += " WHERE " + " AND ".join(metric_clauses)
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
        "selected_batch_id": "__all__" if all_batches else selected_batch,
        "all_batches": all_batches,
        "batches": batches,
        "sources": sources,
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


def _metric_value(fields: dict[str, Any], key: str) -> float | None:
    value, _unit, status = normalize_value(key, fields.get(key))
    return float(value) if status == "parsed" and value is not None else None


def _metric_detail(fields: dict[str, Any], key: str) -> dict[str, Any]:
    raw = fields.get(key)
    value, unit, status = normalize_value(key, raw)
    return {
        "raw_value": raw,
        "normalized_value": float(value) if value is not None else None,
        "unit": unit,
        "parse_status": status,
    }


def creator_display_name(raw_name: Any, platform_creator_id: Any, internal_label: Any) -> str:
    name = str(raw_name or "").strip()
    if name and name != str(internal_label or "").strip():
        return name
    platform_id = str(platform_creator_id or "").strip()
    return platform_id or "历史样本未保存原始名称"


def _source_field(fields: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = fields.get(name)
        if value is not None and str(value).strip():
            return value
    return None


def _creator_snapshots(db_path: Path, batch_id: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with connection(db_path) as conn:
        sql = """
            SELECT r.record_id, r.batch_id, r.record_type, r.captured_at,
                   r.fields_json, r.creator_id, r.note_id, c.pseudonym,
                   c.creator_name, c.platform_creator_id,
                   c.location, b.captured_at AS batch_captured_at,
                   COALESCE(NULLIF(r.source, ''), b.source) AS batch_source
            FROM records r
            JOIN creators c ON c.creator_id = r.creator_id
            JOIN batches b ON b.batch_id = r.batch_id
        """
        params: list[Any] = []
        if batch_id:
            sql += " WHERE r.batch_id = ?"
            params.append(batch_id)
        sql += " ORDER BY b.captured_at DESC, r.record_id DESC"
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            fields = json.loads(row.pop("fields_json"))
            if not isinstance(fields, dict):
                fields = {}
            if not row.get("creator_name") or row["creator_name"] == row.get("pseudonym"):
                row["creator_name"] = _source_field(
                    fields,
                    ("creator_name", "nickname", "blogger_name", "达人名称", "达人昵称", "博主名称", "博主名", "创作者名称", "昵称", "账号名称", "作者昵称"),
                ) or row.get("creator_name")
            if not row.get("platform_creator_id"):
                row["platform_creator_id"] = _source_field(
                    fields,
                    ("platform_creator_id", "creator_id", "blogger_id", "达人ID", "博主ID", "创作者ID", "用户ID", "UID", "小红书号"),
                )
            row["pseudonym"] = creator_display_name(
                row.get("creator_name"), row.get("platform_creator_id"), row.get("pseudonym")
            )
            row["fields"] = fields
        batches = [
            dict(row)
            for row in conn.execute(
                "SELECT batch_id, source, captured_at, record_count FROM batches ORDER BY captured_at DESC, rowid DESC"
            ).fetchall()
        ]

    return rows, batches


def _merge_creator_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        latest.setdefault((row["creator_id"], row["record_type"]), row)

    creators: dict[str, dict[str, Any]] = {}
    for (creator_id, record_type), row in latest.items():
        creator = creators.setdefault(
            creator_id,
            {
                "creator_id": creator_id,
                "pseudonym": row["pseudonym"],
                "creator_name": row["creator_name"],
                "platform_creator_id": row["platform_creator_id"],
                "location": row.get("location") or "",
                "fields": {},
                "record_types": set(),
                "last_seen_at": row["batch_captured_at"],
            },
        )
        creator["record_types"].add(record_type)
        creator["fields"].update(row["fields"])
        if row["batch_captured_at"] > creator["last_seen_at"]:
            creator["last_seen_at"] = row["batch_captured_at"]

    output = []
    for creator in creators.values():
        fields = creator["fields"]
        followers = _metric_value(fields, "粉丝数")
        quote_fields = {
            "全部报价": _metric_detail(fields, "全部报价"),
            "图文笔记一口价": _metric_detail(fields, "图文笔记一口价"),
            "视频笔记一口价": _metric_detail(fields, "视频笔记一口价"),
        }
        image_price = quote_fields["图文笔记一口价"]["normalized_value"]
        video_price = quote_fields["视频笔记一口价"]["normalized_value"]
        # Only the source's overall exact quote belongs in the single-value
        # scatter/ranking. Image and video rates remain separate values.
        quote = quote_fields["全部报价"]["normalized_value"]
        reading = _metric_value(fields, "阅读中位数")
        if reading is None:
            reading = _metric_value(fields, "阅读中位数（日常）")
        engagement = _metric_value(fields, "互动中位数")
        if engagement is None:
            engagement = _metric_value(fields, "互动中位数（日常）")
        categories = [
            item.strip()
            for item in re.split(r"[、,，/|]+", str(fields.get("内容类目") or ""))
            if item.strip()
        ]
        output.append(
            {
                **creator,
                "record_types": sorted(creator["record_types"]),
                "followers": followers,
                "quote": quote,
                "image_price": image_price,
                "video_price": video_price,
                "quote_fields": quote_fields,
                "reading": reading,
                "engagement": engagement,
                "categories": categories,
            }
        )
    output.sort(key=lambda item: item["pseudonym"])
    return output


def creator_library(
    db_path: Path,
    *,
    query: str = "",
    category: str = "",
    followers_min: float | None = None,
    followers_max: float | None = None,
    quote_min: float | None = None,
    quote_max: float | None = None,
    location: str = "",
    sort_by: str = "pseudonym",
    sort_desc: bool = True,
    batch_id: str | None = None,
    source: str = "",
    creator_ids: list[str] | None = None,
) -> dict[str, Any]:
    rows, _batches = _creator_snapshots(db_path, batch_id=batch_id)
    if source:
        rows = [row for row in rows if row["batch_source"] == source]
    creators = _merge_creator_rows(rows)
    q = query.casefold().strip()
    location_filter = location.casefold().strip()
    selected = []
    for creator in creators:
        if q and q not in creator["pseudonym"].casefold() and q not in str(creator.get("platform_creator_id") or "").casefold():
            continue
        if category and category not in creator["categories"]:
            continue
        if followers_min is not None and (creator["followers"] is None or creator["followers"] < followers_min):
            continue
        if followers_max is not None and (creator["followers"] is None or creator["followers"] > followers_max):
            continue
        if quote_min is not None and (creator["quote"] is None or creator["quote"] < quote_min):
            continue
        if quote_max is not None and (creator["quote"] is None or creator["quote"] > quote_max):
            continue
        if location_filter and location_filter not in str(creator.get("location") or "").casefold():
            continue
        if creator_ids and creator["creator_id"] not in creator_ids:
            continue
        selected.append(
            {
                "creator_id": creator["creator_id"],
                "pseudonym": creator["pseudonym"],
                "creator_name": creator["pseudonym"],
                "platform_creator_id": creator["platform_creator_id"],
                "location": creator.get("location") or "",
                "followers": creator["followers"],
                "quote": creator["quote"],
                "image_price": creator["image_price"],
                "video_price": creator["video_price"],
                "quote_fields": creator["quote_fields"],
                "reading": creator["reading"],
                "engagement": creator["engagement"],
                "categories": creator["categories"],
                "record_types": creator["record_types"],
                "last_seen_at": creator["last_seen_at"],
            }
        )
    sort_fields = {"pseudonym": "pseudonym", "followers": "followers", "quote": "quote", "reading": "reading", "engagement": "engagement"}
    sort_field = sort_fields.get(sort_by, "pseudonym")
    if sort_field == "pseudonym":
        selected.sort(key=lambda item: item["pseudonym"].casefold(), reverse=not sort_desc)
    else:
        selected.sort(
            key=lambda item: (
                item[sort_field] is None,
                -(item[sort_field] or 0) if sort_desc else (item[sort_field] or 0),
                item["pseudonym"].casefold(),
            )
        )
    categories = sorted({category for item in creators for category in item["categories"]})
    locations = sorted({str(item.get("location")) for item in creators if item.get("location")})
    return {"count": len(selected), "categories": categories, "locations": locations, "creators": selected}


def overview_data(db_path: Path) -> dict[str, Any]:
    with connection(db_path) as conn:
        counts = {
            "creators": int(conn.execute("SELECT COUNT(*) FROM creators").fetchone()[0]),
            "notes": int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]),
            "comments": int(conn.execute("SELECT COUNT(*) FROM comment_snapshots").fetchone()[0]),
            "batches": int(conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]),
        }
        latest = conn.execute(
            """SELECT MAX(value) FROM (
                   SELECT MAX(captured_at) AS value FROM records
                   UNION ALL SELECT MAX(captured_at) FROM comment_snapshots
                   UNION ALL SELECT MAX(captured_at) FROM batches
               )"""
        ).fetchone()[0]
        sources = [
            dict(row)
            for row in conn.execute(
                """WITH source_counts AS (
                       SELECT COALESCE(NULLIF(r.source, ''), b.source) AS source_label,
                              COUNT(*) AS record_count, 0 AS comment_count
                       FROM records r JOIN batches b ON b.batch_id=r.batch_id
                       GROUP BY COALESCE(NULLIF(r.source, ''), b.source)
                       UNION ALL
                       SELECT source AS source_label, 0 AS record_count,
                              COUNT(*) AS comment_count
                       FROM comment_snapshots GROUP BY source
                   )
                   SELECT source_label AS source, SUM(record_count) AS record_count,
                          SUM(comment_count) AS comment_count
                   FROM source_counts GROUP BY source_label
                   ORDER BY (SUM(record_count)+SUM(comment_count)) DESC, source_label"""
            ).fetchall()
        ]
    for item in sources:
        item["total_count"] = int(item["record_count"] or 0) + int(item["comment_count"] or 0)
    total = sum(item["total_count"] for item in sources)
    for item in sources:
        item["share_pct"] = round(item["total_count"] * 100 / total, 1) if total else 0.0
    return {"counts": counts, "latest_updated_at": latest, "sources": sources, "source_scope": "按已保存记录行与评论行统计"}


def _topics(value: Any) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _note_day(value: Any) -> str | None:
    match = re.search(r"(\d{4})[-/.年](\d{1,2})(?:[-/.月](\d{1,2}))?", str(value or ""))
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3) or 1)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _note_text_analysis(notes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from research.r4_3_comment_analysis.comment_analysis import load_stopwords, tokenize

    stopwords = load_stopwords()
    result: dict[str, dict[str, Any]] = {}
    for kind, field in (("title", "title_raw"), ("body", "body")):
        frequencies: Counter[str] = Counter()
        sample_count = 0
        for note in notes:
            value = str(note.get(field) or "")
            if not value.strip():
                continue
            sample_count += 1
            frequencies.update(tokenize(value, stopwords))
        result[kind] = {
            "sample_count": sample_count,
            "token_count": sum(frequencies.values()),
            "frequencies": [{"word": word, "count": count} for word, count in frequencies.most_common(100)],
        }

    topic_frequencies: Counter[str] = Counter(
        topic for note in notes for topic in note.get("topics", []) if str(topic).strip()
    )
    result["topics"] = {
        "sample_count": sum(bool(note.get("topics")) for note in notes),
        "token_count": sum(topic_frequencies.values()),
        "frequencies": [{"word": word, "count": count} for word, count in topic_frequencies.most_common(100)],
    }
    return result


def _percent_detail(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"raw_value": None, "value": None}
    value = raw
    if isinstance(raw, str):
        cleaned = raw.strip().replace(",", "")
        if cleaned in {"", "-", "—", "--"}:
            return {"raw_value": raw, "value": None}
        if cleaned.endswith("%"):
            cleaned = cleaned[:-1].strip()
        try:
            value = float(cleaned)
        except ValueError:
            return {"raw_value": raw, "value": None}
    try:
        number = float(value)
    except (TypeError, ValueError):
        return {"raw_value": raw, "value": None}
    return {"raw_value": raw, "value": number}


def _audience_distribution(fields: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    raw = next((fields.get(key) for key in keys if fields.get(key) is not None), None)
    if not isinstance(raw, dict):
        return []
    return [
        {"raw_label": str(label), "label": str(label).replace("_", "–"), **_percent_detail(value)}
        for label, value in raw.items()
        if _percent_detail(value)["value"] is not None
    ]


def _audience_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row["record_type"] == "fan_analysis":
            latest.setdefault((str(row["creator_id"]), str(row["batch_source"])), row)

    profiles = []
    label_map = {"female": "女性", "male": "男性", "woman": "女性", "man": "男性"}
    for row in latest.values():
        fields = row["fields"]
        gender = _audience_distribution(fields, ("gender_share", "gender_distribution", "性别分布", "粉丝性别分布"))
        for item in gender:
            item["label"] = label_map.get(item["raw_label"].casefold(), item["label"])
        age = _audience_distribution(fields, ("age_share", "age_distribution", "年龄分布", "粉丝年龄分布"))
        gender_top = fields.get("gender_top") if isinstance(fields.get("gender_top"), dict) else {}
        age_top = fields.get("age_top") if isinstance(fields.get("age_top"), dict) else {}
        gender_leading = gender_top.get("leading_group") or gender_top.get("主要类别")
        gender_leading_share = gender_top.get("share_percent") or gender_top.get("占比")
        age_leading = age_top.get("leading_band") or age_top.get("主要年龄段")
        age_leading_share = age_top.get("share_percent") or age_top.get("占比")
        if not gender_leading and gender:
            top = max(gender, key=lambda item: item["value"])
            gender_leading, gender_leading_share = top["raw_label"], top["raw_value"]
        if not age_leading and age:
            top = max(age, key=lambda item: item["value"])
            age_leading, age_leading_share = top["raw_label"], top["raw_value"]
        gender_leading_detail = _percent_detail(gender_leading_share)
        age_leading_detail = _percent_detail(age_leading_share)

        def list_entries(keys: tuple[str, ...], label_keys: tuple[str, ...], value_keys: tuple[str, ...]) -> list[dict[str, Any]]:
            raw = next((fields.get(key) for key in keys if fields.get(key) is not None), None)
            if isinstance(raw, dict):
                return [
                    {"raw_label": str(label), "label": str(label), **_percent_detail(value)}
                    for label, value in raw.items()
                ]
            if isinstance(raw, list):
                result = []
                for item in raw:
                    if isinstance(item, dict):
                        label = next((item.get(key) for key in label_keys if item.get(key) is not None), None)
                        value = next((item.get(key) for key in value_keys if item.get(key) is not None), None)
                    else:
                        label, value = item, None
                    if label is not None and str(label).strip():
                        result.append({"raw_label": str(label), "label": str(label), **_percent_detail(value)})
                return result
            return []

        regions = list_entries(
            ("top_provinces", "top_regions", "粉丝地域", "粉丝省份"),
            ("name", "region", "province", "地区", "省份"),
            ("share", "share_percent", "占比", "percent"),
        )
        cities = list_entries(
            ("top_cities", "粉丝城市"),
            ("name", "city", "城市"),
            ("share", "share_percent", "占比", "percent"),
        )
        interests = list_entries(
            ("interest_share", "top_interests", "兴趣分布", "粉丝兴趣"),
            ("name", "interest", "兴趣", "标签"),
            ("share", "share_percent", "占比", "percent"),
        )
        devices = list_entries(
            ("device_brand_share_examples", "device_share", "设备品牌占比", "设备分布"),
            ("name", "brand", "device", "设备"),
            ("share", "share_percent", "占比", "percent"),
        )
        apple_raw = fields.get("apple_device_share_raw")
        if apple_raw is None:
            apple_raw = fields.get("apple_device_share_percent")
        if apple_raw is not None and not devices:
            devices = [{"raw_label": "Apple（页面提供值）", "label": "Apple（页面提供值）", **_percent_detail(apple_raw)}]

        shares = {}
        for name, keys in {
            "active": ("active_share_raw", "active_share_percent", "活跃粉丝占比"),
            "read": ("read_share_raw", "read_share_percent", "阅读粉丝占比"),
            "interaction": ("interaction_share_raw", "interaction_share_percent", "互动粉丝占比"),
            "purchase": ("purchase_share_raw", "purchase_share_percent", "下单粉丝占比"),
        }.items():
            raw = next((fields.get(key) for key in keys if fields.get(key) is not None), None)
            shares[name] = _percent_detail(raw)

        profiles.append({
            "creator_id": row["creator_id"],
            "creator_name": row["pseudonym"],
            "source": row["batch_source"],
            "captured_at": row["captured_at"],
            "period": str(fields.get("period") or fields.get("统计周期") or "未标明"),
            "gender_distribution": gender,
            "gender_leading": label_map.get(str(gender_leading).casefold(), str(gender_leading or "")),
            "gender_leading_raw": str(gender_leading or ""),
            "gender_leading_share": gender_leading_detail,
            "age_distribution": age,
            "age_leading": str(age_leading or ""),
            "age_leading_share": age_leading_detail,
            "regions": regions,
            "cities": cities,
            "interests": interests,
            "devices": devices,
            "shares": shares,
        })
    return sorted(profiles, key=lambda item: (item["source"], item["period"], item["creator_name"], item["creator_id"]))


def _audience_groups(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for profile in profiles:
        grouped.setdefault((profile["source"], profile["period"]), []).append(profile)
    return [
        {
            "group_id": index,
            "source": key[0],
            "period": key[1],
            "label": f"{key[0]} · {key[1]}（n={len(items)}）",
            "creator_count": len(items),
            "profiles": items,
        }
        for index, (key, items) in enumerate(sorted(grouped.items()), start=1)
    ]


def notes_data(
    db_path: Path,
    *,
    batch_id: str | None = None,
    source: str = "",
    creator_id: str = "",
    note_id: str = "",
    category: str = "",
    date_from: str = "",
    date_to: str = "",
    note_type: str = "",
    topic: str = "",
    query: str = "",
) -> dict[str, Any]:
    with connection(db_path) as conn:
        creators = [dict(row) for row in conn.execute(
            "SELECT DISTINCT c.creator_id, c.creator_name, c.platform_creator_id, c.pseudonym FROM creators c JOIN notes n ON n.creator_id=c.creator_id ORDER BY c.creator_name, c.platform_creator_id"
        ).fetchall()]
        for creator in creators:
            creator["pseudonym"] = creator_display_name(
                creator.get("creator_name"), creator.get("platform_creator_id"), creator.get("pseudonym")
            )
        batches = [dict(row) for row in conn.execute(
            "SELECT DISTINCT b.batch_id, b.source, b.captured_at FROM batches b JOIN records r ON r.batch_id=b.batch_id WHERE r.note_id IS NOT NULL ORDER BY b.captured_at DESC, b.rowid DESC"
        ).fetchall()]
        base_rows = conn.execute(
            "SELECT topics_json, note_type FROM notes"
        ).fetchall()
        available_topics = sorted({item for row in base_rows for item in _topics(row["topics_json"])})
        note_types = sorted({str(row["note_type"]) for row in base_rows if row["note_type"]})
        note_options = [dict(row) for row in conn.execute(
            "SELECT n.note_id,n.title,c.creator_name,c.platform_creator_id,c.pseudonym FROM notes n JOIN creators c ON c.creator_id=n.creator_id ORDER BY c.pseudonym,n.title,n.note_id"
        ).fetchall()]
        for option in note_options:
            option["creator_name"] = creator_display_name(
                option.get("creator_name"), option.get("platform_creator_id"), option.get("pseudonym")
            )

        sql = """SELECT n.note_id, n.platform_note_id, n.source_url,
                        n.creator_id, c.creator_name, c.platform_creator_id, c.pseudonym,
                        c.category AS creator_category, n.title, n.body, n.topics_json,
                        n.published_at, n.note_type, n.source AS note_source,
                        n.first_seen_at, n.last_seen_at,
                        r.record_id AS metric_record_id, r.batch_id AS metric_batch_id,
                        r.captured_at AS metric_captured_at,
                        COALESCE(NULLIF(r.source, ''), b.source) AS metric_source,
                        COALESCE(cs.comment_count, 0) AS comment_count
                 FROM notes n JOIN creators c ON c.creator_id=n.creator_id
                 LEFT JOIN records r ON r.record_id=(
                     SELECT r2.record_id FROM records r2
                     WHERE r2.note_id=n.note_id AND r2.record_type IN ('note_detail','xhs_public_note')"""
        params: list[Any] = []
        if batch_id:
            sql += " AND r2.batch_id=?"
            params.append(batch_id)
        if source:
            sql += " AND COALESCE(NULLIF(r2.source,''),(SELECT b2.source FROM batches b2 WHERE b2.batch_id=r2.batch_id))=?"
            params.append(source)
        sql += " ORDER BY r2.captured_at DESC, r2.record_id DESC LIMIT 1)"
        sql += " LEFT JOIN batches b ON b.batch_id=r.batch_id"
        comment_subquery = "SELECT s.note_id, COUNT(*) AS comment_count FROM comment_snapshots s JOIN batches cb ON cb.batch_id=s.batch_id"
        comment_clauses = []
        if batch_id:
            comment_clauses.append("s.batch_id=?")
            params.append(batch_id)
        if source:
            comment_clauses.append("COALESCE(NULLIF(s.source,''),cb.source)=?")
            params.append(source)
        if comment_clauses:
            comment_subquery += " WHERE " + " AND ".join(comment_clauses)
        comment_subquery += " GROUP BY s.note_id"
        sql += f" LEFT JOIN ({comment_subquery}) cs ON cs.note_id=n.note_id"
        clauses: list[str] = []
        if creator_id:
            clauses.append("n.creator_id=?")
            params.append(creator_id)
        if note_id:
            clauses.append("n.note_id=?")
            params.append(note_id)
        if category:
            clauses.append("COALESCE(NULLIF(c.category,''),'')=?")
            params.append(category)
        if note_type:
            clauses.append("n.note_type=?")
            params.append(note_type)
        if batch_id:
            clauses.append("(EXISTS (SELECT 1 FROM records rb WHERE rb.note_id=n.note_id AND rb.batch_id=? AND (?='' OR COALESCE(NULLIF(rb.source,''),(SELECT bb.source FROM batches bb WHERE bb.batch_id=rb.batch_id))=?)) OR EXISTS (SELECT 1 FROM comment_snapshots sc JOIN batches bc ON bc.batch_id=sc.batch_id WHERE sc.note_id=n.note_id AND sc.batch_id=? AND (?='' OR COALESCE(NULLIF(sc.source,''),bc.source)=?)))")
            params.extend([batch_id, source, source, batch_id, source, source])
        elif source:
            clauses.append("(EXISTS (SELECT 1 FROM records rs WHERE rs.note_id=n.note_id AND COALESCE(NULLIF(rs.source,''),(SELECT bs.source FROM batches bs WHERE bs.batch_id=rs.batch_id))=?) OR EXISTS (SELECT 1 FROM comment_snapshots sc JOIN batches bc ON bc.batch_id=sc.batch_id WHERE sc.note_id=n.note_id AND COALESCE(NULLIF(sc.source,''),bc.source)=?))")
            params.extend([source, source])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(r.captured_at,n.last_seen_at) DESC, n.note_id"
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            row["pseudonym"] = creator_display_name(
                row.get("creator_name"), row.get("platform_creator_id"), row.get("pseudonym")
            )

        record_ids = [int(row["metric_record_id"]) for row in rows if row["metric_record_id"] is not None]
        metric_values: dict[int, dict[str, Any]] = {}
        for offset in range(0, len(record_ids), 500):
            chunk = record_ids[offset:offset + 500]
            if not chunk:
                continue
            marks = ",".join("?" for _ in chunk)
            metric_rows = conn.execute(
                f"SELECT record_id, metric_key, numeric_value, raw_value, unit, period, parse_status FROM metric_values WHERE record_id IN ({marks})",
                chunk,
            ).fetchall()
            for metric in metric_rows:
                metric_values.setdefault(int(metric["record_id"]), {})[str(metric["metric_key"])] = {
                    "value": float(metric["numeric_value"]) if metric["numeric_value"] is not None else None,
                    "raw_value": metric["raw_value"],
                    "unit": metric["unit"],
                    "period": metric["period"],
                    "parse_status": metric["parse_status"],
                }

        history_rows = []
        note_ids = list(dict.fromkeys(row["note_id"] for row in rows))
        for offset in range(0, len(note_ids), 500):
            chunk = note_ids[offset:offset + 500]
            if not chunk:
                continue
            marks = ",".join("?" for _ in chunk)
            history_rows.extend(conn.execute(
                f"""SELECT r.note_id, r.captured_at, m.metric_key, m.numeric_value,
                           m.period, COALESCE(NULLIF(r.source, ''), b.source) AS source
                    FROM records r JOIN metric_values m ON m.record_id=r.record_id
                    JOIN batches b ON b.batch_id=r.batch_id
                    WHERE r.note_id IN ({marks}) AND r.record_type IN ('note_detail','xhs_public_note')
                      AND m.numeric_value IS NOT NULL AND m.period<>''""",
                chunk,
            ).fetchall())

    filtered = []
    for row in rows:
        topics = _topics(row["topics_json"])
        day = _note_day(row["published_at"])
        if topic and topic not in topics:
            continue
        if date_from and (day is None or day < date_from):
            continue
        if date_to and (day is None or day > date_to):
            continue
        text_for_search = " ".join(
            str(value or "")
            for value in (row.get("title"), row.get("body"), " ".join(topics), row.get("creator_name"), row.get("platform_creator_id"))
        )
        if query and query.casefold() not in text_for_search.casefold():
            continue
        metrics = metric_values.get(int(row["metric_record_id"]), {}) if row["metric_record_id"] is not None else {}
        get_metric = lambda *keys: next((metrics[key]["value"] for key in keys if key in metrics), None)
        item = {
            "note_id": row["note_id"], "local_note_id": row["note_id"],
            "platform_note_id": row["platform_note_id"], "source_url": row["source_url"],
            "creator_id": row["creator_id"], "creator_name": row["pseudonym"],
            "platform_creator_id": row["platform_creator_id"],
            "title": row["title"] or "（无标题）", "title_raw": row["title"],
            "body": row["body"] if row["body"] is not None else "",
            "topics": topics, "topics_json_raw": row["topics_json"],
            "category": row["creator_category"] or "", "published_at": row["published_at"],
            "note_type": row["note_type"] or "", "source": source or row["metric_source"] or row["note_source"] or "未知来源",
            "batch_id": row["metric_batch_id"] or "", "metric_captured_at": row["metric_captured_at"] or "",
            "comment_count": int(row["comment_count"] or 0), "read_count": get_metric("阅读量", "阅读数"),
            "like_count": get_metric("点赞量", "点赞数"), "favorite_count": get_metric("收藏量", "收藏数"),
            "interaction_comment_count": get_metric("评论量", "评论数"), "share_count": get_metric("分享量", "分享数"),
            "exposure_count": get_metric("曝光量", "曝光数"),
            "follow_count": get_metric("新增关注", "新增关注量", "关注量"),
            "metrics": metrics,
        }
        if item["read_count"] is not None:
            item["read_count"] = float(item["read_count"])
        interaction_values = [item[key] for key in ("like_count", "favorite_count", "interaction_comment_count", "share_count") if item[key] is not None]
        item["interaction_score"] = sum(interaction_values) if interaction_values else None
        item["label"] = f"{item['creator_name']} · {item['title']}"
        filtered.append(item)

    source_rows: dict[str, list[dict[str, Any]]] = {}
    for item in filtered:
        source_rows.setdefault(item["source"], []).append(item)
    top_read = []
    top_interactions = []
    for source, source_items in sorted(source_rows.items()):
        top_read.append({"source": source, "items": [
            {"label": row["label"], "value": row["read_count"]} for row in sorted(source_items, key=lambda row: (row["read_count"] is None, -(row["read_count"] or 0)))[:10] if row["read_count"] is not None
        ]})
        top_interactions.append({"source": source, "items": [
            {"label": row["label"], "value": row["interaction_score"]} for row in sorted(source_items, key=lambda row: (row["interaction_score"] is None, -(row["interaction_score"] or 0)))[:10] if row["interaction_score"] is not None
        ]})

    publish_counts = Counter(day[:7] for row in filtered if (day := _note_day(row["published_at"])) is not None)
    trend_groups: dict[tuple[str, str, str, str], dict[str, float]] = {}
    note_label = {row["note_id"]: row["label"] for row in filtered}
    for hist in history_rows:
        if hist["note_id"] not in note_label:
            continue
        if source and hist["source"] != source:
            continue
        key = (str(hist["note_id"]), str(hist["metric_key"]), str(hist["source"]), str(hist["period"]))
        trend_groups.setdefault(key, {})[str(hist["captured_at"])] = float(hist["numeric_value"])
    metric_trends = [
        {"note_id": note_id, "label": f"{note_label[note_id]} · {metric} · {trend_source} · {period}", "source": trend_source, "metric_key": metric, "period": period, "points": [{"captured_at": time, "value": value} for time, value in sorted(points.items())]}
        for (note_id, metric, trend_source, period), points in trend_groups.items() if len(points) >= 2
    ]

    metric_keys = {
        "曝光量": "exposure_count",
        "阅读量": "read_count",
        "点赞量": "like_count",
        "收藏量": "favorite_count",
        "评论量": "interaction_comment_count",
        "分享量": "share_count",
        "新增关注": "follow_count",
    }
    metric_values_by_source: dict[tuple[str, str], list[float]] = {}
    for item in filtered:
        for metric, field in metric_keys.items():
            value = item.get(field)
            if value is not None:
                metric_values_by_source.setdefault((item["source"], metric), []).append(float(value))

    metric_distributions = []
    interaction_boxplot = []
    for (metric_source, metric), values in sorted(metric_values_by_source.items()):
        ordered = sorted(values)
        if ordered[0] == ordered[-1]:
            bins = [{"label": f"{ordered[0]:g}", "count": len(ordered)}]
        else:
            bin_count = min(5, max(2, len(ordered)))
            width = (ordered[-1] - ordered[0]) / bin_count
            counts = [0] * bin_count
            for value in ordered:
                index = min(bin_count - 1, int((value - ordered[0]) / width))
                counts[index] += 1
            bins = [
                {
                    "label": f"{ordered[0] + index * width:,.0f}–{ordered[0] + (index + 1) * width:,.0f}",
                    "count": count,
                }
                for index, count in enumerate(counts)
            ]
        metric_distributions.append({
            "source": metric_source,
            "metric": metric,
            "sample_count": len(ordered),
            "bins": bins,
        })
        if metric in {"点赞量", "收藏量", "评论量", "分享量"}:
            quantiles = pd.Series(ordered, dtype="float64").quantile([0, 0.25, 0.5, 0.75, 1]).tolist()
            interaction_boxplot.append({
                "label": f"{metric_source} · {metric}",
                "source": metric_source,
                "metric": metric,
                "sample_count": len(ordered),
                "values": [float(value) for value in quantiles],
            })

    exposure_read_scatter = [
        {"label": item["label"], "source": item["source"], "x": item["exposure_count"], "y": item["read_count"]}
        for item in filtered if item["exposure_count"] is not None and item["read_count"] is not None
    ]
    reading_interaction_scatter = [
        {"label": item["label"], "source": item["source"], "x": item["read_count"], "y": item["interaction_score"]}
        for item in filtered if item["read_count"] is not None and item["interaction_score"] is not None
    ]

    public_rows = filtered
    text_analysis = _note_text_analysis(filtered)
    return {
        "count": len(filtered), "notes": public_rows, "creators": creators,
        "note_options": [
            {"note_id": row["note_id"], "title": row["title"] or "（无标题）", "creator_name": row["creator_name"]}
            for row in note_options
        ],
        "note_types": note_types, "topics": available_topics, "batches": batches,
        "charts": {
            "top_read": top_read, "top_interactions": top_interactions,
            "publish_distribution": [{"month": month, "count": count} for month, count in sorted(publish_counts.items())],
            "metric_trends": metric_trends,
            "metric_distributions": metric_distributions,
            "interaction_boxplot": interaction_boxplot,
            "exposure_read_scatter": exposure_read_scatter,
            "reading_interaction_scatter": reading_interaction_scatter,
        },
        "text_analysis": text_analysis,
        "availability": {
            "read_rankings": "按来源分组，使用每篇笔记最新可用指标快照" if any(group["items"] for group in top_read) else "当前筛选没有可比较的阅读指标",
            "metric_trends": "仅展示同一笔记、来源、指标和统计周期的重复快照" if metric_trends else "缺少同一笔记、来源、指标和统计周期的重复快照",
            "metric_distributions": f"逐来源展示精确数值；共 {sum(item['sample_count'] for item in metric_distributions)} 个指标样本" if metric_distributions else "当前筛选没有精确指标值",
            "interaction_boxplot": f"按来源与互动指标分别计算；共 {sum(item['sample_count'] for item in interaction_boxplot)} 个样本" if interaction_boxplot else "当前筛选没有可用互动数值",
        },
    }


def _observed_publish_frequency(
    db_path: Path,
    creator_ids: list[str],
    *,
    batch_id: str,
    source: str = "",
) -> tuple[dict[str, int], str | None]:
    if not creator_ids:
        return {}, None
    marks = ",".join("?" for _ in creator_ids)
    with connection(db_path) as conn:
        batch = conn.execute("SELECT captured_at FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        batch_day = _note_day(batch["captured_at"] if batch else None)
        rows = conn.execute(
            f"""SELECT DISTINCT n.creator_id, n.note_id, n.published_at,
                       COALESCE(NULLIF(r.source,''), b.source) AS source
                FROM records r JOIN batches b ON b.batch_id=r.batch_id
                JOIN notes n ON n.note_id=r.note_id
                WHERE r.batch_id=? AND r.note_id IS NOT NULL
                  AND r.record_type IN ('note_detail','xhs_public_note')
                  AND n.creator_id IN ({marks})""",
            [batch_id, *creator_ids],
        ).fetchall()
    if not batch_day:
        return {}, None
    end_day = date.fromisoformat(batch_day)
    start_day = end_day - timedelta(days=29)
    counts = {creator_id: 0 for creator_id in creator_ids}
    for row in rows:
        if source and row["source"] != source:
            continue
        published_day = _note_day(row["published_at"])
        if published_day and start_day <= date.fromisoformat(published_day) <= end_day:
            counts[str(row["creator_id"])] = counts.get(str(row["creator_id"]), 0) + 1
    return counts, f"该批次记录中，{start_day.isoformat()} 至 {end_day.isoformat()} 已采笔记数；不代表完整发文总量"


def compare_creators(
    db_path: Path,
    creator_ids: list[str],
    *,
    batch_id: str,
    source: str = "",
) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(creator_ids))
    result = creator_library(db_path, batch_id=batch_id, source=source, creator_ids=unique_ids, sort_by="pseudonym")
    counts, frequency_scope = _observed_publish_frequency(db_path, unique_ids, batch_id=batch_id, source=source)
    for creator in result["creators"]:
        creator["publish_frequency_30d"] = counts.get(creator["creator_id"])
        creator["publish_frequency_scope"] = frequency_scope or "批次时间不足，无法计算 30 日窗口"
    result["availability"] = {
        "publish_frequency": frequency_scope or "批次时间不足，无法计算 30 日窗口；缺失不当作 0",
        "comparison_scope": "精确 creator_id；单一批次；来源按筛选值约束",
    }
    result["requested_creator_ids"] = unique_ids
    result["missing_creator_ids"] = [creator_id for creator_id in unique_ids if creator_id not in {item["creator_id"] for item in result["creators"]}]
    return result


def compare_notes(
    db_path: Path,
    note_ids: list[str],
    *,
    batch_id: str,
    source: str = "",
) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(note_ids))
    if not 2 <= len(unique_ids) <= 4:
        raise ValueError("请选择 2 至 4 篇笔记进行对比")
    data = notes_data(db_path, batch_id=batch_id, source=source)
    note_by_id = {item["note_id"]: item for item in data["notes"]}
    selected = [note_by_id[note_id] for note_id in unique_ids if note_id in note_by_id]
    if len(selected) < 2:
        raise ValueError("所选批次和来源中至少需要 2 篇笔记有可比记录")
    sources = {item["source"] for item in selected}
    if len(sources) > 1:
        raise ValueError("所选笔记来源不同，请先选择同一来源")
    creator_ids = list(dict.fromkeys(item["creator_id"] for item in selected))
    creators = creator_library(db_path, batch_id=batch_id, source=source, creator_ids=creator_ids)
    creator_by_id = {item["creator_id"]: item for item in creators["creators"]}
    frequency, frequency_scope = _observed_publish_frequency(db_path, creator_ids, batch_id=batch_id, source=source)
    for item in selected:
        creator = creator_by_id.get(item["creator_id"], {})
        item["followers"] = creator.get("followers")
        item["quote"] = creator.get("quote")
        item["quote_fields"] = creator.get("quote_fields", [])
        item["publish_frequency_30d"] = frequency.get(item["creator_id"])
        components = [item.get(key) for key in ("like_count", "favorite_count", "interaction_comment_count", "share_count")]
        denominator = item.get("read_count")
        item["interaction_rate"] = (
            round(sum(float(value) for value in components) / float(denominator) * 100, 4)
            if denominator is not None and float(denominator) > 0 and all(value is not None for value in components)
            else None
        )
        item["saved_comment_count"] = item.get("comment_count")
        item["publish_frequency_scope"] = frequency_scope or "批次时间不足，无法计算 30 日窗口"
    return {
        "count": len(selected),
        "batch_id": batch_id,
        "source": next(iter(sources)),
        "notes": selected,
        "missing_note_ids": [note_id for note_id in unique_ids if note_id not in note_by_id],
        "availability": {
            "interaction_rate": "(点赞+收藏+评论+分享) / 阅读 × 100%；四项与阅读值均精确可用时计算",
            "publish_frequency": frequency_scope or "批次时间不足，无法计算 30 日窗口；缺失不当作 0",
            "saved_comments": "另列本地保存的评论行数，不与页面评论指标混为一项",
        },
    }


def note_compare_options(db_path: Path, *, batch_id: str, source: str = "") -> list[dict[str, Any]]:
    record_match = "r.note_id=n.note_id AND r.batch_id=? AND r.record_type IN ('note_detail','xhs_public_note')"
    comment_match = "s.note_id=n.note_id AND s.batch_id=?"
    params: list[Any] = [batch_id, batch_id]
    if source:
        record_match += " AND COALESCE(NULLIF(r.source,''),(SELECT rb.source FROM batches rb WHERE rb.batch_id=r.batch_id))=?"
        comment_match += " AND COALESCE(NULLIF(s.source,''),(SELECT sb.source FROM batches sb WHERE sb.batch_id=s.batch_id))=?"
        params = [batch_id, source, batch_id, source]
    with connection(db_path) as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT n.note_id,n.title,c.creator_name,c.platform_creator_id,c.pseudonym
                FROM notes n JOIN creators c ON c.creator_id=n.creator_id
                WHERE EXISTS (SELECT 1 FROM records r WHERE {record_match})
                   OR EXISTS (SELECT 1 FROM comment_snapshots s WHERE {comment_match})
                ORDER BY c.creator_name,n.title,n.note_id""",
            params,
        ).fetchall()
    options = [dict(row) for row in rows]
    for item in options:
        item["creator_name"] = creator_display_name(item.get("creator_name"), item.get("platform_creator_id"), item.get("pseudonym"))
    return options


def analytics_data(
    db_path: Path,
    batch_id: str | None = None,
    *,
    source: str = "",
    creator_id: str = "",
    category: str = "",
    date_from: str = "",
    date_to: str = "",
    note_type: str = "",
    topic: str = "",
    query: str = "",
    note_id: str = "",
    word_kind: str = "body",
) -> dict[str, Any]:
    rows, batches = _creator_snapshots(db_path, batch_id=batch_id)
    if source:
        rows = [row for row in rows if row["batch_source"] == source]
    if creator_id:
        rows = [row for row in rows if row["creator_id"] == creator_id]
    if date_from:
        rows = [row for row in rows if str(row["captured_at"])[:10] >= date_from]
    if date_to:
        rows = [row for row in rows if str(row["captured_at"])[:10] <= date_to]
    if query:
        lowered = query.casefold()
        rows = [
            row for row in rows
            if lowered in json.dumps(row["fields"], ensure_ascii=False).casefold()
            or lowered in str(row.get("creator_name") or "").casefold()
            or lowered in str(row.get("platform_creator_id") or "").casefold()
        ]
    if note_type:
        rows = [
            row for row in rows
            if row["record_type"] not in {"note_detail", "xhs_public_note"}
            or str(row["fields"].get("笔记类型") or row["fields"].get("内容类型") or "") == note_type
        ]
    if topic:
        rows = [
            row for row in rows
            if row["record_type"] not in {"note_detail", "xhs_public_note"}
            or topic in str(row["fields"].get("话题") or row["fields"].get("内容标签") or "")
        ]
    creators = _merge_creator_rows(rows)
    if category:
        creators = [creator for creator in creators if category in creator["categories"]]
        selected_creator_ids = {creator["creator_id"] for creator in creators}
        rows = [row for row in rows if row["creator_id"] in selected_creator_ids]
    selected_creator_ids = {creator["creator_id"] for creator in creators}
    followers = [item for item in creators if item["followers"] is not None]
    quotes = [item for item in creators if item["quote"] is not None]
    reads = [item for item in creators if item["reading"] is not None and item["engagement"] is not None]

    fan_bins = [(0, 10000, "1 万以下"), (10000, 50000, "1–5 万"), (50000, 100000, "5–10 万"), (100000, 500000, "10–50 万"), (500000, None, "50 万以上")]
    follower_distribution = [
        {"name": label, "value": sum(1 for item in followers if item["followers"] >= low and (high is None or item["followers"] < high))}
        for low, high, label in fan_bins
    ]
    quote_bins = [(0, 1000, "1 千以下"), (1000, 5000, "1–5 千"), (5000, 10000, "5 千–1 万"), (10000, 50000, "1–5 万"), (50000, 100000, "5–10 万"), (100000, None, "10 万以上")]
    quote_distribution = [
        {"name": label, "value": sum(1 for item in quotes if item["quote"] >= low and (high is None or item["quote"] < high))}
        for low, high, label in quote_bins
    ]
    category_counts: dict[str, int] = {}
    for creator in creators:
        for category_label in creator["categories"]:
            category_counts[category_label] = category_counts.get(category_label, 0) + 1

    location_counts = Counter(
        str(creator["location"]).strip() for creator in creators if str(creator.get("location") or "").strip()
    )
    quote_status_counts = Counter(
        detail["parse_status"]
        for creator in creators
        for detail in creator["quote_fields"].values()
        if detail["parse_status"] != "missing"
    )
    quote_status_counts["missing"] = max(0, len(creators) * 3 - sum(quote_status_counts.values()))

    latest_note_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row["record_type"] not in {"note_detail", "xhs_public_note"}:
            continue
        if note_id and row.get("note_id") != note_id:
            continue
        latest_note_rows.setdefault((str(row.get("note_id") or ""), str(row["batch_source"])), row)
    note_rows = list(latest_note_rows.values())
    note_metrics = []
    for row in note_rows:
        fields = row["fields"]
        item = {
            "creator": row["pseudonym"],
            "source": row["batch_source"],
            "batch_id": row["batch_id"],
            "note_id": row.get("note_id"),
            "published_at": fields.get("发布时间"),
        }
        for key in ("曝光量", "阅读量", "点赞量", "收藏量", "评论量", "分享量", "新增关注"):
            value = _metric_value(fields, key)
            if value is not None:
                item[key] = value
        note_metrics.append(item)

    fan_keys = ("活跃粉丝占比", "阅读粉丝占比", "互动粉丝占比", "下单粉丝占比")
    fan_averages = []
    for key in fan_keys:
        values = [value for creator in creators if (value := _metric_value(creator["fields"], key)) is not None]
        fan_averages.append({"name": key, "value": round(sum(values) / len(values), 2) if values else None, "count": len(values)})

    numeric_rows = []
    for creator in creators:
        numeric_rows.append(
            {
                "粉丝数": creator["followers"],
                "报价": creator["quote"],
                "阅读中位数": creator["reading"],
                "互动中位数": creator["engagement"],
            }
        )
    frame = pd.DataFrame(numeric_rows)
    correlation = None
    complete = frame.dropna()
    columns = list(frame.columns)
    pair_counts = [
        [int(frame[[left, right]].dropna().shape[0]) for left in columns]
        for right in columns
    ]
    max_pair_count = max((count for y, row in enumerate(pair_counts) for x, count in enumerate(row) if x != y), default=0)
    if max_pair_count >= 3:
        correlation_frame = frame.corr(numeric_only=True, min_periods=3)
        correlation = {
            "labels": list(correlation_frame.columns),
            "values": [[None if pd.isna(value) else round(float(value), 4) for value in row] for row in correlation_frame.to_numpy()],
            "pair_counts": pair_counts,
            "sample_count": max_pair_count,
        }

    trend_groups: list[dict[str, Any]] = []
    if not batch_id:
        with connection(db_path) as conn:
            snapshots = [dict(row) for row in conn.execute(
                """SELECT r.creator_id, r.captured_at, r.record_id, m.numeric_value,
                          m.period, COALESCE(NULLIF(r.source, ''), b.source) AS source
                   FROM records r JOIN metric_values m ON m.record_id=r.record_id
                   JOIN batches b ON b.batch_id=r.batch_id
                   WHERE r.creator_id IS NOT NULL AND m.metric_key='粉丝数'
                     AND m.parse_status='parsed' AND m.numeric_value IS NOT NULL
                     AND TRIM(m.period)<>''
                   ORDER BY r.captured_at, r.record_id"""
            ).fetchall()]
        by_source_period: dict[tuple[str, str], dict[str, dict[str, float]]] = {}
        for snapshot in snapshots:
            if str(snapshot["creator_id"]) not in selected_creator_ids:
                continue
            if source and snapshot["source"] != source:
                continue
            captured_day = str(snapshot["captured_at"])[:10]
            if date_from and captured_day < date_from:
                continue
            if date_to and captured_day > date_to:
                continue
            key = (str(snapshot["source"]), str(snapshot["period"]))
            by_time = by_source_period.setdefault(key, {})
            values = by_time.setdefault(str(snapshot["captured_at"]), {})
            # At a shared timestamp, keep the last saved snapshot for that creator.
            values[str(snapshot["creator_id"])] = float(snapshot["numeric_value"])
        for (trend_source, period), by_time in sorted(by_source_period.items()):
            times = sorted(by_time)
            if len(times) < 2:
                continue
            common_creators = set.intersection(*(set(by_time[time]) for time in times))
            if not common_creators:
                continue
            points = []
            for captured_at in times:
                values = [by_time[captured_at][creator_id] for creator_id in common_creators]
                points.append({
                    "captured_at": captured_at,
                    "median_followers": float(pd.Series(values).median()),
                    "creator_count": len(common_creators),
                })
            trend_groups.append({"source": trend_source, "period": period, "metric_key": "粉丝数", "points": points})

    note_analysis = notes_data(
        db_path,
        batch_id=batch_id,
        source=source,
        creator_id=creator_id,
        note_id=note_id,
        category=category,
        date_from=date_from,
        date_to=date_to,
        note_type=note_type,
        topic=topic,
        query=query,
    )
    selected_kind = word_kind if word_kind in {"title", "body", "topics"} else "body"
    selected_text = note_analysis["text_analysis"][selected_kind]
    frequency = selected_text["frequencies"][:20 if selected_kind == "topics" else 30]
    audience_profiles = _audience_profiles(rows)
    audience_groups = _audience_groups(audience_profiles)

    return {
        "scope": "当前批次" if batch_id else "全部历史快照（创作者按各类型最新记录归并）",
        "counts": {"creators": len(creators), "followers_available": len(followers), "quotes_available": len(quotes), "notes": len(note_metrics), "batches": 1 if batch_id and any(batch["batch_id"] == batch_id for batch in batches) else (0 if batch_id else len(batches))},
        "availability": {
            "follower_price": "可展示样本点" if any(item["followers"] is not None and item["quote"] is not None for item in creators) else "缺少共同字段",
            "reading_engagement": "可展示样本点" if reads else "缺少共同字段",
            "correlation": f"已按两两完整值计算（最多 {max_pair_count} 个样本）" if correlation else f"样本不足：每组指标至少需要 3 条共同有效值，目前共同完整样本 {len(complete)} 条",
            "history": (
                f"按来源与统计周期分组；仅比较跨快照重复创作者（{len(trend_groups)} 组可比数据）"
                if trend_groups else
                "当前选择了单批次，或缺少同一创作者、来源、粉丝指标和统计周期的重复快照"
            ),
            "word_frequency": f"按真实笔记 {selected_text['sample_count']} 条生成 {selected_kind} 词频" if selected_text["sample_count"] else f"当前筛选没有可用的 {selected_kind} 文本",
            "quote_types": f"精确 {quote_status_counts.get('parsed', 0)} 条；起价 {quote_status_counts.get('starting_price', 0)} 条；区间 {quote_status_counts.get('range_unresolved', 0)} 条；缺失 {quote_status_counts.get('missing', 0)} 条",
            "audience": f"{len(audience_profiles)} 条真实画像快照；按来源与统计周期分组" if audience_profiles else "当前筛选没有已保存的受众画像字段",
        },
        "charts": {
            "follower_distribution": follower_distribution,
            "category_distribution": [{"name": name, "value": value} for name, value in sorted(category_counts.items())],
            "quote_distribution": quote_distribution,
            "quote_status_distribution": [
                {"name": label, "value": quote_status_counts.get(status, 0)}
                for status, label in (("parsed", "精确报价"), ("starting_price", "起价"), ("range_unresolved", "区间报价"), ("not_numeric", "其他非数值"), ("missing", "缺失"))
            ],
            "location_distribution": [{"name": name, "value": count} for name, count in sorted(location_counts.items())],
            "follower_price_scatter": [{"name": item["pseudonym"], "x": item["followers"], "y": item["quote"]} for item in creators if item["followers"] is not None and item["quote"] is not None],
            "reading_engagement_scatter": [{"name": item["pseudonym"], "x": item["reading"], "y": item["engagement"]} for item in reads],
            "fan_analysis": fan_averages,
            "correlation": correlation,
            "batch_trend": trend_groups,
            "note_metrics": note_metrics,
            "word_frequency": frequency,
            "quote_sample_count": len(quotes),
            "follower_sample_count": len(followers),
            "follower_price_sample_count": sum(item["followers"] is not None and item["quote"] is not None for item in creators),
            "reading_engagement_sample_count": len(reads),
            "quote_type_counts": dict(quote_status_counts),
            "audience_profile_count": len(audience_profiles),
            "audience_groups": audience_groups,
        },
        "text_analysis": note_analysis["text_analysis"],
    }


def wordcloud_png(
    db_path: Path,
    batch_id: str | None = None,
    *,
    kind: str = "body",
    source: str = "",
    creator_id: str = "",
    category: str = "",
    date_from: str = "",
    date_to: str = "",
    note_type: str = "",
    topic: str = "",
    query: str = "",
    note_id: str = "",
) -> bytes:
    selected_kind = kind if kind in {"title", "body", "topics"} else "body"
    data = notes_data(
        db_path,
        batch_id=batch_id,
        source=source,
        creator_id=creator_id,
        note_id=note_id,
        category=category,
        date_from=date_from,
        date_to=date_to,
        note_type=note_type,
        topic=topic,
        query=query,
    )
    frequencies = {
        item["word"]: int(item["count"])
        for item in data["text_analysis"][selected_kind]["frequencies"]
        if item["count"] > 0
    }
    if not frequencies:
        raise ValueError(f"当前筛选没有可用于{selected_kind}词云的真实文本")

    fonts = [Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simhei.ttf")]
    font_path = next((font for font in fonts if font.is_file()), None)
    if font_path is None:
        raise FileNotFoundError("系统未找到可用的中文字体（微软雅黑或黑体）")
    try:
        from wordcloud import WordCloud
    except ImportError as exc:
        raise RuntimeError("WordCloud 依赖尚未安装；请运行项目 setup.ps1") from exc
    cloud = WordCloud(
        font_path=str(font_path),
        width=1000,
        height=560,
        background_color="#ffffff",
        max_words=100,
        collocations=False,
        random_state=17,
    ).generate_from_frequencies(frequencies)
    buffer = BytesIO()
    cloud.to_image().save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
