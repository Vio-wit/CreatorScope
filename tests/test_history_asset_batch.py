from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from creatorscope.history_asset_batch import discover_assets


class HistoryAssetBatchTests(unittest.TestCase):
    def test_manifest_includes_creator_sources_and_only_note_folder_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "02_MASTER_CREATOR_INDEX_REBUILD_20260820.csv").write_text("creator_name\nA\n", encoding="utf-8")
            (root / "MASTER_CREATOR_INDEX_20260820.csv").write_text("creator_name\nB\n", encoding="utf-8")
            (root / "09_IDENTITY_DEDUP_CORRECTED_20260820.csv").write_text("xhs_profile_id\np1\n", encoding="utf-8")
            blogger = root / "influencer-ai/backend/datasets/output/bloggers/creator-a"
            notes = blogger / "notes"
            notes.mkdir(parents=True)
            (blogger / "record.json").write_text('{"博主昵称":"A"}', encoding="utf-8")
            (blogger / "detail.txt").write_text("达人详情", encoding="utf-8")
            (notes / "note.txt").write_text("标题: 笔记", encoding="utf-8")

            assets = discover_assets(root)
            by_name = {asset.path.name: asset for asset in assets}
            self.assertIn("02_MASTER_CREATOR_INDEX_REBUILD_20260820.csv", by_name)
            self.assertIn("MASTER_CREATOR_INDEX_20260820.csv", by_name)
            self.assertIn("record.json", by_name)
            self.assertIn("note.txt", by_name)
            self.assertNotIn("detail.txt", by_name)
            self.assertTrue(by_name["09_IDENTITY_DEDUP_CORRECTED_20260820.csv"].identity_index_only)
            self.assertFalse(by_name["02_MASTER_CREATOR_INDEX_REBUILD_20260820.csv"].identity_index_only)

    def test_manifest_deduplicates_the_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "02_MASTER_CREATOR_INDEX_REBUILD_20260820.csv"
            path.write_text("creator_name\nA\n", encoding="utf-8")
            assets = discover_assets(root)
            self.assertEqual(sum(asset.path == path.resolve() for asset in assets), 1)


if __name__ == "__main__":
    unittest.main()
