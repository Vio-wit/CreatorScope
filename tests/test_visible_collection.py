from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from creatorscope.db import connection, initialize_database
from creatorscope.tasks import TaskManager


class RecordingBrowser:
    def collect_visible(
        self,
        settings,
        checkpoint,
        *,
        check_control,
        save_checkpoint,
        save_records,
        save_comments,
        report_progress,
        report_failure,
        existing_comment_count,
    ):
        check_control()
        creator_ref = "pgy:/solar/pre-trade/blogger-detail/synthetic"
        note_ref = "https://www.xiaohongshu.com/explore/synthetic-note-id"
        save_records(
            {
                "source": "synthetic visible page",
                "records": [
                    {
                        "type": "creator_profile_sample",
                        "creator_ref": creator_ref,
                        "fields": {"粉丝数": "1200"},
                    },
                    {
                        "type": "note_detail",
                        "creator_ref": creator_ref,
                        "note_ref": note_ref,
                        "fields": {"笔记标题": "Synthetic title", "阅读量": "300"},
                    },
                    {
                        "type": "xhs_public_note",
                        "creator_ref": creator_ref,
                        "note_ref": note_ref,
                        "note_topics": ["#synthetic"],
                        "fields": {"笔记标题": "Synthetic title", "笔记正文": "Synthetic body", "话题": "#synthetic"},
                    },
                ],
            }
        )
        comments = [
            {
                "local_comment_id": "root-hash",
                "comment_text": "Root comment",
                "displayed_time": "2 days ago",
                "comment_level": 1,
                "reply_count_raw": "1",
                "is_pinned": False,
            },
            {
                "local_comment_id": "reply-hash",
                "parent_local_comment_id": "root-hash",
                "comment_text": "Reply comment",
                "displayed_time": "1 day ago",
                "comment_level": 2,
                "like_count_raw": "2",
            },
        ]
        outcome = save_comments(creator_ref, note_ref, comments)
        duplicate = save_comments(creator_ref, note_ref, comments)
        save_checkpoint({"creator_index": 1, "note_index": 0, "comment_count": existing_comment_count + outcome["inserted"]})
        report_progress(80, "synthetic progress", 2)
        return {
            "creators_visited": 1,
            "notes_visited": 1,
            "comment_count": existing_comment_count + outcome["inserted"],
            "dom_rows_added": 2,
            "failure_count": 0,
            "message": f"inserted={outcome['inserted']} duplicate={duplicate['duplicates']}",
        }


class GateBrowser:
    def __init__(self, *, stop_after_start: bool = False):
        self.started = threading.Event()
        self.release = threading.Event()
        self.stop_after_start = stop_after_start

    def collect_visible(
        self,
        settings,
        checkpoint,
        *,
        check_control,
        save_checkpoint,
        save_records,
        save_comments,
        report_progress,
        report_failure,
        existing_comment_count,
    ):
        self.started.set()
        while not self.release.is_set():
            check_control()
            time.sleep(0.01)
        check_control()
        return {
            "creators_visited": 0,
            "notes_visited": 0,
            "comment_count": existing_comment_count,
            "dom_rows_added": 0,
            "failure_count": 0,
            "message": "synthetic control test",
        }


class VisibleCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="creatorscope-collect-")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "unified.sqlite3"
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_incremental_batch_and_comment_parent_are_idempotent(self) -> None:
        manager = TaskManager(self.db_path, self.root / "unused-baseline.json", RecordingBrowser())
        try:
            task_id = manager.submit_visible_collection(
                {"creator_count": 1, "notes_per_creator": 1, "comments_per_note": 2}
            )
            task = self._wait_for_status(task_id, {"completed", "failed"})
            self.assertEqual(task["status"], "completed", task["message"])
            payload = json.loads(task["result_json"])
            self.assertEqual(payload["comment_count"], 2)
            self.assertEqual(payload["dom_rows_added"], 2)
            self.assertEqual(payload["message"], "inserted=2 duplicate=2")
            with connection(self.db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM batches WHERE task_id=?", (task_id,)).fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM records WHERE batch_id=?", (task["batch_id"],)).fetchone()[0], 3)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE batch_id=?", (task["batch_id"],)).fetchone()[0], 2)
                note = conn.execute(
                    "SELECT platform_note_id, platform_note_id_hash, title, body, topics_json FROM notes"
                ).fetchone()
                self.assertIsNone(note["platform_note_id"])
                self.assertIsNotNone(note["platform_note_id_hash"])
                self.assertEqual(note["title"], "Synthetic title")
                self.assertEqual(note["body"], "Synthetic body")
                self.assertEqual(json.loads(note["topics_json"]), ["#synthetic"])
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM creators WHERE platform_creator_id IS NOT NULL").fetchone()[0],
                    0,
                )
                reply = conn.execute(
                    "SELECT parent_comment_id_hash FROM comment_snapshots WHERE comment_level=2 AND batch_id=?",
                    (task["batch_id"],),
                ).fetchone()
                self.assertIsNotNone(reply[0])
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            manager.shutdown()
            self._wait_for_manager(manager)

    def test_limits_are_rejected_before_task_creation(self) -> None:
        manager = TaskManager(self.db_path, self.root / "unused-baseline.json", RecordingBrowser())
        try:
            with self.assertRaises(ValueError):
                manager.submit_visible_collection({"creator_count": 11})
            with self.assertRaises(ValueError):
                manager.submit_visible_collection({"notes_per_creator": 4})
            with self.assertRaises(ValueError):
                manager.submit_visible_collection({"comments_per_note": 21})
            with connection(self.db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        finally:
            manager.shutdown()
            self._wait_for_manager(manager)

    def test_pause_resume_and_stop_are_cooperative(self) -> None:
        browser = GateBrowser()
        manager = TaskManager(self.db_path, self.root / "unused-baseline.json", browser)
        try:
            task_id = manager.submit_visible_collection({"creator_count": 1})
            self.assertTrue(browser.started.wait(3))
            self.assertTrue(manager.pause_task(task_id))
            self.assertEqual(self._read_task(task_id)["status"], "paused")
            self.assertTrue(manager.resume_task(task_id))
            browser.release.set()
            task = self._wait_for_status(task_id, {"completed", "failed"})
            self.assertEqual(task["status"], "completed", task["message"])

            browser2 = GateBrowser()
            manager.browser = browser2
            stop_id = manager.submit_visible_collection({"creator_count": 1})
            self.assertTrue(browser2.started.wait(3))
            self.assertTrue(manager.stop_task(stop_id))
            stopped = self._wait_for_status(stop_id, {"stopped", "failed", "completed"})
            self.assertEqual(stopped["status"], "stopped", stopped["message"])
        finally:
            manager.shutdown()
            self._wait_for_manager(manager)

    def _read_task(self, task_id: str):
        with connection(self.db_path) as conn:
            return conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()

    def _wait_for_status(self, task_id: str, statuses: set[str]):
        deadline = time.time() + 6
        while time.time() < deadline:
            row = self._read_task(task_id)
            if row["status"] in statuses:
                return row
            time.sleep(0.025)
        self.fail(f"task did not reach one of {sorted(statuses)}")

    @staticmethod
    def _wait_for_manager(manager: TaskManager) -> None:
        deadline = time.time() + 3
        while time.time() < deadline:
            with manager._lock:
                if not manager._active and not manager._scheduled:
                    return
            time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
