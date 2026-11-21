from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    parameters_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    batch_id TEXT,
    error TEXT,
    error_message TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    filters_json TEXT NOT NULL DEFAULT '{}',
    record_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS creators (
    creator_id TEXT PRIMARY KEY,
    pseudonym TEXT NOT NULL UNIQUE,
    creator_name TEXT NOT NULL DEFAULT '',
    platform TEXT,
    platform_creator_id TEXT,
    platform_creator_id_hash TEXT,
    category TEXT,
    location TEXT,
    follower_count REAL,
    introduction TEXT,
    source_key_hash TEXT NOT NULL UNIQUE,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    note_id TEXT PRIMARY KEY,
    creator_id TEXT NOT NULL,
    note_key_hash TEXT NOT NULL UNIQUE,
    platform_note_id TEXT,
    platform_note_id_hash TEXT,
    title TEXT,
    body TEXT,
    topics_json TEXT,
    published_at TEXT,
    note_type TEXT,
    source_url TEXT,
    source TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    FOREIGN KEY(creator_id) REFERENCES creators(creator_id)
);

CREATE TABLE IF NOT EXISTS records (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    source TEXT,
    captured_at TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    fields_hash TEXT NOT NULL,
    creator_id TEXT,
    note_id TEXT,
    FOREIGN KEY(batch_id) REFERENCES batches(batch_id),
    FOREIGN KEY(creator_id) REFERENCES creators(creator_id),
    FOREIGN KEY(note_id) REFERENCES notes(note_id),
    UNIQUE(batch_id, record_type, fields_hash)
);

CREATE TABLE IF NOT EXISTS metric_values (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL,
    metric_key TEXT NOT NULL,
    raw_value TEXT,
    numeric_value REAL,
    unit TEXT NOT NULL DEFAULT '',
    period TEXT NOT NULL DEFAULT '',
    parse_status TEXT NOT NULL,
    FOREIGN KEY(record_id) REFERENCES records(record_id) ON DELETE CASCADE,
    UNIQUE(record_id, metric_key)
);

CREATE TABLE IF NOT EXISTS comment_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    local_comment_id_hash TEXT NOT NULL,
    parent_comment_id_hash TEXT,
    comment_text TEXT NOT NULL,
    cleaned_text TEXT NOT NULL,
    displayed_time TEXT,
    like_count_raw TEXT,
    like_count INTEGER,
    reply_count_raw TEXT,
    comment_level INTEGER NOT NULL CHECK (comment_level IN (1, 2)),
    is_pinned INTEGER CHECK (is_pinned IN (0, 1) OR is_pinned IS NULL),
    source TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    UNIQUE(batch_id, note_id, local_comment_id_hash),
    FOREIGN KEY(batch_id) REFERENCES batches(batch_id),
    FOREIGN KEY(note_id) REFERENCES notes(note_id),
    FOREIGN KEY(batch_id, note_id, parent_comment_id_hash)
      REFERENCES comment_snapshots(batch_id, note_id, local_comment_id_hash)
      DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS collection_field_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    record_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_ref TEXT NOT NULL DEFAULT '',
    record_type TEXT NOT NULL DEFAULT '',
    field_name TEXT NOT NULL,
    raw_value TEXT,
    normalized_value_json TEXT,
    normalization_status TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL,
    task_id TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'captured', 'not_displayed', 'not_loaded', 'access_denied',
        'parse_failed', 'not_attempted', 'not_applicable'
    )),
    FOREIGN KEY(batch_id) REFERENCES batches(batch_id),
    FOREIGN KEY(record_id) REFERENCES records(record_id) ON DELETE CASCADE,
    UNIQUE(record_id, field_name)
);

CREATE INDEX IF NOT EXISTS idx_records_batch ON records(batch_id, record_type);
CREATE INDEX IF NOT EXISTS idx_metrics_key ON metric_values(metric_key);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_creator ON notes(creator_id);
CREATE INDEX IF NOT EXISTS idx_records_creator_batch ON records(creator_id, batch_id);
CREATE INDEX IF NOT EXISTS idx_records_note ON records(note_id);
CREATE INDEX IF NOT EXISTS idx_comment_snapshots_batch_note
    ON comment_snapshots(batch_id, note_id, comment_level);
CREATE INDEX IF NOT EXISTS idx_comment_snapshots_parent
    ON comment_snapshots(batch_id, note_id, parent_comment_id_hash);
CREATE INDEX IF NOT EXISTS idx_field_observations_batch
    ON collection_field_observations(batch_id, entity_type, record_type, field_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_field_observations_comment_unique
    ON collection_field_observations(batch_id, entity_type, entity_ref, record_type, field_name)
    WHERE record_id IS NULL;
"""

VIEWS = """
CREATE VIEW IF NOT EXISTS collection_batches AS
SELECT batch_id, source, started_at, completed_at, status,
       filters_json, record_count, error_count
FROM batches;

CREATE VIEW IF NOT EXISTS collection_tasks AS
SELECT task_id, task_type, status, parameters_json, progress,
       created_at, started_at, finished_at,
       COALESCE(error_message, error) AS error_message
FROM tasks;

CREATE VIEW IF NOT EXISTS metric_snapshots AS
SELECT m.metric_id AS id, b.batch_id, r.creator_id, r.note_id,
       m.metric_key AS metric_name, m.numeric_value AS metric_value,
       m.raw_value, m.unit, COALESCE(r.source, b.source) AS source,
       m.period, r.captured_at
FROM metric_values AS m
JOIN records AS r ON r.record_id = m.record_id
JOIN batches AS b ON b.batch_id = r.batch_id;
"""


@contextmanager
def connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(db_path: Path) -> None:
    with connection(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        _backfill_creator_links(conn)
        _backfill_batch_metadata(conn)
        _backfill_field_observations(conn)
        existing_view = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='view' AND name='metric_snapshots'"
        ).fetchone()
        if existing_view and "r.source" not in str(existing_view[0]):
            conn.execute("DROP VIEW metric_snapshots")
        conn.executescript(VIEWS)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, local_timestamp()),
        )


def schema_version_readonly(db_path: Path) -> int:
    """Read the recorded schema version without creating or modifying a database."""
    path = Path(db_path).resolve()
    if not path.is_file():
        return 0
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=20)
    try:
        has_migrations = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if not has_migrations:
            return 0
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def verify_database_readonly(db_path: Path, required_version: int = SCHEMA_VERSION) -> dict[str, object]:
    """Validate a prepared dataset without opening it for writes or migration."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database is missing: {path}")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=20)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        version_row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        version = int(version_row[0] or 0)
        if integrity != "ok" or foreign_key_errors or version < required_version:
            raise sqlite3.DatabaseError(
                f"Demo database is not ready: integrity={integrity}, foreign_key_errors={len(foreign_key_errors)}, schema_version={version}"
            )
        return {"integrity": integrity, "foreign_key_errors": len(foreign_key_errors), "schema_version": version}
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    additions = {
        "tasks": {
            "parameters_json": "TEXT NOT NULL DEFAULT '{}'",
            "error_message": "TEXT",
        },
        "batches": {
            "started_at": "TEXT",
            "completed_at": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'completed'",
            "filters_json": "TEXT NOT NULL DEFAULT '{}'",
            "error_count": "INTEGER NOT NULL DEFAULT 0",
        },
        "creators": {
            "creator_name": "TEXT NOT NULL DEFAULT ''",
            "platform": "TEXT",
            "platform_creator_id": "TEXT",
            "platform_creator_id_hash": "TEXT",
            "category": "TEXT",
            "location": "TEXT",
            "follower_count": "REAL",
            "introduction": "TEXT",
        },
        "notes": {
            "platform_note_id_hash": "TEXT",
            "platform_note_id": "TEXT",
            "title": "TEXT",
            "body": "TEXT",
            "topics_json": "TEXT",
            "published_at": "TEXT",
            "note_type": "TEXT",
            "source_url": "TEXT",
            "source": "TEXT",
        },
        "records": {
            "source": "TEXT",
        },
    }
    for table, columns in additions.items():
        existing = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')

    record_columns = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
    if "creator_id" not in record_columns:
        conn.execute("ALTER TABLE records ADD COLUMN creator_id TEXT REFERENCES creators(creator_id)")
    if "note_id" not in record_columns:
        conn.execute("ALTER TABLE records ADD COLUMN note_id TEXT REFERENCES notes(note_id)")


def _json_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _backfill_field_observations(conn: sqlite3.Connection, batch_id: str | None = None) -> None:
    """Record field-level provenance for stored raw values without changing them."""
    record_sql = """SELECT r.record_id, r.batch_id, r.record_type, r.source, r.captured_at,
                  r.fields_json, r.creator_id, r.note_id, b.source AS batch_source,
                  b.task_id
           FROM records r JOIN batches b ON b.batch_id = r.batch_id"""
    comment_sql = """SELECT c.batch_id, c.local_comment_id_hash, c.comment_text,
                  c.displayed_time, c.like_count_raw, c.like_count, c.reply_count_raw,
                  c.comment_level, c.is_pinned, c.source, c.captured_at, b.task_id,
                  c.raw_payload_json
           FROM comment_snapshots c JOIN batches b ON b.batch_id = c.batch_id"""
    params: tuple[str, ...] = ()
    if batch_id:
        record_sql += " WHERE r.batch_id = ?"
        comment_sql += " WHERE c.batch_id = ?"
        params = (batch_id,)
    record_rows = conn.execute(record_sql, params).fetchall()
    for row in record_rows:
        try:
            fields = json.loads(row["fields_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(fields, dict):
            continue
        record_type = str(row["record_type"] or "")
        entity_type = "creator" if "creator" in record_type or "blogger" in record_type else (
            "note" if "note" in record_type else "record"
        )
        entity_ref = str(row["creator_id"] or row["note_id"] or row["record_id"])
        for name, value in fields.items():
            metric = conn.execute(
                "SELECT numeric_value, unit, parse_status FROM metric_values WHERE record_id=? AND metric_key=?",
                (row["record_id"], str(name)),
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
            status = "not_displayed" if value is None or (isinstance(value, str) and not value.strip()) else "captured"
            conn.execute(
                """INSERT OR IGNORE INTO collection_field_observations
                   (batch_id, record_id, entity_type, entity_ref, record_type, field_name,
                    raw_value, normalized_value_json, normalization_status, source,
                    captured_at, task_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["batch_id"], row["record_id"], entity_type, entity_ref, record_type,
                    str(name), _json_value(value), normalized, normalization_status,
                    str(row["source"] or row["batch_source"] or ""), row["captured_at"],
                    row["task_id"], status,
                ),
            )

    comment_rows = conn.execute(comment_sql, params).fetchall()
    comment_fields = (
        "comment_text", "displayed_time", "like_count_raw", "reply_count_raw",
        "comment_level", "is_pinned",
    )
    for row in comment_rows:
        try:
            payload = json.loads(row["raw_payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        values = {
            "comment_text": row["comment_text"],
            "displayed_time": row["displayed_time"],
            "like_count_raw": row["like_count_raw"],
            "reply_count_raw": row["reply_count_raw"],
            "comment_level": row["comment_level"],
            "is_pinned": row["is_pinned"],
        }
        for name in comment_fields:
            value = values[name]
            if name == "like_count_raw" and row["like_count"] is not None:
                normalized = json.dumps({"value": row["like_count"], "unit": "count"}, ensure_ascii=False)
                normalization_status = "parsed"
            else:
                normalized = None
                normalization_status = ""
            if value is None and name in payload:
                value = payload[name]
            status = "not_displayed" if value is None or (isinstance(value, str) and not value.strip()) else "captured"
            conn.execute(
                """INSERT OR IGNORE INTO collection_field_observations
                   (batch_id, entity_type, entity_ref, record_type, field_name,
                    raw_value, normalized_value_json, normalization_status, source,
                    captured_at, task_id, status)
                   VALUES (?, 'comment', ?, 'comment_snapshot', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["batch_id"], row["local_comment_id_hash"], name,
                    _json_value(value), normalized, normalization_status,
                    row["source"], row["captured_at"], row["task_id"], status,
                ),
            )


def backfill_field_observations(db_path: Path, batch_id: str | None = None) -> None:
    """Idempotently index stored raw values in the field provenance table."""
    with connection(db_path) as conn:
        _backfill_field_observations(conn, batch_id)


def record_field_statuses(
    db_path: Path,
    *,
    batch_id: str,
    task_id: str,
    entity_type: str,
    entity_ref: str,
    record_type: str,
    field_names: tuple[str, ...] | list[str],
    status: str,
    source: str,
) -> int:
    """Append a status-only observation for attempted fields without raw values."""
    allowed_statuses = {
        "not_displayed", "not_loaded", "access_denied",
        "parse_failed", "not_attempted", "not_applicable",
    }
    if status not in allowed_statuses:
        raise ValueError("无效的字段采集状态")
    captured_at = local_timestamp()
    inserted = 0
    with connection(db_path) as conn:
        batch = conn.execute(
            "SELECT task_id FROM batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if not batch or str(batch["task_id"]) != task_id:
            raise ValueError("字段状态不属于当前采集批次")
        for field_name in field_names:
            name = str(field_name).strip()
            if not name:
                continue
            cursor = conn.execute(
                """UPDATE collection_field_observations
                   SET status=?, source=?, captured_at=?, task_id=?
                   WHERE batch_id=? AND entity_type=? AND entity_ref=?
                     AND record_type=? AND field_name=? AND record_id IS NULL""",
                (
                    status, source, captured_at, task_id, batch_id, entity_type,
                    entity_ref, record_type, name,
                ),
            )
            if cursor.rowcount:
                inserted += max(0, int(cursor.rowcount))
                continue
            cursor = conn.execute(
                """INSERT OR IGNORE INTO collection_field_observations
                   (batch_id, entity_type, entity_ref, record_type, field_name,
                    raw_value, normalized_value_json, normalization_status,
                    source, captured_at, task_id, status)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, '', ?, ?, ?, ?)""",
                (batch_id, entity_type, entity_ref, record_type, name, source, captured_at, task_id, status),
            )
            inserted += max(0, int(cursor.rowcount))
    return inserted


def _backfill_batch_metadata(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE batches SET started_at = captured_at WHERE started_at IS NULL")
    conn.execute("UPDATE batches SET completed_at = captured_at WHERE completed_at IS NULL")
    conn.execute("UPDATE batches SET status = 'completed' WHERE status IS NULL OR status = ''")
    conn.execute("UPDATE tasks SET error_message = error WHERE error_message IS NULL AND error IS NOT NULL")


def _source_key_hash(source_key: str) -> str:
    return hashlib.sha256(source_key.encode("utf-8")).hexdigest()


def _ensure_creator(conn: sqlite3.Connection, source_key: str, captured_at: str) -> str:
    key_hash = _source_key_hash(source_key)
    row = conn.execute(
        "SELECT creator_id FROM creators WHERE source_key_hash = ?", (key_hash,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE creators SET last_seen_at = ? WHERE creator_id = ?",
            (captured_at, row[0]),
        )
        return str(row[0])
    index = int(conn.execute("SELECT COUNT(*) FROM creators").fetchone()[0]) + 1
    creator_id = uuid4().hex
    pseudonym = f"博主 {index:04d}"
    conn.execute(
        """INSERT INTO creators
           (creator_id, pseudonym, creator_name, source_key_hash, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (creator_id, pseudonym, "", key_hash, captured_at, captured_at),
    )
    return creator_id


def ensure_creator(conn: sqlite3.Connection, source_key: str, captured_at: str) -> str:
    return _ensure_creator(conn, source_key, captured_at)


def ensure_creator_ref(
    conn: sqlite3.Connection,
    creator_ref: str,
    captured_at: str,
    *,
    creator_name: str = "",
    platform_creator_id: str | None = None,
    platform: str | None = None,
) -> str:
    """Resolve a stable source reference, then reuse a matching platform identity."""
    ref = str(creator_ref).strip()
    creator_id: str
    if ref:
        row = conn.execute(
            "SELECT creator_id FROM creators WHERE source_key_hash = ?",
            (_source_key_hash(ref),),
        ).fetchone()
        if row:
            creator_id = str(row["creator_id"])
        else:
            row = None
            if platform and platform_creator_id:
                row = conn.execute(
                    """SELECT creator_id FROM creators
                       WHERE platform_creator_id = ? AND (platform = ? OR platform IS NULL)
                       ORDER BY first_seen_at, creator_id LIMIT 1""",
                    (str(platform_creator_id).strip(), platform),
                ).fetchone()
            creator_id = str(row["creator_id"]) if row else _ensure_creator(conn, ref, captured_at)
    else:
        row = None
        if platform and platform_creator_id:
            row = conn.execute(
                """SELECT creator_id FROM creators
                   WHERE platform_creator_id = ? AND (platform = ? OR platform IS NULL)
                   ORDER BY first_seen_at, creator_id LIMIT 1""",
                (str(platform_creator_id).strip(), platform),
            ).fetchone()
        creator_id = str(row["creator_id"]) if row else _ensure_creator(conn, uuid4().hex, captured_at)
    conn.execute(
        """UPDATE creators SET
             creator_name = CASE WHEN ? <> '' THEN ? ELSE creator_name END,
             platform_creator_id = COALESCE(NULLIF(?, ''), platform_creator_id),
             platform = COALESCE(NULLIF(?, ''), platform),
             last_seen_at = ?
           WHERE creator_id = ?""",
        (creator_name, creator_name, platform_creator_id, platform, captured_at, creator_id),
    )
    return creator_id


def ensure_note(
    conn: sqlite3.Connection,
    creator_id: str,
    note_key: str,
    captured_at: str,
) -> str:
    return _ensure_note(conn, creator_id, note_key, captured_at)


def ensure_note_ref(
    conn: sqlite3.Connection,
    creator_id: str,
    note_ref: str,
    captured_at: str,
) -> str:
    ref = str(note_ref).strip()
    if ref:
        row = conn.execute(
            "SELECT note_id FROM notes WHERE note_id = ? AND creator_id = ?",
            (ref, creator_id),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE notes SET last_seen_at = ? WHERE note_id = ?",
                (captured_at, ref),
            )
            return ref
    return _ensure_note(conn, creator_id, f"import-ref:{ref or uuid4().hex}", captured_at)


def _ensure_note(
    conn: sqlite3.Connection,
    creator_id: str,
    note_key: str,
    captured_at: str,
) -> str:
    key_hash = _source_key_hash(f"{creator_id}:{note_key}")
    row = conn.execute(
        "SELECT note_id FROM notes WHERE note_key_hash = ?", (key_hash,)
    ).fetchone()
    if row:
        note_id = str(row[0])
        conn.execute(
            "UPDATE notes SET last_seen_at = ? WHERE note_id = ?",
            (captured_at, note_id),
        )
        return note_id
    note_id = uuid4().hex
    conn.execute(
        """INSERT INTO notes(note_id, creator_id, note_key_hash, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?)""",
        (note_id, creator_id, key_hash, captured_at, captured_at),
    )
    return note_id


def _backfill_creator_links(conn: sqlite3.Connection) -> None:
    """Link legacy records conservatively; never infer identity from names or titles."""
    batch_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT batch_id FROM records WHERE creator_id IS NULL AND record_type != 'comment_excerpt_sample'"
        ).fetchall()
    ]
    for batch_id in batch_ids:
        rows = conn.execute(
            """SELECT record_id, record_type, captured_at, fields_json, fields_hash
               FROM records WHERE batch_id = ? AND creator_id IS NULL
                 AND record_type != 'comment_excerpt_sample' ORDER BY record_id""",
            (batch_id,),
        ).fetchall()
        profiles = [row for row in rows if row[1] == "creator_profile_sample"]
        list_rows = [row for row in rows if row[1] == "blogger_list_sample"]
        shared_creator_id: str | None = None
        if len(profiles) == 1:
            profile = profiles[0]
            shared_creator_id = _ensure_creator(
                conn, f"legacy-profile:{profile[4]}", profile[2]
            )
        elif len(list_rows) == 1:
            listing = list_rows[0]
            shared_creator_id = _ensure_creator(
                conn, f"legacy-listing:{listing[4]}", listing[2]
            )

        for row in rows:
            record_id, record_type, captured_at, fields_json, fields_hash = row
            if record_type == "blogger_list_sample" and len(list_rows) > 1:
                creator_id = _ensure_creator(
                    conn, f"legacy-listing:{batch_id}:{record_id}", captured_at
                )
            elif shared_creator_id:
                creator_id = shared_creator_id
            else:
                creator_id = _ensure_creator(
                    conn, f"legacy-record:{record_type}:{fields_hash}", captured_at
                )

            note_id = None
            if record_type == "note_detail":
                note_key = f"legacy-record:{record_id}:{fields_hash}"
                note_id = _ensure_note(conn, creator_id, note_key, captured_at)
                fields = json.loads(fields_json)
                title = str(fields.get("笔记标题") or fields.get("标题") or "")
                published_at = str(fields.get("发布时间") or "")
                conn.execute(
                    "UPDATE notes SET title = ?, published_at = ? WHERE note_id = ?",
                    (title or None, published_at or None, note_id),
                )
            conn.execute(
                "UPDATE records SET creator_id = ?, note_id = ? WHERE record_id = ?",
                (creator_id, note_id, record_id),
            )


def local_timestamp() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")
