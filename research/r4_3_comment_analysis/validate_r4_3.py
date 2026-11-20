from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from creatorscope import create_app

from comment_analysis import DEFAULT_DB, CHARTS_DIR, EXPORTS_DIR, fetch_comments, summarize, load_stopwords, tokenize


SOURCE = ROOT.parent / "r4_2_comment_validation"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_validation(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    source_rows = [json.loads(line) for line in (SOURCE / "raw_comments.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    exported = [json.loads(line) for line in (EXPORTS_DIR / "comments_export.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    with (EXPORTS_DIR / "comments_export.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    by_key = {(row["batch_id"], row["note_id"], row["local_comment_id"]): row for row in source_rows}
    export_by_key = {(row["batch_id"], row["note_id"], row["local_comment_id"]): row for row in exported}
    check(len(source_rows) == 29 and len(exported) == 29 and len(csv_rows) == 29, "JSONL/CSV/import row count differs from R4.2 source")
    check(set(by_key) == set(export_by_key), "Exported comment keys differ from the source")
    check(all(export_by_key[key]["comment_text"] == source["comment_text"] for key, source in by_key.items()), "Original comment text changed in JSONL export")
    check(all(export_by_key[key]["raw_payload"] == source for key, source in by_key.items()), "Raw payload did not round-trip exactly")
    check(all(row["comment_text"] == by_key[(row["batch_id"], row["note_id"], row["local_comment_id"])]["comment_text"] for row in csv_rows), "Original comment text changed in CSV export")

    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        counts = {level: conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE comment_level=?", (level,)).fetchone()[0] for level in (1, 2)}
        total = conn.execute("SELECT COUNT(*) FROM comment_snapshots").fetchone()[0]
        duplicate_keys = conn.execute("SELECT COUNT(*) FROM (SELECT batch_id,note_id,local_comment_id FROM comment_snapshots GROUP BY 1,2,3 HAVING COUNT(*)>1)").fetchone()[0]
        null_metric_count = conn.execute("SELECT COUNT(*) FROM comment_snapshots WHERE like_count_normalized IS NULL").fetchone()[0]
        bad_parent_count = conn.execute("SELECT COUNT(*) FROM comment_snapshots child LEFT JOIN comment_snapshots parent ON parent.batch_id=child.batch_id AND parent.note_id=child.note_id AND parent.local_comment_id=child.parent_comment_id WHERE child.comment_level=2 AND parent.id IS NULL").fetchone()[0]
    finally:
        conn.close()
    check(integrity == "ok" and not foreign_keys, "SQLite integrity or foreign key check failed")
    check((total, counts[1], counts[2]) == (29, 28, 1), "Snapshot hierarchy count does not match R4.2")
    check(duplicate_keys == 0 and bad_parent_count == 0, "Duplicate batch keys or reply parent association found")
    check(null_metric_count > 0, "Missing or non-exact metrics were not retained as NULL")

    app = create_app({
        "DATABASE_PATH": Path(":memory:"),
        "COMMENT_ANALYSIS_DATABASE_PATH": db_path.resolve(),
        "TESTING": True,
    })
    client = app.test_client()
    page = client.get("/comments")
    options_response = client.get("/api/comments/options")
    all_response = client.get("/api/comments/analysis")
    check(page.status_code == 200 and b"\xe8\xaf\x84\xe8\xae\xba\xe5\x88\x86\xe6\x9e\x90" in page.data, "Comment page route did not render")
    check(app.config["DATABASE_PATH"] != app.config["COMMENT_ANALYSIS_DATABASE_PATH"], "Comment routes must use the dedicated database config")
    options = options_response.get_json()
    all_data = all_response.get_json()
    check(options_response.status_code == 200 and len(options["creators"]) == 2 and len(options["notes"]) == 3 and len(options["batches"]) == 1, "Creator/note/batch options mismatch")
    check(all_data["summary"]["comment_count"] == 29, "Dashboard total count mismatch")
    note_id = options["notes"][0]["note_id"]
    expected_note_count = sum(
        1 for row in all_data["comments"] if row["note_id"] == note_id
    )
    note_data = client.get("/api/comments/analysis", query_string={"note_id": note_id}).get_json()
    level_data = client.get("/api/comments/analysis", query_string={"comment_level": "2"}).get_json()
    creator_id = options["creators"][0]["creator_id"]
    creator_data = client.get("/api/comments/analysis", query_string={"creator_id": creator_id}).get_json()
    keyword_data = client.get("/api/comments/analysis", query_string={"q": "链接"}).get_json()
    check(
        expected_note_count > 0
        and note_data["summary"]["comment_count"] == expected_note_count,
        "Note filter count mismatch",
    )
    check(level_data["summary"]["comment_count"] == 1 and level_data["comments"][0]["parent_comment_id"], "Reply level or parent filter mismatch")
    check(creator_data["summary"]["comment_count"] > 0, "Creator filter returned no records")
    check(keyword_data["summary"]["comment_count"] > 0 and all("链接" in row["comment_text"] or "链接" in row["note_title"] or "链接" in row["creator_name"] for row in keyword_data["comments"]), "Keyword filter mismatch")
    cloud = client.get("/api/comments/wordcloud.png", query_string={"note_id": note_id})
    check(cloud.status_code == 200 and cloud.data.startswith(b"\x89PNG\r\n\x1a\n"), "Filtered wordcloud endpoint failed")
    web_csv = client.get("/api/comments/export.csv")
    web_jsonl = client.get("/api/comments/export.jsonl")
    web_jsonl_rows = [json.loads(line) for line in web_jsonl.data.decode("utf-8").splitlines() if line.strip()]
    check(web_csv.status_code == 200 and len(list(csv.DictReader(web_csv.data.decode("utf-8-sig").splitlines()))) == 29, "Web CSV export count mismatch")
    check(web_jsonl.status_code == 200 and len(web_jsonl_rows) == 29 and all(row["comment_text"] == by_key[(row["batch_id"], row["note_id"], row["local_comment_id"])]["comment_text"] for row in web_jsonl_rows), "Web JSONL export changed source comment text")
    check(len([row for row in fetch_comments(db_path)]) == 29 and summarize(fetch_comments(db_path))["summary"]["comment_count"] == 29, "Analysis service did not return all real rows")

    charts = sorted(path.name for path in CHARTS_DIR.glob("*.png"))
    check(len(charts) >= 7, "Expected real sample charts were not generated")
    check((EXPORTS_DIR / "word_frequency.csv").exists(), "Word frequency CSV missing")
    with (EXPORTS_DIR / "word_frequency.csv").open(encoding="utf-8-sig", newline="") as stream:
        word_rows = list(csv.DictReader(stream))
    check(word_rows and all(row.get("frequency", "").isdigit() for row in word_rows), "Word frequency counts are missing")
    negation_tokens = tokenize("不喜欢没有不是", load_stopwords())
    check(all(token in negation_tokens for token in ("不", "没有", "不是")), "Configured stopwords unexpectedly removed negation terms")
    batch_id = options["batches"][0]["batch_id"]
    batch_data = client.get("/api/comments/analysis", query_string={"batch_id": batch_id}).get_json()
    pinned_expected = sum(row["is_pinned"] is False for row in all_data["comments"])
    pinned_data = client.get("/api/comments/analysis", query_string={"is_pinned": "false"}).get_json()
    check(batch_data["summary"]["comment_count"] == 29, "Batch filter mismatch")
    check(pinned_data["summary"]["comment_count"] == pinned_expected, "Pinned-state filter mismatch")
    result = {
        "status": "PASS",
        "database_integrity": integrity,
        "foreign_key_errors": len(foreign_keys),
        "source_rows": len(source_rows),
        "jsonl_export_rows": len(exported),
        "csv_export_rows": len(csv_rows),
        "comments": total,
        "root_comments": counts[1],
        "replies": counts[2],
        "duplicate_keys": duplicate_keys,
        "invalid_parent_links": bad_parent_count,
        "creator_options": len(options["creators"]),
        "note_options": len(options["notes"]),
        "batch_options": len(options["batches"]),
        "comment_database_separate_from_main": app.config["DATABASE_PATH"] != app.config["COMMENT_ANALYSIS_DATABASE_PATH"],
        "batch_filter_count": batch_data["summary"]["comment_count"],
        "not_pinned_filter_count": pinned_data["summary"]["comment_count"],
        "note_filter_count": note_data["summary"]["comment_count"],
        "reply_filter_count": level_data["summary"]["comment_count"],
        "keyword_filter_count": keyword_data["summary"]["comment_count"],
        "wordcloud_png": True,
        "charts": charts,
        "text_export_unchanged": True,
        "web_csv_export_rows": len(list(csv.DictReader(web_csv.data.decode("utf-8-sig").splitlines()))),
        "web_jsonl_export_rows": len(web_jsonl_rows),
    }
    report_path = ROOT / "reports" / "validation_report.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run_validation(), ensure_ascii=True))
