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

from .db import backfill_field_observations, connection, ensure_creator_ref, ensure_note, ensure_note_ref, local_timestamp


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
    "曝光中位数（合作）",
    "阅读中位数（合作）",
    "互动中位数（合作）",
    "曝光中位数",
    "阅读中位数",
    "互动中位数",
    "中位点赞量",
    "中位收藏量",
    "中位评论量",
    "中位分享量",
    "中位关注量",
    "关注量",
    "新增关注",
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
DURATION_KEYS = {"视频时长", "时长"}
KNOWN_LABELS = {
    *COUNT_KEYS,
    *RATE_KEYS,
    *PRICE_KEYS,
    "内容类目",
    "发布时间",
}

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_RECORDS = 500
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
EXPORT_META_KEYS = {
    "batch_id", "record_id", "record_type", "type", "captured_at", "creator_ref",
    "note_ref", "period_displayed", "period_selector_displayed", "fields_json",
    "creator_key", "identity_key", "数据类型", "采集时间", "批次编号",
    "local_creator_id", "local_note_id",
}
RAW_ID_KEYS = {"creator_ref", "creator_key", "creator_id", "note_ref", "note_id"}
NON_METRIC_FIELD_KEYS = {
    "creator_name", "platform_creator_id", "creator_id", "nickname",
    "创作者名称", "平台公开ID", "公开平台ID", "公开ID", "博主信息原始显示",
}

# 评论区交互按钮的占位文案：未展示计数时按钮上只有动作词，不应当作数字字段落库。
_INTERACTION_ACTION_WORDS = {
    "赞", "点赞", "回复", "评论", "收藏", "分享", "转发", "赞藏", "推荐", "收起", "展开",
}


def _clean_interaction_raw(raw_value: Any) -> str | None:
    """清洗评论点赞/回复原始文案；纯动作文案且不含数字时返回 None。"""
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    if not re.search(r"\d", text) and text in _INTERACTION_ACTION_WORDS:
        return None
    return text



def normalize_value(key: str, raw_value: Any) -> tuple[float | None, str, str]:
    if raw_value is None:
        return None, _unit_for(key), "missing"

    raw = str(raw_value).strip()
    if not raw or raw in {"-", "--", "—", "–"}:
        return None, _unit_for(key), "displayed_unavailable"

    if key in DURATION_KEYS:
        match = re.fullmatch(r"(\d{1,3}):(\d{2})(?::(\d{2}))?", raw)
        if match:
            parts = [int(part) for part in match.groups() if part is not None]
            seconds = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
            return seconds, "seconds", "parsed"
        return None, "seconds", "not_numeric"

    # Do not extract incidental digits from category labels or dates.
    if key not in COUNT_KEYS | RATE_KEYS | PRICE_KEYS:
        return None, _unit_for(key), "text_or_date"

    if key in PRICE_KEYS and re.search(r"(?:起价|起)\s*$", raw):
        return None, _unit_for(key), "starting_price"

    cleaned = raw.replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
    cleaned = cleaned.replace("元", "").strip()
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
    if key in DURATION_KEYS:
        return "seconds"
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
    session_data = normalized.startswith("session")
    auth_data = normalized.startswith("auth") and not normalized.startswith("author")
    return normalized in AUTH_SECRET_NAMES or normalized.endswith(AUTH_SECRET_SUFFIXES) or session_data or auth_data


def _retain_collected_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _retain_collected_value(item)
            for key, item in value.items()
            if not _is_private_field(key)
        }
    if isinstance(value, list):
        return [_retain_collected_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _retain_collected_value(value)
        for key, value in fields.items()
        if not _is_private_field(key)
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
                "creator_ref", "creator_key", "platform_creator_id", "creator_id", "blogger_id", "达人ID",
                "博主ID", "创作者ID", "用户ID", "UID", "小红书号", "研究对象编号",
                "博主名称", "达人昵称", "昵称",
            ),
        )
        note_ref = _first_present(row, ("note_ref", "platform_note_id", "note_id", "source_url", "笔记ID", "笔记链接"))
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

        for key in ("creator_name", "platform_creator_id", "platform_note_id", "source_url"):
            if row.get(key) is not None and str(row[key]).strip():
                fields.setdefault(key, str(row[key]).strip())

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
            record_source = str(item.get("source") or source)
            record_captured_at = str(item.get("captured_at") or captured_at)
            for key in ("creator_ref", "creator_key", "creator_id", "note_ref", "note_id"):
                if item.get(key):
                    clean_fields.setdefault(key, str(item[key]))
            record_creator_ref = (
                creator_id_override
                or item.get("creator_ref")
                or item.get("creator_key")
                or item.get("creator_id")
                or payload.get("creator_ref")
                or payload.get("creator_key")
                or f"task:{task_id}:creator:1"
            )
            creator_name = _first_present(
                clean_fields,
                ("creator_name", "nickname", "blogger_name", "达人名称", "达人昵称", "博主名称", "博主名", "创作者名称", "昵称", "账号名称", "作者昵称"),
            )
            platform_creator_id = _first_present(
                clean_fields,
                ("platform_creator_id", "creator_id", "blogger_id", "达人ID", "博主ID", "创作者ID", "用户ID", "UID", "小红书号", "平台公开ID", "公开平台ID", "公开ID"),
            )
            if creator_id_override:
                creator_row = conn.execute(
                    "SELECT 1 FROM creators WHERE creator_id = ?", (creator_id_override,)
                ).fetchone()
                if not creator_row:
                    raise ValueError("所选创作者已不存在，请刷新页面后重试")
                creator_id = creator_id_override
            else:
                creator_id = ensure_creator_ref(
                    conn,
                    str(record_creator_ref),
                    str(item.get("captured_at") or captured_at),
                    creator_name=creator_name,
                    platform_creator_id=platform_creator_id,
                    platform="pgy" if "蒲公英" in record_source else None,
                )
            if creator_id_override and (creator_name or platform_creator_id):
                conn.execute(
                    """UPDATE creators SET
                         creator_name = CASE WHEN ? <> '' THEN ? ELSE creator_name END,
                         platform_creator_id = COALESCE(NULLIF(?, ''), platform_creator_id)
                       WHERE creator_id = ?""",
                    (creator_name, creator_name, platform_creator_id, creator_id),
                )

            # Scope the dedupe key to a creator so identical field values for
            # different people in the same batch are not silently collapsed.
            digest = _record_hash(record_type, {"creator_id": creator_id, **clean_fields})
            note_id = None
            if record_type in {"note_detail", "xhs_public_note"}:
                title = str(clean_fields.get("笔记标题") or clean_fields.get("标题") or "")
                published = str(clean_fields.get("发布时间") or "")
                note_timestamp = record_captured_at
                source_note_ref = item.get("note_ref") or item.get("note_id")
                if source_note_ref:
                    note_id = ensure_note_ref(conn, creator_id, str(source_note_ref), note_timestamp)
                    note_ref = str(source_note_ref)
                    platform_note_id = note_ref.rstrip("/").split("/")[-1].split("?")[0]
                    platform_note_id_hash = hashlib.sha256(note_ref.encode("utf-8")).hexdigest()
                else:
                    # Without a platform ID, keep this note scoped to this record
                    # and batch instead of treating its title as an identity key.
                    note_key = f"no-platform-id:{batch_id}:{digest}"
                    note_id = ensure_note(conn, creator_id, note_key, note_timestamp)
                    note_ref = ""
                    platform_note_id = None
                    platform_note_id_hash = None
                conn.execute(
                    """UPDATE notes SET platform_note_id = COALESCE(?, platform_note_id),
                       platform_note_id_hash = COALESCE(?, platform_note_id_hash),
                       title = COALESCE(?, title), body = COALESCE(?, body),
                       published_at = COALESCE(?, published_at),
                       note_type = COALESCE(?, note_type), source = ?,
                       source_url = COALESCE(?, source_url),
                       topics_json = COALESCE(?, topics_json)
                       WHERE note_id = ?""",
                    (
                        platform_note_id,
                        platform_note_id_hash,
                        title or None,
                        clean_fields.get("笔记正文") or clean_fields.get("正文"),
                        published or None,
                        clean_fields.get("笔记类型") or clean_fields.get("内容类型"),
                        record_source,
                        note_ref if note_ref.startswith(("http://", "https://")) else None,
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
                    (batch_id, record_type, source, captured_at, fields_json,
                     fields_hash, creator_id, note_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    record_type,
                    record_source,
                    record_captured_at,
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
                if key in RAW_ID_KEYS or key in NON_METRIC_FIELD_KEYS or isinstance(value, (dict, list)):
                    continue
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

            declared_statuses = item.get("field_statuses")
            if isinstance(declared_statuses, dict):
                entity_type = "creator" if "creator" in record_type or "blogger" in record_type else (
                    "note" if "note" in record_type else "record"
                )
                entity_ref = str(creator_id or note_id or record_id)
                allowed_statuses = {
                    "captured", "not_displayed", "not_loaded", "access_denied",
                    "parse_failed", "not_attempted", "not_applicable",
                }
                for field_name, field_status in declared_statuses.items():
                    name = str(field_name)
                    status_value = str(field_status)
                    if _is_private_field(name) or status_value not in allowed_statuses:
                        continue
                    raw_value = clean_fields.get(name)
                    metric = conn.execute(
                        "SELECT numeric_value, unit, parse_status FROM metric_values WHERE record_id=? AND metric_key=?",
                        (record_id, name),
                    ).fetchone()
                    normalized = None
                    normalization_status = ""
                    if metric:
                        normalization_status = str(metric["parse_status"] or "")
                        if metric["numeric_value"] is not None:
                            normalized = json.dumps(
                                {"value": metric["numeric_value"], "unit": metric["unit"]},
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                    conn.execute(
                        """INSERT OR IGNORE INTO collection_field_observations
                           (batch_id, record_id, entity_type, entity_ref, record_type,
                            field_name, raw_value, normalized_value_json,
                            normalization_status, source, captured_at, task_id, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            batch_id, record_id, entity_type, entity_ref, record_type,
                            name, None if raw_value is None else str(raw_value), normalized,
                            normalization_status, record_source, record_captured_at,
                            task_id, status_value,
                        ),
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

    backfill_field_observations(db_path, batch_id)
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
    """Append visible comment rows and retain all collected public fields."""
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
        platform_note_id = note_ref.rstrip("/").split("/")[-1].split("?")[0]
        conn.execute(
            """UPDATE notes SET platform_note_id = COALESCE(?, platform_note_id),
               platform_note_id_hash = COALESCE(platform_note_id_hash, ?),
               source_url = COALESCE(?, source_url) WHERE note_id = ?""",
            (
                platform_note_id or None,
                hashlib.sha256(note_ref.encode("utf-8")).hexdigest() if note_ref else None,
                note_ref if note_ref.startswith(("http://", "https://")) else None,
                note_id,
            ),
        )
        local_ids: dict[str, str] = {}
        prepared: list[tuple[dict[str, Any], str, str | None, str, str]] = []
        for item in comments:
            raw_text = item.get("comment_text")
            text = "" if raw_text is None else str(raw_text)
            if not text.strip():
                continue
            source_local_id = item.get("local_comment_id")
            if source_local_id is None or str(source_local_id) == "":
                source_local_id = item.get("comment_id")
            generated_key = item.get("local_comment_key")
            has_source_local_id = source_local_id is not None and str(source_local_id) != ""
            local_id_value = source_local_id if has_source_local_id else generated_key
            local_id = "" if local_id_value is None else str(local_id_value)
            local_id_origin = str(
                item.get("local_comment_id_origin")
                or (
                    "provided_source_field"
                    if has_source_local_id
                    else item.get("local_comment_key_origin") or "generated_key"
                )
            )
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
                local_id_origin = "generated_fallback_key"
            local_hash = hashlib.sha256(
                f"{note_id}\x1f{local_id}".encode("utf-8")
            ).hexdigest()
            local_ids[local_id] = local_hash
            parent_local_id = next(
                (
                    value
                    for value in (
                        item.get("parent_local_comment_id"),
                        item.get("parent_comment_id"),
                        item.get("parent_local_comment_key"),
                    )
                    if value is not None and str(value) != ""
                ),
                None,
            )
            has_parent_local_id = parent_local_id is not None and str(parent_local_id) != ""
            parent_hash = local_ids.get(str(parent_local_id)) if has_parent_local_id else None
            parent_id_origin = str(
                item.get("parent_comment_id_origin")
                or (
                    "provided_source_field"
                    if any(
                        value is not None and str(value) != ""
                        for value in (item.get("parent_comment_id"), item.get("parent_local_comment_id"))
                    )
                    else "generated_key"
                    if item.get("parent_local_comment_key") is not None
                    else "missing"
                )
            )
            prepared.append((item, local_hash, parent_hash, local_id_origin, parent_id_origin))

        for item, local_hash, parent_hash, local_id_origin, parent_id_origin in prepared:
            raw_text = item.get("comment_text")
            text = "" if raw_text is None else str(raw_text)
            level = 2 if int(item.get("comment_level") or 1) == 2 else 1
            clean_text = re.sub(r"\s+", " ", text).strip()
            safe_payload = _clean_fields(item)
            safe_payload["comment_level"] = level
            safe_payload.setdefault("local_comment_id_origin", local_id_origin)
            safe_payload.setdefault("parent_comment_id_origin", parent_id_origin)
            if local_id_origin.startswith("generated_"):
                safe_payload.setdefault("local_comment_key", str(item.get("local_comment_key") or local_id))
            like_raw = _clean_interaction_raw(item.get("like_count_raw"))
            reply_raw = _clean_interaction_raw(item.get("reply_count_raw"))
            like_value = normalize_value("点赞量", like_raw)[0] if like_raw else None
            safe_payload["like_count_raw"] = like_raw
            safe_payload["reply_count_raw"] = reply_raw
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
                    str(reply_raw) if reply_raw is not None else None,
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
    backfill_field_observations(db_path, batch_id)
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
