from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from creatorscope.db import initialize_database
from creatorscope.ingest import ingest_comment_snapshots, ingest_payload
from research.r4_3_comment_analysis.comment_analysis import fetch_comments
from research.r4_3_comment_analysis.comments_web import comment_analysis_bp


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
