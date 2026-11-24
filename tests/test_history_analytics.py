from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from creatorscope import create_app
from creatorscope.history_analytics import (
    HistoryAnalyticsError,
    NOTE_SAMPLE_SHA256,
    analysis_data,
    history_creator_library,
    history_note_library,
    list_library_sources,
    list_sources,
    note_samples,
    _connection,
)
from creatorscope.history_evidence import MARKER, SCHEMA
from creatorscope.history_evidence import import_file


class HistoryAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="creatorscope-history-analytics-")
        self.root = Path(self.temp.name)
        self.history_db = self.root / "history.sqlite3"
        self._build_history_database()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _build_history_database(self) -> None:
        conn = sqlite3.connect(self.history_db)
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO metadata VALUES ('application', ?)", (MARKER,))

        def add_source(source_id: str, name: str, digest: str, count: int = 1) -> None:
            conn.execute(
                "INSERT INTO sources (source_id,sha256,format,source_name,first_imported_at,record_count,parsed_count,error_count) VALUES (?,?,?,?,?,?,?,0)",
                (source_id, digest, "csv", name, "2026-11-23T00:00:00+00:00", count, count),
            )

        def add_record(source_id: str, record_id: str, row_number: int, fields: dict[str, tuple[float | None, str | None, str]]) -> None:
            conn.execute(
                "INSERT INTO source_records (record_id,source_id,row_number,raw_json,raw_text,row_digest,duplicate_content,identity_status,capture_time,parse_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (record_id, source_id, row_number, "{}", None, f"digest-{record_id}", 0, "pending", None, "parsed"),
            )
            for target, (value, unit, status) in fields.items():
                conn.execute(
                    "INSERT INTO field_evidence (record_id,original_field,target_field,raw_json,numeric_value,text_value,unit,period,parse_status,mapping_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (record_id, target, target, json.dumps(value), value, None, unit, None, status, "test"),
                )

        add_source("ept", "EPT", "e" * 64, 3)
        add_record("ept", "ept-1", 1, {
            "followers": (0, "count", "parsed"),
            "daily_read_median": (100, "count", "parsed"),
            "daily_interaction_median": (10, "count", "parsed"),
        })
        add_record("ept", "ept-2", 2, {
            "followers": (10_000, "count", "parsed"),
            "daily_read_median": (300, "count", "parsed"),
            "daily_interaction_median": (30, "count", "parsed"),
        })
        add_record("ept", "ept-3", 3, {
            "followers": (50, None, "parsed"),
            "daily_read_median": (0, "count", "parsed"),
            "daily_interaction_median": (5, "count", "unparsed"),
        })
        add_source("musinsa", "MUSINSA", "m" * 64)
        add_record("musinsa", "musinsa-1", 1, {
            "followers": (500, "count", "parsed"),
            "daily_read_median": (50, "count", "parsed"),
            "daily_interaction_median": (5, "count", "parsed"),
        })
        add_source("detail-1", "达人详情样本01", "d" * 64)
        add_record("detail-1", "detail-1-row", 1, {"followers": (900, "count", "parsed")})
        add_source("detail-2", "达人详情样本02", "f" * 64)
        add_record("detail-2", "detail-2-row", 1, {"followers": (1200, "count", "parsed")})

        for index, digest in enumerate(NOTE_SAMPLE_SHA256, 1):
            source_id = f"note-source-{index}"
            add_source(source_id, f"rednote_note__笔记文本样本{index:02d}", digest)
            conn.execute(
                "INSERT INTO source_records (record_id,source_id,row_number,raw_json,raw_text,row_digest,duplicate_content,identity_status,capture_time,parse_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"note-{index}", source_id, 1, "{}", f"URL: https://example.test/{index}\n正文摘录", f"note-digest-{index}", 0, "unassigned", None, "parsed"),
            )
            values = {
                "note_title": (f"展示标题 {index}", "text"),
                "note_body_candidate": (f"正文内容 {index} [话题]", "text"),
                "note_topics_candidate": (
                    str(["课程展示", "[话题]", f"话题{index}"]) if index == 2
                    else json.dumps(["课程展示", "[话题]", f"话题{index}"], ensure_ascii=False),
                    "text",
                ),
            }
            for target, (value, unit) in values.items():
                conn.execute(
                    "INSERT INTO field_evidence (record_id,original_field,target_field,raw_json,numeric_value,text_value,unit,period,parse_status,mapping_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (f"note-{index}", target, target, json.dumps(value, ensure_ascii=False), None, value, unit, None, "text", "test"),
                )
        conn.commit()
        conn.close()

    def test_sources_and_analysis_keep_datasets_separate_and_zero_valid(self) -> None:
        options = list_sources(self.history_db)["sources"]
        self.assertEqual([item["key"] for item in options], ["EPT", "MUSINSA", "creator_detail"])
        ept = analysis_data(self.history_db, "EPT")
        self.assertEqual(ept["source"]["sample_count"], 3)
        self.assertEqual(ept["source"]["source_file_count"], 1)
        self.assertEqual(ept["metrics"]["followers"]["valid_count"], 2)
        self.assertEqual(ept["metrics"]["daily_read_median"]["valid_count"], 3)
        self.assertEqual(ept["metrics"]["daily_read_median"]["minimum"], 0)
        self.assertEqual(ept["performance_pairs"][0]["reading"], 100)
        self.assertEqual(len(ept["performance_pairs"]), 2)
        self.assertEqual(sum(item["count"] for item in ept["follower_distribution"]), 2)
        self.assertEqual(analysis_data(self.history_db, "MUSINSA")["source"]["sample_count"], 1)
        details = analysis_data(self.history_db, "creator_detail")
        self.assertEqual(details["source"]["sample_count"], 2)
        self.assertEqual(details["metrics"]["followers"]["valid_count"], 2)
        self.assertEqual(ept["records"][0]["lineage"]["sha256"], "e" * 64)

    def test_history_library_merges_only_matching_strong_ids_and_pages_results(self) -> None:
        db = self.root / "expanded-history.sqlite3"
        index = self.root / "index.json"
        detail = self.root / "detail.json"
        index.write_text(json.dumps([
            {"creator_name": "历史名", "xhs_profile_id": "profile-1", "latest_known_followers": "10000"},
            {"creator_name": "同名无编号", "latest_known_followers": "20000"},
            {"creator_name": "达人名单_2026.xlsx", "latest_known_followers": "20260728214705"},
        ], ensure_ascii=False), encoding="utf-8")
        detail.write_text(json.dumps([
            {"博主昵称": "详情新名", "xhs_profile_id": "profile-1", "粉丝数": "1.2w", "地域": "首尔"},
        ], ensure_ascii=False), encoding="utf-8")
        import_file(db, index, "rednote_creator__历史索引")
        import_file(db, detail, "rednote_creator__达人详情")

        options = list_library_sources(db)["sources"]
        all_history = next(item for item in options if item["key"] == "history_all")
        self.assertEqual(all_history["sample_count"], 4)
        self.assertEqual(all_history["entity_count"], 2)

        first = history_creator_library(db, "history_all", limit=1, offset=0)
        self.assertEqual(first["result_count"], 2)
        self.assertEqual(first["source"]["entity_count"], 2)
        self.assertEqual(len(first["records"]), 1)
        linked = history_creator_library(db, "history_all", query="历史名", limit=10)
        self.assertEqual(linked["result_count"], 1)
        self.assertEqual(linked["records"][0]["name"], "详情新名")
        self.assertEqual(linked["records"][0]["aliases"], ["历史名", "详情新名"])
        self.assertEqual(linked["records"][0]["followers"], 12000)
        self.assertEqual(len(linked["records"][0]["source_records"]), 2)
        self.assertEqual(linked["records"][0]["source_file_count"], 2)
        self.assertTrue(all(record["source_paths"] for record in linked["records"][0]["source_records"]))
        self.assertEqual(history_creator_library(db, "history_all", query="同名无编号")["result_count"], 1)
        self.assertEqual(history_creator_library(db, "history_all", query="达人名单")["result_count"], 0)

    def test_product_records_are_excluded_from_library_counts_and_analysis(self) -> None:
        conn = sqlite3.connect(self.history_db)
        conn.execute(
            "INSERT INTO source_records (record_id,source_id,row_number,raw_json,raw_text,row_digest,duplicate_content,identity_status,capture_time,parse_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("musinsa-product", "musinsa", 2,
             json.dumps({"creator_name": "抓绒图案连帽衫-灰色", "xhs_profile_url": "https://www.musinsa.com/products/6257477"}, ensure_ascii=False),
             None, "digest-musinsa-product", 0, "pending", None, "parsed"),
        )
        conn.execute(
            "INSERT INTO field_evidence (record_id,original_field,target_field,raw_json,numeric_value,text_value,unit,period,parse_status,mapping_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("musinsa-product", "latest_known_followers", "followers", json.dumps("16825067"), 16_825_067, None, "count", None, "parsed", "test"),
        )
        conn.commit()
        conn.close()

        option = next(item for item in list_library_sources(self.history_db)["sources"] if item["key"] == "MUSINSA")
        page = history_creator_library(self.history_db, "MUSINSA", limit=10)
        analysis = analysis_data(self.history_db, "MUSINSA")
        self.assertEqual(option["sample_count"], 1)
        self.assertEqual(page["result_count"], 1)
        self.assertEqual([item["name"] for item in page["records"]], ["未命名记录"])
        self.assertEqual(analysis["source"]["sample_count"], 1)
        self.assertEqual(analysis["metrics"]["followers"]["valid_count"], 1)
        self.assertEqual(analysis["field_coverage"]["record_count"], 1)

    def test_library_source_counts_match_visible_rows_with_sparse_records(self) -> None:
        conn = sqlite3.connect(self.history_db)
        conn.execute(
            "INSERT INTO source_records (record_id,source_id,row_number,raw_json,raw_text,row_digest,duplicate_content,identity_status,capture_time,parse_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("ept-sparse", "ept", 4, "{}", None, "digest-ept-sparse", 0, "pending", None, "parsed"),
        )
        conn.execute("INSERT INTO source_paths VALUES (?,?,?)", ("ept", "sparse-ept.csv", "2026-11-23T00:00:00+00:00"))
        conn.commit()
        conn.close()

        option = next(item for item in list_library_sources(self.history_db)["sources"] if item["key"] == "EPT")
        page = history_creator_library(self.history_db, "EPT", limit=10)
        self.assertEqual(option["sample_count"], page["source"]["sample_count"])
        self.assertEqual(option["entity_count"], page["source"]["entity_count"])
        self.assertEqual(option["entity_count"], page["result_count"])
        self.assertTrue(page["records"][0]["source_records"][0]["source_paths"])

    def test_history_reads_are_read_only_and_preserve_database_bytes(self) -> None:
        before = hashlib.sha256(self.history_db.read_bytes()).hexdigest()
        analysis_data(self.history_db)
        note_samples(self.history_db)
        conn = _connection(self.history_db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO sources (source_id,sha256,format,source_name,first_imported_at) VALUES ('bad','x','csv','bad','now')")
        finally:
            conn.close()
        after = hashlib.sha256(self.history_db.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_note_samples_expose_only_selected_fields_and_no_page_text(self) -> None:
        result = note_samples(self.history_db)
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["samples"][0]["title"], "展示标题 1")
        self.assertEqual(result["samples"][0]["topics"], ["课程展示", "话题1"])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("正文摘录", serialized)

    def test_history_note_library_searches_content_and_removes_topic_marker_noise(self) -> None:
        page = history_note_library(self.history_db, query="课程展示", limit=2)
        self.assertEqual(page["total_count"], 3)
        self.assertEqual(page["result_count"], 3)
        self.assertEqual((page["title_count"], page["body_count"], page["topics_count"]), (3, 3, 3))
        self.assertEqual(len(page["samples"]), 2)
        self.assertNotIn("[话题]", page["samples"][0]["body"])
        self.assertNotIn("话题", page["samples"][0]["topics"])
        python_literal = next(item for item in page["samples"] if item["title"] == "展示标题 2")
        self.assertEqual(python_literal["topics"], ["课程展示", "话题2"])
        self.assertEqual(history_note_library(self.history_db, query="不存在")["result_count"], 0)

    def test_invalid_source_and_unmarked_database_are_rejected(self) -> None:
        with self.assertRaises(HistoryAnalyticsError):
            analysis_data(self.history_db, "unknown")
        unmarked = self.root / "unmarked.sqlite3"
        conn = sqlite3.connect(unmarked)
        conn.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        conn.execute("INSERT INTO metadata VALUES ('application','other')")
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(HistoryAnalyticsError, "格式不受支持"):
            analysis_data(unmarked)

    def test_flask_endpoints_read_the_isolated_history_database(self) -> None:
        app_db = self.root / "app.sqlite3"
        app = create_app({
            "TESTING": True,
            "DATABASE_PATH": app_db,
            "HISTORY_EVIDENCE_DB": self.history_db,
            "AI_REPORTS_PATH": self.root / "ai-reports",
            "NEWS_DATA_PATH": self.root / "news.jsonl",
            "BASELINE_PATH": self.root / "missing-baseline.json",
        })
        try:
            client = app.test_client()
            response = client.get("/api/history-analysis?source=EPT")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["source"]["sample_count"], 3)
            self.assertEqual(client.get("/api/history-analysis?source=bad").status_code, 400)
            self.assertEqual(client.get("/api/history-analysis/note-samples").get_json()["sample_count"], 3)
            self.assertEqual(client.get("/api/history-analysis/note-library?limit=2").get_json()["result_count"], 3)
            self.assertIn("sources", client.get("/api/history-analysis/library-sources").get_json())
            self.assertEqual(client.get("/api/history-analysis/library?source=bad").status_code, 400)
            self.assertEqual(client.get("/api/history-analysis/library?limit=201").status_code, 400)
            for page in ("/", "/ai", "/comments", "/quality", "/news"):
                page_response = client.get(page)
                self.assertEqual(page_response.status_code, 200, page)
                self.assertIn("更多工具", page_response.get_data(as_text=True))
            self.assertIn("historyFollowersChart", client.get("/").get_data(as_text=True))
        finally:
            app.extensions["creatorscope_tasks"].shutdown()

    def test_optional_history_endpoints_return_empty_when_store_is_absent(self) -> None:
        app = create_app({
            "TESTING": True,
            "DATABASE_PATH": self.root / "app-without-history.sqlite3",
            "HISTORY_EVIDENCE_DB": self.root / "not-created.sqlite3",
            "AI_REPORTS_PATH": self.root / "ai-reports-without-history",
            "NEWS_DATA_PATH": self.root / "news-without-history.jsonl",
            "BASELINE_PATH": self.root / "missing-baseline.json",
        })
        try:
            client = app.test_client()
            sources = client.get("/api/history-analysis/sources")
            self.assertEqual(sources.status_code, 200)
            self.assertEqual(sources.get_json(), {"sources": []})

            note_samples = client.get("/api/history-analysis/note-samples")
            self.assertEqual(note_samples.status_code, 200)
            self.assertEqual(note_samples.get_json(), {"sample_count": 0, "samples": []})

            note_library = client.get("/api/history-analysis/note-library?offset=3&limit=5")
            self.assertEqual(note_library.status_code, 200)
            self.assertEqual(note_library.get_json(), {
                "total_count": 0,
                "result_count": 0,
                "title_count": 0,
                "body_count": 0,
                "topics_count": 0,
                "offset": 3,
                "limit": 5,
                "samples": [],
            })

            library_sources = client.get("/api/history-analysis/library-sources")
            self.assertEqual(library_sources.status_code, 200)
            self.assertEqual(library_sources.get_json(), {"sources": []})
        finally:
            app.extensions["creatorscope_tasks"].shutdown()


if __name__ == "__main__":
    unittest.main()
