from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from creatorscope.db import connection, initialize_database
from creatorscope.ingest import ingest_payload
from creatorscope.migrations import (
    backup_database,
    import_r42_comment_source,
    migrate_legacy_database,
    verify_database,
)


class UnifiedDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="creatorscope-db-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_schema_initialization_is_repeatable_and_has_required_views(self) -> None:
        db_path = self.root / "unified.sqlite3"
        initialize_database(db_path)
        initialize_database(db_path)

        with connection(db_path) as conn:
            views = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='view'"
                )
            }
            self.assertTrue(
                {"collection_batches", "collection_tasks", "metric_snapshots"}.issubset(views)
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=3").fetchone()[0],
                1,
            )
            field_columns = {row[1] for row in conn.execute('PRAGMA table_info("collection_field_observations")')}
            self.assertTrue({"raw_value", "normalized_value_json", "source", "captured_at", "status"}.issubset(field_columns))
            records = {row[1] for row in conn.execute('PRAGMA table_info("records")')}
            self.assertIn("source", records)
            view_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='view' AND name='metric_snapshots'"
            ).fetchone()[0]
            self.assertIn("COALESCE(r.source, b.source)", view_sql)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_record_source_column_is_added_to_an_existing_database(self) -> None:
        db_path = self.root / "unified.sqlite3"
        initialize_database(db_path)
        with connection(db_path) as conn:
            conn.execute("DROP VIEW metric_snapshots")
            conn.execute('ALTER TABLE records DROP COLUMN "source"')

        initialize_database(db_path)
        with connection(db_path) as conn:
            columns = {row[1] for row in conn.execute('PRAGMA table_info("records")')}
            self.assertIn("source", columns)
            view_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='view' AND name='metric_snapshots'"
            ).fetchone()[0]
            self.assertIn("COALESCE(r.source, b.source)", view_sql)

    def test_v3_field_observation_migration_backfills_existing_raw_records(self) -> None:
        db_path = self.root / "v2-copy.sqlite3"
        initialize_database(db_path)
        with connection(db_path) as conn:
            conn.execute(
                "INSERT INTO tasks(task_id,task_type,source,status,created_at) VALUES('old-task','import','test','completed','2026-01-01T00:00:00+08:00')"
            )
        ingest_payload(
            db_path,
            "old-task",
            {
                "source": "historical public sample",
                "records": [{
                    "type": "xhs_public_note",
                    "creator_ref": "creator-ref-raw",
                    "note_ref": "https://www.xiaohongshu.com/explore/note-raw-id",
                    "fields": {"笔记标题": "原始标题保留", "点赞量": "10+"},
                }],
            },
        )
        with connection(db_path) as conn:
            original_fields = conn.execute("SELECT fields_json FROM records").fetchone()[0]
            conn.execute("DROP TABLE collection_field_observations")
            conn.execute("DELETE FROM schema_migrations WHERE version=3")

        initialize_database(db_path)
        with connection(db_path) as conn:
            self.assertEqual(conn.execute("SELECT fields_json FROM records").fetchone()[0], original_fields)
            row = conn.execute(
                "SELECT raw_value, status, source FROM collection_field_observations WHERE field_name='点赞量'"
            ).fetchone()
            self.assertEqual(tuple(row), ("10+", "captured", "historical public sample"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_legacy_copy_is_idempotent_and_keeps_source_unchanged(self) -> None:
        source = self.root / "legacy.sqlite3"
        destination = self.root / "unified.sqlite3"
        self._create_legacy_database(source)
        source_hash = self._sha256(source)

        first = migrate_legacy_database(source, destination)
        second = migrate_legacy_database(source, destination)

        self.assertEqual(first["tasks"], 1)
        self.assertEqual(first["records"], 2)
        self.assertEqual(first["metric_values"], 1)
        self.assertEqual(sum(second.values()), 0)
        self.assertEqual(source_hash, self._sha256(source))
        verification = verify_database(destination)
        self.assertEqual(verification["integrity"], "ok")
        self.assertEqual(verification["foreign_key_errors"], 0)
        self.assertEqual(verification["counts"]["records"], 2)
        self.assertEqual(verification["counts"]["metric_values"], 1)

    def test_backup_creates_and_reuses_verified_copy(self) -> None:
        source = self.root / "legacy.sqlite3"
        backup = self.root / "backup" / "legacy.sqlite3"
        self._create_legacy_database(source)

        first = backup_database(source, backup)
        second = backup_database(source, backup)

        self.assertEqual(first["status"], "created_and_verified")
        self.assertEqual(second["status"], "already_verified")
        self.assertEqual(self._logical_hash(source), self._logical_hash(backup))

    def test_comment_import_deduplicates_within_batch_and_appends_across_batches(self) -> None:
        db_path = self.root / "unified.sqlite3"
        first_source = self._make_comment_source(self.root / "r42-a", "batch-a")
        second_source = self._make_comment_source(self.root / "r42-b", "batch-b")

        first = import_r42_comment_source(db_path, first_source)
        repeat = import_r42_comment_source(db_path, first_source)
        later = import_r42_comment_source(db_path, second_source)

        self.assertEqual((first["inserted"], first["duplicate_skips"], first["total_records"]), (29, 0, 29))
        self.assertEqual((repeat["inserted"], repeat["duplicate_skips"], repeat["total_records"]), (0, 29, 29))
        self.assertEqual((later["inserted"], later["duplicate_skips"], later["total_records"]), (29, 0, 29))
        verification = verify_database(db_path)
        self.assertEqual(verification["integrity"], "ok")
        self.assertEqual(verification["foreign_key_errors"], 0)
        self.assertEqual(verification["counts"]["comment_snapshots"], 58)
        self.assertEqual(verification["counts"]["batches"], 2)
        with connection(db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM creators WHERE creator_name IN ('Creator A','Creator B')").fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM notes WHERE title LIKE 'Sample note %'").fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM comment_snapshots WHERE comment_text='repeated text'"
                ).fetchone()[0],
                58,
            )

    def _create_legacy_database(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY, task_type TEXT NOT NULL, source TEXT NOT NULL,
                status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, batch_id TEXT, error TEXT, result_json TEXT
            );
            CREATE TABLE batches (
                batch_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
                captured_at TEXT NOT NULL, record_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            );
            CREATE TABLE creators (
                creator_id TEXT PRIMARY KEY, pseudonym TEXT NOT NULL UNIQUE,
                source_key_hash TEXT NOT NULL UNIQUE, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE notes (
                note_id TEXT PRIMARY KEY, creator_id TEXT NOT NULL,
                note_key_hash TEXT NOT NULL UNIQUE, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL, FOREIGN KEY(creator_id) REFERENCES creators(creator_id)
            );
            CREATE TABLE records (
                record_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
                record_type TEXT NOT NULL, captured_at TEXT NOT NULL, fields_json TEXT NOT NULL,
                fields_hash TEXT NOT NULL, FOREIGN KEY(batch_id) REFERENCES batches(batch_id),
                UNIQUE(batch_id, record_type, fields_hash)
            );
            CREATE TABLE metric_values (
                metric_id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL,
                metric_key TEXT NOT NULL, raw_value TEXT, numeric_value REAL,
                unit TEXT NOT NULL DEFAULT '', period TEXT NOT NULL DEFAULT '',
                parse_status TEXT NOT NULL,
                FOREIGN KEY(record_id) REFERENCES records(record_id) ON DELETE CASCADE,
                UNIQUE(record_id, metric_key)
            );
            INSERT INTO tasks(task_id,task_type,source,status,created_at)
                VALUES('task-1','capture','pgy','completed','2026-01-01T00:00:00+08:00');
            INSERT INTO batches(batch_id,task_id,source,captured_at,record_count)
                VALUES('batch-1','task-1','pgy','2026-01-01T00:00:00+08:00',2);
            INSERT INTO records(batch_id,record_type,captured_at,fields_json,fields_hash)
                VALUES('batch-1','creator_profile_sample','2026-01-01T00:00:00+08:00','{}','hash-1');
            INSERT INTO records(batch_id,record_type,captured_at,fields_json,fields_hash)
                VALUES('batch-1','note_detail','2026-01-01T00:00:00+08:00','{"标题":"Same title"}','hash-2');
            INSERT INTO metric_values(record_id,metric_key,raw_value,numeric_value,parse_status)
                VALUES(1,'粉丝数','1200',1200,'parsed');
            """
        )
        conn.commit()
        conn.close()

    def _make_comment_source(self, directory: Path, batch_id: str) -> Path:
        directory.mkdir(parents=True)
        note_ids = ("platform-note-a", "platform-note-b")
        note_metadata = [
            {"note_id": note_ids[0], "creator_name": "Creator A", "title": "Sample note A", "status": "opened"},
            {"note_id": note_ids[1], "creator_name": "Creator B", "title": "Sample note B", "status": "opened"},
        ]
        rows = []
        for index in range(28):
            note_id = note_ids[index % 2]
            rows.append(
                {
                    "batch_id": batch_id,
                    "note_id": note_id,
                    "local_comment_id": f"root-{index}",
                    "parent_comment_id": None,
                    "comment_text": "repeated text",
                    "comment_level": 1,
                    "capture_time": "2026-01-01T00:00:00+08:00",
                    "displayed_time": "today",
                    "like_count_raw": None,
                    "like_count_normalized": None,
                    "reply_count_raw": "0",
                    "is_pinned": False,
                    "source": "xiaohongshu",
                }
            )
        rows.append(
            {
                "batch_id": batch_id,
                "note_id": note_ids[0],
                "local_comment_id": "reply-1",
                "parent_comment_id": "root-0",
                "comment_text": "repeated text",
                "comment_level": 2,
                "capture_time": "2026-01-01T00:01:00+08:00",
                "displayed_time": "today",
                "like_count_raw": "10+",
                "like_count_normalized": None,
                "reply_count_raw": None,
                "is_pinned": None,
                "source": "xiaohongshu",
            }
        )
        for index, row in enumerate(rows):
            row["batch_id"] = batch_id
            row["capture_time"] = f"2026-01-01T00:{index:02d}:00+08:00"
        (directory / "raw_comments.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        (directory / "quality_report.json").write_text(
            json.dumps({"batch_id": batch_id, "by_note": note_metadata}),
            encoding="utf-8",
        )
        (directory / "collection_log.json").write_text(
            json.dumps(
                [
                    {"event": "batch_start", "time": "2026-01-01T00:00:00+08:00"},
                    {"event": "batch_finished", "time": "2026-01-01T00:30:00+08:00"},
                ]
            ),
            encoding="utf-8",
        )
        return directory

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _logical_hash(path: Path) -> str:
        conn = sqlite3.connect(path)
        try:
            digest = hashlib.sha256()
            for statement in conn.iterdump():
                digest.update(statement.encode("utf-8"))
                digest.update(bytes((10,)))
            return digest.hexdigest()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
