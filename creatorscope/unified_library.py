"""Read-only projection across CreatorScope captures and local rednote evidence."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .analytics import creator_library
from .history_analytics import HistoryAnalyticsError, history_creator_library


COURSE_SAMPLE_BATCH_ID = "3a70fbec3f714bada4b4fc6f1c5dc6b3"
COURSE_SAMPLE_BASELINE = "2026-11-23T14:38:36+08:00"
PAGE_LIMIT_MAX = 200
BUCKETS = (
    ("recent", "基线后新增采集"),
    ("course", "五人课堂采集"),
    ("historical", "既有采集与历史资料"),
)
EXPECTED_FIELDS = (
    "name", "platform_id", "followers", "daily_read_median", "daily_interaction_median",
    "likes_and_favorites", "published_notes", "image_fixed_price", "video_fixed_price",
    "category", "account_ip_location",
)
EMPTY_TEXT = {"", "-", "--", "—", "–", "null", "none", "n/a", "暂无", "未获取", "未知"}
ACCOUNT_IP_FIELD_NAMES = (
    "蒲公英账号IP属地", "蒲公英账号 IP 属地", "账号IP属地", "账号 IP 属地",
    "小红书账号IP属地", "小红书账号 IP 属地", "IP属地", "IP 属地",
)


def _stamp(path: Path | None) -> tuple[Any, ...]:
    if path is None:
        return (None,)
    values: list[Any] = [str(path.resolve())]
    for candidate in (path, path.with_name(path.name + "-wal")):
        try:
            stat = candidate.stat()
            values.extend((stat.st_mtime_ns, stat.st_size))
        except OSError:
            values.extend((None, None))
    return tuple(values)


def _time_key(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().casefold() not in EMPTY_TEXT
    try:
        number = float(value)
    except (TypeError, ValueError):
        return bool(value)
    return math.isfinite(number) and number >= 0


def _number(value: Any) -> float | None:
    if not _present(value):
        return None
    try:
        if isinstance(value, str):
            text = value.strip().replace(",", "").replace("，", "")
            multiplier = 10_000 if text.endswith(("万", "w", "W")) else 1
            if multiplier != 1:
                text = text[:-1]
            value = text
        parsed = float(value) * multiplier if isinstance(value, str) else float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    except (TypeError, ValueError, UnboundLocalError):
        return None


def _first(raw: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = raw.get(name)
        if _present(value):
            return value
    return None


def _app_record(creator: dict[str, Any], scope: str) -> dict[str, Any]:
    raw_fields = creator.get("fields") if isinstance(creator.get("fields"), dict) else {}
    name = str(creator.get("pseudonym") or creator.get("creator_name") or "").strip()
    platform_id = str(creator.get("platform_creator_id") or "").strip()
    source = str(creator.get("last_source") or "CreatorScope 本地采集").strip()
    category_values = creator.get("categories") or []
    category = "、".join(str(item).strip() for item in category_values if str(item).strip())
    ip_location = str(_first(raw_fields, ACCOUNT_IP_FIELD_NAMES) or "").strip()
    quote_fields = creator.get("quote_fields") or {}
    image_quote = quote_fields.get("图文笔记一口价", {})
    video_quote = quote_fields.get("视频笔记一口价", {})
    likes = _number(_first(raw_fields, ("获赞与收藏", "获赞和收藏", "获赞收藏")))
    published = _number(_first(raw_fields, ("发布笔记数", "发布笔记", "笔记数")))
    fields = {
        "name": name,
        "platform_id": platform_id,
        "followers": _number(creator.get("followers")),
        "daily_read_median": _number(creator.get("reading")),
        "daily_interaction_median": _number(creator.get("engagement")),
        "likes_and_favorites": likes,
        "published_notes": published,
        "image_fixed_price": image_quote.get("raw_value"),
        "video_fixed_price": video_quote.get("raw_value"),
        "category": category,
        "account_ip_location": ip_location,
        "historical_region": "",
    }
    completeness = sum(_present(fields[key]) for key in EXPECTED_FIELDS)
    record = {
        "ref": f"creator:{creator.get('creator_id')}",
        "identity_id": creator.get("creator_id"),
        "identity_key": None,
        "name": name or "未命名记录",
        "platform": creator.get("platform") or "xiaohongshu",
        "platform_id": platform_id,
        "identifiers": {"platform_creator_id": platform_id} if platform_id else {},
        "bucket": scope,
        "bucket_label": dict((key, label) for key, label in BUCKETS)[scope],
        "source_name": source,
        "captured_at": creator.get("last_seen_at"),
        "batch_id": creator.get("last_batch_id"),
        "fields": fields,
        "raw_fields": raw_fields,
        "quote_fields": quote_fields,
        "details": [
            {"label": str(key), "raw_value": str(value) if value is not None else ""}
            for key, value in raw_fields.items()
        ],
        "source_records": [{
            "source_name": source,
            "batch_id": creator.get("last_batch_id"),
            "captured_at": creator.get("last_seen_at"),
            "record_types": creator.get("record_types") or [],
        }],
        "aliases": [name] if name else [],
        "completeness": {"valid_fields": completeness, "total_fields": len(EXPECTED_FIELDS),
                         "percentage": round(completeness * 100 / len(EXPECTED_FIELDS), 1)},
        "_app_platform_id": platform_id,
        "_app_platform": str(creator.get("platform") or "xiaohongshu").casefold(),
        "_priority": {"recent": 0, "course": 1, "historical": 2}[scope],
        "_scope_kind": "creator_scope",
    }
    return record


def _history_record(source: dict[str, Any]) -> dict[str, Any]:
    detail_values = {str(item.get("key") or ""): item for item in (source.get("details") or [])}
    image_price = detail_values.get("image_fixed_price", {}).get("raw_value")
    video_price = detail_values.get("video_fixed_price", {}).get("raw_value")
    fields = {
        "name": source.get("name"),
        "platform_id": source.get("xiaohongshu_account") or source.get("xiaohongshu_profile_id") or source.get("pgy_creator_id"),
        "followers": _number(source.get("followers")),
        "daily_read_median": _number(source.get("daily_read_median")),
        "daily_interaction_median": _number(source.get("daily_interaction_median")),
        "likes_and_favorites": _number(source.get("likes_and_favorites")),
        "published_notes": _number(source.get("published_notes")),
        "image_fixed_price": image_price,
        "video_fixed_price": video_price,
        "category": source.get("category") or "",
        "account_ip_location": "",
        "historical_region": source.get("region") or "",
    }
    quote_names = {"图文一口价", "图文报价", "全部报价", "视频一口价", "视频报价"}
    details = list(source.get("details") or [])
    prices = list(source.get("prices") or [])
    for item in details:
        label = str(item.get("label") or "")
        if label in quote_names and item.get("raw_value"):
            prices.append({"label": label, "raw_value": item["raw_value"]})
    completeness = sum(_present(fields[key]) for key in EXPECTED_FIELDS)
    identifiers = {
        key: source.get(value) for key, value in (
            ("xiaohongshu_account", "xiaohongshu_account"),
            ("xiaohongshu_profile_id", "xiaohongshu_profile_id"),
            ("pgy_creator_id", "pgy_creator_id"),
        ) if source.get(value)
    }
    return {
        "ref": f"history:{source.get('ref')}",
        "identity_id": source.get("identity_id"),
        "identity_key": source.get("identity_id") or source.get("ref"),
        "name": str(source.get("name") or "未命名记录"),
        "platform": "xiaohongshu",
        "platform_id": fields["platform_id"] or "",
        "identifiers": identifiers,
        "bucket": "historical",
        "bucket_label": dict((key, label) for key, label in BUCKETS)["historical"],
        "source_name": source.get("source_name") or "rednote 历史资料",
        "captured_at": source.get("capture_time"),
        "batch_id": None,
        "fields": fields,
        "raw_fields": {},
        "quote_fields": {},
        "prices": prices,
        "details": details,
        "source_records": source.get("source_records") or [{
            "source_name": source.get("source_name"), "sha256": source.get("sha256"),
            "source_paths": source.get("source_paths") or [], "row_number": source.get("row_number"),
            "capture_time": source.get("capture_time"),
        }],
        "aliases": source.get("aliases") or [],
        "completeness": {"valid_fields": completeness, "total_fields": len(EXPECTED_FIELDS),
                         "percentage": round(completeness * 100 / len(EXPECTED_FIELDS), 1)},
        "_app_platform_id": None,
        "_app_platform": "xiaohongshu",
        "_priority": 2,
        "_scope_kind": "history",
    }


def _strong_key(record: dict[str, Any]) -> tuple[str, ...] | None:
    if record["_scope_kind"] == "creator_scope" and record.get("_app_platform_id"):
        return ("creator_scope", record["_app_platform"], record["_app_platform_id"].casefold())
    if record["_scope_kind"] == "history" and record.get("identity_key"):
        return ("history_identity", str(record["identity_key"]))
    return None


def _merge_strong_identities(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    singles: list[dict[str, Any]] = []
    for record in records:
        key = _strong_key(record)
        if key is None:
            singles.append(record)
        else:
            groups.setdefault(key, []).append(record)
    merged = list(singles)
    for values in groups.values():
        values.sort(key=lambda item: (
            item["_priority"], -item["completeness"]["valid_fields"],
            -((_time_key(item.get("captured_at")).timestamp()) if _time_key(item.get("captured_at")) else 0),
        ))
        primary = dict(values[0])
        primary["fields"] = dict(primary["fields"])
        primary["source_records"] = []
        primary["aliases"] = []
        for item in values:
            for key, value in item["fields"].items():
                if not _present(primary["fields"].get(key)) and _present(value):
                    primary["fields"][key] = value
            for source_record in item.get("source_records") or []:
                if source_record not in primary["source_records"]:
                    primary["source_records"].append(source_record)
            for alias in [item.get("name"), *(item.get("aliases") or [])]:
                if alias and alias != "未命名记录" and alias not in primary["aliases"]:
                    primary["aliases"].append(alias)
        valid_count = sum(_present(primary["fields"].get(key)) for key in EXPECTED_FIELDS)
        primary["completeness"] = {"valid_fields": valid_count, "total_fields": len(EXPECTED_FIELDS),
                                   "percentage": round(valid_count * 100 / len(EXPECTED_FIELDS), 1)}
        merged.append(primary)
    return merged


@lru_cache(maxsize=4)
def _load_records(
    main_path: str,
    course_path: str,
    history_path: str,
    fingerprint: tuple[Any, ...],
) -> tuple[dict[str, Any], ...]:
    del fingerprint  # It is part of the cache key so WAL/database changes invalidate the snapshot.
    main_db = Path(main_path) if main_path else None
    course_db = Path(course_path) if course_path else None
    history_db = Path(history_path) if history_path else None
    records: list[dict[str, Any]] = []
    source_counts = {key: 0 for key, _ in BUCKETS}
    warnings: list[str] = []

    if main_db and main_db.is_file():
        for creator in creator_library(main_db, read_only=True)["creators"]:
            captured = _time_key(creator.get("last_seen_at"))
            baseline = _time_key(COURSE_SAMPLE_BASELINE)
            scope = "recent" if captured and baseline and captured > baseline else "historical"
            record = _app_record(creator, scope)
            records.append(record)
            source_counts[scope] += 1
    else:
        warnings.append("正式本地库不可用")

    if course_db and course_db.is_file():
        try:
            course = creator_library(course_db, batch_id=COURSE_SAMPLE_BATCH_ID, read_only=True)
            for creator in course["creators"]:
                record = _app_record(creator, "course")
                records.append(record)
                source_counts["course"] += 1
        except Exception as exc:
            warnings.append(f"五人课堂批次暂不可读：{exc}")
    else:
        warnings.append("五人课堂隔离库不可用")

    if history_db and history_db.is_file():
        try:
            history = history_creator_library(history_db, "history_all", limit=25_000)
            for creator in history["records"]:
                records.append(_history_record(creator))
                source_counts["historical"] += 1
        except HistoryAnalyticsError as exc:
            warnings.append(f"rednote 历史库暂不可读：{exc}")
    else:
        warnings.append("rednote 历史证据库不可用")

    records = _merge_strong_identities(records)
    source_counts = {key: sum(1 for record in records if record["bucket"] == key) for key, _ in BUCKETS}
    for record in records:
        record.pop("_app_platform_id", None)
        record.pop("_app_platform", None)
        record.pop("_scope_kind", None)
    records.sort(key=_sort_key)
    return tuple((source_counts, warnings, records))


def _sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    bucket_priority = {key: index for index, (key, _label) in enumerate(BUCKETS)}
    bucket = record.get("bucket") or "historical"
    if bucket == "recent":
        captured = _time_key(record.get("captured_at"))
        return (bucket_priority[bucket], -(captured.timestamp() if captured else 0),
                -record["completeness"]["valid_fields"], record.get("name", "").casefold(), record["ref"])
    return (bucket_priority[bucket], -record["completeness"]["valid_fields"],
            -(record["fields"].get("followers") or 0), record.get("name", "").casefold(), record["ref"])


def _database_paths(main_db: Path, course_db: Path | None, history_db: Path | None) -> tuple[str, str, str, tuple[Any, ...]]:
    main = Path(main_db).expanduser().resolve()
    course = Path(course_db).expanduser().resolve() if course_db is not None else None
    history = Path(history_db).expanduser().resolve() if history_db is not None else None
    fingerprint = (*_stamp(main), *_stamp(course), *_stamp(history))
    return str(main), str(course or ""), str(history or ""), fingerprint


def unified_creator_library(
    main_db: Path,
    course_db: Path | None,
    history_db: Path | None,
    *,
    query: str = "",
    bucket: str = "all",
    category: str = "",
    ip_location: str = "",
    followers_min: float | None = None,
    followers_max: float | None = None,
    daily_read_min: float | None = None,
    daily_read_max: float | None = None,
    daily_interaction_min: float | None = None,
    daily_interaction_max: float | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    if bucket not in {"all", *(key for key, _label in BUCKETS)}:
        raise ValueError("请选择有效的数据分组")
    if offset < 0 or not 1 <= limit <= PAGE_LIMIT_MAX:
        raise ValueError("分页范围无效（每页最多 200 条）")
    main_path, course_path, history_path, fingerprint = _database_paths(main_db, course_db, history_db)
    counts, warnings, cached_records = _load_records(main_path, course_path, history_path, fingerprint)
    categories = sorted({
        value.strip()
        for record in cached_records
        for value in str(record["fields"].get("category") or "").split("、")
        if value.strip()
    }, key=str.casefold)
    ip_locations = sorted({
        str(record["fields"].get("account_ip_location") or "").strip()
        for record in cached_records
        if str(record["fields"].get("account_ip_location") or "").strip()
    }, key=str.casefold)
    records = []
    q = query.strip().casefold()
    category_query = category.strip().casefold()
    location_query = ip_location.strip().casefold()
    for record in cached_records:
        fields = record["fields"]
        if bucket != "all" and record["bucket"] != bucket:
            continue
        search_blob = " ".join(str(value) for value in (
            record.get("name"), *record.get("aliases", []), *record.get("identifiers", {}).values(),
            record.get("platform_id"), fields.get("category"), fields.get("historical_region"),
            record.get("source_name"),
        )).casefold()
        if q and q not in search_blob:
            continue
        if category_query and category_query not in str(fields.get("category") or "").casefold():
            continue
        if location_query and location_query not in str(fields.get("account_ip_location") or "").casefold():
            continue
        followers = fields.get("followers")
        if followers_min is not None and (followers is None or followers < followers_min):
            continue
        if followers_max is not None and (followers is None or followers > followers_max):
            continue
        daily_read = fields.get("daily_read_median")
        if daily_read_min is not None and (daily_read is None or daily_read < daily_read_min):
            continue
        if daily_read_max is not None and (daily_read is None or daily_read > daily_read_max):
            continue
        daily_interaction = fields.get("daily_interaction_median")
        if daily_interaction_min is not None and (daily_interaction is None or daily_interaction < daily_interaction_min):
            continue
        if daily_interaction_max is not None and (daily_interaction is None or daily_interaction > daily_interaction_max):
            continue
        records.append(record)
    page = records[offset:offset + limit]
    def has_numeric(record: dict[str, Any], field: str) -> bool:
        value = record["fields"].get(field)
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    follower_buckets = [
        {"label": label, "count": sum(1 for record in records if has_numeric(record, "followers") and lower <= record["fields"]["followers"] < upper)}
        for label, lower, upper in (("1万以下", 0, 10000), ("1–5万", 10000, 50000), ("5–10万", 50000, 100000), ("10–50万", 100000, 500000), ("50万以上", 500000, math.inf))
    ]
    return {
        "total_count": len(records),
        "filtered_summary": {
            "followers_valid": sum(has_numeric(record, "followers") for record in records),
            "reading_interaction_valid": sum(has_numeric(record, "daily_read_median") and has_numeric(record, "daily_interaction_median") for record in records),
            "category_valid": sum(_present(record["fields"].get("category")) for record in records),
            "follower_distribution": follower_buckets,
        },
        "offset": offset,
        "limit": limit,
        "records": page,
        "groups": [
            {"key": key, "label": label, "count": counts[key]}
            for key, label in BUCKETS
        ],
        "categories": categories,
        "ip_locations": ip_locations,
        "all_group_counts": counts,
        "total_entry_count": sum(counts.values()),
        "cross_source_identity_note": "仅在 CreatorScope 内部平台 ID 类型一致或历史库已有强 ID 关联时合并；不同系统间的名称相同不会自动合并。",
        "warnings": warnings,
    }


def unified_creator_records_by_refs(
    main_db: Path,
    course_db: Path | None,
    history_db: Path | None,
    refs: list[str],
) -> list[dict[str, Any]]:
    """Return selected records from the same read-only projection used by the library."""
    selected_refs = list(dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip()))
    if not 2 <= len(selected_refs) <= 4:
        raise ValueError("请选择 2 至 4 位达人进行对比")
    main_path, course_path, history_path, fingerprint = _database_paths(main_db, course_db, history_db)
    _counts, _warnings, cached_records = _load_records(main_path, course_path, history_path, fingerprint)
    by_ref = {record["ref"]: record for record in cached_records}
    missing = [ref for ref in selected_refs if ref not in by_ref]
    if missing:
        raise ValueError("部分达人不在当前只读数据集中，请重新搜索后选择")
    return [by_ref[ref] for ref in selected_refs]
