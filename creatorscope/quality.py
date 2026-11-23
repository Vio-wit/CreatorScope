from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .domain import FIELD_CATALOG

_CORE_METRIC_GROUPS = {
    "粉丝数": "followers",
    "阅读中位数（日常）": "reading", "阅读中位数": "reading",
    "互动中位数（日常）": "interaction", "互动中位数": "interaction",
    "全部报价": "price", "合作报价": "price", "图文报价": "price",
    "视频报价": "price", "图文笔记一口价": "price", "视频笔记一口价": "price",
}


@contextmanager
def _read_only_connection(db_path: Path):
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _elapsed_seconds(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(completed_at)
    except ValueError:
        return None
    return round(max(0.0, (end - start).total_seconds()), 2)


def _coverage_target_key(row: sqlite3.Row) -> str:
    if row["record_note_id"] is not None:
        if row["platform_note_id"]:
            return f"note:{row['platform_note_id']}"
        if row["source_url"]:
            parsed = urlparse(str(row["source_url"]))
            last_segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
            return f"note:{last_segment or row['record_note_id']}"
        return f"note-db:{row['record_note_id']}"
    if row["record_creator_id"] is not None:
        if row["platform_creator_id"]:
            return f"creator:{row['platform_creator_id']}"
        return f"creator-db:{row['record_creator_id']}"

    entity_ref = str(row["entity_ref"] or "")
    parsed = urlparse(entity_ref)
    if parsed.scheme and parsed.netloc:
        last_segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if last_segment:
            return f"note:{last_segment}"
        return f"url:{parsed.netloc}{parsed.path}"
    return f"{row['entity_type']}:{entity_ref}"


def _five_creator_field_grade(target_rows: dict[str, list[sqlite3.Row]]) -> str:
    """Grade only a fully observed sample with five distinct explicit platform IDs."""
    if not target_rows:
        return "not_verified"
    if len(target_rows) != 5:
        return "incomplete"
    identities: set[str] = set()
    successful = 0
    for rows in target_rows.values():
        ids = {str(row["platform_creator_id"]).strip() for row in rows if row["platform_creator_id"]}
        if len(ids) != 1:
            return "incomplete"
        identities.update(ids)
        statuses = {str(row["status"]) for row in rows}
        if statuses <= {"not_attempted", "not_applicable"}:
            return "incomplete"
        def usable(row: sqlite3.Row) -> bool:
            if row["status"] != "captured":
                return False
            spec = FIELD_CATALOG.get(str(row["field_name"]), {})
            numeric = row["metric_unit"] not in (None, "", "text") or spec.get("unit") in {
                "人", "次", "篇", "人民币元", "%", "秒"
            }
            return not numeric or row["normalization_status"] == "parsed"

        if any(usable(row) for row in rows):
            successful += 1
    if len(identities) != 5:
        return "incomplete"
    return "stable" if successful >= 4 else "partial" if successful == 3 else "unstable"


def batch_quality_report(db_path: Path, batch_id: str) -> dict[str, Any] | None:
    """Summarize only saved values and task counters for one collection batch."""
    with _read_only_connection(db_path) as conn:
        batch = conn.execute(
            """SELECT b.*, t.result_json, t.parameters_json, t.task_type, t.status AS task_status
               FROM batches b LEFT JOIN tasks t ON t.task_id = b.task_id
               WHERE b.batch_id = ?""",
            (batch_id,),
        ).fetchone()
        if not batch:
            return None

        record_rows = conn.execute(
            """SELECT record_type, COUNT(*) AS row_count
               FROM records WHERE batch_id = ? GROUP BY record_type ORDER BY record_type""",
            (batch_id,),
        ).fetchall()
        field_rows = conn.execute(
            """SELECT o.record_type, o.field_name, o.status, o.normalized_value_json,
                      o.normalization_status, o.entity_type, o.entity_ref, o.captured_at,
                      r.note_id AS record_note_id, r.creator_id AS record_creator_id,
                      n.platform_note_id, n.source_url, c.platform_creator_id, m.unit AS metric_unit
               FROM collection_field_observations o
               LEFT JOIN records r ON r.record_id = o.record_id
               LEFT JOIN metric_values m ON m.record_id = o.record_id AND m.metric_key = o.field_name
               LEFT JOIN notes n ON n.note_id = r.note_id
               LEFT JOIN creators c ON c.creator_id = r.creator_id
               WHERE o.batch_id = ?
               ORDER BY o.record_type, o.field_name, o.captured_at""",
            (batch_id,),
        ).fetchall()
        metric_counts = conn.execute(
            """SELECT COUNT(*) AS total,
                      COUNT(CASE WHEN raw_value IS NOT NULL AND raw_value <> '' THEN 1 END) AS raw_count,
                      COUNT(CASE WHEN parse_status = 'parsed' THEN 1 END) AS parsed_count
               FROM metric_values m JOIN records r ON r.record_id = m.record_id
               WHERE r.batch_id = ?""",
            (batch_id,),
        ).fetchone()
        comment_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM comment_snapshots WHERE batch_id = ?", (batch_id,)
            ).fetchone()[0]
        )
        creator_count = int(
            conn.execute(
                """SELECT COUNT(DISTINCT creator_id) FROM (
                       SELECT creator_id FROM records WHERE batch_id=? AND creator_id IS NOT NULL
                       UNION
                       SELECT n.creator_id FROM comment_snapshots c JOIN notes n ON n.note_id=c.note_id
                       WHERE c.batch_id=?
                   )""",
                (batch_id, batch_id),
            ).fetchone()[0]
        )
        note_count = int(
            conn.execute(
                """SELECT COUNT(DISTINCT note_id) FROM (
                       SELECT note_id FROM records WHERE batch_id=? AND note_id IS NOT NULL
                       UNION
                       SELECT note_id FROM comment_snapshots WHERE batch_id=?
                   )""",
                (batch_id, batch_id),
            ).fetchone()[0]
        )
        observation_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM collection_field_observations WHERE batch_id=?",
                (batch_id,),
            ).fetchone()[0]
        )
        creator_metrics = conn.execute(
            """SELECT r.creator_id, c.platform_creator_id, r.source, r.captured_at,
                      m.metric_key, m.raw_value, m.parse_status,
                      EXISTS(SELECT 1 FROM collection_field_observations o
                             JOIN records p ON p.record_id=o.record_id
                             WHERE p.batch_id=r.batch_id AND p.creator_id=r.creator_id
                               AND p.record_type='creator_profile_sample'
                               AND o.field_name='平台公开ID' AND o.status='captured'
                               AND o.raw_value=c.platform_creator_id) AS identity_observed
               FROM records r JOIN creators c ON c.creator_id=r.creator_id
               JOIN metric_values m ON m.record_id=r.record_id
               WHERE r.batch_id=? AND r.note_id IS NULL""", (batch_id,),
        ).fetchall()

    try:
        task_result = json.loads(batch["result_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        task_result = {}

    try:
        parameters = json.loads(batch["parameters_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        parameters = {}
    settings = parameters.get("settings") if isinstance(parameters, dict) else {}
    five_creator_task = (batch["task_type"] == "visible_collection" and isinstance(settings, dict)
                         and settings.get("creator_count") == 5)

    creator_acceptance = None
    if five_creator_task:
        by_identity: dict[str, dict[str, Any]] = {}
        for row in creator_metrics:
            identity = str(row["platform_creator_id"] or "").strip()
            group = _CORE_METRIC_GROUPS.get(str(row["metric_key"]))
            if not identity or not row["identity_observed"] or not row["source"] or not row["captured_at"]:
                continue
            entry = by_identity.setdefault(identity, {"creator_ids": set(), "groups": set()})
            entry["creator_ids"].add(str(row["creator_id"]))
            if group and row["raw_value"] not in (None, "") and row["parse_status"] == "parsed":
                entry["groups"].add(group)
        success = sum(len(item["creator_ids"]) == 1 and len(item["groups"]) >= 2 for item in by_identity.values())
        creator_acceptance = {
            "target": 5, "success": success, "minimum_success": ceil(5 * 0.7),
            "success_pct": round(success * 100 / 5, 1),
            "passed": success >= ceil(5 * 0.7) if batch["status"] == "completed" and success <= 5 else None,
            "note": "只统计本批明确观察到平台 ID、来源和时间齐全且至少两类核心指标成功解析的唯一达人；身份冲突不计。",
        }

    records_by_type = {str(row["record_type"]): int(row["row_count"]) for row in record_rows}
    grouped_fields: dict[tuple[str, str], dict[str, Any]] = {}
    targets_by_type: dict[str, set[str]] = {}
    targets_by_field: dict[tuple[str, str], dict[str, list[sqlite3.Row]]] = {}
    for row in field_rows:
        record_type = str(row["record_type"])
        field_name = str(row["field_name"])
        target_key = _coverage_target_key(row)
        targets_by_type.setdefault(record_type, set()).add(target_key)
        targets_by_field.setdefault((record_type, field_name), {}).setdefault(target_key, []).append(row)
        item = grouped_fields.setdefault(
            (record_type, field_name),
            {"record_type": record_type, "field_name": field_name, "states": {}, "normalized_count": 0, "parse_success_count": 0},
        )
        item["normalized_count"] += int(row["normalized_value_json"] is not None)
        item["parse_success_count"] += int(row["normalization_status"] == "parsed")

    field_coverage = []
    for item in grouped_fields.values():
        key = (item["record_type"], item["field_name"])
        for target_rows in targets_by_field[key].values():
            statuses = [str(row["status"]) for row in target_rows]
            if "captured" in statuses:
                effective_status = "captured"
            else:
                latest = max(target_rows, key=lambda row: str(row["captured_at"] or ""))
                effective_status = str(latest["status"])
            item["states"][effective_status] = int(item["states"].get(effective_status, 0)) + 1
        if item["record_type"] == "comment_snapshot":
            denominator = comment_count
        else:
            denominator = len(targets_by_type.get(item["record_type"], set()))
        captured = int(item["states"].get("captured", 0))
        item["expected_records"] = denominator
        item["captured_records"] = captured
        item["observed_records"] = len(targets_by_field[key])
        item["unobserved_records"] = max(0, denominator - item["observed_records"])
        item["five_creator_grade"] = (
            _five_creator_field_grade(targets_by_field[key])
            if five_creator_task and item["record_type"] in {
                "blogger_list_sample", "creator_profile_sample", "creator_content_performance", "fan_analysis"
            } else None
        )
        item["coverage_pct"] = round(captured * 100 / denominator, 1) if denominator else None
        field_coverage.append(item)
    field_coverage.sort(key=lambda item: (item["record_type"], item["field_name"]))

    raw_metric_count = int(metric_counts["raw_count"] or 0)
    parsed_metric_count = int(metric_counts["parsed_count"] or 0)
    metrics_total = int(metric_counts["total"] or 0)
    return {
        "batch_id": batch_id,
        "source": str(batch["source"] or ""),
        "status": str(batch["status"] or ""),
        "task_status": str(batch["task_status"] or ""),
        "creator_acceptance": creator_acceptance,
        "started_at": batch["started_at"],
        "completed_at": batch["completed_at"],
        "elapsed_seconds": _elapsed_seconds(batch["started_at"], batch["completed_at"]),
        "counts": {
            "records": sum(records_by_type.values()),
            "records_by_type": records_by_type,
            "creators": creator_count,
            "notes": note_count,
            "comments": comment_count,
            "field_observations": observation_count,
            "duplicate_records": int(task_result.get("duplicate_records", task_result.get("duplicate_count", 0)) or 0),
            "duplicate_comments": int(task_result.get("duplicate_comments", 0) or 0),
            "failures": int(batch["error_count"] or 0),
        },
        "metrics": {
            "stored_values": metrics_total,
            "raw_values": raw_metric_count,
            "parsed_values": parsed_metric_count,
            "parse_success_pct": round(parsed_metric_count * 100 / raw_metric_count, 1) if raw_metric_count else None,
        },
        "field_coverage": field_coverage,
        "failure_records": task_result.get("failures", [])[:100],
        "notes": [
            "覆盖率按对应采集对象去重；同一对象后来成功捕获时，成功状态优先于此前失败状态。",
            "没有明确字段观察状态的记录不会被推定为页面未展示。",
            "comments 计数只包括本批次实际保存的可见评论，不代表全量评论。",
            "五人字段分级仅限目标为五人的采集任务，且五个对象均有不同的明确平台 ID 和字段观察；仅表示本次小样本取得情况。",
        ],
    }


def coverage_by_field(db_path: Path) -> list[dict[str, Any]]:
    """跨批次汇总字段观察状态。

    分母是"该字段实际产生过状态观察的记录数"，不同批次的对象数不同，
    所以这里只给观测占比，不合并成单一覆盖率。
    """

    with _read_only_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT record_type, field_name,
                      COUNT(*) AS observed,
                      SUM(CASE WHEN status = 'captured' THEN 1 ELSE 0 END) AS captured
               FROM collection_field_observations
               GROUP BY record_type, field_name
               ORDER BY record_type, field_name"""
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        observed = int(row["observed"] or 0)
        captured = int(row["captured"] or 0)
        result.append({
            "record_type": str(row["record_type"] or ""),
            "field_name": str(row["field_name"] or ""),
            "observed": observed,
            "captured": captured,
            "share_pct": round(captured * 100 / observed, 1) if observed else None,
        })
    return result


def quality_overview(db_path: Path, *, limit: int = 20) -> dict[str, Any]:
    """批次级质量摘要，供本地质量看板列表使用。

    逐批调用 batch_quality_report 以保持与批次详情完全一致的口径；
    因此按最新批次倒序取前 limit 个批次（默认 20）。
    """

    limit = max(1, min(int(limit), 200))
    with _read_only_connection(db_path) as conn:
        batch_ids = [
            str(row["batch_id"])
            for row in conn.execute(
                """SELECT batch_id FROM batches
                   ORDER BY COALESCE(completed_at, started_at, captured_at) DESC, batch_id
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        ]
    batches: list[dict[str, Any]] = []
    for batch_id in batch_ids:
        report = batch_quality_report(db_path, batch_id)
        if not report:
            continue
        coverage = report.get("field_coverage") or []
        captured = sum(int(item.get("captured_records") or 0) for item in coverage)
        expected = sum(int(item.get("expected_records") or 0) for item in coverage)
        counts = report.get("counts") or {}
        metrics = report.get("metrics") or {}
        batches.append({
            "batch_id": report.get("batch_id"),
            "source": report.get("source"),
            "status": report.get("status"),
            "task_status": report.get("task_status"),
            "started_at": report.get("started_at"),
            "completed_at": report.get("completed_at"),
            "elapsed_seconds": report.get("elapsed_seconds"),
            "counts": {
                "records": counts.get("records", 0),
                "creators": counts.get("creators", 0),
                "notes": counts.get("notes", 0),
                "comments": counts.get("comments", 0),
                "duplicates": counts.get("duplicate_records", 0),
                "failures": counts.get("failures", 0),
            },
            "field_count": len(coverage),
            "captured_observations": captured,
            "expected_observations": expected,
            "coverage_pct": round(captured * 100 / expected, 1) if expected else None,
            "parse_success_pct": metrics.get("parse_success_pct"),
            "partial_fields": sum(
                1 for item in coverage
                if item.get("coverage_pct") is not None and item.get("coverage_pct") < 100
            ),
        })

    totals = {
        "batches": len(batches),
        "records": sum(item["counts"]["records"] for item in batches),
        "creators": sum(item["counts"]["creators"] for item in batches),
        "notes": sum(item["counts"]["notes"] for item in batches),
        "comments": sum(item["counts"]["comments"] for item in batches),
        "failures": sum(item["counts"]["failures"] for item in batches),
        "fields": sum(item["field_count"] for item in batches),
        "partial_fields": sum(item["partial_fields"] for item in batches),
    }
    captured_total = sum(item["captured_observations"] for item in batches)
    expected_total = sum(item["expected_observations"] for item in batches)
    totals["coverage_pct"] = round(captured_total * 100 / expected_total, 1) if expected_total else None
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "limit": limit,
        "batches": batches,
        "totals": totals,
        "notes": [
            "批次按完成时间倒序，最多列出最近若干个批次；覆盖率口径与批次详情完全一致。",
            "覆盖率按采集对象去重统计，同一对象后来成功捕获时以成功状态为准。",
            "缺失不等于平台未提供：没有明确字段观察状态的记录不会被推定。",
        ],
    }
