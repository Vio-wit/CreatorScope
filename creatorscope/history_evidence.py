"""Isolated, append-only historical evidence import; never opens the app database.

The input files remain untouched. Imported text may contain personal information or
access-bearing URLs: keep evidence databases and generated reports out of Git.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

MARKER = "creatorscope-isolated-history-v1"
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (
  source_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, format TEXT NOT NULL,
  source_name TEXT NOT NULL, first_imported_at TEXT NOT NULL,
  source_capture_time TEXT, record_count INTEGER NOT NULL DEFAULT 0,
  parsed_count INTEGER NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE source_paths (
  source_id TEXT NOT NULL REFERENCES sources(source_id), path TEXT NOT NULL,
  first_seen_at TEXT NOT NULL, PRIMARY KEY(source_id,path)
);
CREATE TABLE import_runs (
  run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id),
  path TEXT NOT NULL, imported_at TEXT NOT NULL, duplicate_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL
);
CREATE TABLE identities (
  identity_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('identified','pending','conflict')),
  first_seen_at TEXT NOT NULL
);
CREATE TABLE identity_keys (
  identity_id TEXT NOT NULL REFERENCES identities(identity_id), platform TEXT NOT NULL,
  key_type TEXT NOT NULL, value TEXT NOT NULL,
  UNIQUE(platform,key_type,value), UNIQUE(identity_id,platform,key_type)
);
CREATE TABLE identity_aliases (
  identity_id TEXT NOT NULL REFERENCES identities(identity_id), name TEXT NOT NULL,
  PRIMARY KEY(identity_id,name)
);
CREATE TABLE source_records (
  record_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id),
  row_number INTEGER NOT NULL, raw_json TEXT NOT NULL, raw_text TEXT,
  row_digest TEXT NOT NULL, duplicate_content INTEGER NOT NULL DEFAULT 0,
  identity_id TEXT REFERENCES identities(identity_id), identity_status TEXT NOT NULL,
  capture_time TEXT, parse_status TEXT NOT NULL, error TEXT,
  UNIQUE(source_id,row_number)
);
CREATE TABLE identity_claims (
  record_id TEXT NOT NULL REFERENCES source_records(record_id), platform TEXT NOT NULL,
  key_type TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY(record_id,platform,key_type,value)
);
CREATE TABLE field_evidence (
  evidence_id INTEGER PRIMARY KEY, record_id TEXT NOT NULL REFERENCES source_records(record_id),
  original_field TEXT NOT NULL, target_field TEXT NOT NULL, raw_json TEXT NOT NULL,
  numeric_value REAL, text_value TEXT, unit TEXT, period TEXT, parse_status TEXT NOT NULL,
  mapping_version TEXT NOT NULL, UNIQUE(record_id,original_field,target_field)
);
CREATE INDEX idx_evidence_field ON field_evidence(target_field,parse_status);
CREATE INDEX idx_records_source ON source_records(source_id);
CREATE INDEX idx_records_digest ON source_records(row_digest);
CREATE INDEX idx_claim_lookup ON identity_claims(platform,key_type,value);
"""

FIELD_MAP = {
    "粉丝数": ("followers", "count"), "latest_known_followers": ("followers", "count"),
    "阅读中位数（日常）": ("daily_read_median", "count"),
    "阅读中位数_日常": ("daily_read_median", "count"),
    "互动中位数（日常）": ("daily_interaction_median", "count"),
    "互动中位数_日常": ("daily_interaction_median", "count"),
    "曝光中位数（合作）": ("sponsored_exposure_median", "count"),
    "阅读中位数（合作）": ("sponsored_read_median", "count"),
    "互动中位数（合作）": ("sponsored_interaction_median", "count"),
    "全部报价": ("all_price", "price"), "图文报价": ("image_price", "price"),
    "视频报价": ("video_price", "price"), "图文笔记一口价": ("image_fixed_price", "price"),
    "视频笔记一口价": ("video_fixed_price", "price"),
    "内容类目": ("category", "text"), "内容领域": ("category", "text"),
    "地域": ("region", "text"), "region": ("region", "text"),
    "获赞与收藏": ("likes_and_favorites", "count"),
    "发布笔记数": ("published_notes", "count"), "发布笔记": ("published_notes", "count"),
    "title": ("note_title", "text"), "笔记标题": ("note_title", "text"),
    "content": ("note_body", "text"), "body": ("note_body", "text"),
    "body_candidate": ("note_body_candidate", "text"),
    "topics_candidate": ("note_topics_candidate", "text"),
    "hashtags": ("note_topics", "text"), "话题": ("note_topics", "text"),
    "like_count": ("note_likes", "count"), "collect_count": ("note_favorites", "count"),
    "comment_count": ("note_comments", "count"), "share_count": ("note_shares", "count"),
    "comment_text": ("comment_text", "text"),
    "comment_like_text": ("comment_likes", "count"),
    "reply_count_if_visible": ("comment_replies", "count"),
}
IDENTIFIERS = {
    "小红书号": ("xiaohongshu", "public_account"), "xhs_account": ("xiaohongshu", "public_account"),
    "平台公开ID": ("xiaohongshu", "public_account"),
    "xhs_profile_id": ("xiaohongshu", "profile_id"),
    "蒲公英用户ID": ("pgy", "creator_id"), "pgy_id": ("pgy", "creator_id"),
}
EMPTY = {"", "-", "--", "—", "–", "暂无", "无", "未知", "未获取", "未公开", "待采集", "null", "none", "n/a"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _meaningful(value: Any) -> bool:
    return value is not None and str(value).strip().lower() not in EMPTY


def _open_database(path: Path) -> sqlite3.Connection:
    path = path.expanduser().resolve()
    if not path.parent.is_dir():
        raise ValueError("数据库父目录不存在；请先指定已有的隔离目录")
    if not path.exists():
        with path.open("xb"):
            pass
        conn = sqlite3.connect(path)
        try:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO metadata VALUES ('application', ?)", (MARKER,))
            conn.commit()
        except Exception:
            conn.close()
            raise
    else:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key='application'").fetchone()
            if not row or row[0] != MARKER:
                raise ValueError("目标不是 CreatorScope 隔离历史证据库，已拒绝写入")
        except (sqlite3.DatabaseError, ValueError):
            conn.close()
            raise ValueError("目标不是 CreatorScope 隔离历史证据库，已拒绝写入") from None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _capture_time(row: dict[str, Any]) -> str | None:
    # Page update dates are not collection timestamps.
    for key in ("采集时间", "抓取时间", "crawled_at", "collected_at", "captured_at"):
        if _meaningful(row.get(key)):
            return str(row[key]).strip()
    return None


def _claims(row: dict[str, Any]) -> list[tuple[str, str, str]]:
    result = set()
    for key, (platform, kind) in IDENTIFIERS.items():
        if _meaningful(row.get(key)):
            result.add((platform, kind, str(row[key]).strip()))
    for key in ("小红书主页链接", "小红书主页URL", "xhs_profile_url", "author_profile_url"):
        value = str(row.get(key) or "").strip()
        parsed = urlparse(value)
        match = re.fullmatch(r"/user/profile/([A-Za-z0-9_-]+)/*", parsed.path)
        if parsed.hostname in {"www.xiaohongshu.com", "xiaohongshu.com"} and match:
            result.add(("xiaohongshu", "profile_id", match.group(1)))
    return sorted(result)


def _identity(conn: sqlite3.Connection, claims: list[tuple[str, str, str]], name: str) -> tuple[str, str]:
    matches = set()
    for claim in claims:
        row = conn.execute("SELECT identity_id FROM identity_keys WHERE platform=? AND key_type=? AND value=?", claim).fetchone()
        if row:
            matches.add(row[0])
    kinds: dict[tuple[str, str], set[str]] = {}
    for platform, kind, value in claims:
        kinds.setdefault((platform, kind), set()).add(value)
    conflict = len(matches) > 1 or any(len(values) > 1 for values in kinds.values())
    identity_id = next(iter(matches)) if len(matches) == 1 else None
    if identity_id:
        for platform, kind, value in claims:
            prior = conn.execute("SELECT value FROM identity_keys WHERE identity_id=? AND platform=? AND key_type=?", (identity_id, platform, kind)).fetchone()
            if prior and prior[0] != value:
                conflict = True
    if conflict:
        identity_id = uuid4().hex
        status = "conflict"
    elif identity_id:
        status = "identified"
    else:
        identity_id = uuid4().hex
        status = "identified" if claims else "pending"
    if not matches or conflict:
        conn.execute("INSERT INTO identities VALUES (?,?,?)", (identity_id, status, now()))
    if not conflict:
        for platform, kind, value in claims:
            conn.execute("INSERT OR IGNORE INTO identity_keys VALUES (?,?,?,?)", (identity_id, platform, kind, value))
    if name:
        conn.execute("INSERT OR IGNORE INTO identity_aliases VALUES (?,?)", (identity_id, name))
    if matches and not conflict:
        status = "linked"
    return identity_id, status


def _value(value: Any, kind: str, target: str) -> tuple[float | None, str | None, str | None, str]:
    if value is None:
        return None, None, None, "null"
    if not _meaningful(value):
        return None, None, None, "displayed_unavailable" if str(value).strip() else "empty"
    if kind == "text":
        return None, json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value), None, "text"
    raw = str(value).strip()
    suffix = 1.0
    if raw.lower().endswith("w") or raw.endswith("万"):
        raw, suffix = raw[:-1], 10000.0
    if target == "published_notes" and raw.endswith("篇"):
        raw = raw[:-1].strip()
    unit = "count" if kind == "count" else ("CNY" if "¥" in raw or "￥" in raw or "元" in raw else None)
    is_start = bool(re.search(r"起\s*$", raw))
    if kind == "price":
        raw = re.sub(r"[¥￥元,，\s]|起$", "", raw)
    else:
        raw = re.sub(r"[,，\s]", "", raw)
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        return None, None, unit, "unparsed"
    # A bare price has no proven currency; keep its numeric value with NULL unit.
    return float(raw) * suffix, None, unit, "starting_price" if is_start else "parsed"


def _records(data: bytes, suffix: str) -> list[tuple[int, dict[str, Any], str | None, str | None]]:
    text = data.decode("utf-8-sig")
    if suffix == ".csv":
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV 表头缺失或重复，不能无损映射")
        result = []
        previous_line = reader.line_num
        for row in reader:
            start_line = previous_line + 1
            previous_line = reader.line_num
            error = "列数超过表头" if None in row else None
            # Preserve CSV null key by encoding it as a list of surplus values.
            if None in row:
                row["__extra_columns__"] = row.pop(None)
            result.append((start_line, row, None, error))
        return result
    if suffix == ".json":
        obj = json.loads(text)
        values = obj if isinstance(obj, list) else obj.get("records") if isinstance(obj, dict) and isinstance(obj.get("records"), list) else [obj]
        return [(i, x if isinstance(x, dict) else {"_value": x}, None, None if isinstance(x, dict) else "记录不是对象") for i, x in enumerate(values, 1)]
    if suffix == ".jsonl":
        result = []
        for i, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                x = json.loads(line)
                result.append((i, x if isinstance(x, dict) else {"_value": x}, line, None if isinstance(x, dict) else "记录不是对象"))
            except json.JSONDecodeError:
                result.append((i, {}, line, "JSONL 格式错误"))
        return result
    if suffix == ".txt":
        # Some note exports include the whole page. Extract a bounded candidate,
        # never claim it is cleaned正文; keep the complete original in raw_text.
        title = next((line.partition(":")[2].strip() for line in text.splitlines() if line.startswith("标题:")), "")
        fields = {"title": title} if title else {}
        lines = text.splitlines()
        anchors = [i for i, line in enumerate(lines) if i >= 3 and title and line.strip() == title]
        if len(anchors) == 1:
            start = anchors[0] + 1
            end = next((i for i in range(start, len(lines)) if lines[i].strip() == "猜你想搜" or re.fullmatch(r"共\s*\d+\s*条评论", lines[i].strip())), None)
            if end is not None and end > start:
                body = "\n".join(lines[start:end]).strip()
                if body:
                    fields["body_candidate"] = re.sub(r"https?://\S+", "[链接]", body)
                    topics = re.findall(r"#([^\s#]+)", fields["body_candidate"])
                    if topics:
                        fields["topics_candidate"] = topics
        return [(1, fields, text, None if title else "未识别标题")]
    raise ValueError("仅支持 CSV、JSON、JSONL 和笔记 TXT")


def import_file(db_path: Path, source_path: Path, source_name: str, *, identity_index_only: bool = False) -> dict[str, Any]:
    source_path = source_path.expanduser().resolve(strict=True)
    if source_path == db_path.expanduser().resolve():
        raise ValueError("来源文件不能是目标数据库")
    data = source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    suffix = source_path.suffix.lower()
    parsed = _records(data, suffix)
    conn = _open_database(db_path)
    try:
        with conn:
            existing = conn.execute("SELECT source_id, record_count FROM sources WHERE sha256=?", (digest,)).fetchone()
            imported_at = now()
            run_id = uuid4().hex
            if existing:
                conn.execute("INSERT OR IGNORE INTO source_paths VALUES (?,?,?)", (existing[0], str(source_path), imported_at))
                conn.execute("INSERT INTO import_runs VALUES (?,?,?,?,?,?)", (run_id, existing[0], str(source_path), imported_at, existing[1], "duplicate"))
                return {"source_id": existing[0], "run_id": run_id, "sha256": digest, "status": "duplicate", "records": existing[1], "duplicates": existing[1]}
            source_id = uuid4().hex
            times = {_capture_time(row) for _, row, _, err in parsed if not err}
            common_time = next(iter(times)) if len(times) == 1 and None not in times else None
            conn.execute("INSERT INTO sources (source_id,sha256,format,source_name,first_imported_at,source_capture_time) VALUES (?,?,?,?,?,?)", (source_id,digest,suffix[1:],source_name,imported_at,common_time))
            conn.execute("INSERT INTO source_paths VALUES (?,?,?)", (source_id,str(source_path),imported_at))
            errors = 0
            for row_number, row, raw_text, error in parsed:
                record_id = uuid4().hex
                claims = _claims(row)
                name = str(row.get("博主昵称") or row.get("creator_name") or row.get("创作者名称") or "").strip()
                is_creator = bool(name or claims)
                if identity_index_only:
                    identity_id, status = None, "index_only"
                elif is_creator and not error:
                    identity_id, status = _identity(conn, claims, name)
                else:
                    identity_id, status = None, "unassigned"
                errors += bool(error)
                raw_json = json.dumps(row,ensure_ascii=False)
                canonical = json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(",", ":"))
                row_digest = hashlib.sha256((canonical + "\0" + (raw_text or "")).encode("utf-8")).hexdigest()
                duplicate_content = int(bool(conn.execute("SELECT 1 FROM source_records WHERE row_digest=? LIMIT 1", (row_digest,)).fetchone()))
                conn.execute("INSERT INTO source_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (record_id,source_id,row_number,raw_json,raw_text,row_digest,duplicate_content,identity_id,status,_capture_time(row),"error" if error else "parsed",error))
                for platform, kind, value in claims:
                    conn.execute("INSERT INTO identity_claims VALUES (?,?,?,?)", (record_id,platform,kind,value))
                for original, (target, kind) in FIELD_MAP.items():
                    if original not in row:
                        continue
                    value = row[original]
                    numeric, text, unit, state = _value(value,kind,target)
                    if kind == "price" and state == "starting_price":
                        target += "_starting"
                    # A page-level period does not prove the period of every
                    # field. Only accept an explicitly field-scoped period.
                    period_raw = row.get(f"{original}统计周期")
                    period = str(period_raw).strip() if _meaningful(period_raw) else None
                    conn.execute("INSERT INTO field_evidence (record_id,original_field,target_field,raw_json,numeric_value,text_value,unit,period,parse_status,mapping_version) VALUES (?,?,?,?,?,?,?,?,?,?)", (record_id,original,target,json.dumps(value,ensure_ascii=False),numeric,text,unit,period,state,"v1"))
            conn.execute("UPDATE sources SET record_count=?,parsed_count=?,error_count=? WHERE source_id=?", (len(parsed),len(parsed)-errors,errors,source_id))
            conn.execute("INSERT INTO import_runs VALUES (?,?,?,?,?,?)", (run_id,source_id,str(source_path),imported_at,0,"completed"))
        return {"source_id": source_id,"run_id":run_id,"sha256":digest,"status":"completed","records":len(parsed),"parsed":len(parsed)-errors,"errors":errors}
    finally:
        conn.close()


def quality_report(db_path: Path) -> dict[str, Any]:
    path = db_path.expanduser().resolve(strict=True)
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT value FROM metadata WHERE key='application'").fetchone()[0] != MARKER:
            raise ValueError("不是隔离历史证据库")
        def rows(sql: str) -> list[dict[str, Any]]:
            return [dict(row) for row in conn.execute(sql)]
        return {
            "sources": rows("SELECT source_id,source_name,sha256,format,first_imported_at,source_capture_time,record_count,parsed_count,error_count FROM sources ORDER BY first_imported_at,source_name"),
            "source_paths": rows("SELECT source_id,path,first_seen_at FROM source_paths ORDER BY source_id,path"),
            "runs": rows("SELECT source_id,status,COUNT(*) AS runs,SUM(duplicate_count) AS duplicate_records FROM import_runs GROUP BY source_id,status"),
            "identity_records": rows("SELECT identity_status,COUNT(*) AS count FROM source_records GROUP BY identity_status"),
            "identity_by_source": rows("SELECT source_id,identity_status,COUNT(*) AS count FROM source_records GROUP BY source_id,identity_status"),
            "duplicate_rows_by_source": rows("SELECT source_id,SUM(duplicate_content) AS duplicate_rows FROM source_records GROUP BY source_id"),
            "identities": rows("SELECT status,COUNT(*) AS count FROM identities GROUP BY status"),
            "fields": rows("SELECT e.target_field,e.parse_status,COUNT(*) AS count FROM field_evidence e JOIN source_records r ON r.record_id=e.record_id WHERE r.identity_status<>'index_only' GROUP BY e.target_field,e.parse_status ORDER BY e.target_field,e.parse_status"),
            "fields_by_source": rows("SELECT r.source_id,e.target_field,e.parse_status,COUNT(*) AS count FROM field_evidence e JOIN source_records r ON r.record_id=e.record_id GROUP BY r.source_id,e.target_field,e.parse_status ORDER BY r.source_id,e.target_field,e.parse_status"),
            "source_field_coverage": rows("""SELECT r.source_id,e.target_field,s.record_count AS source_records,
                COUNT(DISTINCT e.record_id) AS observed_records,
                COUNT(DISTINCT CASE WHEN e.parse_status IN ('parsed','starting_price','text') THEN e.record_id END) AS available_records,
                ROUND(100.0 * COUNT(DISTINCT CASE WHEN e.parse_status IN ('parsed','starting_price','text') THEN e.record_id END) / NULLIF(s.record_count,0), 2) AS coverage_pct
                FROM field_evidence e JOIN source_records r ON r.record_id=e.record_id JOIN sources s ON s.source_id=r.source_id
                GROUP BY r.source_id,e.target_field,s.record_count ORDER BY r.source_id,e.target_field"""),
            "conflicts": rows("SELECT source_id,row_number,record_id FROM source_records WHERE identity_status='conflict' ORDER BY source_id,row_number"),
            "errors": rows("SELECT source_id,row_number,error FROM source_records WHERE parse_status='error' ORDER BY source_id,row_number"),
            "index_hint_records": conn.execute("""SELECT COUNT(DISTINCT c.record_id) FROM identity_claims c JOIN source_records r ON r.record_id=c.record_id WHERE r.identity_status<>'index_only' AND EXISTS (SELECT 1 FROM identity_claims i JOIN source_records ir ON ir.record_id=i.record_id WHERE ir.identity_status='index_only' AND i.platform=c.platform AND i.key_type=c.key_type AND i.value=c.value)""").fetchone()[0],
            "unknown_capture_time": conn.execute("SELECT COUNT(*) FROM source_records WHERE capture_time IS NULL").fetchone()[0],
        }
    finally:
        conn.close()


def trace_record(db_path: Path, record_id: str) -> dict[str, Any]:
    """Explicit opt-in trace may contain sensitive source text; never log by default."""
    conn = sqlite3.connect(f"{db_path.expanduser().resolve(strict=True).as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        marker = conn.execute("SELECT value FROM metadata WHERE key='application'").fetchone()
        if not marker or marker[0] != MARKER:
            raise ValueError("不是隔离历史证据库")
        record = conn.execute("SELECT * FROM source_records WHERE record_id=?", (record_id,)).fetchone()
        if not record:
            raise ValueError("未找到来源记录")
        hints = conn.execute("""SELECT DISTINCT ir.source_id,ir.row_number,ir.record_id
            FROM identity_claims c JOIN identity_claims i
              ON i.platform=c.platform AND i.key_type=c.key_type AND i.value=c.value
            JOIN source_records ir ON ir.record_id=i.record_id
            WHERE c.record_id=? AND ir.identity_status='index_only' ORDER BY ir.source_id,ir.row_number""", (record_id,)).fetchall()
        return {"record": dict(record), "paths": [dict(x) for x in conn.execute("SELECT path FROM source_paths WHERE source_id=?", (record['source_id'],))],
                "claims": [dict(x) for x in conn.execute("SELECT platform,key_type,value FROM identity_claims WHERE record_id=?", (record_id,))],
                "fields": [dict(x) for x in conn.execute("SELECT * FROM field_evidence WHERE record_id=?", (record_id,))],
                "index_candidates": [dict(x) for x in hints]}
    finally:
        conn.close()


def list_records(db_path: Path, source_id: str, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("limit 必须为 1–500，offset 不能为负")
    conn = sqlite3.connect(f"{db_path.expanduser().resolve(strict=True).as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        marker = conn.execute("SELECT value FROM metadata WHERE key='application'").fetchone()
        if not marker or marker[0] != MARKER:
            raise ValueError("不是隔离历史证据库")
        return [dict(row) for row in conn.execute(
            "SELECT record_id,row_number,identity_status,parse_status,error FROM source_records WHERE source_id=? ORDER BY row_number LIMIT ? OFFSET ?",
            (source_id, limit, offset),
        )]
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="CreatorScope 隔离历史证据导入")
    parser.add_argument("--db", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("path", type=Path)
    imp.add_argument("--name", required=True)
    imp.add_argument("--identity-index-only", action="store_true")
    sub.add_parser("report")
    trace = sub.add_parser("trace")
    trace.add_argument("record_id")
    listing = sub.add_parser("list-records")
    listing.add_argument("source_id")
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    result = (import_file(args.db,args.path,args.name,identity_index_only=args.identity_index_only) if args.action == "import"
              else trace_record(args.db,args.record_id) if args.action == "trace"
              else list_records(args.db,args.source_id,limit=args.limit,offset=args.offset) if args.action == "list-records"
              else quality_report(args.db))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
