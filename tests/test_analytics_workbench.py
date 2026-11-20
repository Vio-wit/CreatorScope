from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from creatorscope.analytics import analytics_data, creator_library, notes_data, overview_data
from creatorscope.db import connection, initialize_database
from creatorscope import create_app
from research.r4_3_comment_analysis.comment_analysis import compare_word_frequencies


class AnalyticsWorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="creatorscope-analytics-")
        self.db_path = Path(self.temp_dir.name) / "analytics.sqlite3"
        initialize_database(self.db_path)
        self._seed()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _batch(self, conn, batch_id: str, source: str, captured: str) -> None:
        task_id = f"task-{batch_id}"
        conn.execute(
            "INSERT INTO tasks(task_id,task_type,source,status,progress,message,parameters_json,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (task_id, "file_import", source, "completed", 100, "fixture", "{}", captured, captured),
        )
        conn.execute(
            "INSERT INTO batches(batch_id,task_id,source,captured_at,started_at,completed_at,status,record_count,error_count) VALUES(?,?,?,?,?,?,?,?,?)",
            (batch_id, task_id, source, captured, captured, captured, "completed", 0, 0),
        )

    def _record(self, conn, batch_id: str, record_type: str, captured: str, creator_id: str, note_id: str | None, fields: dict[str, str], period: str = "") -> int:
        cursor = conn.execute(
            "INSERT INTO records(batch_id,record_type,captured_at,fields_json,fields_hash,creator_id,note_id) VALUES(?,?,?,?,?,?,?)",
            (batch_id, record_type, captured, json.dumps(fields, ensure_ascii=False), f"{batch_id}-{record_type}-{creator_id}-{note_id or 'none'}", creator_id, note_id),
        )
        record_id = int(cursor.lastrowid)
        for key, raw in fields.items():
            value = float(raw) if raw.replace(".", "", 1).isdigit() else None
            conn.execute(
                "INSERT INTO metric_values(record_id,metric_key,raw_value,numeric_value,unit,period,parse_status) VALUES(?,?,?,?,?,?,?)",
                (record_id, key, raw, value, "count" if value is not None else "", period, "parsed" if value is not None else "unparsed"),
            )
        return record_id

    def _seed(self) -> None:
        with connection(self.db_path) as conn:
            for creator_id, pseudonym, location in (
                ("creator-a", "匿名创作者 A", "上海"),
                ("creator-b", "匿名创作者 B", "杭州"),
            ):
                conn.execute(
                    "INSERT INTO creators(creator_id,pseudonym,creator_name,platform,location,source_key_hash,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)",
                    (creator_id, pseudonym, "", "xiaohongshu", location, f"hash-{creator_id}", "2026-11-01", "2026-11-02"),
                )
            for batch_id, source, captured in (
                ("batch-a", "pgy_visible", "2026-11-01T10:00:00"),
                ("batch-b", "pgy_visible", "2026-11-02T10:00:00"),
                ("batch-c", "xhs_import", "2026-11-03T10:00:00"),
                ("batch-d", "xhs_import", "2026-11-04T10:00:00"),
            ):
                self._batch(conn, batch_id, source, captured)
            conn.execute(
                "INSERT INTO notes(note_id,creator_id,note_key_hash,title,body,topics_json,published_at,note_type,source,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("note-a", "creator-a", "note-hash-a", "上海护肤分享", "", json.dumps(["#护肤", "#日常"], ensure_ascii=False), "2026-11-01", "视频", "pgy_visible", "2026-11-01", "2026-11-02"),
            )
            conn.execute(
                "INSERT INTO notes(note_id,creator_id,note_key_hash,title,body,topics_json,published_at,note_type,source,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("note-b", "creator-b", "note-hash-b", "旅行记录", "", json.dumps(["#旅行"], ensure_ascii=False), "2026-11-12", "图文", "xhs_import", "2026-11-03", "2026-11-03"),
            )
            self._record(conn, "batch-a", "creator_profile_sample", "2026-11-01T10:00:00", "creator-a", None, {"粉丝数": "100", "阅读中位数": "20", "互动中位数": "5", "全部报价": "500", "内容类目": "美妆"}, "当前粉丝快照")
            self._record(conn, "batch-a", "creator_profile_sample", "2026-11-01T10:00:00", "creator-b", None, {"粉丝数": "200", "阅读中位数": "35", "互动中位数": "9", "全部报价": "800", "内容类目": "旅行"}, "当前粉丝快照")
            self._record(conn, "batch-b", "creator_profile_sample", "2026-11-02T10:00:00", "creator-a", None, {"粉丝数": "120", "阅读中位数": "25", "互动中位数": "6", "全部报价": "600", "内容类目": "美妆"}, "当前粉丝快照")
            self._record(conn, "batch-c", "creator_profile_sample", "2026-11-03T10:00:00", "creator-a", None, {"粉丝数": "140", "阅读中位数": "27", "互动中位数": "7", "全部报价": "650", "内容类目": "美妆"}, "当前粉丝快照")
            self._record(conn, "batch-d", "creator_profile_sample", "2026-11-04T10:00:00", "creator-a", None, {"粉丝数": "150", "阅读中位数": "30", "互动中位数": "8", "全部报价": "700", "内容类目": "美妆"}, "当前粉丝快照")
            self._record(conn, "batch-a", "note_detail", "2026-11-01T10:10:00", "creator-a", "note-a", {"阅读量": "1000", "点赞量": "100", "收藏量": "20", "评论量": "10", "分享量": "5"}, "累计")
            self._record(conn, "batch-b", "note_detail", "2026-11-02T10:10:00", "creator-a", "note-a", {"阅读量": "1200", "点赞量": "120", "收藏量": "25", "评论量": "12", "分享量": "6"}, "累计")
            self._record(conn, "batch-c", "note_detail", "2026-11-03T10:10:00", "creator-b", "note-b", {"阅读量": "500", "点赞量": "50"}, "累计")
            for batch_id, note_id, text in (
                ("batch-a", "note-a", "好用 温和 保湿"),
                ("batch-a", "note-b", "旅行 风景"),
                ("batch-b", "note-a", "好用 温和 轻薄"),
            ):
                conn.execute(
                    "INSERT INTO comment_snapshots(batch_id,note_id,local_comment_id_hash,comment_text,cleaned_text,comment_level,source,captured_at,raw_payload_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (batch_id, note_id, f"comment-{batch_id}-{note_id}", text, text, 1, "visible_public", "2026-11-03T12:00:00", "{}"),
                )

    def test_overview_creator_filters_and_all_batch_dashboard(self) -> None:
        overview = overview_data(self.db_path)
        self.assertEqual(overview["counts"], {"creators": 2, "notes": 2, "comments": 3, "batches": 4})
        self.assertEqual(sum(source["total_count"] for source in overview["sources"]), 11)
        creators = creator_library(self.db_path, location="上海", batch_id="batch-b", sort_by="followers")
        self.assertEqual([item["creator_id"] for item in creators["creators"]], ["creator-a"])
        dashboard = create_app({"DATABASE_PATH": self.db_path, "BASELINE_PATH": self.db_path.parent / "missing.json"}).test_client().get("/api/dashboard?batch_id=__all__")
        self.assertEqual(dashboard.status_code, 200)
        self.assertTrue(dashboard.get_json()["all_batches"])
        self.assertEqual(dashboard.get_json()["counts"]["records"], 8)

    def test_note_filters_rankings_and_strict_metric_history(self) -> None:
        result = notes_data(self.db_path, topic="#护肤", date_from="2026-11-01", date_to="2026-11-05", note_type="视频", batch_id="batch-b")
        self.assertEqual(result["count"], 1)
        note = result["notes"][0]
        self.assertEqual(note["note_id"], "note-a")
        self.assertEqual(note["read_count"], 1200)
        self.assertEqual(note["comment_count"], 1)
        self.assertEqual(len(result["charts"]["metric_trends"]), 5)
        self.assertTrue(all("pgy_visible" in series["label"] and "累计" in series["label"] for series in result["charts"]["metric_trends"]))
        self.assertEqual(notes_data(self.db_path, topic="#护肤", date_from="2026-11-13")["count"], 0)

    def test_trend_requires_same_source_period_and_repeated_creator(self) -> None:
        result = analytics_data(self.db_path)
        groups = result["charts"]["batch_trend"]
        self.assertEqual({group["source"] for group in groups}, {"pgy_visible", "xhs_import"})
        pgy = next(group for group in groups if group["source"] == "pgy_visible")
        self.assertEqual(pgy["period"], "当前粉丝快照")
        self.assertEqual([point["median_followers"] for point in pgy["points"]], [100, 120])
        self.assertEqual([point["creator_count"] for point in pgy["points"]], [1, 1])
        self.assertEqual(analytics_data(self.db_path, batch_id="batch-a")["charts"]["batch_trend"], [])

    def test_comment_word_change_only_uses_common_notes(self) -> None:
        baseline = [
            {"note_id": "note-a", "source": "visible_public", "cleaned_text": "好用 温和 保湿"},
            {"note_id": "note-b", "source": "visible_public", "cleaned_text": "旅行 风景"},
        ]
        comparison = [
            {"note_id": "note-a", "source": "visible_public", "cleaned_text": "好用 温和 轻薄"},
            {"note_id": "note-other", "source": "visible_public", "cleaned_text": "噪声"},
        ]
        result = compare_word_frequencies(baseline, comparison, stopwords=set())
        self.assertEqual(result["common_note_count"], 1)
        self.assertEqual(result["baseline_comment_count"], 1)
        self.assertEqual(result["comparison_comment_count"], 1)
        self.assertIn("轻薄", {item["word"] for item in result["words"]})

    def test_notes_api_and_export_apply_filters(self) -> None:
        app = create_app({"DATABASE_PATH": self.db_path, "BASELINE_PATH": self.db_path.parent / "missing.json"})
        client = app.test_client()
        response = client.get("/api/notes?topic=%23%E6%8A%A4%E8%82%A4&creator_id=creator-a&batch_id=batch-b")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 1)
        exported = client.get("/api/notes/export.csv?topic=%23%E6%8A%A4%E8%82%A4&batch_id=batch-b")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("上海护肤分享", exported.get_data(as_text=True))
        self.assertNotIn("旅行记录", exported.get_data(as_text=True))
        compared = client.get("/api/creators/compare?creator_id=creator-a&creator_id=creator-b&batch_id=batch-a")
        self.assertEqual(compared.status_code, 200)
        self.assertEqual(compared.get_json()["count"], 2)
        rejected = client.get("/api/creators/compare?creator_id=creator-a&creator_id=creator-b&batch_id=__all__")
        self.assertEqual(rejected.status_code, 400)
        word_change = client.get("/api/comments/word-change?baseline_batch_id=batch-a&comparison_batch_id=batch-b")
        self.assertEqual(word_change.status_code, 200)
        self.assertEqual(word_change.get_json()["common_note_count"], 1)
        self.assertGreater(len(word_change.get_json()["words"]), 0)

    def test_per_record_source_and_raw_cleaned_and_statistics_exports(self) -> None:
        with connection(self.db_path) as conn:
            note_record = conn.execute(
                "SELECT record_id FROM records WHERE batch_id='batch-b' AND record_type='note_detail'"
            ).fetchone()[0]
            conn.execute(
                "UPDATE records SET source='pgy_note_detail_source' WHERE record_id=?",
                (note_record,),
            )
            conn.execute(
                "UPDATE metric_values SET raw_value='—', numeric_value=NULL, parse_status='displayed_unavailable' WHERE record_id=? AND metric_key='收藏量'",
                (note_record,),
            )

        overview = overview_data(self.db_path)
        source_counts = {item["source"]: item["record_count"] for item in overview["sources"]}
        self.assertEqual(source_counts["pgy_note_detail_source"], 1)
        note = next(
            item for item in notes_data(self.db_path, batch_id="batch-b")["notes"]
            if item["note_id"] == "note-a"
        )
        self.assertEqual(note["source"], "pgy_note_detail_source")

        client = create_app({
            "DATABASE_PATH": self.db_path,
            "BASELINE_PATH": self.db_path.parent / "missing.json",
            "TESTING": True,
        }).test_client()
        raw_json = client.get("/api/export/json?batch_id=batch-b")
        self.assertEqual(raw_json.status_code, 200)
        raw_records = raw_json.get_json()["records"]
        raw_note = next(row for row in raw_records if row["type"] == "note_detail")
        self.assertEqual(raw_note["batch_id"], "batch-b")
        self.assertEqual(raw_note["source"], "pgy_note_detail_source")
        self.assertEqual(raw_note["fields"]["阅读量"], "1200")

        raw_csv = client.get("/api/export/csv?batch_id=batch-b")
        self.assertEqual(raw_csv.status_code, 200)
        csv_rows = list(csv.DictReader(raw_csv.data.decode("utf-8-sig").splitlines()))
        csv_note = next(row for row in csv_rows if row["record_type"] == "note_detail")
        self.assertEqual(csv_note["source"], "pgy_note_detail_source")

        metrics_json = client.get("/api/export/metrics.json?batch_id=batch-b")
        self.assertEqual(metrics_json.status_code, 200)
        metrics = metrics_json.get_json()["metrics"]
        reading = next(row for row in metrics if row["record_id"] == note_record and row["metric_key"] == "阅读量")
        self.assertEqual(reading["raw_value"], "1200")
        self.assertEqual(reading["normalized_value"], 1200)
        self.assertEqual(reading["unit"], "count")
        self.assertEqual(reading["period"], "累计")
        self.assertEqual(reading["source"], "pgy_note_detail_source")
        self.assertEqual(reading["missing_status"], "present")
        unavailable = next(row for row in metrics if row["record_id"] == note_record and row["metric_key"] == "收藏量")
        self.assertIsNone(unavailable["normalized_value"])
        self.assertEqual(unavailable["parse_status"], "displayed_unavailable")
        self.assertEqual(unavailable["missing_status"], "missing")

        metrics_csv = client.get("/api/export/metrics.csv?batch_id=batch-b")
        self.assertEqual(metrics_csv.status_code, 200)
        metric_csv_rows = list(csv.DictReader(metrics_csv.data.decode("utf-8-sig").splitlines()))
        csv_reading = next(row for row in metric_csv_rows if row["record_id"] == str(note_record) and row["metric_key"] == "阅读量")
        self.assertEqual(csv_reading["source"], "pgy_note_detail_source")
        self.assertEqual(csv_reading["normalized_value"], "1200.0")
        self.assertEqual(csv_reading["period"], "累计")

        api_statistics = client.get("/api/analytics?batch_id=batch-b").get_json()
        exported_statistics = client.get("/api/export/analytics.json?batch_id=batch-b")
        self.assertEqual(exported_statistics.status_code, 200)
        self.assertEqual(exported_statistics.get_json()["statistics"], api_statistics)


if __name__ == "__main__":
    unittest.main()
