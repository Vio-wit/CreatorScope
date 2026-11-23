from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from creatorscope.browser import CaptureError, ManualDismissRequired, PauseForUser, PgyVisibleBrowser
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
        raise PauseForUser("页面要求验证码或安全验证，任务已暂停；请在可见浏览器中按正常流程处理")


class ManualDismissBrowser:
    def __init__(self):
        self.calls = 0
        self.waiting = threading.Event()
        self.dismissed = threading.Event()

    def collect_visible(self, *_args, **kwargs):
        self.calls += 1
        kwargs["check_control"]()
        if self.calls == 1:
            raise ManualDismissRequired(
                "普通页面弹窗需要手动关闭；关闭后将自动继续采集",
                None,
                kind="test", pgy=True,
            )
        return {
            "creators_visited": 1,
            "notes_visited": 1,
            "comment_count": 0,
            "dom_rows_added": 0,
            "failure_count": 0,
            "message": "resumed after manual close",
        }

    def wait_for_manual_dismissal(self, _requirement, *, check_control):
        self.waiting.set()
        while not self.dismissed.wait(0.01):
            check_control()


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
        save_checkpoint(
            {
                "creator_index": 1,
                "note_index": 0,
                "comment_count": existing_comment_count,
                "creator_targets": [
                    {"kind": "creator_ref", "value": "pgy:creator-1"},
                    {"kind": "creator_ref", "value": "pgy:creator-2"},
                ],
            }
        )
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

    def test_visible_creator_loop_honors_single_batch_and_resume_bounds(self) -> None:
        class RowLocator:
            def __init__(self, count):
                self._count = count

            def count(self):
                return self._count

        class Page:
            def __init__(self, url, row_count):
                self.url = url
                self.row_count = row_count

            def is_closed(self):
                return False

            def get_by_role(self, role):
                return RowLocator(self.row_count) if role == "row" else RowLocator(0)

        def run_case(creator_count, checkpoint_creator_index, *, row_order=None, creator_targets=None):
            ordered_creators = row_order or list(range(1, creator_count + 1))
            list_page = Page("https://pgy.xiaohongshu.com/solar/pre-trade/note/kol", len(ordered_creators) + 1)
            list_records = [
                {
                    "type": "blogger_list_sample",
                    "creator_ref": f"pgy:id:creator-{creator_number}",
                    "visible_row_index": index + 1,
                    "fields": {"创作者名称": f"creator-{creator_number}"},
                }
                for index, creator_number in enumerate(ordered_creators)
            ]
            browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
            browser._context = SimpleNamespace(pages=[list_page])
            browser._page = list_page
            browser._check_page_blocker = lambda _page, **_kwargs: None
            browser._capture_list_rows = lambda _page: list_records
            opened_rows = []

            def open_creator_row(_page, row_index, expected_record=None):
                opened_rows.append(row_index)
                return (
                    Page(
                        f"https://pgy.xiaohongshu.com/solar/pre-trade/blogger-detail/detail-{row_index}",
                        0,
                    ),
                    True,
                )

            browser._open_creator_row = open_creator_row
            browser._capture_creator_records = lambda _page, creator_ref: [
                {
                    "type": "creator_profile_sample",
                    "creator_ref": creator_ref,
                    "fields": {"粉丝数": "1200"},
                }
            ]
            browser._close_owned_page = lambda _page: None
            saved_records = []
            saved_checkpoints = []
            result = browser._collect_visible(
                {
                    "creator_count": creator_count,
                    "collect_creator": True,
                    "collect_notes": False,
                    "collect_comments": False,
                },
                {
                    "creator_index": checkpoint_creator_index,
                    "note_index": 0,
                    "creator_targets": creator_targets if creator_targets is not None else [],
                },
                check_control=lambda: None,
                save_checkpoint=lambda value: saved_checkpoints.append(value),
                save_records=lambda payload: saved_records.extend(payload["records"]),
                save_comments=lambda *_args: {"inserted": 0},
                report_progress=lambda *_args: None,
                report_failure=lambda *_args: None,
                report_note_success=lambda *_args: None,
                report_field_status=lambda *_args: None,
                existing_comment_count=0,
            )
            return result, saved_records, opened_rows, saved_checkpoints

        one, one_records, _, one_checkpoints = run_case(1, 0)
        ten, ten_records, _, _ = run_case(10, 0)
        resumed, resumed_records, _, _ = run_case(10, 4)
        reordered, _, reordered_rows, _ = run_case(
            1,
            0,
            row_order=[2, 1],
            creator_targets=one_checkpoints[0]["creator_targets"],
        )
        self.assertEqual(one["creators_visited"], 1)
        self.assertEqual(len(one_records), 2)
        self.assertEqual(ten["creators_visited"], 10)
        self.assertEqual(len(ten_records), 20)
        self.assertEqual(resumed["creators_visited"], 6)
        self.assertEqual(len(resumed_records), 12)
        self.assertEqual(reordered["creators_visited"], 1)
        self.assertEqual(reordered_rows, [2])
        with self.assertRaisesRegex(PauseForUser, "目标为 5 位.*仅 4 位"):
            run_case(5, 0, row_order=[1, 2, 3, 4])

    def test_note_panel_escape_requires_closure_and_avoids_other_dialogs(self) -> None:
        from playwright.sync_api import sync_playwright

        browser_impl = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        browser_impl._check_page_blocker = lambda *_args, **_kwargs: None
        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True)
            try:
                page = chromium.new_page()
                page.set_content("""<div id='panel'>复制小红书笔记链接</div>
                    <script>document.addEventListener('keydown', e => {
                    if (e.key === 'Escape') document.querySelector('#panel').remove();
                    });</script>""")
                browser_impl._close_visible_note_panel(page)
                self.assertEqual(page.get_by_text("复制小红书笔记链接", exact=True).count(), 0)
                page.set_content("<div>复制小红书笔记链接</div><div role='dialog'>其他对话框</div>")
                with self.assertRaises(ManualDismissRequired):
                    browser_impl._close_visible_note_panel(page)
                self.assertEqual(page.get_by_text("复制小红书笔记链接", exact=True).count(), 1)
            finally:
                chromium.close()

    def test_failed_note_checkpoint_saved_before_manual_panel_close(self) -> None:
        class Locator:
            def __init__(self, count):
                self.value = count

            def count(self):
                return self.value

        class Page:
            def __init__(self, url):
                self.url = url

            def is_closed(self):
                return False

            def get_by_role(self, _role):
                return Locator(2)

            def locator(self, _selector):
                return Locator(1)

        page = Page("https://pgy.xiaohongshu.com/solar/pre-trade/note/kol")
        detail = Page("https://pgy.xiaohongshu.com/solar/pre-trade/blogger-detail/one")
        browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        browser._context = SimpleNamespace(pages=[page])
        browser._page = page
        browser._check_page_blocker = lambda *_args, **_kwargs: None
        browser._capture_list_rows = lambda _page: [{"creator_ref": "pgy:one", "visible_row_index": 1, "fields": {"创作者名称": "one"}}]
        browser._open_creator_row = lambda *_args, **_kwargs: (detail, True)
        browser._capture_creator_records = lambda *_args: [{"type": "creator_profile_sample", "fields": {"粉丝数": "1"}}]
        browser._open_notes_tab = lambda *_args: None
        def fail_copy(*_args):
            raise CaptureError("复制链接失败")
        browser._open_and_copy_note = fail_copy
        browser._close_owned_page = lambda *_args: None
        def wait_for_close(*_args):
            raise ManualDismissRequired("手动关闭", detail, kind="note", pgy=True)
        browser._close_visible_note_panel = wait_for_close
        checkpoints = []
        with self.assertRaises(ManualDismissRequired):
            browser._collect_visible(
                {"creator_count": 1, "collect_creator": True, "collect_notes": True, "collect_comments": False},
                {"creator_index": 0, "note_index": 0, "creator_targets": []},
                check_control=lambda: None, save_checkpoint=checkpoints.append,
                save_records=lambda *_args: None, save_comments=lambda *_args: {},
                report_progress=lambda *_args: None, report_failure=lambda *_args: None,
                report_note_success=lambda *_args: None, report_field_status=lambda *_args: None,
                existing_comment_count=0,
            )
        self.assertEqual(checkpoints[-1]["note_index"], 1)
        self.assertEqual(checkpoints[-1]["creator_index"], 0)

    def test_resume_selects_unique_matching_list_without_navigation(self) -> None:
        class Page:
            url = "https://pgy.xiaohongshu.com/solar/pre-trade/note/kol"

            def __init__(self, refs):
                self.refs = refs

            def is_closed(self):
                return False

        original = Page(["pgy:one", "pgy:two"])
        other = Page(["pgy:other"])
        browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        browser._context = SimpleNamespace(pages=[original, other])
        browser._page = other
        browser._capture_list_rows = lambda page: [{"creator_ref": ref} for ref in page.refs]
        targets = [{"kind": "creator_ref", "value": ref} for ref in original.refs]
        self.assertIs(browser._select_collection_page(targets), original)
        original.refs = ["pgy:wrong"]
        with self.assertRaises(PauseForUser):
            browser._select_collection_page(targets)
        original.refs = ["pgy:one", "pgy:two"]
        other.refs = list(original.refs)
        with self.assertRaises(PauseForUser):
            browser._select_collection_page(targets)

    def test_creator_checkpoint_resolves_reordered_visible_rows_by_stable_identity(self) -> None:
        browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        original = {
            "type": "blogger_list_sample",
            "creator_ref": "pgy:/solar/pre-trade/blogger-detail/creator-123",
            "visible_row_index": 1,
            "fields": {"创作者名称": "原目标创作者"},
        }
        other = {
            "type": "blogger_list_sample",
            "creator_ref": "pgy:/solar/pre-trade/blogger-detail/creator-456",
            "visible_row_index": 1,
            "fields": {"创作者名称": "另一位创作者"},
        }
        moved_target = {**original, "visible_row_index": 6}
        descriptor = browser._creator_target_descriptor(original)
        self.assertIsNotNone(descriptor)

        resolved = browser._resolve_creator_targets([other, moved_target], [descriptor])

        self.assertEqual(resolved[0]["fields"]["创作者名称"], "原目标创作者")
        self.assertEqual(resolved[0]["visible_row_index"], 6)

    def test_creator_checkpoint_pauses_when_visible_identity_is_missing_or_ambiguous(self) -> None:
        browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        target_record = {
            "creator_ref": "pgy:/solar/pre-trade/blogger-detail/creator-123",
            "fields": {"创作者名称": "目标创作者"},
        }
        descriptor = browser._creator_target_descriptor(target_record)
        self.assertIsNotNone(descriptor)
        duplicate = {**target_record}

        with self.assertRaisesRegex(PauseForUser, "无法唯一匹配"):
            browser._resolve_creator_targets([target_record, duplicate], [descriptor])
        with self.assertRaisesRegex(PauseForUser, "无法唯一匹配"):
            browser._resolve_creator_targets([], [descriptor])

    def test_legacy_checkpoint_with_saved_records_is_not_resumed_by_row_index(self) -> None:
        checkpoint = {"creator_index": 0, "note_index": 0, "comment_count": 0}
        normalized = TaskManager._ensure_stable_creator_checkpoint(checkpoint, 0)
        self.assertEqual(normalized["creator_targets"], [])

        with self.assertRaisesRegex(PauseForUser, "身份信息"):
            TaskManager._ensure_stable_creator_checkpoint(
                {"creator_index": 0, "note_index": 0, "comment_count": 0},
                1,
            )

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
            with connection(self.db_path) as conn:
                conn.execute("UPDATE tasks SET progress=100 WHERE task_id=?", (task_id,))
            self.assertTrue(manager.resume_task(task_id))
            self.assertEqual(self._read_task(task_id)["progress"], 0)
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
            self.assertIn("验证码或安全验证", task["message"])
            parameters = json.loads(task["parameters_json"])
            self.assertEqual(
                parameters["checkpoint"],
                {
                    "creator_index": 0,
                    "note_index": 0,
                    "comment_count": 0,
                    "creator_targets": [],
                },
            )
            with connection(self.db_path) as conn:
                batch = conn.execute(
                    "SELECT status, record_count FROM batches WHERE task_id=?", (task_id,)
                ).fetchone()
                self.assertEqual(tuple(batch), ("paused", 0))
        finally:
            manager.shutdown()
            self._wait_for_manager(manager)

    def test_manual_dismiss_prompt_auto_resumes_from_saved_checkpoint(self) -> None:
        browser = ManualDismissBrowser()
        manager = TaskManager(self.db_path, self.root / "unused-baseline.json", browser)
        try:
            task_id = manager.submit_visible_collection({"creator_count": 1})
            self.assertTrue(browser.waiting.wait(3))
            waiting = self._wait_for_status(task_id, {"waiting_user", "failed", "paused"})
            self.assertEqual(waiting["status"], "waiting_user", waiting["message"])
            self.assertIn("手动关闭", waiting["message"])
            self.assertEqual(browser.calls, 1)

            browser.dismissed.set()
            completed = self._wait_for_status(task_id, {"completed", "failed", "paused"})
            self.assertEqual(completed["status"], "completed", completed["message"])
            self.assertEqual(browser.calls, 2)
            self.assertIn("resumed after manual close", completed["message"])
        finally:
            manager.shutdown()
            self._wait_for_manager(manager)

    def test_manual_dismiss_wait_checks_the_overlay_until_it_is_hidden(self) -> None:
        class Locator:
            def __init__(self, page):
                self.page = page

            def count(self):
                return 1

            def nth(self, _index):
                return self

            def is_visible(self):
                return self.page.overlay_visible

        class Page:
            def __init__(self):
                self.overlay_visible = True
                self.selector = None

            def is_closed(self):
                return False

            def locator(self, selector):
                self.selector = selector
                return Locator(self)

            def wait_for_timeout(self, timeout):
                self.assert_wait = timeout
                self.overlay_visible = False

        page = Page()
        browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        browser._check_page_blocker = lambda _page, **_kwargs: None
        requirement = ManualDismissRequired(
            "close the popup", page, kind="overlay", pgy=True, selector="div.notice-mask"
        )
        control_checks = []

        browser._wait_for_manual_dismissal(requirement, check_control=lambda: control_checks.append(True))

        self.assertFalse(page.overlay_visible)
        self.assertEqual(page.selector, "div.notice-mask")
        self.assertEqual(page.assert_wait, 500)
        self.assertGreaterEqual(len(control_checks), 2)

    def test_captcha_page_blocker_remains_a_hard_pause(self) -> None:
        class BodyLocator:
            def inner_text(self, timeout):
                assert timeout == 5000
                return "请完成安全验证"

        class Page:
            def locator(self, selector):
                assert selector == "body"
                return BodyLocator()

        browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        with self.assertRaisesRegex(PauseForUser, "验证码或安全验证"):
            browser._check_page_blocker(Page(), pgy=True)

    def test_phone_login_sms_code_label_is_not_a_security_challenge(self) -> None:
        class BodyLocator:
            def inner_text(self, timeout):
                assert timeout == 5000
                return "手机号登录 请输入手机号 输入验证码 获取验证码 登录"

        class Page:
            def locator(self, selector):
                assert selector == "body"
                return BodyLocator()

        browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        browser._check_page_blocker(Page(), pgy=False)

    def test_waiting_for_manual_close_can_be_stopped(self) -> None:
        browser = ManualDismissBrowser()
        manager = TaskManager(self.db_path, self.root / "unused-baseline.json", browser)
        try:
            task_id = manager.submit_visible_collection({"creator_count": 1})
            self.assertTrue(browser.waiting.wait(3))
            waiting = self._wait_for_status(task_id, {"waiting_user", "failed", "paused"})
            self.assertEqual(waiting["status"], "waiting_user", waiting["message"])

            self.assertTrue(manager.stop_task(task_id))
            stopped = self._wait_for_status(task_id, {"stopped", "failed", "completed"})
            self.assertEqual(stopped["status"], "stopped", stopped["message"])
            self.assertEqual(browser.calls, 1)
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
