from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from creatorscope import create_app
from creatorscope.db import connection, initialize_database
from creatorscope.history_evidence import import_file
from creatorscope.unified_library import COURSE_SAMPLE_BATCH_ID, unified_creator_library


class UnifiedCreatorLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="creatorscope-unified-library-")
        self.root = Path(self.temp.name)
        self.main_db = self.root / "main.sqlite3"
        self.course_db = self.root / "course.sqlite3"
        self.history_db = self.root / "history.sqlite3"
        self._make_app_db(self.main_db, course=False)
        self._make_app_db(self.course_db, course=True)
        self._make_history_db()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _make_app_db(self, path: Path, *, course: bool) -> None:
        initialize_database(path)
        with connection(path) as conn:
            if course:
                entries = [
                    ("shared-course", "课程同 ID", "shared-id", "2026-11-23T14:38:36+08:00", {"粉丝数": "12000", "阅读中位数（日常）": "300", "互动中位数（日常）": "25", "图文笔记一口价": "¥1,000", "内容类目": "美妆"}),
                    ("course-complete", "完整课堂达人", "", "2026-11-23T14:38:36+08:00", {"粉丝数": "30000", "阅读中位数（日常）": "500", "互动中位数（日常）": "40", "获赞与收藏": "10000", "发布笔记数": "88", "图文笔记一口价": "¥1,200", "视频笔记一口价": "¥2,000", "内容类目": "旅行"}),
                ]
                batch_id = COURSE_SAMPLE_BATCH_ID
                source = "蒲公英与小红书公开可见页面"
            else:
                entries = [
                    ("new-creator", "新采集达人", "shared-id", "2026-11-24T09:00:00+08:00", {"粉丝数": "15000", "阅读中位数（日常）": "350", "互动中位数（日常）": "30", "内容类目": "美妆", "获赞与收藏": "5000", "发布笔记数": "44", "账号IP属地": "上海"}),
                    ("old-creator", "早期采集达人", "", "2026-11-20T09:00:00+08:00", {"粉丝数": "2000"}),
                ]
                batch_id = "batch-main"
                source = "蒲公英与小红书公开可见页面"
            for creator_id, name, platform_id, captured, fields in entries:
                record_batch_id = batch_id if course else f"{batch_id}-{creator_id}"
                task_id = f"task-{record_batch_id}"
                existing_batch = conn.execute("SELECT 1 FROM batches WHERE batch_id=?", (record_batch_id,)).fetchone()
                if not existing_batch:
                    conn.execute(
                        "INSERT INTO tasks(task_id,task_type,source,status,created_at) VALUES(?,?,?,?,?)",
                        (task_id, "visible_collection", source, "completed", captured),
                    )
                    conn.execute(
                        "INSERT INTO batches(batch_id,task_id,source,captured_at,status,record_count) VALUES(?,?,?,?,?,?)",
                        (record_batch_id, task_id, source, captured, "completed", len(entries) if course else 1),
                    )
                conn.execute(
                    "INSERT INTO creators(creator_id,pseudonym,creator_name,platform,platform_creator_id,location,source_key_hash,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (creator_id, f"internal-{creator_id}", name, "xiaohongshu", platform_id or None, "浙江 杭州", f"hash-{creator_id}", captured, captured),
                )
                conn.execute(
                    "INSERT INTO records(batch_id,record_type,source,captured_at,fields_json,fields_hash,creator_id) VALUES(?,?,?,?,?,?,?)",
                    (record_batch_id, "creator_profile_sample", source, captured, json.dumps(fields, ensure_ascii=False), f"record-{creator_id}", creator_id),
                )

    def _make_history_db(self) -> None:
        source = self.root / "history.json"
        source.write_text(json.dumps([
            {"creator_name": "完整课堂达人", "xhs_profile_id": "history-profile", "latest_known_followers": "7000", "阅读中位数（日常）": "120", "互动中位数（日常）": "8"},
            {"creator_name": "误入商品", "xhs_profile_url": "https://www.musinsa.com/products/6257477", "latest_known_followers": "16825067"},
        ], ensure_ascii=False), encoding="utf-8")
        import_file(self.history_db, source, "rednote_creator__历史索引")

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_unified_order_filters_products_and_preserves_separate_names(self) -> None:
        before = [self._hash(path) for path in (self.main_db, self.course_db, self.history_db)]
        page = unified_creator_library(self.main_db, self.course_db, self.history_db, limit=20)
        after = [self._hash(path) for path in (self.main_db, self.course_db, self.history_db)]

        self.assertEqual(before, after)
        self.assertEqual(page["total_count"], 4)
        self.assertEqual(page["all_group_counts"], {"recent": 1, "course": 1, "historical": 2})
        self.assertEqual(page["records"][0]["name"], "新采集达人")
        self.assertEqual(page["records"][0]["bucket"], "recent")
        course = [record for record in page["records"] if record["bucket"] == "course"]
        self.assertEqual([record["name"] for record in course], ["完整课堂达人"])
        self.assertNotIn("误入商品", [record["name"] for record in page["records"]])
        self.assertEqual(sum(record["name"] == "完整课堂达人" for record in page["records"]), 2)
        self.assertEqual(page["records"][0]["fields"]["image_fixed_price"], "¥1,000")
        self.assertEqual(page["records"][0]["fields"]["account_ip_location"], "上海")
        course_record = next(record for record in page["records"] if record["bucket"] == "course")
        self.assertEqual(course_record["fields"]["account_ip_location"], "")
        with connection(self.main_db, readonly=True) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE creators SET pseudonym='must-not-write' WHERE creator_id='new-creator'")

    def test_readonly_connection_does_not_create_missing_database(self) -> None:
        missing = self.root / "missing" / "must-not-exist.sqlite3"
        with self.assertRaises(FileNotFoundError):
            with connection(missing, readonly=True):
                pass
        self.assertFalse(missing.exists())

    def test_search_group_and_pagination_use_filtered_union(self) -> None:
        course = unified_creator_library(self.main_db, self.course_db, self.history_db, bucket="course", limit=10)
        self.assertEqual(course["total_count"], 1)
        search = unified_creator_library(self.main_db, self.course_db, self.history_db, query="history-profile", limit=10)
        self.assertEqual(search["total_count"], 1)
        self.assertEqual(search["records"][0]["bucket"], "historical")
        first = unified_creator_library(self.main_db, self.course_db, self.history_db, limit=2, offset=0)
        second = unified_creator_library(self.main_db, self.course_db, self.history_db, limit=2, offset=2)
        self.assertEqual(len(first["records"]), 2)
        self.assertEqual(len(second["records"]), 2)
        self.assertFalse({item["ref"] for item in first["records"]} & {item["ref"] for item in second["records"]})

    def test_api_reads_unified_library_from_isolated_sources(self) -> None:
        app = create_app({
            "TESTING": True,
            "DEMO_MODE": True,
            "DATABASE_PATH": self.main_db,
            "COURSE_SAMPLE_DATABASE": self.course_db,
            "HISTORY_EVIDENCE_DB": self.history_db,
            "AI_REPORTS_PATH": self.root / "ai-reports",
            "NEWS_DATA_PATH": self.root / "news.jsonl",
            "BASELINE_PATH": self.root / "missing-baseline.json",
        })
        try:
            response = app.test_client().get("/api/unified-creators?limit=10")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["total_count"], 4)
            self.assertEqual(payload["all_group_counts"]["course"], 1)
            self.assertIn("美妆", payload["categories"])
            self.assertIn("上海", payload["ip_locations"])
            self.assertEqual(app.test_client().get("/api/unified-creators?limit=201").status_code, 400)
            refs = [item["ref"] for item in payload["records"][:2]]
            compare_query = "&".join(f"ref={ref}" for ref in refs)
            compare = app.test_client().get(f"/api/unified-creators/compare?{compare_query}")
            self.assertEqual(compare.status_code, 200)
            self.assertEqual(compare.get_json()["count"], 2)
            self.assertEqual(
                app.test_client().get("/api/unified-creators/compare?ref=missing&ref=also-missing").status_code,
                400,
            )
            home = app.test_client().get("/").get_data(as_text=True)
            self.assertIn("统一达人清单", home)
            self.assertNotIn("历史达人库", home)
            self.assertNotIn('href="/report"', home)
            self.assertNotIn('href="/news"', home)
        finally:
            app.extensions["creatorscope_tasks"].shutdown()


if __name__ == "__main__":
    unittest.main()
