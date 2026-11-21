from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from creatorscope.browser import PgyVisibleBrowser
from creatorscope.db import connection, ensure_creator_ref, initialize_database
from creatorscope.ingest import ingest_comment_snapshots, ingest_payload
from research.r4_3_comment_analysis.comment_analysis import fetch_comments
from research.r4_3_comment_analysis.comments_web import comment_analysis_bp


class _TextLocator:
    def __init__(self, values=(), items=()):
        self.values = list(values)
        self.items = list(items)

    def all_inner_texts(self):
        return self.values

    def all(self):
        return self.items

    def count(self):
        return len(self.items)


class _CreatorRow:
    def __init__(self, cells=(), metadata=None):
        self.cells = list(cells)
        self.metadata = metadata or {}

    def get_by_role(self, role):
        return _TextLocator(self.cells if role in {"cell", "gridcell"} else ())

    def locator(self, selector):
        return _TextLocator()

    def evaluate(self, _script):
        return self.metadata


class _CreatorListPage:
    def __init__(self, headers, rows):
        self.headers = headers
        self.rows = rows

    def get_by_role(self, role):
        if role == "columnheader":
            return _TextLocator(self.headers)
        if role == "row":
            return _TextLocator(items=self.rows)
        return _TextLocator()

    def locator(self, _selector):
        return _TextLocator()


class RawDataPreservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="creatorscope-raw-")
        self.db_path = Path(self.temp_dir.name) / "raw.sqlite3"
        initialize_database(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO tasks(task_id,task_type,source,status,created_at) VALUES(?,?,?,?,?)",
                ("task-raw", "file_import", "synthetic", "running", "2026-11-21T10:00:00"),
            )
            conn.execute(
                "INSERT INTO batches(batch_id,task_id,source,captured_at,started_at,status) VALUES(?,?,?,?,?,?)",
                ("batch-raw", "task-raw", "synthetic", "2026-11-21T10:00:00", "2026-11-21T10:00:00", "running"),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_creator_list_header_mapping_preserves_name_id_and_raw_metrics(self) -> None:
        headers = [
            "操作",
            "阅读中位数（日常）",
            "博主信息",
            "粉丝数",
            "互动中位数（日常）",
            "全部报价",
        ]
        cells = [
            "查看",
            "820",
            "创作者甲\n小红书号：user-90210",
            "5万",
            "300",
            "￥3,000",
        ]
        record = PgyVisibleBrowser._parse_creator_list_row(
            headers,
            cells,
            {
                "links": [{
                    "text": "创作者甲",
                    "href": "https://pgy.xiaohongshu.com/solar/pre-trade/blogger-detail/detail-90210",
                }],
                "rowKey": "row-90210",
            },
        )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["fields"]["创作者名称"], "创作者甲")
        self.assertEqual(record["fields"]["平台公开ID"], "user-90210")
        self.assertEqual(record["fields"]["粉丝数"], "5万")
        self.assertEqual(record["fields"]["全部报价"], "￥3,000")
        self.assertEqual(record["creator_ref"], "pgy:/solar/pre-trade/blogger-detail/detail-90210")

        result = ingest_payload(
            self.db_path,
            "task-raw",
            {
                "source": "蒲公英创作者列表",
                "captured_at_local": "2026-11-21T10:05:00",
                "records": [record],
            },
            batch_id_override="batch-raw",
        )
        self.assertEqual(result["record_count"], 1)

        conn = sqlite3.connect(self.db_path)
        try:
            creator = conn.execute(
                "SELECT creator_name, platform_creator_id FROM creators WHERE platform_creator_id=?",
                ("user-90210",),
            ).fetchone()
            self.assertEqual(creator, ("创作者甲", "user-90210"))
            fields_json = conn.execute(
                "SELECT fields_json FROM records WHERE batch_id=?",
                ("batch-raw",),
            ).fetchone()[0]
            saved_fields = json.loads(fields_json)
            self.assertEqual(saved_fields["博主信息原始显示"], "创作者甲\n小红书号：user-90210")
            metric_keys = {
                row[0]
                for row in conn.execute(
                    "SELECT metric_key FROM metric_values WHERE record_id=(SELECT record_id FROM records WHERE batch_id=?)",
                    ("batch-raw",),
                )
            }
            self.assertIn("粉丝数", metric_keys)
            self.assertNotIn("创作者名称", metric_keys)
            self.assertNotIn("平台公开ID", metric_keys)
            observations = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT field_name, raw_value FROM collection_field_observations WHERE batch_id=?",
                    ("batch-raw",),
                )
            }
            self.assertEqual(observations["创作者名称"], "创作者甲")
            self.assertEqual(observations["平台公开ID"], "user-90210")
        finally:
            conn.close()

    def test_creator_list_dom_rows_keep_their_visible_row_positions(self) -> None:
        page = _CreatorListPage(
            ["博主信息", "粉丝数", "全部报价"],
            [
                _CreatorRow(),  # rendered header row
                _CreatorRow(
                    ["创作者甲\n小红书号：user-1", "5万", "￥3,000"],
                    {"links": [], "rowKey": "row-1"},
                ),
                _CreatorRow(
                    ["创作者乙\n小红书号：user-2", "8万", "￥4,000"],
                    {"links": [], "rowKey": "row-2"},
                ),
            ],
        )
        records = PgyVisibleBrowser.__new__(PgyVisibleBrowser)._capture_list_rows(page)
        self.assertEqual(len(records), 2)
        self.assertEqual([item["visible_row_index"] for item in records], [1, 2])
        self.assertEqual([item["fields"]["创作者名称"] for item in records], ["创作者甲", "创作者乙"])
        self.assertEqual([item["fields"]["平台公开ID"] for item in records], ["user-1", "user-2"])

    def test_creator_detail_parser_keeps_visible_identity_metrics_and_audience_values(self) -> None:
        browser = PgyVisibleBrowser.__new__(PgyVisibleBrowser)
        lines = [
            "笔记主页", "直播主页", "课堂样本博主", "小红书号：", "demo-public-123",
            "杭州", "无机构", "生活记录", "vlog", "职场生活", "粉丝数", "50.1w",
            "获赞与收藏", "431.3w", "加入清单", "邀约", "合作报价",
            "图文笔记一口价", "¥120,000", "视频笔记一口价", "¥135,000",
            "数据概览", "博主优势品效型博主发布笔记7篇内容类目生活记录、时尚合作行业服装配饰、美妆个护",
            "曝光中位数", "713,874", "阅读中位数", "425,630", "互动中位数", "12,696",
            "互动率", "2.9%", "视频完播率", "12.8%", "粉丝分析", "粉丝增量", "5,729",
            "粉丝量变化幅度", "1.2%", "活跃粉丝占比", "82.5%", "阅读粉丝占比", "48.9%",
            "互动粉丝占比", "7.8%", "下单粉丝占比", "35.4%", "粉丝画像", "性别分布",
            "女性居多，占比93.0%", "年龄分布", "18-24居多，占比52.1%", "地域分布",
            "国内最高的三个省份：广东（14.3%）、江苏（7.9%）、浙江（7.2%）", "按省份", "按城市",
            "用户设备分布", "苹果用户占比54.95%，消费力较强", "apple inc.", "huawei",
            "用户兴趣", "用户最感兴趣的内容类型为娱乐、生活记录、影视", "小红书官网",
        ]

        profile = browser._profile_record(lines)
        performance = browser._performance_record(lines)
        fans = browser._fans_record(lines)
        self.assertIsNotNone(profile)
        self.assertIsNotNone(performance)
        self.assertIsNotNone(fans)
        assert profile and performance and fans
        self.assertEqual(profile["fields"]["创作者名称"], "课堂样本博主")
        self.assertEqual(profile["fields"]["平台公开ID"], "demo-public-123")
        self.assertEqual(profile["fields"]["地域"], "杭州")
        self.assertEqual(profile["fields"]["标签"], "生活记录\nvlog\n职场生活")
        self.assertEqual(profile["fields"]["人设"], "品效型博主")
        self.assertEqual(profile["fields"]["内容类目"], "生活记录、时尚")
        self.assertEqual(profile["fields"]["合作行业"], "服装配饰、美妆个护")
        self.assertEqual(profile["fields"]["粉丝数"], "50.1w")
        self.assertEqual(performance["fields"]["阅读中位数"], "425,630")
        self.assertEqual(fans["fields"]["粉丝性别分布"], "女性居多，占比93.0%")
        self.assertEqual(fans["fields"]["粉丝年龄分布"], "18-24居多，占比52.1%")
        self.assertEqual(
            fans["fields"]["粉丝地域分布"],
            "国内最高的三个省份：广东（14.3%）、江苏（7.9%）、浙江（7.2%）",
        )
        self.assertEqual(
            fans["fields"]["粉丝设备分布"],
            "苹果用户占比54.95%，消费力较强\napple inc.\nhuawei",
        )
        self.assertEqual(fans["fields"]["粉丝兴趣分布"], "用户最感兴趣的内容类型为娱乐、生活记录、影视")
        self.assertFalse(browser._creator_detail_data_ready(["粉丝数", "--", "曝光中位数", "加载中，请稍等"]))
        self.assertTrue(browser._creator_detail_data_ready(["曝光中位数", "713,874"]))

    def test_creator_reference_reuses_source_and_platform_identity_without_cross_platform_merge(self) -> None:
        with connection(self.db_path) as conn:
            first = ensure_creator_ref(
                conn,
                "pgy:/solar/pre-trade/blogger-detail/first-route",
                "2026-11-21T12:10:00",
                creator_name="课堂样本博主",
                platform_creator_id="public-creator-777",
                platform="pgy",
            )
            same_reference = ensure_creator_ref(
                conn,
                "pgy:/solar/pre-trade/blogger-detail/first-route",
                "2026-11-21T12:11:00",
                creator_name="课堂样本博主",
                platform_creator_id="public-creator-777",
                platform="pgy",
            )
            alternate_reference = ensure_creator_ref(
                conn,
                "pgy:/solar/pre-trade/blogger-detail/alternate-route",
                "2026-11-21T12:12:00",
                creator_name="课堂样本博主",
                platform_creator_id="public-creator-777",
                platform="pgy",
            )
            other_platform = ensure_creator_ref(
                conn,
                "xhs:/explore/alternate-route",
                "2026-11-21T12:13:00",
                creator_name="课堂样本博主",
                platform_creator_id="public-creator-777",
                platform="xhs",
            )
            self.assertEqual(same_reference, first)
            self.assertEqual(alternate_reference, first)
            self.assertNotEqual(other_platform, first)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM creators").fetchone()[0], 2)

    def test_collection_and_comment_ingest_keep_source_values_and_exclude_auth_secrets(self) -> None:
        note_url = "https://www.xiaohongshu.com/explore/public-note-id?source=course"
        payload = {
            "source": "蒲公英可见页面",
            "captured_at_local": "2026-11-21T10:01:00",
            "records": [
                {
                    "type": "creator_profile_sample",
                    "creator_ref": "pgy-visible-creator-ref",
                    "source": "蒲公英可见页面",
                    "fields": {
                        "creator_name": "课程展示创作者",
                        "platform_creator_id": "pgy-account-009",
                        "email": "creator@example.test",
                        "phone": "13800138000",
                        "profile_url": "https://example.test/creator/009",
                        "author_name": "公开作者名称",
                        "password": "excluded-password",
                        "nested": {
                            "email": "nested@example.test",
                            "token_count": 3,
                            "cookie": "excluded-cookie",
                            "request_token": "excluded-request-token",
                            "client_secret": "excluded-client-secret",
                            "session_data": {"value": "excluded-session-value"},
                        },
                        "access_token": "excluded-access-token",
                        "粉丝数": "1200",
                    },
                },
                {
                    "type": "note_detail",
                    "creator_ref": "pgy-visible-creator-ref",
                    "note_ref": note_url,
                    "source": "小红书公开笔记页",
                    "fields": {
                        "笔记标题": "课程展示笔记",
                        "笔记正文": "联系 note@example.test 或 13900139000，详见 https://example.test/note",
                        "曝光量": "10+",
                    },
                },
            ],
        }
        result = ingest_payload(self.db_path, "task-raw", payload, batch_id_override="batch-raw")
        comments = [{
            "local_comment_id": "batch-fingerprint-1",
            "comment_id": "visible-comment-id-77",
            "commenter_name": "评论用户甲",
            "commenter_id": "public-commenter-id-77",
            "profile_url": "https://example.test/profile/77",
            "email": "commenter@example.test",
            "phone": "13700137000",
            "cookie": "excluded-comment-cookie",
            "comment_text": "  评论原文包含 contact@example.test 和 https://example.test/context\n",
            "like_count_raw": "2+",
        }]
        ingest_comment_snapshots(
            self.db_path,
            task_id="task-raw",
            batch_id=result["batch_id"],
            creator_ref="pgy-visible-creator-ref",
            note_ref=note_url,
            comments=comments,
            captured_at="2026-11-21T10:02:00",
        )

        conn = sqlite3.connect(self.db_path)
        try:
            creator = conn.execute(
                "SELECT creator_name, platform_creator_id FROM creators WHERE platform_creator_id=?",
                ("pgy-account-009",),
            ).fetchone()
            self.assertEqual(creator, ("课程展示创作者", "pgy-account-009"))
            note = conn.execute(
                "SELECT platform_note_id, source_url, body FROM notes"
            ).fetchone()
            self.assertEqual(note[0], "public-note-id")
            self.assertEqual(note[1], note_url)
            self.assertIn("note@example.test", note[2])
            self.assertIn("13900139000", note[2])
            self.assertIn("https://example.test/note", note[2])

            record_fields = [json.loads(row[0]) for row in conn.execute("SELECT fields_json FROM records")]
            serialized_fields = json.dumps(record_fields, ensure_ascii=False)
            self.assertIn("creator@example.test", serialized_fields)
            self.assertIn("nested@example.test", serialized_fields)
            self.assertIn("公开作者名称", serialized_fields)
            self.assertIn("token_count", serialized_fields)
            self.assertIn("https://example.test/creator/009", serialized_fields)
            self.assertNotIn("excluded-password", serialized_fields)
            self.assertNotIn("excluded-cookie", serialized_fields)
            self.assertNotIn("excluded-access-token", serialized_fields)
            self.assertNotIn("excluded-request-token", serialized_fields)
            self.assertNotIn("excluded-client-secret", serialized_fields)
            self.assertNotIn("excluded-session-value", serialized_fields)

            comment = conn.execute(
                "SELECT comment_text, cleaned_text, local_comment_id_hash, raw_payload_json FROM comment_snapshots"
            ).fetchone()
            self.assertIn("contact@example.test", comment[0])
            self.assertEqual(comment[0], comments[0]["comment_text"])
            self.assertEqual(comment[1], comments[0]["comment_text"].strip())
            raw_comment = json.loads(comment[3])
            self.assertEqual(raw_comment["comment_id"], "visible-comment-id-77")
            self.assertEqual(raw_comment["commenter_name"], "评论用户甲")
            self.assertEqual(raw_comment["commenter_id"], "public-commenter-id-77")
            self.assertEqual(raw_comment["profile_url"], "https://example.test/profile/77")
            self.assertEqual(raw_comment["email"], "commenter@example.test")
            self.assertEqual(raw_comment["phone"], "13700137000")
            self.assertNotIn("cookie", raw_comment)
            self.assertNotEqual(comment[2], raw_comment["local_comment_id"])
        finally:
            conn.close()

        analysis_row = fetch_comments(self.db_path)[0]
        self.assertEqual(analysis_row["comment_text"], comments[0]["comment_text"])
        self.assertEqual(analysis_row["comment_id"], "visible-comment-id-77")
        self.assertEqual(analysis_row["local_comment_id"], "batch-fingerprint-1")
        self.assertNotEqual(analysis_row["local_comment_id_hash"], analysis_row["local_comment_id"])
        self.assertEqual(analysis_row["local_comment_id_origin"], "provided_source_field")
        self.assertEqual(analysis_row["raw_payload"]["commenter_name"], "评论用户甲")

        ingest_comment_snapshots(
            self.db_path,
            task_id="task-raw",
            batch_id=result["batch_id"],
            creator_ref="pgy-visible-creator-ref",
            note_ref=note_url,
            comments=[{
                "local_comment_key": "generated-fingerprint-2",
                "local_comment_key_origin": "generated_fingerprint",
                "comment_text": "  页面可见的第二条评论  ",
                "comment_level": 1,
            }],
            captured_at="2026-11-21T10:03:00",
        )
        generated_row = next(
            row for row in fetch_comments(self.db_path)
            if row["comment_text"] == "  页面可见的第二条评论  "
        )
        self.assertIsNone(generated_row["local_comment_id"])
        self.assertEqual(generated_row["local_comment_key"], "generated-fingerprint-2")
        self.assertEqual(generated_row["local_comment_id_origin"], "generated_fingerprint")
        self.assertNotEqual(generated_row["local_comment_id_hash"], generated_row["local_comment_key"])

        app = Flask(__name__)
        app.config["COMMENT_ANALYSIS_DATABASE_PATH"] = self.db_path
        app.register_blueprint(comment_analysis_bp)
        client = app.test_client()
        sentiment = client.get("/api/comments/sentiment").get_json()
        reviewed_comment = next(row for row in sentiment["comments"] if row["comment_id"] == "visible-comment-id-77")
        self.assertEqual(reviewed_comment["comment_text"], comments[0]["comment_text"])
        self.assertEqual(sentiment["summary"]["comment_count"], 2)
        review_rows = list(
            csv.DictReader(
                io.StringIO(client.get("/api/comments/sentiment/review.csv").get_data(as_text=True).lstrip("\ufeff"))
            )
        )
        review_comment = next(row for row in review_rows if row["comment_id"] == "visible-comment-id-77")
        self.assertEqual(review_comment["comment_text"], comments[0]["comment_text"])
        self.assertEqual(review_comment["manual_sentiment"], "")
        jsonl_rows = [
            json.loads(line)
            for line in client.get("/api/comments/export.jsonl").get_data(as_text=True).splitlines()
        ]
        exported_source_row = next(row for row in jsonl_rows if row["comment_id"] == "visible-comment-id-77")
        self.assertEqual(exported_source_row["comment_text"], comments[0]["comment_text"])
        self.assertEqual(exported_source_row["raw_payload"]["commenter_id"], "public-commenter-id-77")
        exported_generated_row = next(row for row in jsonl_rows if row["local_comment_key"] == "generated-fingerprint-2")
        self.assertIsNone(exported_generated_row["local_comment_id"])
        csv_rows = list(
            csv.DictReader(
                io.StringIO(client.get("/api/comments/export.csv").get_data(as_text=True).lstrip("\ufeff"))
            )
        )
        csv_source_row = next(row for row in csv_rows if row["comment_id"] == "visible-comment-id-77")
        self.assertEqual(csv_source_row["comment_text"], comments[0]["comment_text"])
        self.assertEqual(json.loads(csv_source_row["raw_payload"])["commenter_name"], "评论用户甲")


if __name__ == "__main__":
    unittest.main()
