from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from creatorscope.analytics import analytics_data, compare_creators, compare_notes, creator_library, note_compare_options, notes_data, overview_data
from creatorscope.db import connection, initialize_database
from creatorscope import create_app
from research.r4_3_comment_analysis.comment_analysis import compare_word_frequencies, summarize


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
            for creator_id, pseudonym, creator_name, platform_id, location, category in (
                ("creator-a", "博主 0001", "Creator A", "platform-creator-a", "上海", "美妆"),
                ("creator-b", "博主 0002", "Creator B", "platform-creator-b", "杭州", "旅行"),
            ):
                conn.execute(
                    "INSERT INTO creators(creator_id,pseudonym,creator_name,platform,platform_creator_id,location,category,source_key_hash,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (creator_id, pseudonym, creator_name, "xiaohongshu", platform_id, location, category, f"hash-{creator_id}", "2026-11-01", "2026-11-02"),
                )
            for batch_id, source, captured in (
                ("batch-a", "pgy_visible", "2026-11-01T10:00:00"),
                ("batch-b", "pgy_visible", "2026-11-02T10:00:00"),
                ("batch-c", "xhs_import", "2026-11-03T10:00:00"),
                ("batch-d", "xhs_import", "2026-11-04T10:00:00"),
            ):
                self._batch(conn, batch_id, source, captured)
            conn.execute(
                "INSERT INTO notes(note_id,creator_id,note_key_hash,platform_note_id,title,body,topics_json,published_at,note_type,source_url,source,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("note-a", "creator-a", "note-hash-a", "platform-note-a", "上海护肤分享", "raw note body", json.dumps(["#护肤", "#日常"], ensure_ascii=False), "2026-11-01", "视频", "https://example.test/note-a", "pgy_visible", "2026-11-01", "2026-11-02"),
            )
            conn.execute(
                "INSERT INTO notes(note_id,creator_id,note_key_hash,platform_note_id,title,body,topics_json,published_at,note_type,source_url,source,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("note-b", "creator-b", "note-hash-b", "platform-note-b", "旅行记录", "raw travel body", json.dumps(["#旅行"], ensure_ascii=False), "2026-11-12", "图文", "https://example.test/note-b", "xhs_import", "2026-11-03", "2026-11-03"),
            )
            self._record(conn, "batch-a", "creator_profile_sample", "2026-11-01T10:00:00", "creator-a", None, {"粉丝数": "100", "阅读中位数": "20", "互动中位数": "5", "全部报价": "500", "内容类目": "美妆"}, "当前粉丝快照")
            self._record(conn, "batch-a", "creator_profile_sample", "2026-11-01T10:00:00", "creator-b", None, {"粉丝数": "200", "阅读中位数": "35", "互动中位数": "9", "全部报价": "800", "内容类目": "旅行"}, "当前粉丝快照")
            self._record(conn, "batch-b", "creator_profile_sample", "2026-11-02T10:00:00", "creator-a", None, {"粉丝数": "120", "阅读中位数": "25", "互动中位数": "6", "全部报价": "600", "内容类目": "美妆"}, "当前粉丝快照")
            self._record(conn, "batch-c", "creator_profile_sample", "2026-11-03T10:00:00", "creator-a", None, {"粉丝数": "140", "阅读中位数": "27", "互动中位数": "7", "全部报价": "650", "内容类目": "美妆"}, "当前粉丝快照")
            self._record(conn, "batch-d", "creator_profile_sample", "2026-11-04T10:00:00", "creator-a", None, {"粉丝数": "150", "阅读中位数": "30", "互动中位数": "8", "全部报价": "700", "内容类目": "美妆"}, "当前粉丝快照")
            self._record(conn, "batch-a", "note_detail", "2026-11-01T10:10:00", "creator-a", "note-a", {"阅读量": "1000", "点赞量": "100", "收藏量": "20", "评论量": "10", "分享量": "5"}, "累计")
            self._record(conn, "batch-b", "note_detail", "2026-11-02T10:10:00", "creator-a", "note-a", {"阅读量": "1200", "点赞量": "120", "收藏量": "25", "评论量": "12", "分享量": "6"}, "累计")
            self._record(conn, "batch-c", "xhs_public_note", "2026-11-03T10:10:00", "creator-b", "note-b", {"阅读量": "500", "点赞量": "50"}, "累计")
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
        self.assertEqual(creators["creators"][0]["creator_name"], "Creator A")
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
        self.assertTrue(all(series["source"] == "pgy_visible" and series["metric_key"] for series in result["charts"]["metric_trends"]))
        self.assertEqual(notes_data(self.db_path, topic="#护肤", date_from="2026-11-13")["count"], 0)

    def test_note_analysis_uses_note_bodies_and_separates_source_distributions(self) -> None:
        all_notes = notes_data(self.db_path)
        self.assertEqual(all_notes["count"], 2)
        self.assertEqual(all_notes["text_analysis"]["body"]["sample_count"], 2)
        self.assertEqual({item["creator_name"] for item in all_notes["note_options"]}, {"Creator A", "Creator B"})
        self.assertIn("raw", {item["word"] for item in all_notes["text_analysis"]["body"]["frequencies"]})
        self.assertIn("#护肤", {item["word"] for item in all_notes["text_analysis"]["topics"]["frequencies"]})
        self.assertTrue(all("sample_count" in item for item in all_notes["charts"]["metric_distributions"]))
        self.assertTrue(all(item["source"] in {"pgy_visible", "xhs_import"} for item in all_notes["charts"]["metric_distributions"]))
        pgy_notes = notes_data(self.db_path, source="pgy_visible", category="美妆", query="护肤")
        self.assertEqual(pgy_notes["count"], 1)
        self.assertEqual({item["source"] for item in pgy_notes["charts"]["top_read"]}, {"pgy_visible"})
        self.assertEqual({row["note_id"] for row in notes_data(self.db_path, note_id="note-b")["notes"]}, {"note-b"})

        word_frequency = analytics_data(self.db_path, source="pgy_visible", creator_id="creator-a", word_kind="body")
        words = {item["word"] for item in word_frequency["charts"]["word_frequency"]}
        self.assertIn("raw", words)
        self.assertGreater(word_frequency["charts"]["follower_price_sample_count"], 0)
        self.assertEqual(word_frequency["counts"]["notes"], 1)
        self.assertEqual(word_frequency["charts"]["note_metrics"][0]["note_id"], "note-a")
        public_note_metrics = analytics_data(self.db_path, source="xhs_import")
        self.assertIn("note-b", {item["note_id"] for item in public_note_metrics["charts"]["note_metrics"]})

    def test_audience_profiles_keep_raw_categories_and_group_by_source_and_period(self) -> None:
        profile_fields = {
            "gender_share": {"female": "92.98%", "male": "7.01%"},
            "age_share": {"18_24": "52.1%", "25_34": "35.0%"},
            "top_provinces": [{"name": "广东", "share": "14.3%"}, {"name": "江苏", "share": "7.9%"}],
            "top_interests": ["生活记录", "影视"],
            "device_brand_share_examples": {"Apple": "54.95%", "Huawei": "13.66%"},
            "active_share_raw": "82.5%",
            "period": "近30日",
        }
        with connection(self.db_path) as conn:
            for creator_id, captured_at, suffix in (
                ("creator-a", "2026-11-01T10:20:00", "a"),
                ("creator-b", "2026-11-01T10:21:00", "b"),
            ):
                payload = {**profile_fields, "_source_row": 1 if suffix == "a" else 2}
                conn.execute(
                    "INSERT INTO records(batch_id,record_type,captured_at,fields_json,fields_hash,creator_id,source) VALUES(?,?,?,?,?,?,?)",
                    ("batch-a", "fan_analysis", captured_at, json.dumps(payload, ensure_ascii=False), f"fan-{suffix}", creator_id, ""),
                )

        result = analytics_data(self.db_path, source="pgy_visible")
        groups = result["charts"]["audience_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["creator_count"], 2)
        first = groups[0]["profiles"][0]
        self.assertEqual(first["period"], "近30日")
        self.assertEqual(first["gender_distribution"][0]["raw_value"], "92.98%")
        self.assertEqual(first["gender_distribution"][0]["label"], "女性")
        self.assertEqual(first["age_distribution"][0]["label"], "18–24")
        self.assertEqual(first["regions"][0]["label"], "广东")
        self.assertEqual(first["devices"][0]["raw_value"], "54.95%")
        self.assertEqual(result["availability"]["audience"], "2 条真实画像快照；按来源与统计周期分组")

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

    def test_comment_length_and_reply_charts_use_raw_and_exact_values(self) -> None:
        rows = [
            {"comment_text": " 赞 " , "cleaned_text": "赞", "like_count_normalized": 2, "reply_count_raw": "0", "comment_level": 1, "is_pinned": False, "note_id": "note-a", "note_title": "标题", "creator_id": "creator-a", "creator_name": "公开名称"},
            {"comment_text": "很喜欢", "cleaned_text": "很喜欢", "like_count_normalized": None, "reply_count_raw": "2", "comment_level": 1, "is_pinned": False, "note_id": "note-a", "note_title": "标题", "creator_id": "creator-a", "creator_name": "公开名称"},
            {"comment_text": "一般", "cleaned_text": "一般", "like_count_normalized": None, "reply_count_raw": "10+", "comment_level": 2, "is_pinned": None, "note_id": "note-a", "note_title": "标题", "creator_id": "creator-a", "creator_name": "公开名称"},
        ]
        result = summarize(rows, stopwords=set())
        self.assertEqual(result["summary"]["length_count"], 3)
        self.assertEqual(result["summary"]["average_length"], 2.7)
        self.assertEqual(result["length_boxplot"]["sample_count"], 3)
        self.assertEqual(result["length_boxplot"]["values"][0], 2)
        self.assertEqual(result["length_boxplot"]["values"][4], 3)
        self.assertEqual(result["reply_histogram"], [{"label": "0", "count": 1}, {"label": "2", "count": 1}])
        self.assertEqual(result["reply_histogram_sample_count"], 2)
        self.assertEqual(result["reply_histogram_excluded_count"], 1)

    def test_notes_api_and_export_apply_filters(self) -> None:
        app = create_app({"DATABASE_PATH": self.db_path, "BASELINE_PATH": self.db_path.parent / "missing.json"})
        client = app.test_client()
        response = client.get("/api/notes?topic=%23%E6%8A%A4%E8%82%A4&creator_id=creator-a&batch_id=batch-b")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 1)
        note_filtered = client.get("/api/notes?note_id=note-b")
        self.assertEqual(note_filtered.status_code, 200)
        self.assertEqual(note_filtered.get_json()["count"], 1)
        exported = client.get("/api/notes/export.csv?topic=%23%E6%8A%A4%E8%82%A4&batch_id=batch-b")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("上海护肤分享", exported.get_data(as_text=True))
        self.assertIn("raw note body", exported.get_data(as_text=True))
        self.assertIn("platform-note-a", exported.get_data(as_text=True))
        self.assertIn("https://example.test/note-a", exported.get_data(as_text=True))
        self.assertIn("platform-creator-a", exported.get_data(as_text=True))
        self.assertNotIn("旅行记录", exported.get_data(as_text=True))
        exported_rows = list(csv.DictReader(exported.data.decode("utf-8-sig").splitlines()))
        exported_note = next(row for row in exported_rows if row["platform_note_id"] == "platform-note-a")
        metric_payload = json.loads(exported_note["metrics"])
        self.assertEqual(metric_payload["阅读量"]["raw_value"], "1200")
        self.assertEqual(metric_payload["阅读量"]["parse_status"], "parsed")
        cloud = client.get("/api/wordcloud.png?kind=body&source=pgy_visible&category=%E7%BE%8E%E5%A6%86")
        self.assertEqual(cloud.status_code, 200)
        self.assertTrue(cloud.data.startswith(b"\x89PNG\r\n\x1a\n"))
        compared = client.get("/api/creators/compare?creator_id=creator-a&creator_id=creator-b&batch_id=batch-a")
        self.assertEqual(compared.status_code, 200)
        self.assertEqual(compared.get_json()["count"], 2)
        self.assertEqual(compared.get_json()["availability"]["comparison_scope"], "精确 creator_id；单一批次；来源按筛选值约束")
        self.assertEqual(next(row for row in compared.get_json()["creators"] if row["creator_id"] == "creator-a")["publish_frequency_30d"], 1)
        rejected = client.get("/api/creators/compare?creator_id=creator-a&creator_id=creator-b&batch_id=__all__")
        self.assertEqual(rejected.status_code, 400)
        word_change = client.get("/api/comments/word-change?baseline_batch_id=batch-a&comparison_batch_id=batch-b")
        self.assertEqual(word_change.status_code, 200)
        self.assertEqual(word_change.get_json()["common_note_count"], 1)
        self.assertGreater(len(word_change.get_json()["words"]), 0)

    def test_note_comparison_uses_exact_ids_single_batch_and_raw_values(self) -> None:
        with connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO notes(note_id,creator_id,note_key_hash,platform_note_id,title,body,topics_json,published_at,note_type,source_url,source,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("note-c", "creator-a", "note-hash-c", "platform-note-c", "原始笔记标题 C", "原始正文 C", "[]", "2026-10-20", "图文", "https://example.test/note-c", "pgy_visible", "2026-11-01", "2026-11-01"),
            )
            self._record(conn, "batch-a", "note_detail", "2026-11-01T10:12:00", "creator-a", "note-c", {"阅读量": "500", "点赞量": "20", "收藏量": "10", "评论量": "5", "分享量": "2"}, "累计")

        options = note_compare_options(self.db_path, batch_id="batch-a", source="pgy_visible")
        self.assertEqual({item["note_id"] for item in options}, {"note-a", "note-c"})
        direct = compare_notes(self.db_path, ["note-a", "note-c"], batch_id="batch-a", source="pgy_visible")
        self.assertEqual(direct["count"], 2)
        self.assertEqual(direct["source"], "pgy_visible")
        by_id = {item["note_id"]: item for item in direct["notes"]}
        self.assertEqual(by_id["note-c"]["title"], "原始笔记标题 C")
        self.assertEqual(by_id["note-c"]["body"], "原始正文 C")
        self.assertEqual(by_id["note-a"]["interaction_rate"], 13.5)
        self.assertEqual(by_id["note-c"]["interaction_rate"], 7.4)
        self.assertEqual(by_id["note-c"]["publish_frequency_30d"], 2)

        client = create_app({"DATABASE_PATH": self.db_path, "BASELINE_PATH": self.db_path.parent / "missing.json", "TESTING": True}).test_client()
        response = client.get("/api/notes/compare?note_id=note-a&note_id=note-c&batch_id=batch-a&source=pgy_visible")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual({item["note_id"] for item in payload["notes"]}, {"note-a", "note-c"})
        self.assertEqual(payload["availability"]["saved_comments"], "另列本地保存的评论行数，不与页面评论指标混为一项")
        all_batches = client.get("/api/notes/compare?note_id=note-a&note_id=note-c&batch_id=__all__")
        self.assertEqual(all_batches.status_code, 400)
        with self.assertRaisesRegex(ValueError, "至少需要 2 篇"):
            compare_notes(self.db_path, ["note-a", "note-b"], batch_id="batch-a", source="pgy_visible")

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
        self.assertEqual(raw_note["creator_name"], "Creator A")
        self.assertEqual(raw_note["platform_creator_id"], "platform-creator-a")
        self.assertEqual(raw_note["platform_note_id"], "platform-note-a")
        self.assertEqual(raw_note["source_url"], "https://example.test/note-a")
        self.assertEqual(raw_note["local_creator_id"], "creator-a")
        self.assertEqual(raw_note["local_note_id"], "note-a")

        raw_csv = client.get("/api/export/csv?batch_id=batch-b")
        self.assertEqual(raw_csv.status_code, 200)
        csv_rows = list(csv.DictReader(raw_csv.data.decode("utf-8-sig").splitlines()))
        csv_note = next(row for row in csv_rows if row["record_type"] == "note_detail")
        self.assertEqual(csv_note["source"], "pgy_note_detail_source")
        self.assertEqual(csv_note["creator_name"], "Creator A")
        self.assertEqual(csv_note["platform_creator_id"], "platform-creator-a")
        self.assertEqual(csv_note["platform_note_id"], "platform-note-a")
        self.assertEqual(csv_note["source_url"], "https://example.test/note-a")

        metrics_json = client.get("/api/export/metrics.json?batch_id=batch-b")
        self.assertEqual(metrics_json.status_code, 200)
        metrics = metrics_json.get_json()["metrics"]
        reading = next(row for row in metrics if row["record_id"] == note_record and row["metric_key"] == "阅读量")
        self.assertEqual(reading["raw_value"], "1200")
        self.assertEqual(reading["normalized_value"], 1200)
        self.assertEqual(reading["unit"], "count")
        self.assertEqual(reading["period"], "累计")
        self.assertEqual(reading["source"], "pgy_note_detail_source")
        self.assertEqual(reading["creator_name"], "Creator A")
        self.assertEqual(reading["platform_creator_id"], "platform-creator-a")
        self.assertEqual(reading["platform_note_id"], "platform-note-a")
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
