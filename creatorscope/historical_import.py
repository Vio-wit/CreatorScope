from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .db import connection, ensure_creator, ensure_note, initialize_database, local_timestamp


ADAPTER_VERSION = "historical-v1"
SOURCE_FILES = (
    "research/continuous_collection_validation/raw_creators.jsonl",
    "research/continuous_collection_validation/raw_notes_pgy.jsonl",
    "research/continuous_collection_validation/raw_notes_xhs.jsonl",
    "research/continuous_collection_validation/raw_comments.jsonl",
    "research/xhs_public_access_probe/raw_notes_xhs.jsonl",
    "research/xhs_public_access_probe/raw_comments_xhs.jsonl",
    "research/pgy_note_detail_investigation_20261120/actual_samples.json",
)
R42_SOURCE = "research/r4_2_comment_validation/raw_comments.jsonl"
AUTH_SECRET_NAMES = {
    "password", "passwd", "pwd", "cookie", "cookies", "session", "sessionid",
    "sessiontoken", "token", "authtoken", "accesstoken", "refreshtoken", "bearertoken",
    "authorization", "auth", "authdata", "credentials", "credential", "secret",
    "apikey", "clientsecret", "apisecret", "privatekey",
}
AUTH_SECRET_SUFFIXES = (
    "password", "passwd", "pwd", "cookie", "cookies", "session", "sessionid", "sessiontoken",
    "token", "authtoken", "accesstoken", "refreshtoken", "bearertoken", "authorization",
    "credential", "secret", "apikey", "clientsecret", "apisecret", "privatekey",
)


def _sha256(value: str | bytes) -> str:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{_sha256(value)[:24]}"


def _normal_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _is_auth_secret_key(key: Any) -> bool:
    normalized = _normal_key(key)
    session_data = normalized.startswith("session")
    auth_data = normalized.startswith("auth") and not normalized.startswith("author")
    return normalized in AUTH_SECRET_NAMES or normalized.endswith(AUTH_SECRET_SUFFIXES) or session_data or auth_data


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not _is_auth_secret_key(key)
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _safe_value(value)
        for key, value in row.items()
        if not _is_auth_secret_key(key)
    }


def _read_jsonl(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    rows: list[tuple[int, dict[str, Any]]] = []
    malformed = 0
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            rows.append((line_no, value))
        else:
            malformed += 1
    return rows, malformed


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _capture_time(value: Any, fallback: str | None = None) -> str:
    text = str(value or fallback or local_timestamp()).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00"
    return text or local_timestamp()


def _dataset_capture_time(root: Path, relative: str) -> str | None:
    metadata = root / Path(relative).parent / "collection_batches.json"
    if not metadata.exists():
        return None
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        candidates = payload.get("batches") if isinstance(payload.get("batches"), list) else [payload]
    else:
        return None
    for item in candidates:
        if isinstance(item, dict):
            value = _first(item, "captured_at", "captured_on", "started_at", "capture_time")
            if value:
                return _capture_time(value)
    return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _parse_numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text or text in {"-", "—", "–"}:
        return None
    factor = 1.0
    if text.endswith(("万", "w", "W")):
        factor, text = 10000.0, text[:-1]
    elif text.endswith("千"):
        factor, text = 1000.0, text[:-1]
    text = text.removesuffix("%").removesuffix("元").removesuffix("次").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    return float(text) * factor


def _metric_specs(
    value: Any,
    prefix: str = "",
    inherited_period: str = "",
) -> list[tuple[str, str | None, float | None, str, str]]:
    specs: list[tuple[str, str | None, float | None, str, str]] = []
    if isinstance(value, dict):
        period = str(
            value.get("period")
            or value.get("period_displayed")
            or value.get("period_selector_displayed")
            or value.get("trend_period_displayed")
            or inherited_period
        )
        if not prefix and "metrics" in value and isinstance(value["metrics"], (dict, list)):
            return _metric_specs(value["metrics"], "", period)
        if "raw_value" in value or "normalized_value" in value:
            raw = value.get("raw_value")
            normalized = value.get("normalized_value")
            numeric = _parse_numeric(normalized if normalized is not None else raw)
            unit = str(value.get("unit") or "")
            if prefix:
                specs.append((prefix, None if raw is None else str(raw), numeric, unit, period))
            return specs
        if "raw" in value or "value" in value:
            raw = value.get("raw", value.get("value"))
            normalized = value.get("normalized", value.get("value"))
            if prefix:
                specs.append((prefix, None if raw is None else str(raw), _parse_numeric(normalized), str(value.get("unit") or ""), period))
            return specs
        for key, item in value.items():
            if key in {"unit", "period", "period_displayed", "period_selector_displayed", "trend_period_displayed", "raw_value", "normalized_value"}:
                continue
            nested = f"{prefix}.{key}" if prefix else str(key)
            specs.extend(_metric_specs(item, nested, period))
        return specs
    if isinstance(value, list):
        return specs
    if prefix and (isinstance(value, (int, float)) or value is None or isinstance(value, str)):
        specs.append((prefix, None if value is None else str(value), _parse_numeric(value), "", inherited_period))
    return specs


def _creator_for(
    conn: sqlite3.Connection,
    source_key: str,
    captured_at: str,
    platform: str,
    external_id: Any = None,
    row: dict[str, Any] | None = None,
) -> str:
    creator_id = ensure_creator(conn, source_key, captured_at)
    row = row or {}
    tags = _first(row, "category_tags", "content_categories", "category")
    category = ", ".join(str(x) for x in tags if x) if isinstance(tags, list) else (str(tags) if tags else None)
    location = _first(row, "city_raw", "city_displayed", "location")
    follower_count = _parse_numeric(_first(row, "followers_count", "follower_count"))
    if follower_count is None and isinstance(row.get("followers"), dict):
        follower_count = _parse_numeric(row["followers"].get("approximate_value"))
    creator_name = _first(row, "creator_name", "nickname", "username", "blogger_name", "达人名称", "达人昵称", "博主名称", "博主名", "昵称")
    creator_id_value = external_id or _first(row, "platform_creator_id", "creator_id", "blogger_id", "达人ID", "博主ID", "用户ID", "UID")
    platform_hash = _sha256(str(creator_id_value)) if creator_id_value not in (None, "") else None
    conn.execute(
        """UPDATE creators SET platform = COALESCE(platform, ?),
           creator_name = CASE WHEN ? <> '' THEN ? ELSE creator_name END,
           platform_creator_id = COALESCE(NULLIF(?, ''), platform_creator_id),
           platform_creator_id_hash = COALESCE(platform_creator_id_hash, ?),
           category = COALESCE(category, ?), location = COALESCE(location, ?),
           follower_count = COALESCE(follower_count, ?)
           WHERE creator_id = ?""",
        (platform, creator_name, creator_name, creator_id_value, platform_hash, category, location, follower_count, creator_id),
    )
    return creator_id


def _backfill_creator_identity(
    conn: sqlite3.Connection,
    creator_id: str,
    fields: dict[str, Any],
) -> None:
    creator_name = _first(
        fields,
        "creator_name", "nickname", "username", "blogger_name",
        "达人名称", "达人昵称", "博主名称", "博主名", "昵称",
    )
    platform_creator_id = _first(
        fields,
        "platform_creator_id", "creator_id", "blogger_id",
        "达人ID", "博主ID", "用户ID", "UID",
    )
    if creator_name in (None, "") and platform_creator_id in (None, ""):
        return

    row = conn.execute(
        "SELECT creator_name, pseudonym, platform_creator_id FROM creators WHERE creator_id = ?",
        (creator_id,),
    ).fetchone()
    if not row:
        return
    source_name = str(creator_name) if creator_name is not None else ""
    source_id = str(platform_creator_id) if platform_creator_id is not None else ""
    if source_name == row["pseudonym"]:
        source_name = ""
    platform_hash = _sha256(source_id) if source_id else None
    conn.execute(
        """UPDATE creators SET
             creator_name = CASE
                 WHEN (creator_name IS NULL OR creator_name = '' OR creator_name = pseudonym) AND ? <> ''
                 THEN ? ELSE creator_name END,
             platform_creator_id = COALESCE(NULLIF(platform_creator_id, ''), NULLIF(?, '')),
             platform_creator_id_hash = COALESCE(platform_creator_id_hash, ?)
           WHERE creator_id = ?""",
        (source_name, source_name, source_id, platform_hash, creator_id),
    )


def _note_for(
    conn: sqlite3.Connection,
    creator_id: str,
    note_key: str,
    captured_at: str,
    external_id: Any = None,
    fields: dict[str, Any] | None = None,
    explicit_creator_link: bool = False,
) -> tuple[str, bool]:
    external_hash = _sha256(str(external_id)) if external_id not in (None, "") else None
    note_id: str | None = None
    if external_hash:
        matches = conn.execute(
            "SELECT note_id FROM notes WHERE platform_note_id_hash = ? ORDER BY first_seen_at, note_id",
            (external_hash,),
        ).fetchall()
        if matches:
            note_id = str(matches[0][0])
    if note_id is None:
        note_id = ensure_note(
            conn,
            creator_id,
            f"platform-id-hash:{external_hash}" if external_hash else note_key,
            captured_at,
        )
    fields = fields or {}
    title = _first(fields, "title", "page_title")
    body = _first(fields, "content_text_raw", "body_text", "body", "content")
    topics = _first(fields, "hashtags", "topics")
    note_type = _first(fields, "note_type", "content_type")
    source_url = _first(fields, "note_url", "source_url", "share_url", "share_url_base", "笔记链接")
    row = conn.execute("SELECT creator_id FROM notes WHERE note_id = ?", (note_id,)).fetchone()
    new_creator = creator_id if explicit_creator_link else (row[0] if row else creator_id)
    _backfill_creator_identity(conn, str(new_creator), fields)
    conn.execute(
        """UPDATE notes SET creator_id = ?,
           platform_note_id = COALESCE(NULLIF(platform_note_id, ''), ?),
           platform_note_id_hash = COALESCE(platform_note_id_hash, ?),
           source_url = COALESCE(NULLIF(source_url, ''), NULLIF(?, '')),
           title = COALESCE(title, ?), body = COALESCE(body, ?),
           topics_json = COALESCE(topics_json, ?), published_at = COALESCE(published_at, ?),
           note_type = COALESCE(note_type, ?), source = COALESCE(source, ?),
           last_seen_at = CASE WHEN last_seen_at < ? THEN ? ELSE last_seen_at END
           WHERE note_id = ?""",
        (
            new_creator,
            str(external_id) if external_id not in (None, "") else None,
            external_hash,
            str(source_url) if source_url is not None else None,
            str(title) if title is not None else None,
            str(body) if body is not None else None,
            json.dumps(topics, ensure_ascii=False) if topics is not None else None,
            str(fields.get("published_at") or fields.get("published_raw") or "") or None,
            str(note_type) if note_type is not None else None,
            str(fields.get("source") or "historical") or None,
            captured_at,
            captured_at,
            note_id,
        ),
    )
    return note_id, bool(external_hash)


def _insert_record(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    record_type: str,
    captured_at: str,
    creator_id: str | None,
    note_id: str | None,
    source_file: str,
    source_row: int,
    fields: dict[str, Any],
    metric_payload: Any = None,
) -> tuple[bool, int, int, int, str]:
    safe = _safe_fields(fields)
    safe["_source_file"] = source_file
    safe["_source_row"] = source_row
    canonical_fields = {k: v for k, v in safe.items() if not k.startswith("_")}
    digest_data = {
        "type": record_type,
        "creator_id": creator_id,
        "note_id": note_id,
        "captured_at": captured_at,
        "fields": canonical_fields,
    }
    digest = _sha256(json.dumps(digest_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    source_candidates = conn.execute(
        """SELECT record_id, fields_json, creator_id, note_id FROM records
           WHERE batch_id = ? AND record_type = ? AND captured_at = ? ORDER BY record_id""",
        (batch_id, record_type, captured_at),
    ).fetchall()
    matching_source_rows = []
    for candidate in source_candidates:
        try:
            candidate_fields = json.loads(candidate[1])
        except (TypeError, json.JSONDecodeError):
            continue
        if candidate_fields.get("_source_file") == source_file and candidate_fields.get("_source_row") == source_row:
            score = int(candidate[3] == note_id and note_id is not None) * 4
            score += int(candidate[2] == creator_id and creator_id is not None) * 2
            score += int(candidate[3] is not None)
            matching_source_rows.append((score, int(candidate[0])))
    if matching_source_rows:
        inserted = False
        record_id = max(matching_source_rows)[1]
        existing_row = conn.execute(
            "SELECT fields_json FROM records WHERE record_id = ?", (record_id,)
        ).fetchone()
        if existing_row:
            existing_fields = json.loads(existing_row[0])
            merged_fields = dict(existing_fields)
            for key, value in safe.items():
                merged_fields.setdefault(key, value)
            if merged_fields != existing_fields:
                conn.execute(
                    "UPDATE records SET fields_json = ? WHERE record_id = ?",
                    (json.dumps(merged_fields, ensure_ascii=False, sort_keys=True), record_id),
                )
        if record_type == "comment_excerpt_sample":
            conn.execute(
                "UPDATE records SET creator_id = ?, note_id = ? WHERE record_id = ?",
                (creator_id, note_id, record_id),
            )
    else:
        cur = conn.execute(
            """INSERT OR IGNORE INTO records
               (batch_id, record_type, captured_at, fields_json, fields_hash, creator_id, note_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, record_type, captured_at, json.dumps(safe, ensure_ascii=False, sort_keys=True), digest, creator_id, note_id),
        )
        inserted = bool(cur.rowcount)
        if inserted:
            record_id = int(cur.lastrowid)
        else:
            existing = conn.execute(
                "SELECT record_id FROM records WHERE batch_id = ? AND record_type = ? AND fields_hash = ?",
                (batch_id, record_type, digest),
            ).fetchone()
            record_id = int(existing[0]) if existing else 0
            if record_id and record_type == "comment_excerpt_sample":
                conn.execute(
                    "UPDATE records SET creator_id = ?, note_id = ? WHERE record_id = ?",
                    (creator_id, note_id, record_id),
                )
    metric_count = 0
    metric_periods_backfilled = 0
    if metric_payload is not None:
        for key, raw, numeric, unit, period in _metric_specs(metric_payload):
            if not key or not record_id:
                continue
            status = "missing" if raw is None else ("parsed" if numeric is not None else "unresolved")
            metric_cur = conn.execute(
                """INSERT OR IGNORE INTO metric_values
                   (record_id, metric_key, raw_value, numeric_value, unit, period, parse_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record_id, key, raw, numeric, unit, period, status),
            )
            metric_count += int(bool(metric_cur.rowcount))
            if not metric_cur.rowcount and period:
                updated = conn.execute(
                    "UPDATE metric_values SET period = ? WHERE record_id = ? AND metric_key = ? AND period = ''",
                    (period, record_id, key),
                )
                metric_periods_backfilled += int(bool(updated.rowcount))
    return inserted, metric_count, metric_periods_backfilled, record_id


def _make_batch(conn: sqlite3.Connection, root: Path, relative: str, file_bytes: bytes) -> str:
    digest = _sha256(file_bytes)
    identity = f"{ADAPTER_VERSION}:{relative}:{digest}"
    batch_id = _stable_id("hist", identity)
    task_id = _stable_id("histtask", identity)
    path = root / relative
    if path.suffix.lower() == ".jsonl":
        rows, _ = _read_jsonl(path)
        times = [str(_first(row, "captured_at", "captured_on", "captured_at_local", "capture_time")) for _, row in rows if _first(row, "captured_at", "captured_on", "captured_at_local", "capture_time")]
    else:
        payload = _read_json(path)
        times = [str(payload.get("captured_on") or "")]
    captured = _capture_time(times[0] if times else None, _dataset_capture_time(root, relative))
    source = f"historical:{Path(relative).parent.name}"
    now = local_timestamp()
    conn.execute(
        """INSERT OR IGNORE INTO tasks(task_id, task_type, source, status, progress,
           parameters_json, created_at, started_at, finished_at)
           VALUES (?, 'historical_import', ?, 'completed', 100, ?, ?, ?, ?)""",
        (task_id, source, json.dumps({"source_file": relative, "source_sha256": digest, "adapter": ADAPTER_VERSION}, sort_keys=True), captured, captured, captured),
    )
    conn.execute(
        """INSERT OR IGNORE INTO batches
           (batch_id, task_id, source, captured_at, started_at, completed_at,
            status, filters_json, record_count, error_count)
           VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, 0, 0)""",
        (batch_id, task_id, source, captured, captured, captured, json.dumps({"source_file": relative, "source_sha256": digest, "adapter": ADAPTER_VERSION}, sort_keys=True)),
    )
    return batch_id


def _finish_batch(conn: sqlite3.Connection, batch_id: str, result: dict[str, Any]) -> None:
    count = int(conn.execute("SELECT COUNT(*) FROM records WHERE batch_id = ?", (batch_id,)).fetchone()[0])
    count += int(conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE batch_id = ?", (batch_id,)).fetchone()[0])
    conn.execute("UPDATE batches SET record_count = ?, status = 'completed' WHERE batch_id = ?", (count, batch_id))
    conn.execute(
        """UPDATE tasks SET progress = 100, status = 'completed',
           finished_at = COALESCE(finished_at, ?), result_json = ?
           WHERE task_id = (SELECT task_id FROM batches WHERE batch_id = ?)""",
        (local_timestamp(), json.dumps(result, ensure_ascii=False, sort_keys=True), batch_id),
    )


def _ensure_batch_for_file(conn: sqlite3.Connection, root: Path, relative: str) -> str:
    path = root / relative
    file_bytes = path.read_bytes()
    return _make_batch(conn, root, relative, file_bytes)


def _record_and_count(
    conn: sqlite3.Connection,
    report: dict[str, Any],
    batch_id: str,
    record_type: str,
    captured_at: str,
    creator_id: str | None,
    note_id: str | None,
    relative: str,
    row_no: int,
    fields: dict[str, Any],
    metric_payload: Any = None,
    link_kind: str = "none",
) -> None:
    inserted, metrics, periods_backfilled, record_id = _insert_record(
        conn,
        batch_id=batch_id,
        record_type=record_type,
        captured_at=captured_at,
        creator_id=creator_id,
        note_id=note_id,
        source_file=relative,
        source_row=row_no,
        fields=fields,
        metric_payload=metric_payload,
    )
    report["inserted_records"] += int(inserted)
    report["duplicate_records_skipped"] += int(not inserted)
    report["metric_values_inserted"] += metrics
    report["metric_periods_backfilled"] += periods_backfilled
    stored_metrics = int(conn.execute("SELECT COUNT(*) FROM metric_values WHERE record_id = ?", (record_id,)).fetchone()[0]) if record_id else 0
    report["audit_candidates"].append({
        "source_file": relative,
        "source_row": row_no,
        "record_type": record_type,
        "creator_link": "linked" if creator_id else "none",
        "note_link": link_kind if note_id else "none",
        "outcome": "present_and_verified" if record_id else "missing_after_import",
        "metrics": stored_metrics,
        "has_text": any(isinstance(v, str) and v.strip() for k, v in fields.items() if k.lower() in {"title", "body", "body_text", "comment_text", "comment_text_excerpt", "content_text_raw"}),
        "raw_fields": _safe_fields(fields),
    })


def _import_creator_rows(
    conn: sqlite3.Connection,
    root: Path,
    relative: str,
    rows: list[tuple[int, dict[str, Any]]],
    report: dict[str, Any],
) -> None:
    batch_id = _ensure_batch_for_file(conn, root, relative)
    report["batches_created_or_reused"] += 1
    for row_no, row in rows:
        captured = _capture_time(_first(row, "captured_on", "captured_at"))
        external_id = row.get("creator_id")
        report["creator_profile_rows_seen"] += 1
        report["creator_profile_rows_with_platform_id"] += int(external_id not in (None, ""))
        creator_key = f"platform:pgy:creator:{external_id}" if external_id else f"isolated:{relative}:row:{row_no}"
        creator_id = _creator_for(conn, creator_key, captured, "pgy", external_id, row)
        profile_fields = _safe_fields(row)
        profile_metrics = {
            "followers_count": row.get("followers_count"),
            "likes_and_favorites_count": row.get("likes_and_favorites_count"),
            "image_quote_cny": row.get("image_quote_cny"),
            "video_quote_cny": row.get("video_quote_cny"),
            "performance_medians": row.get("performance_medians", {}),
            "fan_snapshot": row.get("fan_snapshot", {}),
        }
        _record_and_count(conn, report, batch_id, "creator_profile_sample", captured, creator_id, None, relative, row_no, profile_fields, profile_metrics)
        if isinstance(row.get("performance_medians"), dict):
            _record_and_count(conn, report, batch_id, "creator_content_performance", captured, creator_id, None, relative, row_no, row["performance_medians"], row["performance_medians"])
        if isinstance(row.get("fan_snapshot"), dict):
            _record_and_count(conn, report, batch_id, "fan_analysis", captured, creator_id, None, relative, row_no, row["fan_snapshot"], row["fan_snapshot"])
    _finish_batch(conn, batch_id, {"source_rows": len(rows), "adapter": ADAPTER_VERSION})


def _import_note_rows(
    conn: sqlite3.Connection,
    root: Path,
    relative: str,
    rows: list[tuple[int, dict[str, Any]]],
    report: dict[str, Any],
    source_note_keys: dict[str, str],
) -> None:
    batch_id = _ensure_batch_for_file(conn, root, relative)
    report["batches_created_or_reused"] += 1
    for row_no, row in rows:
        captures = row.get("captures") if isinstance(row.get("captures"), list) else []
        capture_entries = [(i, c) for i, c in enumerate(captures, start=1) if isinstance(c, dict)] or [(1, {})]
        captured_base = _capture_time(_first(row, "captured_at", "captured_on", "publish_time_display_raw"))
        external_id = row.get("note_id") or _extract_note_id(row.get("note_url") or row.get("share_url_base"))
        source_creator_id = row.get("creator_id")
        report["note_rows_seen"] += 1
        report["note_rows_with_platform_creator_id"] += int(source_creator_id not in (None, ""))
        if source_creator_id:
            creator_key = f"platform:pgy:creator:{source_creator_id}"
            creator_id = _creator_for(conn, creator_key, captured_base, str(row.get("source") or "pgy"), source_creator_id, row)
            explicit_creator = True
        else:
            creator_key = f"isolated:{relative}:row:{row_no}"
            creator_id = _creator_for(conn, creator_key, captured_base, str(row.get("source") or "xiaohongshu"), row=row)
            explicit_creator = False
        fields = _safe_fields(row)
        note_key = str(row.get("note_key") or f"{relative}:row:{row_no}")
        note_id, exact = _note_for(conn, creator_id, f"{relative}:row:{row_no}:{note_key}", captured_base, external_id, fields, explicit_creator)
        for possible_key in (row.get("note_key"), row.get("note_id")):
            if possible_key not in (None, ""):
                source_note_keys[str(possible_key)] = note_id
        if external_id:
            source_note_keys[str(external_id)] = note_id
        if exact:
            report["exact_note_id_rows"] += 1
            report["linked_notes"] += 1
        else:
            report["isolated_notes"] += 1
        metrics = row.get("metrics") or row.get("normalized_values") or {}
        if not metrics and captures:
            metrics = capture_entries[0][1].get("xhs_metrics_raw", {})
        for capture_index, capture in capture_entries:
            captured = _capture_time(capture.get("captured_at") if capture else None, captured_base)
            capture_metrics = capture.get("xhs_metrics_raw") if isinstance(capture.get("xhs_metrics_raw"), (dict, list)) else metrics
            capture_fields = dict(fields)
            if len(capture_entries) > 1:
                capture_fields["capture_ordinal"] = capture_index
            metric_payload = {"period": row.get("period") or row.get("period_displayed"), "metrics": capture_metrics}
            _record_and_count(conn, report, batch_id, "note_detail", captured, creator_id, note_id, relative, row_no, capture_fields, metric_payload, "exact_platform_id" if exact else "source_row")
    _finish_batch(conn, batch_id, {"source_rows": len(rows), "adapter": ADAPTER_VERSION})


def _insert_comment_snapshot(
    conn: sqlite3.Connection,
    report: dict[str, Any],
    *,
    batch_id: str,
    relative: str,
    row_no: int,
    note_id: str,
    text: str,
    captured_at: str,
    displayed_time: Any = None,
    likes_raw: Any = None,
    likes: Any = None,
    replies_raw: Any = None,
    pinned: Any = None,
    comment_level: Any = None,
    raw_fields: dict[str, Any] | None = None,
    source: str,
    scope: str,
) -> None:
    local_hash = _sha256(f"{batch_id}:{relative}:{row_no}")
    numeric_likes = _parse_numeric(likes if likes is not None else likes_raw)
    like_count = int(numeric_likes) if numeric_likes is not None and numeric_likes.is_integer() else None
    pinned_value = int(pinned) if isinstance(pinned, bool) else None
    raw_values = _safe_fields(raw_fields or {})
    source_level = comment_level if comment_level is not None else (raw_fields or {}).get("comment_level", 1)
    normalized_level = 2 if str(source_level).strip().lower() in {"2", "reply", "回复"} else 1
    for key, value in {
        "source_file": relative,
        "source_row": row_no,
        "content_scope": scope,
        "displayed_time": displayed_time,
        "like_count_raw": likes_raw,
        "reply_count_raw": replies_raw,
        "comment_level": source_level,
        "comment_level_normalized": normalized_level,
        "is_pinned": pinned_value,
    }.items():
        raw_values.setdefault(key, value)
    raw_payload = json.dumps(raw_values, ensure_ascii=False, sort_keys=True)
    cur = conn.execute(
        """INSERT OR IGNORE INTO comment_snapshots
           (batch_id, note_id, local_comment_id_hash, parent_comment_id_hash,
            comment_text, cleaned_text, displayed_time, like_count_raw, like_count,
            reply_count_raw, comment_level, is_pinned, source, captured_at, raw_payload_json)
           VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            batch_id,
            note_id,
            local_hash,
            text,
            text.strip(),
            str(displayed_time) if displayed_time is not None else None,
            str(likes_raw) if likes_raw is not None else None,
            like_count,
            str(replies_raw) if replies_raw is not None else None,
            normalized_level,
            pinned_value,
            source,
            captured_at,
            raw_payload,
        ),
    )
    inserted = bool(cur.rowcount)
    if not inserted:
        found = conn.execute(
            """SELECT raw_payload_json FROM comment_snapshots
               WHERE batch_id = ? AND note_id = ? AND local_comment_id_hash = ?""",
            (batch_id, note_id, local_hash),
        ).fetchone()
        if not found:
            raise ValueError("Comment snapshot was neither inserted nor found after idempotent import")
        previous = json.loads(found[0])
        merged = dict(previous)
        merged.update(raw_values)
        if merged != previous:
            conn.execute(
                """UPDATE comment_snapshots SET raw_payload_json = ?
                   WHERE batch_id = ? AND note_id = ? AND local_comment_id_hash = ?""",
                (json.dumps(merged, ensure_ascii=False, sort_keys=True), batch_id, note_id, local_hash),
            )
    report["inserted_comment_snapshots"] += int(inserted)
    report["duplicate_comments_skipped"] += int(not inserted)
    report["audit_candidates"].append({
        "source_file": relative,
        "source_row": row_no,
        "record_type": "comment_snapshot" if scope == "full_visible_comment" else "comment_sample",
        "creator_link": "unknown",
        "note_link": "exact_platform_id",
        "outcome": "present_and_verified",
        "metrics": int(like_count is not None),
        "has_text": bool(text.strip()),
        "raw_fields": raw_values,
    })


def _extract_note_id(value: Any) -> str | None:
    if value is None:
        return None
    match = re.search(r"(?:explore/)?([0-9a-fA-F]{24})(?:[/?#]|$)", str(value))
    return match.group(1) if match else None


def _import_comment_excerpt_rows(
    conn: sqlite3.Connection,
    root: Path,
    relative: str,
    rows: list[tuple[int, dict[str, Any]]],
    report: dict[str, Any],
    source_note_keys: dict[str, str],
) -> None:
    batch_id = _ensure_batch_for_file(conn, root, relative)
    report["batches_created_or_reused"] += 1
    for row_no, row in rows:
        note_ref = str(row.get("note_key") or "")
        note_id = source_note_keys.get(note_ref)
        link_kind = "source_note_key" if note_id else "none"
        creator_id = None
        if note_id:
            owner = conn.execute("SELECT creator_id FROM notes WHERE note_id = ?", (note_id,)).fetchone()
            creator_id = str(owner[0]) if owner else None
        text = str(row.get("comment_text_excerpt") or "")
        fields = {
            **_safe_fields(row),
            "comment_text_excerpt": text,
            "display_time_raw": row.get("display_time_raw"),
            "likes_count": row.get("likes_count"),
            "likes_raw": row.get("likes_raw"),
            "content_scope": "excerpt_only",
        }
        captured = _capture_time(row.get("captured_at"), _dataset_capture_time(root, relative))
        _record_and_count(conn, report, batch_id, "comment_excerpt_sample", captured, creator_id, note_id, relative, row_no, fields, {"likes": {"raw_value": row.get("likes_raw"), "normalized_value": row.get("likes_count"), "unit": "count"}}, link_kind)
        report["comment_rows_seen"] += 1
        report["comment_rows_linked"] += int(note_id is not None)
    _finish_batch(conn, batch_id, {"source_rows": len(rows), "content_scope": "excerpt_only", "adapter": ADAPTER_VERSION})


def _import_xhs_comments(
    conn: sqlite3.Connection,
    root: Path,
    relative: str,
    rows: list[tuple[int, dict[str, Any]]],
    report: dict[str, Any],
) -> None:
    batch_id = _ensure_batch_for_file(conn, root, relative)
    report["batches_created_or_reused"] += 1
    for row_no, row in rows:
        captured = _capture_time(row.get("captured_at"), _dataset_capture_time(root, relative))
        external_id = row.get("note_id")
        note_id = None
        if external_id:
            note_hash = _sha256(str(external_id))
            matches = conn.execute("SELECT note_id FROM notes WHERE platform_note_id_hash = ? ORDER BY first_seen_at, note_id", (note_hash,)).fetchall()
            if matches:
                note_id = str(matches[0][0])
        if note_id is None:
            creator_id = _creator_for(conn, f"isolated:{relative}:comment-note:{row_no}", captured, "xiaohongshu")
            note_id, _ = _note_for(conn, creator_id, f"{relative}:comment-note:{row_no}", captured, external_id)
        report["comment_rows_seen"] += 1
        report["comment_rows_linked"] += int(bool(external_id))
        _insert_comment_snapshot(
            conn,
            report,
            batch_id=batch_id,
            relative=relative,
            row_no=row_no,
            note_id=note_id,
            text=str(row.get("comment_text") or ""),
            captured_at=captured,
            displayed_time=row.get("displayed_time_raw"),
            likes_raw=row.get("likes_raw"),
            likes=row.get("likes_normalized"),
            replies_raw=row.get("replies_raw"),
            pinned=row.get("is_pinned"),
            comment_level=row.get("comment_level", row.get("level", 1)),
            raw_fields=row,
            source="xiaohongshu_public_page",
            scope="full_visible_comment",
        )
    _finish_batch(conn, batch_id, {"source_rows": len(rows), "content_scope": "visible_comment", "adapter": ADAPTER_VERSION})


def _import_actual_sample(
    conn: sqlite3.Connection,
    root: Path,
    relative: str,
    payload: dict[str, Any],
    report: dict[str, Any],
) -> None:
    batch_id = _ensure_batch_for_file(conn, root, relative)
    report["batches_created_or_reused"] += 1
    captured = _capture_time(payload.get("captured_on"))
    creator = payload.get("creator") if isinstance(payload.get("creator"), dict) else {}
    creator_id = _creator_for(conn, f"isolated:{relative}:creator", captured, "pgy", row=creator)
    for record_type, value, metric_payload in (
        ("creator_profile_sample", creator, {"followers": creator.get("followers"), "quotes_rmb": creator.get("quotes_rmb"), "category_tags": []}),
        ("creator_content_performance", payload.get("creator_performance", {}), payload.get("creator_performance", {})),
        ("fan_analysis", payload.get("fan_analysis", {}), payload.get("fan_analysis", {})),
    ):
        if isinstance(value, dict) and value:
            _record_and_count(conn, report, batch_id, record_type, captured, creator_id, None, relative, 1, value, metric_payload)
    notes = payload.get("notes") if isinstance(payload.get("notes"), list) else []
    for note_index, note in enumerate(notes, start=1):
        if not isinstance(note, dict):
            continue
        report["note_rows_seen"] += 1
        detail = note.get("xhs_public_page") if isinstance(note.get("xhs_public_page"), dict) else {}
        pgy_detail = note.get("pgy_detail") if isinstance(note.get("pgy_detail"), dict) else {}
        note_id_external = _extract_note_id(note.get("note_url"))
        merged = {**detail, **pgy_detail}
        merged["source"] = "pgy_note_detail_investigation"
        note_id, exact = _note_for(
            conn,
            creator_id,
            f"{relative}:note:{note_index}",
            captured,
            note_id_external,
            merged,
            explicit_creator_link=False,
        )
        _record_and_count(conn, report, batch_id, "note_detail", captured, creator_id, note_id, relative, note_index, merged, pgy_detail.get("metrics") or detail.get("metrics"), "exact_platform_id" if exact else "source_row")
        if exact:
            report["exact_note_id_rows"] += 1
            report["linked_notes"] += 1
        comments = detail.get("comment_samples") if isinstance(detail.get("comment_samples"), list) else []
        for comment_index, comment in enumerate(comments, start=1):
            if not isinstance(comment, dict):
                continue
            _insert_comment_snapshot(
                conn,
                report,
                batch_id=batch_id,
                relative=relative,
                row_no=note_index * 1000 + comment_index,
                note_id=note_id,
                text=str(comment.get("text") or ""),
                captured_at=captured,
                displayed_time=comment.get("time_displayed"),
                likes_raw=comment.get("likes_displayed"),
                replies_raw=comment.get("replies_displayed"),
                pinned=comment.get("pinned"),
                comment_level=comment.get("comment_level", comment.get("level", 1)),
                raw_fields=comment,
                source="pgy_detail_comment_sample",
                scope="sample_excerpt",
            )
            report["comment_rows_seen"] += 1
            report["comment_rows_linked"] += int(bool(note_id))
    _finish_batch(conn, batch_id, {"source_rows": 1, "adapter": ADAPTER_VERSION})


def _raw_duplicates(rows: Iterable[tuple[int, dict[str, Any]]]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for _, row in rows:
        digest = _sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        if digest in seen:
            duplicates += 1
        seen.add(digest)
    return duplicates


def _source_summary(relative: str, rows: list[tuple[int, dict[str, Any]]], malformed: int) -> dict[str, Any]:
    all_keys = sorted({str(key) for _, row in rows for key in row})
    coverage: dict[str, str] = {}
    for key in all_keys:
        present = sum(1 for _, row in rows if row.get(key) not in (None, "", [], {}))
        coverage[key] = f"{present}/{len(rows)}"
    return {
        "file": relative,
        "parsed_rows": len(rows),
        "malformed_rows": malformed,
        "duplicate_rows": _raw_duplicates(rows),
        "field_coverage": coverage,
    }


def _inventory(root: Path) -> dict[str, Any]:
    research = root / "research"
    report_dir = (research / "r4_4_integration").resolve()
    files = [
        p for p in research.rglob("*")
        if p.is_file() and report_dir not in p.resolve().parents and p.resolve() != report_dir
    ]
    counts = Counter(p.suffix.lower() or "[no suffix]" for p in files)
    return {
        "all_files": len(files),
        "jsonl_files": counts.get(".jsonl", 0),
        "csv_files": counts.get(".csv", 0),
        "sqlite_main_files": counts.get(".sqlite3", 0),
        "files_by_suffix": dict(sorted(counts.items())),
    }


def _preimported_r42(root: Path, db_path: Path) -> dict[str, Any]:
    source = root / R42_SOURCE
    if not source.exists():
        return {"source_rows": 0, "already_integrated": 0, "status": "source_missing"}
    rows, malformed = _read_jsonl(source)
    batch_ids = {str(row.get("batch_id")) for _, row in rows if row.get("batch_id")}
    integrated = 0
    if db_path.exists():
        conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            for batch_id in batch_ids:
                exists = conn.execute("SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
                if exists:
                    integrated += int(conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE batch_id = ?", (batch_id,)).fetchone()[0])
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return {"source_rows": len(rows), "malformed_rows": malformed, "already_integrated": integrated, "status": "not_reimported_from_r42"}


def _report_markdown(report: dict[str, Any]) -> str:
    inv = report["inventory"]
    lines = [
        "# CreatorScope V1 历史数据整合审计",
        "",
        f"生成时间：{report['generated_at']}",
        f"目标数据库：`{report['database_label']}`（本地忽略文件）",
        "",
        "## 文件盘点",
        "",
        f"- research 下文件：{inv['all_files']}；JSONL：{inv['jsonl_files']}；CSV：{inv['csv_files']}；SQLite 主文件：{inv['sqlite_main_files']}。",
        f"- 实际导入白名单：{len(report['sources'])} 个原始文件；每个来源按 SHA-256 形成独立可重复导入批次。",
        f"- R4.2 原始评论：{report['r42']['source_rows']} 行，其中已存在于阶段 A 统一库 {report['r42']['already_integrated']} 行；本阶段未再次导入。",
        "- R4.3 验证库、图表、分析 CSV 和过程截图属于验证或派生材料，未作为新采集批次导入。",
        "",
        "## 导入结果",
        "",
        f"- 解析成功：{report['parsed_source_rows']} 条顶层来源行；JSONL 格式错误：{report['malformed_rows']} 行。",
        f"- 新增 records：{report['inserted_records']}；同批次重复跳过：{report['duplicate_records_skipped']}；新增指标快照：{report['metric_values_inserted']}。",
        f"- 本次执行补全历史指标周期：{report['metric_periods_backfilled']} 项；原始记录未覆盖。",
        f"- 新增评论快照：{report['inserted_comment_snapshots']}；重复评论跳过：{report['duplicate_comments_skipped']}。摘录与完整可见评论按来源范围区分。",
        f"- 当前白名单批次累计：records {report['historical_totals']['records']}，metric_values {report['historical_totals']['metric_values']}（其中 {report['historical_totals']['metric_values_with_period']} 项有统计周期），comment_snapshots {report['historical_totals']['comment_snapshots']}。",
        f"- 数据库差量：creators {report['db_delta']['creators']:+d}，notes {report['db_delta']['notes']:+d}，records {report['db_delta']['records']:+d}，metric_values {report['db_delta']['metric_values']:+d}，comment_snapshots {report['db_delta']['comment_snapshots']:+d}。",
        "",
        "## 字段缺失与关联",
        "",
        f"- 精确平台笔记 ID：{report['exact_note_id_rows']} 行；无平台 ID、按文件和行隔离的笔记：{report['isolated_notes']} 行。",
        f"- 评论行：{report['comment_rows_seen']}；关联到笔记：{report['comment_rows_linked']}（{report['comment_link_rate']:.1%}）。",
        "- 创作者关联使用源记录中的原始平台 ID；缺少 ID 时按来源行隔离，不按昵称或标题合并。原始名称、ID、链接和正文写入本地记录与实体字段。",
        f"- 档案精确 ID 覆盖：{report['creator_profile_rows_with_platform_id']}/{report['creator_profile_rows_seen']}；笔记直接关联 creator_id：{report['note_rows_with_platform_creator_id']}/{report['note_rows_seen']}（{report['note_creator_link_rate']:.1%}）；评论关联笔记：{report['comment_rows_linked']}/{report['comment_rows_seen']}（{report['comment_link_rate']:.1%}）。",
        "- 每个来源字段覆盖率详见 `report.json`；随机抽查的完整来源字段也保留在 `random_audit` 中。",
        "",
        "## 文件级审计",
        "",
        "| 来源文件 | 解析行 | 格式错误 | 原始重复行 | 缺失字段（低于全量） |",
        "|---|---:|---:|---:|---|",
    ]
    for source in report["sources"]:
        missing = [key for key, ratio in source["field_coverage"].items() if ratio != f"{source['parsed_rows']}/{source['parsed_rows']}"]
        # Keep the report compact; actual ratios remain in report.json.
        missing_text = ", ".join(missing[:8]) or "—"
        if len(missing) > 8:
            missing_text += f" 等 {len(missing)} 个字段"
        lines.append(f"| `{source['file']}` | {source['parsed_rows']} | {source['malformed_rows']} | {source['duplicate_rows']} | {missing_text} |")
    lines.extend([
        "",
        "## 随机抽查",
        "",
        "固定随机种子抽取最多 10 条导入记录；报告保留来源行、关联状态和完整原始字段。",
        "",
        "| 来源 | 行 | 类型 | Creator 关联 | Note 关联 | 结果 | 指标数 | 原始采集字段 JSON |",
        "|---|---:|---|---|---|---|---:|---|",
    ])
    for item in report["random_audit"]:
        raw_fields = json.dumps(item.get("raw_fields", {}), ensure_ascii=False, sort_keys=True).replace("|", "\\|")
        lines.append(f"| `{item['source_file']}` | {item['source_row']} | {item['record_type']} | {item['creator_link']} | {item['note_link']} | {item['outcome']} | {item['metrics']} | `{raw_fields}` |")
    lines.extend([
        "",
        "## 数据范围说明",
        "",
        "- 连续采集目录的 45 条评论只有文本摘录；作为 `comment_excerpt_sample` 保存，不计入完整评论快照。",
        "- 评论样本保留源文件中的全部公开可见字段；来源文件没有采集到的评论者字段不会补造。",
        "- 统计周期及原始展示值保留在 records/metric_values；未将不同来源或不同时间的指标覆盖合并。",
        "- Cookie、Session、Token、密码等认证凭据不会导入；邮箱、电话、平台 ID、名称、链接、正文等来源字段按原值保存。",
        "",
    ])
    return "\n".join(lines)


def integrate_historical_data(
    db_path: Path,
    project_root: Path,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    db_path = Path(db_path)
    root = Path(project_root)
    initialize_database(db_path)
    before = _database_counts(db_path)
    report: dict[str, Any] = {
        "generated_at": local_timestamp(),
        "database_label": db_path.name,
        "inventory": _inventory(root),
        "sources": [],
        "parsed_source_rows": 0,
        "malformed_rows": 0,
        "inserted_records": 0,
        "duplicate_records_skipped": 0,
        "metric_values_inserted": 0,
        "metric_periods_backfilled": 0,
        "inserted_comment_snapshots": 0,
        "duplicate_comments_skipped": 0,
        "comment_rows_seen": 0,
        "comment_rows_linked": 0,
        "linked_notes": 0,
        "exact_note_id_rows": 0,
        "isolated_notes": 0,
        "creator_profile_rows_seen": 0,
        "creator_profile_rows_with_platform_id": 0,
        "note_rows_seen": 0,
        "note_rows_with_platform_creator_id": 0,
        "batches_created_or_reused": 0,
        "audit_candidates": [],
    }
    source_note_keys: dict[str, str] = {}
    loaded: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.exists():
            continue
        if path.suffix.lower() == ".jsonl":
            rows, malformed = _read_jsonl(path)
            summary = _source_summary(relative, rows, malformed)
            loaded[relative] = rows
            report["parsed_source_rows"] += len(rows)
            report["malformed_rows"] += malformed
        else:
            payload = _read_json(path)
            rows = [(1, payload)]
            loaded[relative] = rows
            report["parsed_source_rows"] += 1
            summary = _source_summary(relative, rows, 0)
        report["sources"].append(summary)

    # Entity files first so later comments can use exact note IDs or source note keys.
    for relative in SOURCE_FILES:
        rows = loaded.get(relative)
        if rows is None:
            continue
        with connection(db_path) as conn:
            if relative.endswith("raw_creators.jsonl"):
                _import_creator_rows(conn, root, relative, rows, report)
            elif relative.endswith("raw_notes_pgy.jsonl") or relative.endswith("raw_notes_xhs.jsonl"):
                _import_note_rows(conn, root, relative, rows, report, source_note_keys)
            elif relative == "research/xhs_public_access_probe/raw_comments_xhs.jsonl":
                _import_xhs_comments(conn, root, relative, rows, report)
            elif relative == "research/pgy_note_detail_investigation_20261120/actual_samples.json":
                _import_actual_sample(conn, root, relative, rows[0][1], report)
            elif relative.endswith("raw_comments.jsonl"):
                _import_comment_excerpt_rows(conn, root, relative, rows, report, source_note_keys)
            elif relative.endswith("raw_comments_xhs.jsonl"):
                _import_xhs_comments(conn, root, relative, rows, report)
    report["r42"] = _preimported_r42(root, db_path)
    report["db_delta"] = {key: _database_counts(db_path)[key] - before[key] for key in before}
    report["historical_totals"] = _source_batch_totals(db_path, root)
    report["comment_link_rate"] = (
        report["comment_rows_linked"] / report["comment_rows_seen"]
        if report["comment_rows_seen"]
        else 0.0
    )
    report["note_creator_link_rate"] = (
        report["note_rows_with_platform_creator_id"] / report["note_rows_seen"]
        if report["note_rows_seen"]
        else 0.0
    )
    rng = random.Random(20261121)
    report["random_audit"] = rng.sample(report["audit_candidates"], min(10, len(report["audit_candidates"])))
    report.pop("audit_candidates", None)
    report["integrity"] = _verify_database(db_path)
    if report_dir:
        report_dir = Path(report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (report_dir / "report.md").write_text(_report_markdown(report), encoding="utf-8")
    return report


def _database_counts(db_path: Path) -> dict[str, int]:
    with connection(db_path) as conn:
        return {
            table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in ("creators", "notes", "records", "metric_values", "comment_snapshots")
        }


def _source_batch_totals(db_path: Path, root: Path) -> dict[str, int]:
    totals = {"records": 0, "metric_values": 0, "metric_values_with_period": 0, "comment_snapshots": 0}
    with connection(db_path) as conn:
        for relative in SOURCE_FILES:
            path = root / relative
            if not path.exists():
                continue
            identity = f"{ADAPTER_VERSION}:{relative}:{_sha256(path.read_bytes())}"
            batch_id = _stable_id("hist", identity)
            records = int(conn.execute("SELECT COUNT(*) FROM records WHERE batch_id = ?", (batch_id,)).fetchone()[0])
            metrics = int(conn.execute(
                "SELECT COUNT(*) FROM metric_values m JOIN records r ON r.record_id = m.record_id WHERE r.batch_id = ?",
                (batch_id,),
            ).fetchone()[0])
            metrics_with_period = int(conn.execute(
                "SELECT COUNT(*) FROM metric_values m JOIN records r ON r.record_id = m.record_id WHERE r.batch_id = ? AND m.period != ''",
                (batch_id,),
            ).fetchone()[0])
            comments = int(conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE batch_id = ?", (batch_id,)).fetchone()[0])
            totals["records"] += records
            totals["metric_values"] += metrics
            totals["metric_values_with_period"] += metrics_with_period
            totals["comment_snapshots"] += comments
    return totals


def _verify_database(db_path: Path) -> dict[str, Any]:
    with connection(db_path) as conn:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        duplicate_platform_note_hashes = int(conn.execute(
            "SELECT COUNT(*) FROM (SELECT platform_note_id_hash FROM notes WHERE platform_note_id_hash IS NOT NULL GROUP BY platform_note_id_hash HAVING COUNT(*) > 1)"
        ).fetchone()[0])
    return {"integrity": integrity, "foreign_key_errors": foreign_keys, "duplicate_platform_note_hash_groups": duplicate_platform_note_hashes}


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Idempotently integrate local historical CreatorScope samples.")
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument("--db", type=Path, default=project_root / "instance" / "creatorscope_unified.sqlite3")
    parser.add_argument("--report-dir", type=Path, default=project_root / "research" / "r4_4_integration")
    args = parser.parse_args()
    result = integrate_historical_data(args.db, args.project_root, args.report_dir)
    print(json.dumps({key: value for key, value in result.items() if key not in {"random_audit"}}, ensure_ascii=False, indent=2))
    return 0 if result["integrity"]["integrity"] == "ok" and result["integrity"]["foreign_key_errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
