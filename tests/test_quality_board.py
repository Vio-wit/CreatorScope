from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from creatorscope import create_app
from creatorscope.db import connection, initialize_database


class QualityBoardTests(unittest.TestCase):
    def test_overview_limits_and_read_only_demo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "test.sqlite3"
            initialize_database(db)
            with connection(db) as conn:
                conn.execute("INSERT INTO tasks(task_id,task_type,source,status,created_at) VALUES('t','visible_collection','test','completed','2026-01-01')")
                conn.execute("INSERT INTO batches(batch_id,task_id,source,captured_at,status) VALUES('b','t','test','2026-01-01','completed')")
            app = create_app({"DATABASE_PATH": db, "BASELINE_PATH": root / "missing", "AI_REPORTS_PATH": root / "ai", "NEWS_DATA_PATH": root / "news", "DEMO_MODE": True, "TESTING": True})
            try:
                before = hashlib.sha256(db.read_bytes()).digest()
                client = app.test_client()
                self.assertEqual(client.get("/quality").status_code, 200)
                overview = client.get("/api/quality/overview?limit=1")
                self.assertEqual(overview.status_code, 200)
                self.assertEqual(overview.json["batches"][0]["batch_id"], "b")
                self.assertIsNone(overview.json["batches"][0]["coverage_pct"])
                self.assertEqual(client.get("/api/quality/overview?limit=no").status_code, 400)
                self.assertEqual(client.get("/api/batches/missing/quality").status_code, 404)
                self.assertEqual(before, hashlib.sha256(db.read_bytes()).digest())
            finally:
                app.extensions["creatorscope_tasks"].shutdown()
