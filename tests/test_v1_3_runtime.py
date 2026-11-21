from __future__ import annotations

import tempfile
import hashlib
import sqlite3
import unittest
from pathlib import Path

from creatorscope import create_app
from creatorscope.db import connection, initialize_database, record_field_statuses, schema_version_readonly
from creatorscope.ingest import ingest_payload


class V13RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="creatorscope-v13-")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "demo.sqlite3"
        initialize_database(self.db_path)
        self.app = create_app(
            {
                "DATABASE_PATH": self.db_path,
                "BASELINE_PATH": self.root / "missing-baseline.json",
                "AI_REPORTS_PATH": self.root / "ai-reports",
                "NEWS_DATA_PATH": self.root / "news.jsonl",
                "DEMO_MODE": True,
                "TESTING": True,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.app.extensions["creatorscope_tasks"].shutdown()
        self.temp_dir.cleanup()

    def test_offline_demo_blocks_live_network_and_write_actions(self) -> None:
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("本地离线演示".encode("utf-8"), page.data)
        self.assertEqual(self.client.get("/api/health").json["mode"], "demo")

        blocked = (
            ("/api/browser/open", {}),
            ("/api/browser/capture", {}),
            ("/api/tasks/collect-visible", {}),
            ("/api/tasks/import-baseline", {}),
            ("/api/news/collect", {"source_key": "xinhuanet_tech"}),
            ("/api/ai/test", {}),
            ("/api/ai/analyze", {}),
        )
        for path, payload in blocked:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json=payload).status_code, 409)
        self.assertEqual(self.client.get("/api/export/json").status_code, 200)
        after = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        self.assertEqual(after, before)

    def test_batch_quality_report_is_available_as_local_json(self) -> None:
        with connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO tasks(task_id,task_type,source,status,created_at) VALUES('quality-task','visible_collection','test','completed','2026-11-21T10:00:00+08:00')"
            )
            conn.execute(
                """INSERT INTO batches(batch_id,task_id,source,captured_at,started_at,completed_at,status)
                   VALUES('quality-batch','quality-task','本地历史样本','2026-11-21T10:00:00+08:00',
                          '2026-11-21T10:00:00+08:00','2026-11-21T10:00:01+08:00','running')"""
            )
        ingest_payload(
            self.db_path,
            task_id="quality-task",
            payload={
                "records": [
                    {
                        "type": "xhs_public_note",
                        "source": "小红书公开笔记页",
                        "creator_ref": "creator-a",
                        "note_ref": "https://www.xiaohongshu.com/explore/note-c",
                        "fields": {"笔记标题": "Course title"},
                    }
                ]
            },
            source_override="小红书公开笔记页",
            batch_id_override="quality-batch",
        )
        record_field_statuses(
            self.db_path,
            batch_id="quality-batch",
            task_id="quality-task",
            entity_type="note",
            entity_ref="note-a",
            record_type="xhs_public_note",
            field_names=("笔记标题", "笔记正文"),
            status="access_denied",
            source="小红书公开笔记页",
        )
        record_field_statuses(
            self.db_path,
            batch_id="quality-batch",
            task_id="quality-task",
            entity_type="note",
            entity_ref="https://www.xiaohongshu.com/explore/note-c",
            record_type="xhs_public_note",
            field_names=("笔记标题",),
            status="access_denied",
            source="小红书公开笔记页",
        )
        record_field_statuses(
            self.db_path,
            batch_id="quality-batch",
            task_id="quality-task",
            entity_type="note",
            entity_ref="note-b",
            record_type="xhs_public_note",
            field_names=("笔记标题", "笔记正文"),
            status="not_loaded",
            source="小红书公开笔记页",
        )
        record_field_statuses(
            self.db_path,
            batch_id="quality-batch",
            task_id="quality-task",
            entity_type="note",
            entity_ref="note-b",
            record_type="xhs_public_note",
            field_names=("笔记标题",),
            status="parse_failed",
            source="小红书公开笔记页",
        )
        with connection(self.db_path) as conn:
            conn.execute(
                "UPDATE batches SET status='completed', completed_at='2026-11-21T10:00:01+08:00' WHERE batch_id='quality-batch'"
            )
        response = self.client.get("/api/batches/quality-batch/quality")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["batch_id"], "quality-batch")
        self.assertEqual(response.json["counts"]["records"], 1)
        self.assertIsNotNone(response.json["elapsed_seconds"])
        title_coverage = next(
            field for field in response.json["field_coverage"]
            if field["record_type"] == "xhs_public_note" and field["field_name"] == "笔记标题"
        )
        self.assertEqual(title_coverage["expected_records"], 3)
        self.assertEqual(title_coverage["captured_records"], 1)
        self.assertEqual(title_coverage["states"], {"access_denied": 1, "captured": 1, "parse_failed": 1})
        body_coverage = next(
            field for field in response.json["field_coverage"]
            if field["record_type"] == "xhs_public_note" and field["field_name"] == "笔记正文"
        )
        self.assertEqual(body_coverage["expected_records"], 3)
        self.assertEqual(body_coverage["states"], {"access_denied": 1, "not_loaded": 1})

    def test_live_startup_verifies_backup_before_migrating_existing_database(self) -> None:
        old_db = self.root / "existing-v2.sqlite3"
        backup_dir = self.root / "backups"
        initialize_database(old_db)
        with connection(old_db) as conn:
            conn.execute(
                "INSERT INTO tasks(task_id,task_type,source,status,created_at) VALUES('keep-task','import','test','completed','2026-11-21T10:00:00+08:00')"
            )
            conn.execute("DROP TABLE collection_field_observations")
            conn.execute("DELETE FROM schema_migrations WHERE version=3")
            conn.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(2,'2026-11-21T10:00:00+08:00')"
            )
        self.assertEqual(schema_version_readonly(old_db), 2)

        migration_app = create_app(
            {
                "DATABASE_PATH": old_db,
                "MIGRATION_BACKUP_DIR": backup_dir,
                "BASELINE_PATH": self.root / "missing-baseline.json",
                "AI_REPORTS_PATH": self.root / "ai-reports",
                "NEWS_DATA_PATH": self.root / "news.jsonl",
                "DEMO_MODE": False,
                "TESTING": True,
            }
        )
        try:
            self.assertEqual(schema_version_readonly(old_db), 3)
            backups = list(backup_dir.glob("existing-v2_pre_schema_v3_*.sqlite3"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(schema_version_readonly(backups[0]), 2)
            backup = sqlite3.connect(f"{backups[0].resolve().as_uri()}?mode=ro", uri=True)
            try:
                self.assertEqual(backup.execute("SELECT COUNT(*) FROM tasks WHERE task_id='keep-task'").fetchone()[0], 1)
                self.assertEqual(
                    backup.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='collection_field_observations'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                backup.close()
        finally:
            migration_app.extensions["creatorscope_tasks"].shutdown()

    def test_live_startup_stops_without_mutating_when_migration_backup_fails(self) -> None:
        old_db = self.root / "unprotected-v2.sqlite3"
        blocked_backup_path = self.root / "not-a-backup-directory"
        blocked_backup_path.write_text("preserve this file", encoding="utf-8")
        initialize_database(old_db)
        with connection(old_db) as conn:
            conn.execute("DROP TABLE collection_field_observations")
            conn.execute("DELETE FROM schema_migrations WHERE version=3")
            conn.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(2,'2026-11-21T10:00:00+08:00')"
            )

        with self.assertRaisesRegex(sqlite3.DatabaseError, "已阻止启动和迁移"):
            create_app(
                {
                    "DATABASE_PATH": old_db,
                    "MIGRATION_BACKUP_DIR": blocked_backup_path,
                    "BASELINE_PATH": self.root / "missing-baseline.json",
                    "AI_REPORTS_PATH": self.root / "ai-reports",
                    "NEWS_DATA_PATH": self.root / "news.jsonl",
                    "DEMO_MODE": False,
                    "TESTING": True,
                }
            )
        self.assertEqual(schema_version_readonly(old_db), 2)
        self.assertEqual(blocked_backup_path.read_text(encoding="utf-8"), "preserve this file")
        database = sqlite3.connect(f"{old_db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            self.assertEqual(
                database.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='collection_field_observations'"
                ).fetchone()[0],
                0,
            )
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
