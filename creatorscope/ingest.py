from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from .db import connection, local_timestamp


COUNT_KEYS = {
    "粉丝数",
    "获赞与收藏",
    "发布笔记数",
    "发布笔记",
    "曝光量",
    "阅读量",
    "点赞量",
    "收藏量",
    "评论量",
    "分享量",
    "阅读中位数（日常）",
    "互动中位数（日常）",
    "曝光中位数",
    "阅读中位数",
    "互动中位数",
    "中位点赞量",
    "中位收藏量",
    "中位评论量",
    "中位分享量",
    "中位关注量",
}
RATE_KEYS = {
    "互动率",
    "视频完播率",
    "图文3秒阅读率",
    "粉丝量变化幅度",
    "活跃粉丝占比",
    "阅读粉丝占比",
    "互动粉丝占比",
    "下单粉丝占比",
}
PRICE_KEYS = {"全部报价", "图文笔记一口价", "视频笔记一口价"}
KNOWN_LABELS = {
    *COUNT_KEYS,
    *RATE_KEYS,
    *PRICE_KEYS,
    "内容类目",
    "发布时间",
}


def normalize_value(key: str, raw_value: Any) -> tuple[float | None, str, str]:
    if raw_value is None:
        return None, _unit_for(key), "missing"

    raw = str(raw_value).strip()
    if not raw or raw in {"-", "—", "–"}:
        return None, _unit_for(key), "displayed_unavailable"

    # Do not extract incidental digits from category labels or dates.
    if key not in COUNT_KEYS | RATE_KEYS | PRICE_KEYS:
        return None, _unit_for(key), "text_or_date"

    cleaned = raw.replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
    cleaned = cleaned.replace("元", "").replace("起", "").strip()
    if key in RATE_KEYS:
        cleaned = cleaned.replace("%", "").strip()
    factor = 1.0
    if cleaned.lower().endswith("w") or cleaned.endswith("万"):
        factor = 10000.0
        cleaned = cleaned[:-1] if cleaned.lower().endswith("w") else cleaned[:-1]
    elif cleaned.endswith("千"):
        factor = 1000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("篇") or cleaned.endswith("天"):
        cleaned = cleaned[:-1]

    if re.search(r"\d\s*[-~～至]\s*\d", cleaned):
        return None, _unit_for(key), "range_unresolved"

    match = re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*", cleaned)
    if not match:
        return None, _unit_for(key), "not_numeric"

    value = float(match.group(0)) * factor
    if value.is_integer():
        value = int(value)
    return float(value), _unit_for(key), "parsed"


def _unit_for(key: str) -> str:
    if key in RATE_KEYS:
        return "percent"
    if key in PRICE_KEYS:
        return "CNY"
    if key == "粉丝数":
        return "followers"
    if key == "获赞与收藏":
        return "likes_and_favorites"
    if key == "发布时间":
        return "datetime"
    if key in COUNT_KEYS or key.endswith("数") or key.endswith("量"):
        return "count"
    return "text"


def _period_for(record: dict[str, Any]) -> str:
    return str(
        record.get("period_displayed")
        or record.get("period_selector_displayed")
        or ""
    )


def _record_hash(record_type: str, fields: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"type": record_type, "fields": fields},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ingest_payload(
    db_path: Path,
    task_id: str,
    payload: dict[str, Any],
    source_override: str | None = None,
) -> dict[str, Any]:
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("数据源没有可导入的记录")
    if len(records) > 50:
        raise ValueError("单批次上限为 50 条记录")

    captured_at = str(payload.get("captured_at_local") or local_timestamp())
    source = source_override or str(payload.get("source") or "蒲公英可见页面")
    batch_id = uuid4().hex
    inserted_records = 0
    inserted_metrics = 0
    fields_for_frame: list[dict[str, Any]] = []

    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO batches(batch_id, task_id, source, captured_at) VALUES (?, ?, ?, ?)",
            (batch_id, task_id, source, captured_at),
        )
        for item in records:
            if not isinstance(item, dict):
                continue
            record_type = str(item.get("type") or "unknown")
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            # Persist only the selected, visible fields; never store whole-page text.
            clean_fields = {
                str(key): value
                for key, value in fields.items()
                if isinstance(value, (str, int, float, bool)) or value is None
            }
            digest = _record_hash(record_type, clean_fields)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO records
                    (batch_id, record_type, captured_at, fields_json, fields_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    record_type,
                    captured_at,
                    json.dumps(clean_fields, ensure_ascii=False, sort_keys=True),
                    digest,
                ),
            )
            if cur.rowcount == 0:
                continue
            record_id = int(cur.lastrowid)
            inserted_records += 1
            period = _period_for(item)
            for key, value in clean_fields.items():
                numeric_value, unit, status = normalize_value(key, value)
                raw_value = None if value is None else str(value)
                conn.execute(
                    """
                    INSERT INTO metric_values
                        (record_id, metric_key, raw_value, numeric_value, unit, period, parse_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (record_id, key, raw_value, numeric_value, unit, period, status),
                )
                inserted_metrics += 1
                fields_for_frame.append(
                    {
                        "metric_key": key,
                        "numeric_value": numeric_value,
                        "parse_status": status,
                    }
                )

        metric_frame = pd.DataFrame(fields_for_frame)
        if not metric_frame.empty:
            metric_frame["numeric_value"] = pd.to_numeric(
                metric_frame["numeric_value"], errors="coerce"
            )
            parsed_count = int(
                metric_frame["parse_status"].isin(["parsed"]).sum()
            )
        else:
            parsed_count = 0

        conn.execute(
            "UPDATE batches SET record_count = ? WHERE batch_id = ?",
            (inserted_records, batch_id),
        )
        conn.execute(
            "UPDATE tasks SET batch_id = ? WHERE task_id = ?",
            (batch_id, task_id),
        )

    return {
        "batch_id": batch_id,
        "record_count": inserted_records,
        "metric_count": inserted_metrics,
        "parsed_metric_count": parsed_count,
        "message": f"已写入 {inserted_records} 条记录、{inserted_metrics} 个字段值",
    }


def load_baseline(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("基线 JSON 结构无效")
    return payload
