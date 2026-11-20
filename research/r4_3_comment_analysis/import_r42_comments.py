from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = PROJECT_ROOT / "research" / "r4_2_comment_validation"
OUTPUT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = OUTPUT_DIR / "validation.sqlite3"
REPORT_DIR = OUTPUT_DIR / "reports"
EXPORT_DIR = OUTPUT_DIR / "exports"

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS collection_batches (
    batch_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    status TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS comment_creators (
    creator_id TEXT PRIMARY KEY,
    creator_name TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comment_notes (
    note_id TEXT PRIMARY KEY,
    creator_id TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT,
    note_type TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (creator_id) REFERENCES comment_creators(creator_id)
);
CREATE TABLE IF NOT EXISTS comment_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    local_comment_id TEXT NOT NULL,
    parent_comment_id TEXT,
    comment_text TEXT NOT NULL,
    cleaned_text TEXT NOT NULL,
    displayed_time TEXT,
    like_count_raw TEXT,
    like_count_normalized INTEGER,
    reply_count_raw TEXT,
    is_pinned INTEGER CHECK (is_pinned IN (0, 1) OR is_pinned IS NULL),
    comment_level INTEGER NOT NULL CHECK (comment_level IN (1, 2)),
    source TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    UNIQUE (batch_id, note_id, local_comment_id),
    FOREIGN KEY (batch_id) REFERENCES collection_batches(batch_id),
    FOREIGN KEY (note_id) REFERENCES comment_notes(note_id),
    FOREIGN KEY (batch_id, note_id, parent_comment_id)
      REFERENCES comment_snapshots(batch_id, note_id, local_comment_id)
      DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS idx_comment_snapshots_batch_note
  ON comment_snapshots(batch_id, note_id, comment_level);
CREATE INDEX IF NOT EXISTS idx_comment_snapshots_parent
  ON comment_snapshots(batch_id, note_id, parent_comment_id);
"""


def parse_exact_count(raw: Any) -> int | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return int(value) if re.fullmatch(r"\d+", value) else None


def clean_text(raw: str) -> str:
    """Keep the source text intact; this derived field only folds whitespace."""
    return re.sub(r"\s+", " ", raw).strip()


def load_source(source_dir: Path = SOURCE_DIR) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], dict[str, str]]:
    raw_path = source_dir / "raw_comments.jsonl"
    quality_path = source_dir / "quality_report.json"
    log_path = source_dir / "collection_log.json"
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
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


def validate_source(rows: list[dict[str, Any]], note_metadata: dict[str, dict[str, str]], batch_info: dict[str, str]) -> None:
    if len(rows) != 29:
        raise ValueError(f"R4.2 source row count changed: expected 29, got {len(rows)}")
    if not batch_info.get("batch_id") or not all(batch_info.get(key) for key in ("started_at", "completed_at")):
        raise ValueError("R4.2 batch metadata is incomplete")
    required = {"batch_id", "note_id", "local_comment_id", "comment_text", "comment_level", "capture_time"}
    if any(not required.issubset(row) for row in rows):
        raise ValueError("One or more R4.2 rows are missing required fields")
    if any(not str(row.get("comment_text") or "").strip() for row in rows):
        raise ValueError("Empty comment text found; import stopped before writing")
    if any(row["batch_id"] != batch_info["batch_id"] for row in rows):
        raise ValueError("Mixed or mismatched collection batch IDs")
    if any(row["note_id"] not in note_metadata for row in rows):
        raise ValueError("A note has no verified creator/title mapping in the R4.2 quality report")
    ids = {(row["note_id"], row["local_comment_id"]) for row in rows}
    if len(ids) != len(rows):
        raise ValueError("Duplicate same-batch local fingerprints found in source")
    roots = {row["local_comment_id"] for row in rows if row["comment_level"] == 1}
    if any(row["comment_level"] == 2 and row.get("parent_comment_id") not in roots for row in rows):
        raise ValueError("A reply does not reference a root comment in this batch")


def local_creator_id(creator_name: str) -> str:
    digest = hashlib.sha256(creator_name.encode("utf-8")).hexdigest()[:16]
    return f"local_creator_{digest}"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def export_batch(conn: sqlite3.Connection, batch_id: str, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        SELECT s.batch_id, s.note_id, n.title AS note_title,
               c.creator_name, s.local_comment_id, s.parent_comment_id,
               s.comment_text, s.cleaned_text, s.displayed_time,
               s.like_count_raw, s.like_count_normalized, s.reply_count_raw,
               s.is_pinned, s.comment_level, s.source, s.captured_at,
               s.raw_payload
        FROM comment_snapshots s
        JOIN comment_notes n ON n.note_id=s.note_id
        JOIN comment_creators c ON c.creator_id=n.creator_id
        WHERE s.batch_id=?
        ORDER BY s.note_id, s.comment_level, s.id
        """,
        (batch_id,),
    ).fetchall()
    fields = list(rows[0].keys()) if rows else [
        "batch_id", "note_id", "note_title", "creator_name", "local_comment_id",
        "parent_comment_id", "comment_text", "cleaned_text", "displayed_time",
        "like_count_raw", "like_count_normalized", "reply_count_raw", "is_pinned",
        "comment_level", "source", "captured_at", "raw_payload",
    ]
    jsonl_path = output_dir / "comments_export.jsonl"
    csv_path = output_dir / "comments_export.csv"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            item = dict(row)
            item["is_pinned"] = None if item["is_pinned"] is None else bool(item["is_pinned"])
            item["raw_payload"] = json.loads(item["raw_payload"])
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            item = dict(row)
            if item["is_pinned"] is not None:
                item["is_pinned"] = bool(item["is_pinned"])
            writer.writerow(item)
    return len(rows)


def write_import_report(db_path: Path, output_dir: Path, run_result: dict[str, Any]) -> None:
    report_dir = output_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    history_path = report_dir / "import_runs.jsonl"
    with history_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(run_result, ensure_ascii=False) + "\n")
    history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    lines = [
        "# R4.3 评论数据导入报告", "",
        f"- 验证数据库：`{db_path}`", "- 来源：R4.2 小红书真实评论 JSONL（原文保留，不做脱敏或改写）。",
        f"- 当前数据库记录数：{run_result['total_records']}；一级评论 {run_result['root_records']}，回复 {run_result['reply_records']}。",
        f"- 笔记：{run_result['note_count']}；创作者：{run_result['creator_count']}。",
        f"- 当前导入运行：新增 {run_result['inserted']}，同批已存在/忽略 {run_result['duplicate_skips']}。",
        f"- 数据库完整性：外键错误 {run_result['foreign_key_errors']}。", "",
        "## 同批重复导入记录", "", "| 运行时间 | 新增 | 幂等忽略 | 入库总数 |", "|---|---:|---:|---:|",
    ]
    for run in history:
        lines.append(f"| {run['run_at']} | {run['inserted']} | {run['duplicate_skips']} | {run['total_records']} |")
    lines += [
        "", "## 数据保留说明", "",
        "`comment_text` 按 R4.2 原文保存；`cleaned_text` 是独立派生列，仅折叠首尾/重复空白，原始列不被覆盖。评论者身份不在 R4.2 源数据中，本轮不补采。`10+` 和纯操作词等非精确值继续保留原始值，数值列保持 NULL。", "",
        "`local_comment_id` 只作为同批次指纹；不用于跨批次主体合并。", "",
    ]
    (report_dir / "import_report.md").write_text("\n".join(lines), encoding="utf-8")


def import_data(db_path: Path = DEFAULT_DB, source_dir: Path = SOURCE_DIR, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    rows, note_metadata, batch_info = load_source(source_dir)
    validate_source(rows, note_metadata, batch_info)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        batch_id = batch_info["batch_id"]
        batch_meta = {"source_artifact": "research/r4_2_comment_validation/raw_comments.jsonl", "source_rows": len(rows)}
        conn.execute(
            """INSERT OR IGNORE INTO collection_batches
               (batch_id, source, started_at, completed_at, status, record_count, metadata_json)
               VALUES (?, 'xiaohongshu', ?, ?, 'completed', ?, ?)""",
            (batch_id, batch_info["started_at"], batch_info["completed_at"], len(rows), json.dumps(batch_meta, ensure_ascii=False)),
        )
        creator_ids: dict[str, str] = {}
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for metadata in note_metadata.values():
            name = metadata["creator_name"]
            creator_id = local_creator_id(name)
            creator_ids[name] = creator_id
            conn.execute(
                "INSERT OR IGNORE INTO comment_creators(creator_id, creator_name, platform, created_at) VALUES (?, ?, 'xiaohongshu', ?)",
                (creator_id, name, now),
            )
        for note_id, metadata in note_metadata.items():
            conn.execute(
                """INSERT OR IGNORE INTO comment_notes
                   (note_id, creator_id, source, title, published_at, note_type, created_at)
                   VALUES (?, ?, 'xiaohongshu', ?, NULL, NULL, ?)""",
                (note_id, creator_ids[metadata["creator_name"]], metadata["title"], now),
            )
            saved = conn.execute("SELECT creator_id, title FROM comment_notes WHERE note_id=?", (note_id,)).fetchone()
            if saved["creator_id"] != creator_ids[metadata["creator_name"]] or saved["title"] != metadata["title"]:
                raise ValueError(f"Existing note metadata conflicts with verified source mapping: {note_id}")

        inserted = 0
        duplicate_skips = 0
        for row in sorted(rows, key=lambda item: (item["comment_level"], item["note_id"], item["local_comment_id"])):
            pinned = row.get("is_pinned")
            pinned_int = None if pinned is None else int(bool(pinned))
            values = (
                batch_id, row["note_id"], row["local_comment_id"], row.get("parent_comment_id"),
                row["comment_text"], clean_text(row["comment_text"]), row.get("displayed_time"),
                row.get("like_count_raw"), row.get("like_count_normalized"), row.get("reply_count_raw"),
                pinned_int, int(row["comment_level"]), row.get("source") or "xiaohongshu",
                row["capture_time"], json.dumps(row, ensure_ascii=False, sort_keys=True),
            )
            cur = conn.execute(
                """INSERT OR IGNORE INTO comment_snapshots
                   (batch_id, note_id, local_comment_id, parent_comment_id, comment_text,
                    cleaned_text, displayed_time, like_count_raw, like_count_normalized,
                    reply_count_raw, is_pinned, comment_level, source, captured_at, raw_payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            if cur.rowcount:
                inserted += 1
            else:
                duplicate_skips += 1
                existing = conn.execute(
                    "SELECT raw_payload FROM comment_snapshots WHERE batch_id=? AND note_id=? AND local_comment_id=?",
                    (batch_id, row["note_id"], row["local_comment_id"]),
                ).fetchone()
                if existing is None or existing["raw_payload"] != values[-1]:
                    raise ValueError("An idempotency key already exists with different source payload; import stopped")
        count = conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE batch_id=?", (batch_id,)).fetchone()[0]
        conn.execute("UPDATE collection_batches SET record_count=? WHERE batch_id=?", (count, batch_id))
        foreign_key_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        conn.commit()
        root_count = conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE batch_id=? AND comment_level=1", (batch_id,)).fetchone()[0]
        reply_count = conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE batch_id=? AND comment_level=2", (batch_id,)).fetchone()[0]
        note_count = conn.execute("SELECT COUNT(*) FROM comment_notes").fetchone()[0]
        creator_count = conn.execute("SELECT COUNT(*) FROM comment_creators").fetchone()[0]
        if foreign_key_errors:
            raise sqlite3.IntegrityError(f"Foreign key check returned {foreign_key_errors} errors")
        if count != 29 or root_count != 28 or reply_count != 1:
            raise ValueError(f"Unexpected imported record counts: total={count}, roots={root_count}, replies={reply_count}")
        exported = export_batch(conn, batch_id, output_dir / "exports")
        run_result = {
            "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "batch_id": batch_id,
            "inserted": inserted,
            "duplicate_skips": duplicate_skips,
            "total_records": count,
            "root_records": root_count,
            "reply_records": reply_count,
            "note_count": note_count,
            "creator_count": creator_count,
            "foreign_key_errors": foreign_key_errors,
            "exported_records": exported,
        }
        write_import_report(db_path, output_dir, run_result)
        return run_result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Idempotently import the actual R4.2 comment sample into an isolated SQLite database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    result = import_data(args.db, args.source_dir, args.output_dir)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
