from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from creatorscope.historical_import import integrate_historical_data


class HistoricalImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="creatorscope-history-")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "instance" / "unified.sqlite3"
        self.research = self.root / "research"
        self.continuous = self.research / "continuous_collection_validation"
        self.continuous.mkdir(parents=True)
        (self.continuous / "collection_batches.json").write_text(
            json.dumps([{"batch_id": "synthetic", "captured_on": "2026-11-20"}]),
            encoding="utf-8",
        )
        self.probe = self.research / "xhs_public_access_probe"
        self.probe.mkdir(parents=True)

        creator = {
            "source": "pgy",
            "creator_id": "private-creator-id",
            "creator_name": "Private Display Name",
            "email": "creator@example.test",
            "phone": "13800138000",
            "profile_url": "https://example.test/creator/private-creator-id",
            "password": "must-not-import",
            "cookie": "must-not-import-either",
            "request_token": "must-not-import-request-token",
            "client_secret": "must-not-import-client-secret",
            "session_data": {"state": "must-not-import-session"},
            "token_count": 4,
            "access_token": "must-not-import-access-token",
            "city_raw": "Test City",
            "category_tags": ["topic-a"],
            "followers_count": 1200,
            "captured_on": "2026-11-20",
            "performance_medians": {"read_median": {"raw_value": "10", "normalized_value": 10, "unit": "次", "period": "30d"}},
            "fan_snapshot": {"period": "30d", "active_share_raw": "50%", "active_share_percent": 50},
        }
        self._write_jsonl(self.continuous / "raw_creators.jsonl", [creator])
        self.note_id = "0123456789abcdef01234567"
        pgy_note = {
            "source": "pgy",
            "note_key": "stable-note-key",
            "creator_id": "private-creator-id",
            "note_id": self.note_id,
            "note_url": f"https://example.test/explore/{self.note_id}",
            "title": "Private Note Title",
            "content_text_raw": "sample body",
            "hashtags": ["topic-a"],
            "published_at": "2026-11-18",
            "captured_on": "2026-11-20",
            "metrics": {"reads": {"raw_value": "100", "normalized_value": 100, "unit": "次", "period": "snapshot-1"}},
        }
        self._write_jsonl(self.continuous / "raw_notes_pgy.jsonl", [pgy_note, pgy_note])
        self._write_jsonl(
            self.continuous / "raw_comments.jsonl",
            [
                {"note_key": "stable-note-key", "comment_text_excerpt": "excerpt only", "likes_raw": "2+", "commenter_name": "Excerpt Commenter", "commenter_id": "excerpt-commenter-id", "identity_fields_omitted": True},
                {"note_key": "unmatched-note-key", "comment_text_excerpt": "unmatched excerpt", "identity_fields_omitted": True},
            ],
        )
        self._write_jsonl(
            self.probe / "raw_notes_xhs.jsonl",
            [
                {
                    "note_id": self.note_id,
                    "title": "Private Note Title",
                    "body_text": "sample body",
                    "hashtags": ["topic-a"],
                    "captures": [
                        {"captured_at": "2026-11-20T10:00:00+08:00", "xhs_metrics_raw": {"likes": 10}},
                        {"captured_at": "2026-11-20T11:00:00+08:00", "xhs_metrics_raw": {"likes": 11}},
                    ],
                },
                {
                    "note_id": "fedcba987654321001234567",
                    "creator_name": "Recovered XHS Author",
                    "title": "Previously linked note",
                    "share_url_base": "https://example.test/explore/fedcba987654321001234567",
                    "captures": [{"captured_at": "2026-11-20T12:00:00+08:00", "xhs_metrics_raw": {"likes": 12}}],
                },
            ],
        )
        self._write_jsonl(
            self.probe / "raw_comments_xhs.jsonl",
            [{
                "note_id": self.note_id,
                "comment_text": "visible comment",
                "commenter_name": "Visible Commenter",
                "commenter_id": "visible-commenter-id",
                "commenter_profile_url": "https://example.test/profile/visible-commenter-id",
                "author_name": "Visible Source Author",
                "author_profile_url": "https://example.test/author/visible-source-author",
                "email": "commenter@example.test",
                "phone": "13900139000",
                "comment_level": "2",
                "cookie": "must-not-import",
                "likes_raw": "3",
                "likes_normalized": 3,
                "captured_at": "2026-11-20T11:10:00+08:00",
            }],
        )
        r42 = self.research / "r4_2_comment_validation"
        r42.mkdir(parents=True)
        self._write_jsonl(r42 / "raw_comments.jsonl", [{"batch_id": "R42-test", "note_id": "ignored", "comment_text": "not imported"}])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_historical_sources_preserve_public_fields_and_link_by_exact_ids(self) -> None:
        source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.research.rglob("*.jsonl")}
        report_dir = self.research / "r4_4_integration"
        first = integrate_historical_data(self.db_path, self.root, report_dir)
        conn = sqlite3.connect(self.db_path)
        try:
            placeholder_id = conn.execute("SELECT creator_id FROM creators WHERE creator_name='' LIMIT 1").fetchone()[0]
            conn.execute(
                "UPDATE notes SET creator_id=? WHERE platform_note_id=?",
                (placeholder_id, "fedcba987654321001234567"),
            )
            for record_id, fields_json in conn.execute("SELECT record_id, fields_json FROM records").fetchall():
                fields = json.loads(fields_json)
                for key in ("creator_name", "creator_id", "note_id", "note_url", "email", "phone", "profile_url", "commenter_name", "commenter_id", "commenter_profile_url"):
                    fields.pop(key, None)
                conn.execute("UPDATE records SET fields_json=? WHERE record_id=?", (json.dumps(fields, ensure_ascii=False), record_id))
            conn.execute("UPDATE creators SET creator_name='', platform_creator_id=NULL, platform_creator_id_hash=NULL")
            conn.execute("UPDATE notes SET platform_note_id=NULL, source_url=NULL")
            conn.execute("UPDATE comment_snapshots SET raw_payload_json='{}'")
            candidates = conn.execute("SELECT record_id, fields_json FROM records WHERE record_type='comment_excerpt_sample'").fetchall()
            linked_row = next(record_id for record_id, raw in candidates if json.loads(raw).get("_source_row") == 1)
            conn.execute("UPDATE records SET creator_id=NULL, note_id=NULL WHERE record_id=?", (linked_row,))
            conn.commit()
        finally:
            conn.close()
        second = integrate_historical_data(self.db_path, self.root, report_dir)

        self.assertEqual(first["integrity"], {"integrity": "ok", "foreign_key_errors": 0, "duplicate_platform_note_hash_groups": 0})
        self.assertEqual(first["r42"]["already_integrated"], 0)
        self.assertGreater(first["duplicate_records_skipped"], 0)
        self.assertTrue(first["exact_note_id_rows"] >= 2)
        self.assertEqual((first["comment_rows_linked"], first["comment_rows_seen"]), (2, 3))
        self.assertEqual(second["db_delta"], {key: 0 for key in second["db_delta"]})
        self.assertEqual(second["inserted_records"], 0)
        self.assertEqual(second["inserted_comment_snapshots"], 0)
        self.assertTrue(all(item["outcome"] == "present_and_verified" for item in second["random_audit"]))
        self.assertEqual(source_hashes, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_hashes})
        self.assertTrue((report_dir / "report.json").exists())
        self.assertTrue((report_dir / "report.md").exists())

        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notes WHERE platform_note_id_hash = ?", (hashlib.sha256(self.note_id.encode()).hexdigest(),)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notes WHERE platform_note_id = ?", (self.note_id,)).fetchone()[0], 1)
            creator = conn.execute("SELECT creator_name, platform_creator_id FROM creators WHERE platform_creator_id=?", ("private-creator-id",)).fetchone()
            self.assertEqual(creator, ("Private Display Name", "private-creator-id"))
            note = conn.execute("SELECT platform_note_id, source_url FROM notes WHERE platform_note_id=?", (self.note_id,)).fetchone()
            self.assertEqual(note, (self.note_id, f"https://example.test/explore/{self.note_id}"))
            recovered = conn.execute("""SELECT c.creator_name FROM notes n JOIN creators c USING(creator_id)
                WHERE n.platform_note_id=?""", ("fedcba987654321001234567",)).fetchone()
            self.assertEqual(recovered[0], "Recovered XHS Author")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records WHERE record_type='note_detail'").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records WHERE record_type='comment_excerpt_sample'").fetchone()[0], 2)
            self.assertEqual(conn.execute("""SELECT COUNT(*) FROM records r JOIN notes n ON n.note_id=r.note_id
                WHERE r.record_type='comment_excerpt_sample' AND r.creator_id=n.creator_id""").fetchone()[0], 1)
            self.assertEqual(conn.execute("""SELECT COUNT(*) FROM records r
                WHERE r.record_type='comment_excerpt_sample' AND r.creator_id IS NULL AND r.note_id IS NULL""").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM comment_snapshots").fetchone()[0], 1)
            self.assertEqual(conn.execute("""SELECT COUNT(*) FROM metric_values m
                JOIN records r ON r.record_id = m.record_id
                WHERE m.metric_key='likes' AND r.record_type='note_detail'""").fetchone()[0], 3)
            self.assertEqual(conn.execute("""SELECT period FROM metric_values
                WHERE metric_key='fan_snapshot.active_share_percent'""").fetchone()[0], "30d")
            all_fields = "\n".join(row[0] for row in conn.execute("SELECT fields_json FROM records"))
            self.assertIn("private-creator-id", all_fields)
            self.assertIn("Private Display Name", all_fields)
            self.assertIn("creator@example.test", all_fields)
            self.assertIn("13800138000", all_fields)
            self.assertIn("https://example.test/creator/private-creator-id", all_fields)
            self.assertIn("token_count", all_fields)
            self.assertNotIn("must-not-import", all_fields)
            self.assertNotIn("must-not-import-request-token", all_fields)
            self.assertNotIn("must-not-import-client-secret", all_fields)
            self.assertNotIn("must-not-import-session", all_fields)
            self.assertNotIn("must-not-import-access-token", all_fields)
            raw_comment_row = conn.execute("SELECT comment_level, raw_payload_json FROM comment_snapshots").fetchone()
            self.assertEqual(raw_comment_row[0], 2)
            raw_comment = raw_comment_row[1]
            self.assertIn("Visible Commenter", raw_comment)
            self.assertIn("visible-commenter-id", raw_comment)
            self.assertIn("commenter@example.test", raw_comment)
            self.assertIn("13900139000", raw_comment)
            self.assertIn("Visible Source Author", raw_comment)
            self.assertIn("https://example.test/author/visible-source-author", raw_comment)
            self.assertNotIn("cookie", raw_comment.lower())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE comment_text='not imported'").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
