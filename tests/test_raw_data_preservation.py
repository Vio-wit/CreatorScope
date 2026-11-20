from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from creatorscope.db import initialize_database
from creatorscope.ingest import ingest_comment_snapshots, ingest_payload


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
            "comment_text": "评论原文包含 contact@example.test 和 https://example.test/context",
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
                "SELECT comment_text, raw_payload_json FROM comment_snapshots"
            ).fetchone()
            self.assertIn("contact@example.test", comment[0])
            raw_comment = json.loads(comment[1])
            self.assertEqual(raw_comment["comment_id"], "visible-comment-id-77")
            self.assertEqual(raw_comment["commenter_name"], "评论用户甲")
            self.assertEqual(raw_comment["commenter_id"], "public-commenter-id-77")
            self.assertEqual(raw_comment["profile_url"], "https://example.test/profile/77")
            self.assertEqual(raw_comment["email"], "commenter@example.test")
            self.assertEqual(raw_comment["phone"], "13700137000")
            self.assertNotIn("cookie", raw_comment)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
