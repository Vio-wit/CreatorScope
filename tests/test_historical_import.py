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
                {"note_key": "stable-note-key", "comment_text_excerpt": "excerpt only", "likes_raw": "2+", "identity_fields_omitted": True},
                {"note_key": "unmatched-note-key", "comment_text_excerpt": "unmatched excerpt", "identity_fields_omitted": True},
            ],
        )
        self._write_jsonl(
            self.probe / "raw_notes_xhs.jsonl",
            [{
                "note_id": self.note_id,
                "title": "Private Note Title",
                "body_text": "sample body",
                "hashtags": ["topic-a"],
                "captures": [
                    {"captured_at": "2026-11-20T10:00:00+08:00", "xhs_metrics_raw": {"likes": 10}},
                    {"captured_at": "2026-11-20T11:00:00+08:00", "xhs_metrics_raw": {"likes": 11}},
                ],
            }],
        )
        self._write_jsonl(
            self.probe / "raw_comments_xhs.jsonl",
            [{
                "note_id": self.note_id,
                "comment_text": "visible comment",
                "commenter_name_saved": False,
                "commenter_profile_saved": False,
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

    def test_historical_sources_are_idempotent_private_and_linked_by_exact_ids(self) -> None:
        source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.research.rglob("*.jsonl")}
        report_dir = self.research / "r4_4_integration"
        first = integrate_historical_data(self.db_path, self.root, report_dir)
        conn = sqlite3.connect(self.db_path)
        try:
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
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notes WHERE platform_note_id IS NOT NULL").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records WHERE record_type='note_detail'").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records WHERE record_type='comment_excerpt_sample'").fetchone()[0], 2)
            self.assertEqual(conn.execute("""SELECT COUNT(*) FROM records r JOIN notes n ON n.note_id=r.note_id
                WHERE r.record_type='comment_excerpt_sample' AND r.creator_id=n.creator_id""").fetchone()[0], 1)
            self.assertEqual(conn.execute("""SELECT COUNT(*) FROM records r
                WHERE r.record_type='comment_excerpt_sample' AND r.creator_id IS NULL AND r.note_id IS NULL""").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM comment_snapshots").fetchone()[0], 1)
            self.assertEqual(conn.execute("""SELECT COUNT(*) FROM metric_values m
                JOIN records r ON r.record_id = m.record_id
                WHERE m.metric_key='likes' AND r.record_type='note_detail'""").fetchone()[0], 2)
            self.assertEqual(conn.execute("""SELECT period FROM metric_values
                WHERE metric_key='fan_snapshot.active_share_percent'""").fetchone()[0], "30d")
            all_fields = "\n".join(row[0] for row in conn.execute("SELECT fields_json FROM records"))
            self.assertNotIn("private-creator-id", all_fields)
            self.assertNotIn("Private Display Name", all_fields)
            self.assertNotIn(self.note_id, all_fields)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE comment_text='not imported'").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
