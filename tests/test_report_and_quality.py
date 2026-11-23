from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from creatorscope import create_app
from creatorscope.db import connection, initialize_database, record_field_statuses
from creatorscope.quality import coverage_by_field, quality_overview
from creatorscope.report import build_report, render_docx, render_markdown, report_charts, report_filename


class ReportAndQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="creatorscope-report-")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "report.sqlite3"
        initialize_database(self.db_path)
        self._seed()
        self.app = create_app(
            {
                "DATABASE_PATH": self.db_path,
                "BASELINE_PATH": self.root / "missing-baseline.json",
                "AI_REPORTS_PATH": self.root / "ai-reports",
                "NEWS_DATA_PATH": self.root / "news.jsonl",
                "DEMO_MODE": False,
                "TESTING": True,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.app.extensions["creatorscope_tasks"].shutdown()
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

    def _record(self, conn, batch_id: str, record_type: str, captured: str, creator_id: str, note_id: str | None, fields: dict[str, str]) -> int:
        cursor = conn.execute(
            "INSERT INTO records(batch_id,record_type,captured_at,fields_json,fields_hash,creator_id,note_id) VALUES(?,?,?,?,?,?,?)",
            (batch_id, record_type, captured, json.dumps(fields, ensure_ascii=False), f"{batch_id}-{record_type}-{creator_id}", creator_id, note_id),
        )
        record_id = int(cursor.lastrowid)
        for key, raw in fields.items():
            text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            number = float(text) if text.replace(".", "", 1).isdigit() else None
            conn.execute(
                "INSERT INTO metric_values(record_id,metric_key,raw_value,numeric_value,unit,period,parse_status) VALUES(?,?,?,?,?,?,?)",
                (record_id, key, text, number, "count" if number is not None else "", "累计", "parsed" if number is not None else "unparsed"),
            )
        return record_id

    def _seed(self) -> None:
        with connection(self.db_path) as conn:
            for creator_id, name, platform_id, location, category in (
                ("creator-a", "Creator A", "platform-creator-a", "上海", "美妆"),
                ("creator-b", "Creator B", "platform-creator-b", "杭州", "旅行"),
            ):
                conn.execute(
                    "INSERT INTO creators(creator_id,pseudonym,creator_name,platform,platform_creator_id,location,category,source_key_hash,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (creator_id, f"博主 {creator_id}", name, "xiaohongshu", platform_id, location, category, f"hash-{creator_id}", "2026-11-01", "2026-11-02"),
                )
            self._batch(conn, "batch-q", "蒲公英可见页面", "2026-11-21T10:00:00")
            conn.execute(
                "INSERT INTO notes(note_id,creator_id,note_key_hash,platform_note_id,title,body,topics_json,published_at,note_type,source_url,source,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("note-a", "creator-a", "note-hash-a", "platform-note-a", "上海护肤分享", "真实正文内容 保湿 温和", json.dumps(["#护肤"], ensure_ascii=False), "2026-11-20", "视频", "https://example.test/note-a", "蒲公英可见页面", "2026-11-21", "2026-11-21"),
            )
            self._record(conn, "batch-q", "creator_profile_sample", "2026-11-21T10:00:00", "creator-a", None, {"粉丝数": "12000", "阅读中位数": "800", "全部报价": "3500", "内容类目": "美妆", "所在地": "上海"})
            self._record(conn, "batch-q", "creator_profile_sample", "2026-11-21T10:00:00", "creator-b", None, {"粉丝数": "48000", "阅读中位数": "1500", "全部报价": "8000", "内容类目": "旅行", "所在地": "杭州"})
            self._record(conn, "batch-q", "note_detail", "2026-11-21T10:05:00", "creator-a", "note-a", {"阅读量": "3200", "点赞量": "210", "收藏量": "64", "评论量": "18", "分享量": "9"})
            self._record(
                conn, "batch-q", "fan_analysis", "2026-11-21T10:07:00", "creator-a", None,
                {"gender_share": {"female": "82%", "male": "18%"}, "age_share": {"18-24": "46%", "25-30": "34%"}},
            )
            for comment_hash, note_id, text, level in (
                ("comment-1", "note-a", "保湿效果很好 喜欢", 1),
                ("comment-2", "note-a", "价格有点贵", 2),
            ):
                conn.execute(
                    "INSERT INTO comment_snapshots(batch_id,note_id,local_comment_id_hash,comment_text,cleaned_text,comment_level,source,captured_at,raw_payload_json,reply_count_raw) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("batch-q", note_id, comment_hash, text, text, level, "蒲公英可见页面", "2026-11-21T10:06:00", "{}", "2"),
                )
        record_field_statuses(
            self.db_path,
            batch_id="batch-q",
            task_id="task-batch-q",
            entity_type="note",
            entity_ref="https://example.test/note-a",
            record_type="note_detail",
            field_names=("曝光量", "新增关注"),
            status="access_denied",
            source="蒲公英可见页面",
        )

    # ------------------------------------------------------------------ quality

    def test_quality_overview_summarizes_saved_batches(self) -> None:
        overview = quality_overview(self.db_path)
        self.assertEqual(len(overview["batches"]), 1)
        batch = overview["batches"][0]
        self.assertEqual(batch["batch_id"], "batch-q")
        self.assertEqual(batch["counts"]["records"], 4)
        self.assertEqual(batch["counts"]["comments"], 2)
        self.assertIsNotNone(batch["coverage_pct"])
        self.assertEqual(overview["totals"]["batches"], 1)
        self.assertGreaterEqual(overview["totals"]["partial_fields"], 1)
        self.assertTrue(overview["notes"])

    def test_coverage_by_field_aggregates_observation_states(self) -> None:
        rows = coverage_by_field(self.db_path)
        self.assertTrue(rows)
        denied = next(item for item in rows if item["field_name"] == "曝光量")
        self.assertEqual(denied["record_type"], "note_detail")
        self.assertEqual(denied["captured"], 0)
        self.assertGreaterEqual(denied["observed"], 1)
        self.assertEqual(denied["share_pct"], 0.0)

    def test_quality_pages_and_api_are_read_only(self) -> None:
        self.assertEqual(self.client.get("/quality").status_code, 200)
        response = self.client.get("/api/quality/overview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["batches"][0]["batch_id"], "batch-q")
        self.assertEqual(self.client.get("/api/quality/overview?limit=abc").status_code, 400)
        self.assertEqual(self.client.get("/api/batches/batch-q/quality").status_code, 200)

    # ------------------------------------------------------------------- report

    def test_report_markdown_contains_saved_facts_and_limits(self) -> None:
        bundle = build_report(self.db_path)
        markdown = render_markdown(bundle)
        self.assertIn("# 数据采集与处理综合实训报告", markdown)
        self.assertIn("## 二、数据质量与字段覆盖", markdown)
        self.assertIn("## 七、局限与合规声明", markdown)
        self.assertIn("Creator A", markdown)
        self.assertIn("3,200", markdown)  # 阅读量按千分位展示
        self.assertIn("### 5.1 评论规模", markdown)
        self.assertIn("保湿", markdown)  # 评论高频词进入报告
        self.assertIn("不能代表平台整体", markdown)

    def test_report_bundle_respects_selected_batch(self) -> None:
        bundle = build_report(self.db_path, batch_id="batch-q")
        self.assertEqual(bundle["batch_id"], "batch-q")
        self.assertIsNotNone(bundle["quality"])
        self.assertEqual(bundle["quality"]["batch_id"], "batch-q")
        markdown = render_markdown(bundle)
        self.assertIn("### 2.1 字段覆盖率", markdown)
        self.assertIn("batch-q", markdown)

    def test_report_scope_and_missing_rates_across_batches(self) -> None:
        with connection(self.db_path) as conn:
            self._batch(conn, "batch-other", "其他导入", "2026-11-22T10:00:00")
            self._record(conn, "batch-other", "creator_profile_sample", "2026-11-22T10:00:00", "creator-b", None, {"粉丝数": "50000"})
        selected = build_report(self.db_path, batch_id="batch-other")
        self.assertEqual(selected["overview"]["counts"], {"creators": 1, "notes": 0, "comments": 0, "batches": 1})
        self.assertEqual([item["source"] for item in selected["overview"]["sources"]], ["其他导入"])
        self.assertEqual(sum(item["count"] for item in selected["type_summary"]), 1)
        all_data = build_report(self.db_path)
        self.assertEqual(all_data["overview"]["counts"]["batches"], 2)
        self.assertEqual(sum(item["count"] for item in all_data["type_summary"]), 5)
        self.assertEqual(self.client.get("/report?batch_id=missing").status_code, 404)
        self.assertEqual(self.client.get("/api/report.md?batch_id=missing").status_code, 404)
        self.assertEqual(self.client.get("/api/report.docx?batch_id=missing").status_code, 404)
        from docx import Document
        selected["quality"]["metrics"]["parse_success_pct"] = None
        output = render_docx(selected, self.root / "no-rates.docx")
        document = Document(output)
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("数值解析成功率 —。", text)
        self.assertNotIn("数值解析成功率 0%", text)

    def test_audience_rows_flatten_saved_profiles(self) -> None:
        bundle = build_report(self.db_path)
        self.assertTrue(bundle["analytics"]["charts"]["audience_groups"])
        rows = bundle["audience_rows"]
        self.assertTrue(rows)
        genders = {item["label"]: item["value"] for item in rows if item["dimension"] == "性别"}
        self.assertEqual(genders.get("女性"), 82.0)
        self.assertIn("| 女性 | 82 |", render_markdown(bundle).replace(" ", " "))

    def test_report_charts_and_docx_render_with_data(self) -> None:
        bundle = build_report(self.db_path)
        charts = report_charts(bundle)
        self.assertGreaterEqual(len(charts), 2)
        self.assertTrue(all(payload[:8] == b"\x89PNG\r\n\x1a\n" for _, payload in charts))

        target = self.root / "report.docx"
        render_docx(bundle, target)
        payload = target.read_bytes()
        self.assertEqual(payload[:2], b"PK")
        self.assertGreater(len(payload), 20_000)

    def test_report_routes_serve_markdown_and_word(self) -> None:
        page = self.client.get("/report")
        self.assertEqual(page.status_code, 200)
        self.assertIn("数据采集与处理综合实训报告".encode("utf-8"), page.data)

        markdown = self.client.get("/api/report.md")
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("text/markdown", markdown.headers["Content-Type"])
        self.assertIn("attachment;", markdown.headers["Content-Disposition"])
        self.assertIn("二、数据质量与字段覆盖", markdown.data.decode("utf-8"))

        docx = self.client.get("/api/report.docx")
        self.assertEqual(docx.status_code, 200)
        self.assertIn("wordprocessingml", docx.headers["Content-Type"])
        self.assertEqual(docx.data[:2], b"PK")
        self.assertGreater(len(docx.data), 20_000)

    def test_report_filename_is_stamped(self) -> None:
        bundle = build_report(self.db_path, generated_at="2026-11-22T18:30:00+08:00")
        self.assertEqual(report_filename(bundle, "docx"), "creatorscope_report_20261122_1830.docx")

    def test_empty_database_still_builds_a_report(self) -> None:
        empty = self.root / "empty.sqlite3"
        initialize_database(empty)
        bundle = build_report(empty)
        self.assertEqual(bundle["overview"]["counts"]["batches"], 0)
        self.assertIsNone(bundle["comments"])
        markdown = render_markdown(bundle)
        self.assertIn("当前范围没有已保存的可见评论", markdown)
        self.assertEqual(report_charts(bundle), [])
        payload = io.BytesIO()
        render_docx(bundle, self.root / "empty.docx")
        payload.write((self.root / "empty.docx").read_bytes())
        self.assertEqual(payload.getvalue()[:2], b"PK")


if __name__ == "__main__":
    unittest.main()
