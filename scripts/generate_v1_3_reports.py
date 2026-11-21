from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from creatorscope.db import verify_database_readonly
from creatorscope.quality import batch_quality_report


@contextmanager
def _readonly(path: Path):
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _raw_payload_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with _readonly(path) as conn:
        queries = (
            ("records", "SELECT record_id, fields_json FROM records ORDER BY record_id"),
            ("metrics", "SELECT record_id, metric_key, raw_value FROM metric_values ORDER BY record_id, metric_key"),
            ("comments", "SELECT id, comment_text, displayed_time, like_count_raw, reply_count_raw, raw_payload_json FROM comment_snapshots ORDER BY id"),
        )
        for name, query in queries:
            digest.update(name.encode("utf-8"))
            for row in conn.execute(query):
                digest.update(json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                digest.update(b"\n")
    return digest.hexdigest()


def _load_batches(path: Path) -> list[dict[str, Any]]:
    with _readonly(path) as conn:
        rows = conn.execute(
            "SELECT batch_id, source, captured_at, status, error_count FROM batches ORDER BY captured_at, rowid"
        ).fetchall()
    reports = []
    for row in rows:
        report = batch_quality_report(path, str(row["batch_id"]))
        if report:
            reports.append(report)
    return reports


def _field_coverage(batch_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    for batch in batch_reports:
        for field in batch["field_coverage"]:
            key = (field["record_type"], field["field_name"])
            row = aggregate.setdefault(
                key,
                {
                    "record_type": key[0],
                    "field_name": key[1],
                    "expected_records": 0,
                    "captured_records": 0,
                    "normalized_values": 0,
                    "parse_success_count": 0,
                    "state_counts": Counter(),
                    "sources": set(),
                    "batches": set(),
                },
            )
            row["expected_records"] += int(field["expected_records"] or 0)
            row["captured_records"] += int(field["captured_records"] or 0)
            row["normalized_values"] += int(field["normalized_count"] or 0)
            row["parse_success_count"] += int(field["parse_success_count"] or 0)
            row["state_counts"].update(field["states"])
            row["sources"].add(batch["source"])
            row["batches"].add(batch["batch_id"])
    results = []
    for row in aggregate.values():
        expected = row["expected_records"]
        results.append(
            {
                **{key: value for key, value in row.items() if key not in {"state_counts", "sources", "batches"}},
                "coverage_pct": round(row["captured_records"] * 100 / expected, 1) if expected else None,
                "state_counts": dict(sorted(row["state_counts"].items())),
                "sources": sorted(row["sources"]),
                "batches": sorted(row["batches"]),
            }
        )
    return sorted(results, key=lambda item: (item["record_type"], item["field_name"]))


def _collection_runs(path: Path, batch_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reports_by_batch = {str(item["batch_id"]): item for item in batch_reports}
    with _readonly(path) as conn:
        rows = conn.execute(
            """SELECT t.task_id, t.task_type, t.source, t.status, t.progress, t.message,
                      t.created_at, t.started_at, t.finished_at, t.batch_id, t.result_json,
                      b.source AS batch_source, b.error_count
               FROM tasks t LEFT JOIN batches b ON b.batch_id=t.batch_id
               WHERE t.task_type IN ('visible_collection', 'pgy_visible_capture')
               ORDER BY t.created_at, t.rowid"""
        ).fetchall()
    runs: list[dict[str, Any]] = []
    for row in rows:
        try:
            result = json.loads(row["result_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            result = {}
        quality = reports_by_batch.get(str(row["batch_id"] or ""), {})
        counts = quality.get("counts", {})
        runs.append(
            {
                "scope": "existing_application_task",
                "task_id": row["task_id"],
                "task_type": row["task_type"],
                "status": row["status"],
                "source": row["batch_source"] or row["source"],
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "batch_id": row["batch_id"],
                "counts": {
                    "records": int(counts.get("records", 0)),
                    "creators_visited": int(result.get("creators_visited", 0) or 0),
                    "notes_visited": int(result.get("notes_visited", 0) or 0),
                    "comments": int(counts.get("comments", 0)),
                    "failures": int(row["error_count"] or 0),
                    "duplicates": int(result.get("duplicate_records", result.get("duplicate_count", 0)) or 0),
                },
                "failure_records": result.get("failures", [])[:100],
            }
        )
    runs.append(
        {
            "scope": "V1.3_live_acceptance",
            "requested_rounds": 3,
            "completed_rounds": 0,
            "status": "BLOCKED",
            "execution": "NOT RUN",
            "blocked_reason": "本轮启动检查时 CreatorScope 浏览器关闭，蒲公英页面处于 needLogout 路由；未在无登录状态下发起采集，因此三轮连续真实采集未执行。",
            "synthetic_substitute_used": False,
        }
    )
    return runs


def generate(database: Path, output_dir: Path) -> dict[str, str]:
    verification = verify_database_readonly(database)
    batch_reports = _load_batches(database)
    with _readonly(database) as conn:
        table_counts = {
            table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in ("tasks", "batches", "creators", "notes", "records", "metric_values", "comment_snapshots")
        }
    try:
        dataset_label = database.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        dataset_label = database.name
    field_report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": dataset_label,
        "measurement": "每种记录类型按本批次已保存的对应记录数计算；仅有实际观测或明确采集状态的字段计入。",
        "field_coverage": _field_coverage(batch_reports),
        "batches": [
            {
                "batch_id": item["batch_id"],
                "source": item["source"],
                "status": item["status"],
                "field_coverage": item["field_coverage"],
            }
            for item in batch_reports
        ],
    }

    state_counts: Counter[str] = Counter()
    total_metrics = raw_metrics = parsed_metrics = 0
    total_records = total_comments = total_failures = total_duplicates = 0
    total_field_observation_rows = 0
    batch_statuses: Counter[str] = Counter()
    for item in batch_reports:
        state_counts.update(
            state for field in item["field_coverage"] for state, count in field["states"].items() for _ in range(int(count))
        )
        total_metrics += int(item["metrics"]["stored_values"])
        raw_metrics += int(item["metrics"]["raw_values"])
        parsed_metrics += int(item["metrics"]["parsed_values"])
        total_records += int(item["counts"]["records"])
        total_comments += int(item["counts"]["comments"])
        total_field_observation_rows += int(item["counts"]["field_observations"])
        total_failures += int(item["counts"]["failures"])
        total_duplicates += int(item["counts"]["duplicate_records"]) + int(item["counts"]["duplicate_comments"])
        batch_statuses.update([item["status"]])
    data_quality = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": dataset_label,
        "integrity": verification,
        "row_counts": table_counts,
        "raw_public_payload_sha256": _raw_payload_digest(database),
        "field_states": dict(sorted(state_counts.items())),
        "metrics": {
            "stored_values": total_metrics,
            "raw_values": raw_metrics,
            "parsed_values": parsed_metrics,
            "parse_success_pct": round(parsed_metrics * 100 / raw_metrics, 1) if raw_metrics else None,
        },
        "batch_statuses": dict(sorted(batch_statuses.items())),
        "totals_from_batch_reports": {
            "records": total_records,
            "comments": total_comments,
            "field_observation_rows": total_field_observation_rows,
            "failure_attempts": total_failures,
            "duplicate_rows": total_duplicates,
        },
        "batch_reports": batch_reports,
        "limits": [
            "field_states 按批次、对象和字段去重统计；原始字段观察行数另列，重复尝试可能使原始行数更高。",
            "not_displayed 表示解析器明确检查该字段但页面未提供非空值。",
            "旧字段未建采集状态档案时，缺失值不被推断成 0 或页面未展示。",
            "评论数仅是实际保存的可见评论，不表示平台全量评论。",
        ],
    }
    run_rows = _collection_runs(database, batch_reports)

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "field_coverage": output_dir / "V1.3_FIELD_COVERAGE.json",
        "data_quality": output_dir / "V1.3_DATA_QUALITY.json",
        "collection_runs": output_dir / "V1.3_COLLECTION_RUNS.jsonl",
    }
    paths["field_coverage"].write_text(json.dumps(field_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["data_quality"].write_text(json.dumps(data_quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["collection_runs"].write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in run_rows),
        encoding="utf-8",
    )
    return {name: str(path) for name, path in paths.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate V1.3 field coverage, data quality, and collection-run artifacts.")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "demo" / "creatorscope_demo.sqlite3")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "docs")
    args = parser.parse_args()
    result = generate(args.database, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
