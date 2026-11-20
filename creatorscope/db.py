from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    batch_id TEXT,
    error TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS creators (
    creator_id TEXT PRIMARY KEY,
    pseudonym TEXT NOT NULL UNIQUE,
    source_key_hash TEXT NOT NULL UNIQUE,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    note_id TEXT PRIMARY KEY,
    creator_id TEXT NOT NULL,
    note_key_hash TEXT NOT NULL UNIQUE,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    FOREIGN KEY(creator_id) REFERENCES creators(creator_id)
);

CREATE TABLE IF NOT EXISTS records (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_records_batch ON records(batch_id, record_type);
CREATE INDEX IF NOT EXISTS idx_metrics_key ON metric_values(metric_key);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_creator ON notes(creator_id);
"""


@contextmanager
def connection(db_path: Path) -> Iterator[sqlite3.Connection]:
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
        _ensure_record_columns(conn)
        _backfill_creator_links(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_creator_batch ON records(creator_id, batch_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_note ON records(note_id)")


def _ensure_record_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
    if "creator_id" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN creator_id TEXT REFERENCES creators(creator_id)")
    if "note_id" not in columns:
        conn.execute("ALTER TABLE records ADD COLUMN note_id TEXT REFERENCES notes(note_id)")


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
    conn.execute(
        """INSERT INTO creators(creator_id, pseudonym, source_key_hash, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?)""",
        (creator_id, f"博主 {index:04d}", key_hash, captured_at, captured_at),
    )
    return creator_id


def ensure_creator(conn: sqlite3.Connection, source_key: str, captured_at: str) -> str:
    return _ensure_creator(conn, source_key, captured_at)


def ensure_creator_ref(
    conn: sqlite3.Connection,
    creator_ref: str,
    captured_at: str,
) -> str:
    """Resolve an exported internal reference or hash an import-only source key."""
    ref = str(creator_ref).strip()
    if ref:
        row = conn.execute(
            "SELECT creator_id FROM creators WHERE creator_id = ?", (ref,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE creators SET last_seen_at = ? WHERE creator_id = ?",
                (captured_at, ref),
            )
            return ref
    return _ensure_creator(conn, ref or uuid4().hex, captured_at)


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
    """Link legacy snapshots conservatively; never infer identity from names."""
    batch_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT batch_id FROM records WHERE creator_id IS NULL"
        ).fetchall()
    ]
    for batch_id in batch_ids:
        rows = conn.execute(
            """SELECT record_id, record_type, captured_at, fields_json, fields_hash
               FROM records WHERE batch_id = ? AND creator_id IS NULL ORDER BY record_id""",
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
                fields = json.loads(fields_json)
                title = str(fields.get("笔记标题") or fields.get("标题") or "")
                published = str(fields.get("发布时间") or "")
                note_signature = (
                    f"{title}:{published}"
                    if title
                    else f"{published}:{fields_hash}"
                )
                note_id = _ensure_note(
                    conn, creator_id, note_signature, captured_at
                )
            conn.execute(
                "UPDATE records SET creator_id = ?, note_id = ? WHERE record_id = ?",
                (creator_id, note_id, record_id),
            )


def local_timestamp() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")
