from __future__ import annotations

import hashlib
import json
import argparse
import sqlite3
from pathlib import Path
from typing import Any

from .db import connection, ensure_creator, ensure_note, initialize_database, local_timestamp


LEGACY_TABLES = (
    ("tasks", "task_id"),
    ("creators", "creator_id"),
    ("batches", "batch_id"),
    ("notes", "note_id"),
    ("records", "record_id"),
    ("metric_values", "metric_id"),
)


def _read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    conn = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _logical_hash(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for statement in conn.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(bytes((10,)))
    return digest.hexdigest()


def backup_database(source_db: Path, backup_db: Path) -> dict[str, str]:
    source_db = Path(source_db)
    backup_db = Path(backup_db)
    if not source_db.is_file():
        return {"status": "source_missing", "backup": ""}
    backup_db.parent.mkdir(parents=True, exist_ok=True)
    source = _read_only(source_db)
    try:
        source_hash = _logical_hash(source)
        if backup_db.exists():
            backup = _read_only(backup_db)
            try:
                backup_hash = _logical_hash(backup)
            finally:
                backup.close()
            if source_hash != backup_hash:
                raise FileExistsError(
                    f"Existing backup differs from source; refusing to overwrite: {backup_db}"
                )
            return {"status": "already_verified", "backup": str(backup_db)}

        target = sqlite3.connect(str(backup_db), timeout=20)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
    finally:
        source.close()

    source = _read_only(source_db)
    backup = _read_only(backup_db)
    try:
        source_hash = _logical_hash(source)
        backup_hash = _logical_hash(backup)
    finally:
        source.close()
        backup.close()
    if source_hash != backup_hash:
        raise sqlite3.DatabaseError("Database backup does not match source content")
    return {"status": "created_and_verified", "backup": str(backup_db)}


def _copy_legacy_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    primary_key: str,
) -> int:
    source_exists = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not source_exists:
        return 0

    source_columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
    target_columns = {row[1] for row in target.execute(f'PRAGMA table_info("{table}")')}
    columns = [name for name in source_columns if name in target_columns]
    if primary_key not in columns:
        raise ValueError(f"Legacy {table} table is missing {primary_key}")

    inserted = 0
    select_columns = ", ".join(f'"{name}"' for name in columns)
    for row in source.execute(f'SELECT * FROM "{table}"'):
        values = {name: row[name] for name in columns}
        existing = target.execute(
            f'SELECT {select_columns} FROM "{table}" '
            f'WHERE "{primary_key}" = ?',
            (values[primary_key],),
        ).fetchone()
        if existing:
            if any(existing[name] != values[name] for name in columns):
                raise ValueError(
                    f"Conflicting legacy {table} row for {primary_key}; migration stopped"
                )
            continue
        names_sql = ", ".join(f'"{name}"' for name in columns)
        marks = ", ".join("?" for _ in columns)
        target.execute(
            f'INSERT INTO "{table}" ({names_sql}) VALUES ({marks})',
            [values[name] for name in columns],
        )
        inserted += 1
    return inserted


def migrate_legacy_database(source_db: Path, destination_db: Path) -> dict[str, int]:
    source_db = Path(source_db)
    destination_db = Path(destination_db)
    if source_db.resolve() == destination_db.resolve():
        raise ValueError("Source and destination databases must be different files")
    if not source_db.is_file():
        return {}

    initialize_database(destination_db)
    source = _read_only(source_db)
    inserted: dict[str, int] = {}
    try:
        with connection(destination_db) as target:
            for table, primary_key in LEGACY_TABLES:
                inserted[table] = _copy_legacy_table(
                    source, target, table, primary_key
                )
    finally:
        source.close()

    initialize_database(destination_db)
    return inserted


def _stable_task_id(batch_id: str) -> str:
    return f"import-r42-{_hash(batch_id)[:20]}"


def import_r42_comment_source(
    db_path: Path,
    source_dir: Path,
) -> dict[str, int | str]:
    """Append the authoritative R4.2 source batch to the unified database."""
    from research.r4_3_comment_analysis.import_r42_comments import (
        clean_text,
        load_source,
        local_creator_id,
        validate_source,
    )

    rows, note_metadata, batch_info = load_source(Path(source_dir))
    validate_source(rows, note_metadata, batch_info)
    batch_id = batch_info["batch_id"]
    task_id = _stable_task_id(batch_id)
    started_at = batch_info["started_at"]
    completed_at = batch_info["completed_at"]
    now = local_timestamp()
    note_map: dict[str, str] = {}
    creator_map: dict[str, str] = {}
    inserted = 0
    duplicate_skips = 0

    initialize_database(Path(db_path))
    with connection(Path(db_path)) as conn:
        task_parameters = json.dumps(
            {"source_artifact": "research/r4_2_comment_validation/raw_comments.jsonl"},
            ensure_ascii=False,
            sort_keys=True,
        )
        conn.execute(
            """INSERT OR IGNORE INTO tasks
               (task_id, task_type, source, status, progress, message,
                parameters_json, created_at, started_at, finished_at, batch_id)
               VALUES (?, 'historical_import', 'xiaohongshu', 'completed', 100,
                       'Imported R4.2 visible comment batch', ?, ?, ?, ?, ?)""",
            (task_id, task_parameters, now, started_at, completed_at, batch_id),
        )
        conn.execute(
            """INSERT OR IGNORE INTO batches
               (batch_id, task_id, source, captured_at, started_at, completed_at,
                status, filters_json, record_count, error_count)
               VALUES (?, ?, 'xiaohongshu', ?, ?, ?, 'completed', ?, ?, 0)""",
            (
                batch_id,
                task_id,
                started_at,
                started_at,
                completed_at,
                json.dumps(
                    {
                        "source_artifact": "research/r4_2_comment_validation/raw_comments.jsonl",
                        "capture_mode": "visible browser",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                len(rows),
            ),
        )
        saved_batch = conn.execute(
            "SELECT task_id, source, status FROM batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if not saved_batch or saved_batch["task_id"] != task_id or saved_batch["source"] != "xiaohongshu":
            raise ValueError("R4.2 batch ID conflicts with a different unified batch")

        for metadata in note_metadata.values():
            creator_name = metadata["creator_name"]
            if creator_name in creator_map:
                continue
            captured = started_at or now
            creator_ref = local_creator_id(creator_name)
            creator_id = ensure_creator(conn, f"xiaohongshu:{creator_ref}", captured)
            conn.execute(
                "UPDATE creators SET platform = 'xiaohongshu' WHERE creator_id = ?",
                (creator_id,),
            )
            creator_map[creator_name] = creator_id

        for platform_note_id, metadata in note_metadata.items():
            creator_id = creator_map[metadata["creator_name"]]
            note_id = ensure_note(
                conn,
                creator_id,
                f"xiaohongshu-note:{platform_note_id}",
                started_at or now,
            )
            conn.execute(
                """UPDATE notes
                   SET platform_note_id_hash = ?, title = ?, source = 'xiaohongshu'
                   WHERE note_id = ?""",
                (_hash(platform_note_id), metadata["title"], note_id),
            )
            note_map[platform_note_id] = note_id

        for row in sorted(
            rows,
            key=lambda item: (
                int(item["comment_level"]), item["note_id"], item["local_comment_id"]
            ),
        ):
            note_id = note_map[row["note_id"]]
            local_id_hash = _hash(str(row["local_comment_id"]))
            parent_id = row.get("parent_comment_id")
            parent_id_hash = _hash(str(parent_id)) if parent_id else None
            raw_payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
            values = (
                batch_id,
                note_id,
                local_id_hash,
                parent_id_hash,
                str(row["comment_text"]),
                clean_text(str(row["comment_text"])),
                row.get("displayed_time"),
                row.get("like_count_raw"),
                row.get("like_count_normalized"),
                row.get("reply_count_raw"),
                int(row["comment_level"]),
                None if row.get("is_pinned") is None else int(bool(row["is_pinned"])),
                row.get("source") or "xiaohongshu",
                row["capture_time"],
                raw_payload,
            )
            cursor = conn.execute(
                """INSERT OR IGNORE INTO comment_snapshots
                   (batch_id, note_id, local_comment_id_hash, parent_comment_id_hash,
                    comment_text, cleaned_text, displayed_time, like_count_raw,
                    like_count, reply_count_raw, comment_level, is_pinned,
                    source, captured_at, raw_payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            if cursor.rowcount:
                inserted += 1
            else:
                duplicate_skips += 1
                existing = conn.execute(
                    """SELECT raw_payload_json FROM comment_snapshots
                       WHERE batch_id = ? AND note_id = ? AND local_comment_id_hash = ?""",
                    (batch_id, note_id, local_id_hash),
                ).fetchone()
                if not existing or existing[0] != raw_payload:
                    raise ValueError(
                        "Same-batch comment key contains different source data; import stopped"
                    )

        total = conn.execute(
            "SELECT COUNT(*) FROM comment_snapshots WHERE batch_id = ?", (batch_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE batches SET record_count = ?, status = 'completed' WHERE batch_id = ?",
            (total, batch_id),
        )
        conn.execute(
            "UPDATE tasks SET result_json = ?, progress = 100 WHERE task_id = ?",
            (
                json.dumps(
                    {"inserted": inserted, "duplicate_skips": duplicate_skips, "total": total},
                    sort_keys=True,
                ),
                task_id,
            ),
        )
        roots = conn.execute(
            "SELECT COUNT(*) FROM comment_snapshots WHERE batch_id = ? AND comment_level = 1",
            (batch_id,),
        ).fetchone()[0]
        replies = conn.execute(
            "SELECT COUNT(*) FROM comment_snapshots WHERE batch_id = ? AND comment_level = 2",
            (batch_id,),
        ).fetchone()[0]
        if (total, roots, replies) != (29, 28, 1):
            raise ValueError(
                f"R4.2 source count mismatch after import: total={total}, roots={roots}, replies={replies}"
            )

    return {
        "batch_id": batch_id,
        "inserted": inserted,
        "duplicate_skips": duplicate_skips,
        "total_records": total,
        "root_records": roots,
        "reply_records": replies,
    }


def verify_database(db_path: Path) -> dict[str, Any]:
    conn = _read_only(Path(db_path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        counts = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "tasks", "batches", "creators", "notes", "records",
                "metric_values", "comment_snapshots",
            )
        }
        return {
            "integrity": integrity,
            "foreign_key_errors": len(foreign_keys),
            "counts": counts,
        }
    finally:
        conn.close()


def migrate_unified_database(
    source_db: Path,
    destination_db: Path,
    comment_source_dir: Path,
) -> dict[str, Any]:
    source_db = Path(source_db)
    destination_db = Path(destination_db)
    if source_db.resolve() == destination_db.resolve():
        raise ValueError("Unified database must use a new file; source will remain unchanged")
    copied = migrate_legacy_database(source_db, destination_db)
    comments = import_r42_comment_source(destination_db, comment_source_dir)
    verification = verify_database(destination_db)
    if verification["integrity"] != "ok" or verification["foreign_key_errors"]:
        raise sqlite3.IntegrityError("Unified database integrity or foreign-key validation failed")
    return {"legacy_rows_inserted": copied, "comments": comments, "verification": verification}


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Create or idempotently update the local unified CreatorScope database."
    )
    parser.add_argument(
        "--source-db", type=Path, default=project_root / "instance" / "creatorscope.sqlite3"
    )
    parser.add_argument(
        "--destination-db",
        type=Path,
        default=project_root / "instance" / "creatorscope_unified.sqlite3",
    )
    parser.add_argument(
        "--comment-source-dir",
        type=Path,
        default=project_root / "research" / "r4_2_comment_validation",
    )
    parser.add_argument(
        "--backup-db",
        type=Path,
        default=project_root / "_backup" / "creatorscope_main_pre_unified_20261121.sqlite3",
    )
    args = parser.parse_args()
    backup = backup_database(args.source_db, args.backup_db)
    result = migrate_unified_database(
        args.source_db, args.destination_db, args.comment_source_dir
    )
    print(json.dumps({"backup": backup, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
