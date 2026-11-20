from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = PROJECT_ROOT / "research" / "r4_2_comment_validation"
DEFAULT_DB = PROJECT_ROOT / "instance" / "creatorscope_unified.sqlite3"


def parse_exact_count(raw: Any) -> int | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return int(value) if re.fullmatch(r"\d+", value) else None


def clean_text(raw: str) -> str:
    """Keep source text intact; this derived value only folds whitespace."""
    return re.sub(r"\s+", " ", raw).strip()


def load_source(
    source_dir: Path = SOURCE_DIR,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], dict[str, str]]:
    raw_path = source_dir / "raw_comments.jsonl"
    quality_path = source_dir / "quality_report.json"
    log_path = source_dir / "collection_log.json"
    rows = [
        json.loads(line)
        for line in raw_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    note_metadata = {
        item["note_id"]: {
            "creator_name": item["creator_name"],
            "title": item["title"],
        }
        for item in quality.get("by_note", [])
        if item.get("status") == "opened" and item.get("note_id")
    }
    events = json.loads(log_path.read_text(encoding="utf-8"))
    batch_events = {event.get("event"): event.get("time") for event in events}
    batch_info = {
        "batch_id": quality.get("batch_id", ""),
        "started_at": batch_events.get("batch_start"),
        "completed_at": batch_events.get("batch_finished"),
    }
    return rows, note_metadata, batch_info


def validate_source(
    rows: list[dict[str, Any]],
    note_metadata: dict[str, dict[str, str]],
    batch_info: dict[str, str],
) -> None:
    if len(rows) != 29:
        raise ValueError(f"R4.2 source row count changed: expected 29, got {len(rows)}")
    if not batch_info.get("batch_id") or not all(
        batch_info.get(key) for key in ("started_at", "completed_at")
    ):
        raise ValueError("R4.2 batch metadata is incomplete")
    required = {
        "batch_id", "note_id", "local_comment_id", "comment_text",
        "comment_level", "capture_time",
    }
    if any(not required.issubset(row) for row in rows):
        raise ValueError("One or more R4.2 rows are missing required fields")
    if any(not str(row.get("comment_text") or "").strip() for row in rows):
        raise ValueError("Empty comment text found; import stopped before writing")
    if any(row["batch_id"] != batch_info["batch_id"] for row in rows):
        raise ValueError("Mixed or mismatched collection batch IDs")
    if any(row["note_id"] not in note_metadata for row in rows):
        raise ValueError("A note has no verified creator/title source mapping")
    ids = {(row["note_id"], row["local_comment_id"]) for row in rows}
    if len(ids) != len(rows):
        raise ValueError("Duplicate same-batch comment fingerprints found in source")
    roots = {row["local_comment_id"] for row in rows if row["comment_level"] == 1}
    if any(
        row["comment_level"] == 2 and row.get("parent_comment_id") not in roots
        for row in rows
    ):
        raise ValueError("A reply does not reference a root comment in this batch")


def local_creator_id(creator_name: str) -> str:
    digest = hashlib.sha256(creator_name.encode("utf-8")).hexdigest()[:16]
    return f"local_creator_{digest}"


def import_data(
    db_path: Path = DEFAULT_DB,
    source_dir: Path = SOURCE_DIR,
) -> dict[str, int | str]:
    from creatorscope.migrations import import_r42_comment_source

    return import_r42_comment_source(Path(db_path), Path(source_dir))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Idempotently add the R4.2 visible comment batch to the unified database."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    args = parser.parse_args()
    result = import_data(args.db, args.source_dir)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
