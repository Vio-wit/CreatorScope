from __future__ import annotations

import json
import re
from collections import Counter
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


def _metric_value(fields: dict[str, Any], key: str) -> float | None:
    value, _unit, status = normalize_value(key, fields.get(key))
    return float(value) if status == "parsed" and value is not None else None


def _creator_snapshots(db_path: Path, batch_id: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with connection(db_path) as conn:
        sql = """
            SELECT r.record_id, r.batch_id, r.record_type, r.captured_at,
                   r.fields_json, r.creator_id, c.pseudonym,
                   b.captured_at AS batch_captured_at, b.source AS batch_source
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
        batches = [
            dict(row)
            for row in conn.execute(
                "SELECT batch_id, source, captured_at, record_count FROM batches ORDER BY captured_at DESC, rowid DESC"
            ).fetchall()
        ]

    for row in rows:
        row["fields"] = json.loads(row.pop("fields_json"))
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
        image_price = _metric_value(fields, "图文笔记一口价")
        video_price = _metric_value(fields, "视频笔记一口价")
        quote = _metric_value(fields, "全部报价")
        if quote is None:
            prices = [value for value in (image_price, video_price) if value is not None]
            quote = sum(prices) / len(prices) if prices else None
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
) -> dict[str, Any]:
    rows, _batches = _creator_snapshots(db_path)
    creators = _merge_creator_rows(rows)
    q = query.casefold().strip()
    selected = []
    for creator in creators:
        if q and q not in creator["pseudonym"].casefold():
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
        selected.append(
            {
                "creator_id": creator["creator_id"],
                "pseudonym": creator["pseudonym"],
                "followers": creator["followers"],
                "quote": creator["quote"],
                "image_price": creator["image_price"],
                "video_price": creator["video_price"],
                "reading": creator["reading"],
                "engagement": creator["engagement"],
                "categories": creator["categories"],
                "record_types": creator["record_types"],
                "last_seen_at": creator["last_seen_at"],
            }
        )
    categories = sorted({category for item in creators for category in item["categories"]})
    return {"count": len(selected), "categories": categories, "creators": selected}


def analytics_data(db_path: Path, batch_id: str | None = None) -> dict[str, Any]:
    rows, batches = _creator_snapshots(db_path, batch_id=batch_id)
    creators = _merge_creator_rows(rows)
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
        for category in creator["categories"]:
            category_counts[category] = category_counts.get(category, 0) + 1

    note_rows = [row for row in rows if row["record_type"] == "note_detail"]
    note_metrics = []
    for row in note_rows:
        fields = row["fields"]
        item = {"creator": row["pseudonym"], "published_at": fields.get("发布时间")}
        for key in ("曝光量", "阅读量", "点赞量", "收藏量", "评论量", "分享量"):
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

    trend_points = []
    if not batch_id:
        by_time: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_time.setdefault(row["batch_captured_at"], []).append(row)
        distinct_times = sorted(by_time)
        if len(distinct_times) >= 2:
            for captured_at in distinct_times:
                snapshot_creators = _merge_creator_rows(by_time[captured_at])
                values = [item["followers"] for item in snapshot_creators if item["followers"] is not None]
                if values:
                    trend_points.append({"captured_at": captured_at, "median_followers": float(pd.Series(values).median()), "creator_count": len(values)})

    text_samples = []
    for row in rows:
        for key in ("笔记标题", "笔记正文", "标题", "正文", "内容标签"):
            value = row["fields"].get(key)
            if isinstance(value, str) and value.strip():
                text_samples.append(value.strip())
    words: list[str] = []
    for text in text_samples:
        words.extend(word.strip() for word in jieba.lcut(text) if len(word.strip()) > 1)
    frequency = pd.Series(words, dtype="string").value_counts().head(30) if words else pd.Series(dtype="int64")

    return {
        "scope": "当前批次" if batch_id else "全部历史快照（创作者按各类型最新记录归并）",
        "counts": {"creators": len(creators), "followers_available": len(followers), "quotes_available": len(quotes), "notes": len(note_metrics), "batches": 1 if batch_id and any(batch["batch_id"] == batch_id for batch in batches) else (0 if batch_id else len(batches))},
        "availability": {
            "follower_price": "可展示样本点" if any(item["followers"] is not None and item["quote"] is not None for item in creators) else "缺少共同字段",
            "reading_engagement": "可展示样本点" if reads else "缺少共同字段",
            "correlation": f"已按两两完整值计算（最多 {max_pair_count} 个样本）" if correlation else f"样本不足：每组指标至少需要 3 条共同有效值，目前共同完整样本 {len(complete)} 条",
            "history": "可展示不同采集时间的粉丝量趋势" if len(trend_points) >= 2 else "暂不可判断：至少需要两个不同采集时间的快照；重复导入同一固定样本不构成趋势",
            "word_frequency": f"已从 {len(text_samples)} 条真实文本字段分词" if text_samples else "当前真实样本未保存标题、正文或内容标签",
        },
        "charts": {
            "follower_distribution": follower_distribution,
            "category_distribution": [{"name": name, "value": value} for name, value in sorted(category_counts.items())],
            "quote_distribution": quote_distribution,
            "follower_price_scatter": [{"name": item["pseudonym"], "x": item["followers"], "y": item["quote"]} for item in creators if item["followers"] is not None and item["quote"] is not None],
            "reading_engagement_scatter": [{"name": item["pseudonym"], "x": item["reading"], "y": item["engagement"]} for item in reads],
            "fan_analysis": fan_averages,
            "correlation": correlation,
            "batch_trend": trend_points,
            "note_metrics": note_metrics,
            "word_frequency": [{"word": str(word), "count": int(count)} for word, count in frequency.items()],
        },
    }


def wordcloud_png(db_path: Path, batch_id: str | None = None) -> bytes:
    rows, _batches = _creator_snapshots(db_path, batch_id=batch_id)
    texts = [
        str(value).strip()
        for row in rows
        for key, value in row["fields"].items()
        if key in {"笔记标题", "笔记正文", "标题", "正文", "内容标签"}
        and isinstance(value, str)
        and value.strip()
    ]
    words = [word.strip() for text in texts for word in jieba.lcut(text) if len(word.strip()) > 1]
    frequencies = Counter(words)
    if not frequencies:
        raise ValueError("当前真实样本没有可用于词云的标题、正文或内容标签")

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
