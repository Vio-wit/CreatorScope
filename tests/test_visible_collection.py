from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from creatorscope.browser import PauseForUser, PgyVisibleBrowser
from creatorscope.db import connection, initialize_database
from creatorscope.ingest import normalize_value
from creatorscope.quality import batch_quality_report
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
        report_note_success,
        report_field_status,
        existing_comment_count,
        comment_scope="",
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
                        "source": "蒲公英可见页面",
                        "creator_ref": creator_ref,
                        "fields": {"粉丝数": "1200"},
                    },
                    {
                        "type": "note_detail",
                        "source": "蒲公英可见笔记详情",
                        "creator_ref": creator_ref,
                        "note_ref": note_ref,
                        "fields": {"笔记标题": "Synthetic title", "阅读量": "300"},
                    },
                    {
                        "type": "xhs_public_note",
                        "source": "小红书公开笔记页",
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
        report_note_success(1, 1)
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
        report_note_success,
        report_field_status,
        existing_comment_count,
        comment_scope="",
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


class OverlayBrowser:
    def collect_visible(self, *_args, **_kwargs):
        raise PauseForUser("蒲公英列表被页面弹层遮挡；请在可见浏览器中确认并关闭后恢复任务")


class RetryBrowser:
    def __init__(self):
        self.calls = 0
        self.last_settings = None

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
        report_note_success,
        report_field_status,
        existing_comment_count,
        comment_scope="",
    ):
        self.calls += 1
        self.last_settings = settings
        check_control()
        if self.calls == 1:
            report_failure(1, 2, "公开笔记加载超时")
        else:
            report_note_success(1, 2)
        save_checkpoint({"creator_index": 1, "note_index": 0, "comment_count": existing_comment_count})
        return {
            "creators_visited": 1,
            "notes_visited": 1,
            "comment_count": existing_comment_count,
            "dom_rows_added": 0,
            "failure_count": 1 if self.calls == 1 else 0,
            "message": "retry test",
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
            quality = batch_quality_report(self.db_path, task["batch_id"])
            self.assertEqual(quality["counts"]["records"], 3)
            self.assertEqual(quality["counts"]["comments"], 2)
            self.assertTrue(any(field["field_name"] == "笔记正文" for field in quality["field_coverage"]))
            with connection(self.db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM batches WHERE task_id=?", (task_id,)).fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM records WHERE batch_id=?", (task["batch_id"],)).fetchone()[0], 3)
                record_sources = {
                    row[0]
                    for row in conn.execute(
                        "SELECT DISTINCT source FROM records WHERE batch_id=?",
                        (task["batch_id"],),
                    )
                }
                self.assertEqual(
                    record_sources,
                    {"蒲公英可见页面", "蒲公英可见笔记详情", "小红书公开笔记页"},
                )
                metric_sources = {
                    row[0]
                    for row in conn.execute(
                        "SELECT DISTINCT source FROM metric_snapshots WHERE batch_id=?",
                        (task["batch_id"],),
                    )
                }
                self.assertEqual(metric_sources, record_sources)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE batch_id=?", (task["batch_id"],)).fetchone()[0], 2)
                fields = conn.execute(
                    "SELECT field_name, raw_value, status FROM collection_field_observations WHERE batch_id=? AND record_type='xhs_public_note'",
                    (task["batch_id"],),
                ).fetchall()
                observed = {row["field_name"]: (row["raw_value"], row["status"]) for row in fields}
                self.assertEqual(observed["笔记正文"], ("Synthetic body", "captured"))
                note = conn.execute(
                    "SELECT platform_note_id, platform_note_id_hash, title, body, topics_json FROM notes"
                ).fetchone()
                self.assertEqual(note["platform_note_id"], "synthetic-note-id")
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

    def test_collection_types_can_be_selected_independently(self) -> None:
        settings = TaskManager._validate_collection_settings(
            {"collect_creator": False, "collect_notes": False, "collect_comments": True}
        )
        self.assertFalse(settings["collect_creator"])
        self.assertFalse(settings["collect_notes"])
        self.assertTrue(settings["collect_comments"])

    def test_failed_note_can_be_retried_without_repeating_other_batch_items(self) -> None:
        browser = RetryBrowser()
        manager = TaskManager(self.db_path, self.root / "unused-baseline.json", browser)
        try:
            task_id = manager.submit_visible_collection({"creator_count": 2, "collect_creator": False})
            task = self._wait_for_status(task_id, {"completed", "failed"})
            self.assertEqual(task["status"], "completed", task["message"])
            first_result = json.loads(task["result_json"])
            self.assertEqual(first_result["failures"][0]["note_index"], 2)

            self.assertTrue(manager.retry_failed_task(task_id))
            retried = self._wait_for_status(task_id, {"completed", "failed"})
            self.assertEqual(retried["status"], "completed", retried["message"])
            self.assertEqual(browser.calls, 2)
            self.assertEqual(browser.last_settings["retry_notes"], [{"creator_index": 1, "note_index": 2}])
            self.assertEqual(json.loads(retried["result_json"])["failures"], [])
            self.assertFalse(manager.retry_failed_task(task_id))
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

    def test_user_gate_pauses_batch_and_preserves_checkpoint(self) -> None:
        manager = TaskManager(self.db_path, self.root / "unused-baseline.json", OverlayBrowser())
        try:
            task_id = manager.submit_visible_collection({"creator_count": 1})
            task = self._wait_for_status(task_id, {"paused", "failed"})
            self.assertEqual(task["status"], "paused", task["message"])
            self.assertIn("弹层遮挡", task["message"])
            parameters = json.loads(task["parameters_json"])
            self.assertEqual(
                parameters["checkpoint"],
                {"creator_index": 0, "note_index": 0, "comment_count": 0},
            )
            with connection(self.db_path) as conn:
                batch = conn.execute(
                    "SELECT status, record_count FROM batches WHERE task_id=?", (task_id,)
                ).fetchone()
                self.assertEqual(tuple(batch), ("paused", 0))
        finally:
            manager.shutdown()
            self._wait_for_manager(manager)

    def test_visible_note_title_matching_and_comment_fingerprints_are_scoped(self) -> None:
        title = PgyVisibleBrowser._note_card_title_from_text(
            "谁说北京过冬只能穿黑羽绒服，厉娜\n视频笔记\n阅读量 109,282"
        )
        self.assertEqual(title, "谁说北京过冬只能穿黑羽绒服，厉娜")
        self.assertTrue(PgyVisibleBrowser._titles_match(title, "谁说北京过冬只能穿黑羽绒服，厉娜"))
        self.assertTrue(PgyVisibleBrowser._titles_match(title, "谁说北京过冬只能穿黑羽绒服"))
        self.assertFalse(PgyVisibleBrowser._titles_match(title, "另一个创作者的笔记"))
        first = PgyVisibleBrowser._comment_fingerprint("batch-a", "note", "正文", "1天前", 1, None)
        second = PgyVisibleBrowser._comment_fingerprint("batch-b", "note", "正文", "1天前", 1, None)
        self.assertNotEqual(first, second)
        self.assertTrue(
            PgyVisibleBrowser._is_modal_overlay_interception(
                {"hit": "div.d-modal-mask", "containsHit": False}
            )
        )
        self.assertFalse(
            PgyVisibleBrowser._is_modal_overlay_interception(
                {"hit": "img.head-photo", "containsHit": True}
            )
        )
        self.assertIsNone(normalize_value("点赞量", "10+")[0])
        self.assertEqual(normalize_value("点赞量", "10+")[2], "not_numeric")
        self.assertEqual(normalize_value("视频时长", "01:49"), (109, "seconds", "parsed"))
        self.assertEqual(normalize_value("全部报价", "¥50,000 起"), (None, "CNY", "starting_price"))
        self.assertEqual(normalize_value("全部报价", "¥50,000-¥80,000"), (None, "CNY", "range_unresolved"))

    def test_visible_comment_capture_keeps_rendered_raw_text_and_marks_generated_key(self) -> None:
        class FakeLocator:
            def count(self):
                return 0

        class FakePage:
            def evaluate(self, script, _argument=None):
                if "const visible" in script:
                    return [{
                        "text": "  visible comment\n",
                        "time": "  2 days ago ",
                        "likes": "10+",
                        "replies": None,
                        "pinned": False,
                        "level": 1,
                        "parent_text": None,
                        "parent_time": None,
                    }]
                return 1

            def locator(self, _selector):
                return FakeLocator()

        browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        rows, dom_rows_added = browser._capture_visible_comments(
            FakePage(), "note-ref", 1, lambda: None, "batch-scope", lambda _delta: None
        )
        self.assertEqual(dom_rows_added, 0)
        self.assertEqual(rows[0]["comment_text"], "  visible comment\n")
        self.assertEqual(rows[0]["displayed_time"], "  2 days ago ")
        self.assertEqual(rows[0]["local_comment_key_origin"], "generated_fingerprint")
        self.assertNotIn("local_comment_id", rows[0])

    def test_browser_status_is_nonblocking_during_long_collection(self) -> None:
        browser = PgyVisibleBrowser(self.root / "browser-profile")
        browser._last_status = {
            "open": True,
            "domain": "pgy.xiaohongshu.com",
            "page_kind": "博主广场",
            "tab_count": 1,
            "tabs": [{"domain": "pgy.xiaohongshu.com", "page_kind": "博主广场"}],
        }
        browser._operation_busy.set()
        try:
            started = time.monotonic()
            status = browser.status()
            self.assertLess(time.monotonic() - started, 0.25)
            self.assertTrue(status["busy"])
            self.assertEqual(status["domain"], "pgy.xiaohongshu.com")
            self.assertEqual(status["tab_count"], 1)
        finally:
            browser._operation_busy.clear()
            browser.shutdown()

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
