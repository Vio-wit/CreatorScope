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
    "EPT": {"label": "EPT 候选池", "where": "s.source_name = ? OR s.source_name = 'rednote_creator__EPT'", "match": "EPT"},
    "MUSINSA": {"label": "MUSINSA 候选池", "where": "s.source_name = ? OR s.source_name = 'rednote_creator__MUSINSA'", "match": "MUSINSA"},
    "creator_detail": {
        "label": "达人详情样本",
        "where": "s.source_name LIKE ? OR s.source_name = 'rednote_creator__详情JSON'",
        "match": "达人详情样本%",
    },
}

LIBRARY_SOURCE_GROUPS: dict[str, dict[str, str]] = {
    "history_all": {
        "label": "全部历史达人",
        "where": "s.source_name LIKE ?",
        "match": "rednote_creator__%",
    },
    "EPT": {"label": "EPT 候选池", "where": "s.source_name LIKE ?", "match": "%EPT%"},
    "MUSINSA": {"label": "MUSINSA 候选池", "where": "s.source_name LIKE ?", "match": "%MUSINSA%"},
    "creator_detail": {"label": "达人详情", "where": "s.source_name LIKE ?", "match": "%详情%"},
    "history_index": {"label": "历史索引", "where": "s.source_name LIKE ?", "match": "%索引%"},
    "history_candidates": {"label": "其他历史候选表", "where": "s.source_name LIKE ?", "match": "%候选%"},
}

METRICS: tuple[tuple[str, str, str], ...] = (
    ("followers", "粉丝数", "人"),
    ("daily_read_median", "日常阅读中位数", "次"),
    ("daily_interaction_median", "日常互动中位数", "次"),
    ("likes_and_favorites", "获赞与收藏", "次"),
    ("published_notes", "发布笔记数", "篇"),
)
METRIC_KEYS = {field for field, _, _ in METRICS}
FIELD_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("followers", "粉丝数", "人"),
    ("daily_read_median", "日常阅读中位数", "次"),
    ("daily_interaction_median", "日常互动中位数", "次"),
    ("likes_and_favorites", "获赞与收藏", "次"),
    ("published_notes", "发布笔记数", "篇"),
    ("all_price", "全部报价", "原始报价"),
    ("image_price", "图文报价", "原始报价"),
    ("video_price", "视频报价", "原始报价"),
    ("image_fixed_price", "图文一口价", "原始报价"),
    ("video_fixed_price", "视频一口价", "原始报价"),
    ("sponsored_exposure_median", "合作曝光中位数", "次"),
    ("sponsored_read_median", "合作阅读中位数", "次"),
    ("sponsored_interaction_median", "合作互动中位数", "次"),
    ("category", "内容领域", "文本"),
    ("region", "地域", "文本"),
)
FIELD_LABELS = {field: (label, unit) for field, label, unit in FIELD_CATALOG}
FIELD_TIERS = (
    ("high", "80% 及以上"),
    ("partial", "60% 至低于 80%"),
    ("low", "低于 60%"),
    ("unverified", "未验证"),
)
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


def list_library_sources(path: Path | str | None) -> dict[str, Any]:
    """List browseable history groups without mixing them into analysis groups."""
    conn = _connection(path)
    try:
        result = []
        for key, group in LIBRARY_SOURCE_GROUPS.items():
            sources = _source_files(conn, group["where"], group["match"])
            if not sources:
                continue
            source_ids = [item["source_id"] for item in sources]
            marks = ",".join("?" for _ in source_ids)
            visible_name = " OR ".join(
                f"(json_extract(sr.raw_json, '$.{field}') IS NOT NULL "
                f"AND trim(CAST(json_extract(sr.raw_json, '$.{field}') AS TEXT)) <> '' "
                f"AND lower(trim(CAST(json_extract(sr.raw_json, '$.{field}') AS TEXT))) NOT LIKE '%.xlsx' "
                f"AND lower(trim(CAST(json_extract(sr.raw_json, '$.{field}') AS TEXT))) NOT LIKE '%.csv' "
                f"AND lower(trim(CAST(json_extract(sr.raw_json, '$.{field}') AS TEXT))) NOT LIKE '%.json' "
                f"AND lower(trim(CAST(json_extract(sr.raw_json, '$.{field}') AS TEXT))) NOT LIKE '%.txt')"
                for field in (
                    "博主昵称", "creator_name", "创作者名称", "blogger_name", "达人名称",
                    "达人昵称", "博主名称", "昵称", "账号名称",
                )
            )
            if key == "history_all":
                entity_count_sql = f"""
                    COUNT(DISTINCT CASE WHEN
                        EXISTS (SELECT 1 FROM identity_claims ic WHERE ic.record_id=sr.record_id)
                        OR ({visible_name})
                        OR EXISTS (
                            SELECT 1 FROM field_evidence ve WHERE ve.record_id=sr.record_id
                              AND ve.target_field IN ('followers','daily_read_median','daily_interaction_median',
                                  'likes_and_favorites','published_notes','category','region','all_price',
                                  'image_price','video_price','image_fixed_price','video_fixed_price')
                              AND ve.parse_status IN ('parsed','text','starting_price')
                        )
                    THEN COALESCE(sr.identity_id, sr.record_id) END)
                """
            else:
                # Match the browse API: every non-index-only source record is
                # available in the table, so the selector should count its
                # distinct linked identity even when some fields are sparse.
                entity_count_sql = "COUNT(DISTINCT COALESCE(sr.identity_id, sr.record_id))"
            counts = conn.execute(
                f"""
                SELECT COUNT(DISTINCT sr.record_id) AS records,
                       {entity_count_sql} AS entities
                FROM source_records sr
                WHERE sr.source_id IN ({marks}) AND sr.identity_status <> 'index_only'
                """,
                source_ids,
            ).fetchone()
            if not counts["records"]:
                continue
            result.append({
                "key": key,
                "label": group["label"],
                "sample_count": int(counts["records"]),
                "entity_count": int(counts["entities"]),
                "source_file_count": len(sources),
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


def _evidence_state(row: sqlite3.Row) -> str:
    status = row["parse_status"]
    numeric = row["numeric_value"]
    text = row["text_value"]
    if status in {"parsed", "starting_price"} and numeric is not None:
        try:
            if math.isfinite(float(numeric)):
                return "starting_price" if status == "starting_price" else "numeric"
        except (TypeError, ValueError):
            pass
    if status == "text" and isinstance(text, str) and text.strip():
        return "text"
    if status == "displayed_unavailable":
        return "displayed_unavailable"
    if status in {"empty", "missing", "null"}:
        return "empty"
    if status in {"unparsed", "unresolved", "not_numeric", "error"}:
        return "parse_failed"
    return "other"


def _field_coverage(conn: sqlite3.Connection, source_ids: list[str], total: int) -> dict[str, Any]:
    placeholders = ",".join("?" for _ in source_ids)
    rows = conn.execute(
        f"""
        SELECT sr.record_id, fe.target_field, fe.original_field, fe.raw_json,
               fe.numeric_value, fe.text_value, fe.unit, fe.period, fe.parse_status
        FROM source_records sr
        JOIN field_evidence fe ON fe.record_id=sr.record_id
        WHERE sr.source_id IN ({placeholders})
        ORDER BY fe.target_field, sr.record_id, fe.original_field
        """,
        source_ids,
    ).fetchall()

    evidence: dict[str, dict[str, list[sqlite3.Row]]] = {}
    original_fields: dict[str, set[str]] = {}
    units: dict[str, set[str]] = {}
    periods: dict[str, set[str]] = {}
    for row in rows:
        field = row["target_field"]
        evidence.setdefault(field, {}).setdefault(row["record_id"], []).append(row)
        if row["original_field"]:
            original_fields.setdefault(field, set()).add(str(row["original_field"]))
        if row["unit"]:
            units.setdefault(field, set()).add(str(row["unit"]))
        if row["period"]:
            periods.setdefault(field, set()).add(str(row["period"]))

    target_fields = list(dict.fromkeys([field for field, _, _ in FIELD_CATALOG] + sorted(evidence)))
    field_rows = []
    tier_counts = {key: 0 for key, _ in FIELD_TIERS}
    for field in target_fields:
        records = evidence.get(field, {})
        counts = {
            "numeric_success_count": 0,
            "text_count": 0,
            "starting_price_count": 0,
            "displayed_unavailable_count": 0,
            "empty_count": 0,
            "parse_failed_count": 0,
            "other_count": 0,
        }
        priority = ("numeric", "text", "starting_price", "parse_failed", "displayed_unavailable", "empty", "other")
        for record_rows in records.values():
            states = {_evidence_state(row) for row in record_rows}
            state = next((candidate for candidate in priority if candidate in states), "other")
            key = {
                "numeric": "numeric_success_count",
                "text": "text_count",
                "starting_price": "starting_price_count",
                "displayed_unavailable": "displayed_unavailable_count",
                "empty": "empty_count",
                "parse_failed": "parse_failed_count",
                "other": "other_count",
            }[state]
            counts[key] += 1

        numeric_count = counts["numeric_success_count"]
        text_count = counts["text_count"]
        starting_price_count = counts["starting_price_count"]
        usable_count = numeric_count + text_count + starting_price_count
        raw_other_count = sum(
            1 for record_rows in records.values()
            if any(_evidence_state(row) == "other" and row["raw_json"] not in (None, "null") for row in record_rows)
        )
        raw_value_count = usable_count + counts["parse_failed_count"] + raw_other_count
        parse_attempts = raw_value_count
        parse_success_pct = round(100 * usable_count / parse_attempts, 1) if parse_attempts else None
        coverage_pct = round(100 * raw_value_count / total, 1) if total else 0.0
        if not records:
            tier = "unverified"
        elif coverage_pct >= 80:
            tier = "high"
        elif coverage_pct >= 60:
            tier = "partial"
        else:
            tier = "low"
        tier_counts[tier] += 1
        label, default_unit = FIELD_LABELS.get(field, (field.replace("_", " "), ""))
        field_rows.append({
            "key": field,
            "label": label,
            "unit": default_unit,
            "total_count": total,
            "observed_count": len(records),
            "collected_count": raw_value_count,
            "usable_count": usable_count,
            "coverage_pct": coverage_pct,
            "parse_success_pct": parse_success_pct,
            "not_read_count": max(total - len(records), 0),
            "tier": tier,
            "tier_label": dict(FIELD_TIERS)[tier],
            "original_fields": sorted(original_fields.get(field, set())),
            "units": sorted(units.get(field, set())),
            "periods": sorted(periods.get(field, set())),
            **counts,
            "other_raw_count": raw_other_count,
        })

    tier_order = {key: index for index, (key, _) in enumerate(FIELD_TIERS)}
    field_rows.sort(key=lambda item: (tier_order[item["tier"]], -item["coverage_pct"], item["label"]))

    return {
        "field_count": len(field_rows),
        "record_count": total,
        "distribution": [
            {"key": key, "label": label, "count": tier_counts[key]}
            for key, label in FIELD_TIERS
        ],
        "fields": field_rows,
    }


def _raw_value(raw_json: str | None) -> Any:
    try:
        return json.loads(raw_json) if raw_json is not None else None
    except (TypeError, json.JSONDecodeError):
        return raw_json


def _first_text(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.casefold() not in {"-", "--", "—", "null", "none", "n/a"}:
            if re.search(r"(?i)\.(?:xlsx|xls|csv|jsonl?|txt)$", text):
                continue
            return text
    return ""


def _creator_claims(conn: sqlite3.Connection, record_ids: list[str]) -> dict[str, dict[str, str]]:
    if not record_ids:
        return {}
    marks = ",".join("?" for _ in record_ids)
    claims: dict[str, dict[str, str]] = {}
    rows = conn.execute(
        f"SELECT record_id, platform, key_type, value FROM identity_claims WHERE record_id IN ({marks})",
        record_ids,
    ).fetchall()
    for row in rows:
        record = claims.setdefault(row["record_id"], {})
        if row["platform"] == "xiaohongshu" and row["key_type"] == "public_account":
            record["xiaohongshu_account"] = row["value"]
        elif row["platform"] == "xiaohongshu" and row["key_type"] == "profile_id":
            record["xiaohongshu_profile_id"] = row["value"]
        elif row["platform"] == "pgy" and row["key_type"] == "creator_id":
            record["pgy_creator_id"] = row["value"]
    return claims


def _history_source_priority(source_name: str) -> int:
    folded = source_name.casefold()
    if "详情" in source_name:
        return 0
    if "ept" in folded or "musinsa" in folded:
        return 1
    if "早期" in source_name:
        return 2
    if "索引" in source_name:
        return 3
    return 4


def _merge_history_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge only records already linked by the evidence store's strong IDs."""
    grouped: dict[str, dict[str, Any]] = {}
    priorities: dict[str, dict[str, tuple[int, str]]] = {}
    for record in records:
        identity = str(record.get("identity_id") or record["ref"])
        rank = _history_source_priority(str(record.get("source_name") or ""))
        group = grouped.get(identity)
        if group is None:
            group = dict(record)
            group["aliases"] = []
            group["source_records"] = []
            group["source_names"] = []
            group["details"] = []
            group["prices"] = []
            grouped[identity] = group
            priorities[identity] = {}
            for field in ("followers", "daily_read_median", "daily_interaction_median", "likes_and_favorites", "published_notes", "category", "region"):
                value = record.get(field)
                if value not in (None, ""):
                    priorities[identity][field] = (rank, str(record.get("capture_time") or ""))

        name = str(record.get("name") or "").strip()
        if name and name != "未命名记录" and name not in group["aliases"]:
            group["aliases"].append(name)
        source_ref = {
            "source_name": record.get("source_name", ""),
            "sha256": record.get("sha256", ""),
            "source_paths": record.get("source_paths", []),
            "row_number": record.get("row_number"),
            "capture_time": record.get("capture_time"),
        }
        if source_ref not in group["source_records"]:
            group["source_records"].append(source_ref)
        if record.get("source_name") and record["source_name"] not in group["source_names"]:
            group["source_names"].append(record["source_name"])

        for field in ("xiaohongshu_account", "xiaohongshu_profile_id", "pgy_creator_id"):
            if record.get(field) and not group.get(field):
                group[field] = record[field]

        for field in ("followers", "daily_read_median", "daily_interaction_median", "likes_and_favorites", "published_notes", "category", "region"):
            value = record.get(field)
            if value in (None, ""):
                continue
            candidate_time = str(record.get("capture_time") or "")
            prior = priorities[identity].get(field)
            if prior is None or rank < prior[0] or (rank == prior[0] and candidate_time > prior[1]):
                group[field] = value
                priorities[identity][field] = (rank, candidate_time)

        for detail in record.get("details", []):
            if detail not in group["details"]:
                group["details"].append(detail)
        for price in record.get("prices", []):
            tagged = {**price, "source_name": record.get("source_name", ""), "row_number": record.get("row_number")}
            if tagged not in group["prices"]:
                group["prices"].append(tagged)

        if rank < _history_source_priority(str(group.get("source_name") or "")):
            group["name"] = name or group.get("name")
            group["source_name"] = record.get("source_name")
            group["sha256"] = record.get("sha256")
            group["row_number"] = record.get("row_number")
            group["capture_time"] = record.get("capture_time")

    merged = list(grouped.values())
    for item in merged:
        item["ref"] = item.get("identity_id") or item["ref"]
        source_paths = {
            path
            for row in item["source_records"]
            for path in row.get("source_paths", [])
        }
        item["source_file_count"] = len(source_paths) or len({row["sha256"] for row in item["source_records"] if row.get("sha256")})
        item["source_names"] = sorted(item["source_names"])
        item["aliases"] = sorted(item["aliases"], key=str.casefold)
        item["source_records"] = item["source_records"][:30]
        item["details"] = item["details"][:80]
    return merged


def history_creator_library(
    path: Path | str | None,
    source_key: str = "EPT",
    *,
    query: str = "",
    category: str = "",
    location: str = "",
    followers_min: float | None = None,
    followers_max: float | None = None,
    daily_read_min: float | None = None,
    daily_read_max: float | None = None,
    daily_interaction_min: float | None = None,
    daily_interaction_max: float | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    group = LIBRARY_SOURCE_GROUPS.get(source_key) or SOURCE_GROUPS.get(source_key)
    if group is None:
        raise HistoryAnalyticsError("请选择有效的数据来源")
    where, match = group["where"], group["match"]
    conn = _connection(path)
    try:
        sources = _source_files(conn, where, match)
        if not sources:
            raise HistoryAnalyticsError("所选数据来源暂时没有记录")
        source_ids = [source["source_id"] for source in sources]
        marks = ",".join("?" for _ in source_ids)
        paths_by_source: dict[str, list[str]] = {}
        for path_row in conn.execute(
            f"SELECT source_id, path FROM source_paths WHERE source_id IN ({marks}) ORDER BY path",
            source_ids,
        ).fetchall():
            paths_by_source.setdefault(path_row["source_id"], []).append(path_row["path"])
        rows = conn.execute(
            f"""
            SELECT sr.record_id, sr.source_id, sr.identity_id, sr.identity_status, sr.row_number, sr.capture_time, sr.raw_json,
                   s.source_name, s.sha256, fe.target_field, fe.original_field,
                   fe.raw_json AS field_raw_json, fe.numeric_value, fe.text_value,
                   fe.unit, fe.period, fe.parse_status
            FROM source_records sr
            JOIN sources s ON s.source_id=sr.source_id
            LEFT JOIN field_evidence fe ON fe.record_id=sr.record_id
                AND fe.target_field IN ({','.join('?' for _ in FIELD_LABELS)})
            WHERE sr.source_id IN ({marks}) AND sr.identity_status <> 'index_only'
            ORDER BY s.source_name, sr.row_number, fe.target_field
            """,
            (*FIELD_LABELS.keys(), *source_ids),
        ).fetchall()

        by_record: dict[str, dict[str, Any]] = {}
        for row in rows:
            record = by_record.setdefault(row["record_id"], {
                "ref": row["record_id"],
                "identity_id": row["identity_id"],
                "identity_status": row["identity_status"],
                "row_number": row["row_number"],
                "capture_time": row["capture_time"],
                "source_name": row["source_name"],
                "sha256": row["sha256"],
                "source_paths": paths_by_source.get(row["source_id"], []),
                "raw": _raw_value(row["raw_json"]),
                "fields": {},
                "evidence": [],
            })
            field = row["target_field"]
            if field:
                record["evidence"].append(row)
                previous = record["fields"].get(field)
                ranks = {"numeric": 0, "text": 1, "starting_price": 2, "parse_failed": 3, "displayed_unavailable": 4, "empty": 5, "other": 6}
                if previous is None or ranks[_evidence_state(row)] < ranks[_evidence_state(previous)]:
                    record["fields"][field] = row

        claims = _creator_claims(conn, list(by_record))
        results = []
        q = query.strip().casefold()
        category_filter = category.strip().casefold()
        location_filter = location.strip().casefold()
        for record_id, record in by_record.items():
            raw = record["raw"] if isinstance(record["raw"], dict) else {}
            ids = claims.get(record_id, {})
            name = _first_text(raw, (
                "博主昵称", "creator_name", "创作者名称", "blogger_name", "达人名称", "达人昵称", "博主名称", "昵称", "账号名称",
            ))
            profile = record["fields"]

            def numeric(field: str) -> float | None:
                evidence_row = profile.get(field)
                if evidence_row is None or not _eligible_value(evidence_row):
                    return None
                return float(evidence_row["numeric_value"])

            def text_value(field: str) -> str:
                evidence_row = profile.get(field)
                if evidence_row is None or evidence_row["parse_status"] != "text":
                    return ""
                return str(evidence_row["text_value"] or "").strip()

            categories = text_value("category")
            region = text_value("region")
            values = {
                "ref": record_id,
                "identity_id": record["identity_id"],
                "identity_status": record["identity_status"],
                "name": name or "未命名记录",
                "xiaohongshu_account": ids.get("xiaohongshu_account", ""),
                "xiaohongshu_profile_id": ids.get("xiaohongshu_profile_id", ""),
                "pgy_creator_id": ids.get("pgy_creator_id", ""),
                "followers": numeric("followers"),
                "daily_read_median": numeric("daily_read_median"),
                "daily_interaction_median": numeric("daily_interaction_median"),
                "likes_and_favorites": numeric("likes_and_favorites"),
                "published_notes": numeric("published_notes"),
                "category": categories,
                "region": region,
                "source_name": record["source_name"],
                "sha256": record["sha256"],
                "source_paths": record["source_paths"],
                "row_number": record["row_number"],
                "capture_time": record["capture_time"],
                "prices": [],
                "details": [],
            }
            for evidence_row in record["evidence"]:
                field = evidence_row["target_field"]
                raw_value = _raw_value(evidence_row["field_raw_json"])
                if raw_value is None:
                    display_value = ""
                elif isinstance(raw_value, (dict, list)):
                    display_value = json.dumps(raw_value, ensure_ascii=False)
                else:
                    display_value = str(raw_value)
                label, unit = FIELD_LABELS[field]
                detail = {
                    "key": field,
                    "label": label,
                    "original_field": evidence_row["original_field"],
                    "raw_value": display_value,
                    "numeric_value": evidence_row["numeric_value"],
                    "text_value": evidence_row["text_value"],
                    "unit": evidence_row["unit"] or unit,
                    "period": evidence_row["period"],
                    "parse_status": evidence_row["parse_status"],
                    "source_name": record["source_name"],
                    "row_number": record["row_number"],
                    "capture_time": record["capture_time"],
                }
                values["details"].append(detail)
                if field in {"all_price", "image_price", "video_price", "image_fixed_price", "video_fixed_price"}:
                    values["prices"].append({"label": label, "raw_value": display_value})

            if source_key == "history_all" and not name and not any(ids.values()):
                visible_metrics = (
                    values["followers"], values["daily_read_median"], values["daily_interaction_median"],
                    values["likes_and_favorites"], values["published_notes"], values["category"], values["region"],
                )
                if not any(value not in (None, "") for value in visible_metrics) and not values["prices"]:
                    continue

            searchable = " ".join(str(value) for value in (
                values["name"], values["xiaohongshu_account"], values["xiaohongshu_profile_id"],
                values["pgy_creator_id"], values["category"], values["region"],
            )).casefold()
            if source_key != "history_all":
                if q and q not in searchable:
                    continue
                if category_filter and category_filter not in categories.casefold():
                    continue
                if location_filter and location_filter not in region.casefold():
                    continue
                followers = values["followers"]
                if followers_min is not None and (followers is None or followers < followers_min):
                    continue
                if followers_max is not None and (followers is None or followers > followers_max):
                    continue
                daily_read = values["daily_read_median"]
                if daily_read_min is not None and (daily_read is None or daily_read < daily_read_min):
                    continue
                if daily_read_max is not None and (daily_read is None or daily_read > daily_read_max):
                    continue
                daily_interaction = values["daily_interaction_median"]
                if daily_interaction_min is not None and (daily_interaction is None or daily_interaction < daily_interaction_min):
                    continue
                if daily_interaction_max is not None and (daily_interaction is None or daily_interaction > daily_interaction_max):
                    continue
            results.append(values)

        visible_entity_count: int | None = None
        if source_key == "history_all":
            results = _merge_history_records(results)
            visible_entity_count = len(results)
            filtered = []
            for values in results:
                searchable = " ".join(str(value) for value in (
                    values.get("name", ""), *values.get("aliases", []),
                    values.get("xiaohongshu_account", ""), values.get("xiaohongshu_profile_id", ""),
                    values.get("pgy_creator_id", ""), values.get("category", ""), values.get("region", ""),
                )).casefold()
                if q and q not in searchable:
                    continue
                if category_filter and category_filter not in str(values.get("category") or "").casefold():
                    continue
                if location_filter and location_filter not in str(values.get("region") or "").casefold():
                    continue
                followers = values.get("followers")
                if followers_min is not None and (followers is None or followers < followers_min):
                    continue
                if followers_max is not None and (followers is None or followers > followers_max):
                    continue
                daily_read = values.get("daily_read_median")
                if daily_read_min is not None and (daily_read is None or daily_read < daily_read_min):
                    continue
                if daily_read_max is not None and (daily_read is None or daily_read > daily_read_max):
                    continue
                daily_interaction = values.get("daily_interaction_median")
                if daily_interaction_min is not None and (daily_interaction is None or daily_interaction < daily_interaction_min):
                    continue
                if daily_interaction_max is not None and (daily_interaction is None or daily_interaction > daily_interaction_max):
                    continue
                filtered.append(values)
            results = filtered
        else:
            for values in results:
                values["source_records"] = [{
                    "source_name": values["source_name"],
                    "sha256": values["sha256"],
                    "source_paths": values["source_paths"],
                    "row_number": values["row_number"],
                    "capture_time": values["capture_time"],
                }]
                values["source_file_count"] = len(values["source_paths"]) or 1
        results.sort(key=lambda item: (item["followers"] is None, -(item["followers"] or 0), item["name"].casefold()))
        result_count = len(results)
        page_records = results[offset:offset + limit]
        entity_count = visible_entity_count
        if entity_count is None:
            entity_count = conn.execute(
                f"""SELECT COUNT(DISTINCT COALESCE(sr.identity_id, sr.record_id))
                    FROM source_records sr WHERE sr.source_id IN ({marks}) AND sr.identity_status <> 'index_only'""",
                source_ids,
            ).fetchone()[0]
        return {
            "source": {
                "key": source_key,
                "label": group["label"],
                "sample_count": sum(source["saved_records"] for source in sources),
                "entity_count": int(entity_count),
                "source_file_count": len(sources),
            },
            "result_count": result_count,
            "offset": offset,
            "limit": limit,
            "records": page_records,
        }
    finally:
        conn.close()


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
            "field_coverage": _field_coverage(conn, source_ids, total),
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
                body = re.sub(r"(?<!\w)@[^\s，。；;]+", "", body)
                sample["body"] = re.sub(r"\[\s*话题\s*\]", "", body, flags=re.IGNORECASE).strip()
            elif field == "note_topics_candidate":
                try:
                    topics = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    topics = re.findall(r"#([^\s#]+)", value)
                if isinstance(topics, list):
                    clean_topics = []
                    for topic in topics:
                        name = re.sub(r"\[\s*话题\s*\]", "", str(topic), flags=re.IGNORECASE).strip().lstrip("#")
                        if name and name.casefold() != "话题" and name not in clean_topics:
                            clean_topics.append(name)
                    sample["topics"] = clean_topics
        selected = [samples[digest] for digest in NOTE_SAMPLE_SHA256 if digest in samples and samples[digest]["title"] and samples[digest]["body"]]
        return {"sample_count": len(selected), "samples": selected}
    finally:
        conn.close()


def history_note_library(
    path: Path | str | None,
    *,
    query: str = "",
    offset: int = 0,
    limit: int = 12,
) -> dict[str, Any]:
    """Read searchable title/body/topic samples from imported history notes."""
    conn = _connection(path)
    try:
        rows = conn.execute(
            """
            SELECT s.source_name, s.sha256,
                   (SELECT MIN(sp.path) FROM source_paths sp WHERE sp.source_id=s.source_id) AS source_path,
                   sr.record_id, sr.row_number, sr.capture_time,
                   sr.raw_json, fe.target_field, fe.text_value
            FROM sources s
            JOIN source_records sr ON sr.source_id=s.source_id
            LEFT JOIN field_evidence fe ON fe.record_id=sr.record_id
                AND fe.target_field IN ('note_title','note_body','note_body_candidate','note_topics','note_topics_candidate')
            WHERE s.source_name LIKE 'rednote_note__%'
            ORDER BY s.source_name, s.sha256, sr.row_number, fe.target_field
            """
        ).fetchall()
        notes: dict[str, dict[str, Any]] = {}
        for row in rows:
            note = notes.setdefault(row["record_id"], {
                "title": "", "body": "", "topics": [], "author": "",
                "lineage": {
                    "source_name": row["source_name"], "sha256": row["sha256"],
                    "source_path": row["source_path"], "row_number": row["row_number"], "capture_time": row["capture_time"],
                },
            })
            raw = _raw_value(row["raw_json"])
            if isinstance(raw, dict) and not note["author"]:
                note["author"] = _first_text(raw, ("author_name", "author", "creator_name", "博主昵称", "达人名称"))
            field, value = row["target_field"], row["text_value"]
            if not value:
                continue
            if field == "note_title" and not note["title"]:
                note["title"] = str(value).strip()
            elif field in {"note_body", "note_body_candidate"} and not note["body"]:
                body = re.sub(r"https?://\S+", "", str(value))
                body = re.sub(r"(?<!\w)@[^\s，。；;]+", "", body)
                body = re.sub(r"\[\s*话题\s*\]", "", body, flags=re.IGNORECASE)
                note["body"] = body.strip()
            elif field in {"note_topics", "note_topics_candidate"} and not note["topics"]:
                try:
                    topics = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    topics = re.findall(r"#([^\s#]+)", str(value))
                if isinstance(topics, str):
                    topics = [topics]
                if isinstance(topics, list):
                    normalized = []
                    for topic in topics:
                        name = re.sub(r"\[\s*话题\s*\]", "", str(topic), flags=re.IGNORECASE).strip().lstrip("#")
                        if name and name.casefold() not in {"话题", "正文", "无"} and name not in normalized:
                            normalized.append(name)
                    note["topics"] = normalized

        notes_list = list(notes.values())
        title_count = sum(bool(note["title"]) for note in notes_list)
        body_count = sum(bool(note["body"]) for note in notes_list)
        topics_count = sum(bool(note["topics"]) for note in notes_list)
        q = query.strip().casefold()
        matched = [
            note for note in notes_list
            if not q or q in " ".join((note["title"], note["body"], " ".join(note["topics"]), note["author"])).casefold()
        ]
        return {
            "total_count": len(notes_list),
            "result_count": len(matched),
            "title_count": title_count,
            "body_count": body_count,
            "topics_count": topics_count,
            "offset": offset,
            "limit": limit,
            "samples": matched[offset:offset + limit],
        }
    finally:
        conn.close()
