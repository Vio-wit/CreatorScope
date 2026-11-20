from __future__ import annotations

import hashlib
import csv
import io
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from .db import connection, ensure_creator_ref, ensure_note, ensure_note_ref, local_timestamp


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

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_RECORDS = 500
IDENTITY_KEYS = {
    "昵称", "博主名称", "博主名", "达人名称", "达人昵称", "创作者名称",
    "博主id", "达人id", "创作者id", "用户id", "uid", "小红书号",
    "creatorid", "bloggerid", "userid", "nickname", "creatorname",
    "bloggername", "creatorref", "creatorkey", "creatorurl", "bloggerurl",
    "profileurl", "username", "userhandle", "账号名称", "账号名", "账号昵称",
    "作者昵称", "博主主页链接", "笔记链接", "researchid", "noteref", "研究对象编号",
}
PRIVATE_KEYS = {"password", "passwd", "cookie", "token", "手机号", "手机号码", "邮箱", "登录账号"}
EXPORT_META_KEYS = {
    "batch_id", "record_id", "record_type", "type", "captured_at", "creator_ref",
    "note_ref", "period_displayed", "period_selector_displayed", "fields_json",
    "creator_key", "identity_key", "数据类型", "采集时间", "批次编号",
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


def _normalized_key(key: Any) -> str:
    return re.sub(r"[\s_\-（）()]+", "", str(key)).lower()


def _is_private_field(key: Any) -> bool:
    normalized = _normalized_key(key)
    return (
        normalized in IDENTITY_KEYS
        or normalized in PRIVATE_KEYS
        or any(token in normalized for token in ("password", "cookie", "token", "手机号", "邮箱", "头像"))
    )


def _clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in fields.items()
        if not _is_private_field(key)
        and (isinstance(value, (str, int, float, bool)) or value is None)
    }


def _first_present(row: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def payload_from_upload(filename: str, content: bytes) -> dict[str, Any]:
    """Parse bounded JSON/CSV uploads without retaining the original file."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("导入文件超过 2 MB 限制")
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON 文件无法解析；请使用 UTF-8 编码的 JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise ValueError("JSON 需包含 records 数组")
        if len(payload["records"]) > MAX_UPLOAD_RECORDS:
            raise ValueError("单个文件最多导入 500 条记录")
        return payload
    if suffix != ".csv":
        raise ValueError("仅支持 .json 或 .csv 文件")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV 文件需使用 UTF-8 编码") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or len(reader.fieldnames) > 200:
        raise ValueError("CSV 缺少表头或字段数超过 200 列")

    records: list[dict[str, Any]] = []
    for index, row in enumerate(reader, start=1):
        if index > MAX_UPLOAD_RECORDS:
            raise ValueError("单个文件最多导入 500 行")
        if not any(value and str(value).strip() for value in row.values()):
            continue
        record_type = _first_present(row, ("record_type", "type", "数据类型"))
        creator_ref = _first_present(
            row,
            (
                "creator_ref", "creator_key", "creator_id", "blogger_id", "达人ID",
                "博主ID", "创作者ID", "用户ID", "UID", "小红书号", "研究对象编号",
                "博主名称", "达人昵称", "昵称",
            ),
        )
        note_ref = _first_present(row, ("note_ref", "note_id", "笔记ID"))
        captured_at = _first_present(row, ("captured_at", "采集时间"))
        period = _first_present(row, ("period_displayed", "period_selector_displayed", "统计周期"))

        fields: dict[str, Any] = {}
        if row.get("fields_json"):
            try:
                decoded = json.loads(str(row["fields_json"]))
            except json.JSONDecodeError as exc:
                raise ValueError(f"CSV 第 {index} 行 fields_json 不是有效 JSON") from exc
            if not isinstance(decoded, dict):
                raise ValueError(f"CSV 第 {index} 行 fields_json 必须是对象")
            fields.update(decoded)
        else:
            for key, value in row.items():
                if key is None or _normalized_key(key) in {
                    _normalized_key(item) for item in EXPORT_META_KEYS
                } or _is_private_field(key):
                    continue
                if value is not None and str(value).strip() != "":
                    fields[str(key)] = str(value).strip()

        raw_type = record_type or "blogger_list_sample"
        type_aliases = {
            "博主列表": "blogger_list_sample",
            "博主详情": "creator_profile_sample",
            "博主资料与报价": "creator_profile_sample",
            "内容表现": "creator_content_performance",
            "粉丝画像": "fan_analysis",
            "笔记详情面板": "note_detail",
            "笔记案例": "note_detail",
        }
        records.append(
            {
                "type": type_aliases.get(raw_type, raw_type),
                "creator_ref": creator_ref or f"csv-row:{index}",
                "note_ref": note_ref,
                "captured_at": captured_at,
                "period_displayed": period,
                "fields": fields,
            }
        )
    if not records:
        raise ValueError("CSV 中没有可导入的数据行")
    return {"source": "CSV 文件导入", "records": records}


def ingest_payload(
    db_path: Path,
    task_id: str,
    payload: dict[str, Any],
    source_override: str | None = None,
    creator_id_override: str | None = None,
    batch_id_override: str | None = None,
) -> dict[str, Any]:
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("数据源没有可导入的记录")
    if len(records) > MAX_UPLOAD_RECORDS:
        raise ValueError("单批次上限为 500 条记录")

    captured_at = str(payload.get("captured_at_local") or local_timestamp())
    source = source_override or str(payload.get("source") or "蒲公英可见页面")
    batch_id = batch_id_override or uuid4().hex
    inserted_records = 0
    inserted_metrics = 0
    duplicate_records = 0
    fields_for_frame: list[dict[str, Any]] = []

    with connection(db_path) as conn:
        if batch_id_override:
            batch = conn.execute(
                "SELECT task_id, status FROM batches WHERE batch_id = ?",
                (batch_id_override,),
            ).fetchone()
            if not batch or str(batch["task_id"]) != task_id:
                raise ValueError("采集批次不存在或不属于当前任务")
            if str(batch["status"]) not in {"queued", "running", "paused"}:
                raise ValueError("采集批次已结束，不能继续写入")
            conn.execute(
                "UPDATE batches SET status='running', completed_at=NULL WHERE batch_id=?",
                (batch_id_override,),
            )
        else:
            conn.execute(
                """INSERT INTO batches
                   (batch_id, task_id, source, captured_at, started_at, status)
                   VALUES (?, ?, ?, ?, ?, 'running')""",
                (batch_id, task_id, source, captured_at, captured_at),
            )
        for item in records:
            if not isinstance(item, dict):
                continue
            record_type = str(item.get("type") or "unknown")
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            # Persist only the selected, visible fields; never store whole-page text.
            clean_fields = _clean_fields(fields)
            record_creator_ref = (
                creator_id_override
                or item.get("creator_ref")
                or item.get("creator_key")
                or payload.get("creator_ref")
                or payload.get("creator_key")
                or f"task:{task_id}:creator:1"
            )
            if creator_id_override:
                creator_row = conn.execute(
                    "SELECT 1 FROM creators WHERE creator_id = ?", (creator_id_override,)
                ).fetchone()
                if not creator_row:
                    raise ValueError("所选匿名博主已不存在，请刷新页面后重试")
                creator_id = creator_id_override
            else:
                creator_id = ensure_creator_ref(
                    conn, str(record_creator_ref), str(item.get("captured_at") or captured_at)
                )

            # Scope the dedupe key to a creator so identical field values for
            # different people in the same batch are not silently collapsed.
            digest = _record_hash(record_type, {"creator_id": creator_id, **clean_fields})
            note_id = None
            if record_type in {"note_detail", "xhs_public_note"}:
                title = str(clean_fields.get("笔记标题") or clean_fields.get("标题") or "")
                published = str(clean_fields.get("发布时间") or "")
                note_timestamp = str(item.get("captured_at") or captured_at)
                if item.get("note_ref"):
                    note_id = ensure_note_ref(conn, creator_id, str(item["note_ref"]), note_timestamp)
                    platform_note_id_hash = hashlib.sha256(
                        str(item["note_ref"]).encode("utf-8")
                    ).hexdigest()
                else:
                    # Without a platform ID, keep this note scoped to this record
                    # and batch instead of treating its title as an identity key.
                    note_key = f"no-platform-id:{batch_id}:{digest}"
                    note_id = ensure_note(conn, creator_id, note_key, note_timestamp)
                    platform_note_id_hash = None
                conn.execute(
                    """UPDATE notes SET platform_note_id_hash = COALESCE(?, platform_note_id_hash),
                       title = COALESCE(?, title), body = COALESCE(?, body),
                       published_at = COALESCE(?, published_at),
                       note_type = COALESCE(?, note_type), source = ?,
                       topics_json = COALESCE(?, topics_json)
                       WHERE note_id = ?""",
                    (
                        platform_note_id_hash,
                        title or None,
                        clean_fields.get("笔记正文") or clean_fields.get("正文"),
                        published or None,
                        clean_fields.get("笔记类型") or clean_fields.get("内容类型"),
                        source,
                        json.dumps(item.get("note_topics"), ensure_ascii=False)
                        if isinstance(item.get("note_topics"), list)
                        else None,
                        note_id,
                    ),
                )

            if record_type == "creator_profile_sample":
                follower_value = next(
                    (
                        value
                        for key, value in clean_fields.items()
                        if key in {"粉丝数", "粉丝量", "粉丝数量"}
                    ),
                    None,
                )
                follower_count = (
                    normalize_value("粉丝数", follower_value)[0]
                    if follower_value is not None
                    else None
                )
                category = next(
                    (clean_fields[key] for key in ("领域", "内容领域", "创作领域", "类目") if clean_fields.get(key)),
                    None,
                )
                location = next(
                    (clean_fields[key] for key in ("所在地", "城市", "地区", "地域") if clean_fields.get(key)),
                    None,
                )
                introduction = next(
                    (clean_fields[key] for key in ("简介", "个人简介", "创作者简介") if clean_fields.get(key)),
                    None,
                )
                conn.execute(
                    """UPDATE creators SET platform = ?, category = ?, location = ?,
                       follower_count = COALESCE(?, follower_count), introduction = ?
                       WHERE creator_id = ?""",
                    ("xiaohongshu", category, location, follower_count, introduction, creator_id),
                )
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO records
                    (batch_id, record_type, captured_at, fields_json, fields_hash, creator_id, note_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    record_type,
                    str(item.get("captured_at") or captured_at),
                    json.dumps(clean_fields, ensure_ascii=False, sort_keys=True),
                    digest,
                    creator_id,
                    note_id,
                ),
            )
            if cur.rowcount == 0:
                duplicate_records += 1
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

        if batch_id_override:
            conn.execute(
                """UPDATE batches SET record_count = (
                       SELECT COUNT(*) FROM records WHERE batch_id = ?
                   ), status='running' WHERE batch_id = ?""",
                (batch_id, batch_id),
            )
        else:
            conn.execute(
                """UPDATE batches SET record_count = ?, completed_at = ?,
                   status = 'completed' WHERE batch_id = ?""",
                (inserted_records, local_timestamp(), batch_id),
            )
        conn.execute(
            "UPDATE tasks SET batch_id = ? WHERE task_id = ?",
            (batch_id, task_id),
        )

    return {
        "batch_id": batch_id,
        "record_count": inserted_records,
        "duplicate_count": duplicate_records,
        "metric_count": inserted_metrics,
        "parsed_metric_count": parsed_count,
        "message": f"已写入 {inserted_records} 条记录、{inserted_metrics} 个字段值",
    }


def ingest_comment_snapshots(
    db_path: Path,
    *,
    task_id: str,
    batch_id: str,
    creator_ref: str,
    note_ref: str,
    comments: list[dict[str, Any]],
    captured_at: str | None = None,
    source: str = "小红书公开可见页面",
) -> dict[str, int]:
    """Append visible comment rows to the task batch without storing commenter identity."""
    from .db import ensure_creator_ref, ensure_note_ref

    if len(comments) > 20:
        raise ValueError("单篇笔记评论采集上限为 20 条")
    captured_at = captured_at or local_timestamp()
    inserted = duplicate = 0
    with connection(db_path) as conn:
        batch = conn.execute(
            "SELECT task_id, status FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if not batch or str(batch["task_id"]) != task_id:
            raise ValueError("采集批次不存在或不属于当前任务")
        if str(batch["status"]) not in {"queued", "running", "paused"}:
            raise ValueError("采集批次已结束，不能继续写入")
        creator_id = ensure_creator_ref(conn, creator_ref, captured_at)
        note_id = ensure_note_ref(conn, creator_id, note_ref, captured_at)
        local_ids: dict[str, str] = {}
        prepared: list[tuple[dict[str, Any], str, str | None]] = []
        for item in comments:
            text = str(item.get("comment_text") or "").strip()
            if not text:
                continue
            local_id = str(item.get("local_comment_id") or "")
            if not local_id:
                local_id = _record_hash(
                    "comment",
                    {
                        "text": text,
                        "time": item.get("displayed_time"),
                        "level": item.get("comment_level", 1),
                        "parent": item.get("parent_local_comment_id"),
                    },
                )
            local_hash = hashlib.sha256(
                f"{note_id}\x1f{local_id}".encode("utf-8")
            ).hexdigest()
            local_ids[local_id] = local_hash
            parent_local_id = item.get("parent_local_comment_id")
            parent_hash = local_ids.get(str(parent_local_id)) if parent_local_id else None
            prepared.append((item, local_hash, parent_hash))

        for item, local_hash, parent_hash in prepared:
            text = str(item.get("comment_text") or "").strip()
            level = 2 if int(item.get("comment_level") or 1) == 2 else 1
            clean_text = re.sub(r"\s+", " ", text).strip()
            safe_payload = {
                "displayed_time": item.get("displayed_time"),
                "like_count_raw": item.get("like_count_raw"),
                "reply_count_raw": item.get("reply_count_raw"),
                "comment_level": level,
                "is_pinned": item.get("is_pinned"),
            }
            like_raw = item.get("like_count_raw")
            like_value = normalize_value("点赞量", like_raw)[0] if like_raw else None
            cursor = conn.execute(
                """INSERT OR IGNORE INTO comment_snapshots
                   (batch_id, note_id, local_comment_id_hash, parent_comment_id_hash,
                    comment_text, cleaned_text, displayed_time, like_count_raw,
                    like_count, reply_count_raw, comment_level, is_pinned, source,
                    captured_at, raw_payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch_id,
                    note_id,
                    local_hash,
                    parent_hash,
                    text,
                    clean_text,
                    str(item.get("displayed_time") or "") or None,
                    str(like_raw) if like_raw is not None else None,
                    int(like_value) if like_value is not None else None,
                    str(item.get("reply_count_raw"))
                    if item.get("reply_count_raw") is not None
                    else None,
                    level,
                    None
                    if item.get("is_pinned") is None
                    else int(bool(item.get("is_pinned"))),
                    source,
                    str(item.get("captured_at") or captured_at),
                    json.dumps(safe_payload, ensure_ascii=False),
                ),
            )
            if cursor.rowcount:
                inserted += 1
            else:
                duplicate += 1
        return {"inserted": inserted, "duplicates": duplicate}


def finish_collection_batch(db_path: Path, batch_id: str, status: str) -> None:
    if status not in {"completed", "paused", "stopped", "failed"}:
        raise ValueError("无效的批次状态")
    with connection(db_path) as conn:
        conn.execute(
            """UPDATE batches SET status=?, completed_at=?, record_count=(
                   SELECT COUNT(*) FROM records WHERE batch_id=?
               ) WHERE batch_id=?""",
            (
                status,
                local_timestamp() if status != "paused" else None,
                batch_id,
                batch_id,
            ),
        )


def load_baseline(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("基线 JSON 结构无效")
    return payload
