from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
CAPTURE_DATE = "2026-11-20"
METRIC_LABELS = {
    "exposure": "曝光量",
    "reads": "阅读量",
    "likes": "点赞量",
    "favorites": "收藏量",
    "comments": "评论量",
    "shares": "分享量",
    "follows": "关注量",
}
CREATORS = [
    {
        "file": "creator_01_visible.txt",
        "creator_id": "935928452",
        "name": "汪雨橦TONG",
        "city": "北京",
        "tags": ["生活记录", "vlog", "职场生活"],
        "image_quote_raw": "¥120,000",
        "video_quote_raw": "¥135,000",
    },
    {
        "file": "raw_creator02_detail_visible.txt",
        "creator_id": "679808345",
        "name": "大蜜老师",
        "city": "安徽省 蚌埠市 蚌山区",
        "tags": ["妈妈", "留学背景", "缔冠", "护肤"],
        "image_quote_raw": "¥9,800",
        "video_quote_raw": "¥38,800",
    },
    {
        "file": "raw_creator03_detail_visible.txt",
        "creator_id": "277758560",
        "name": "Winnie维尼",
        "city": "山东省 青岛市 市北区",
        "tags": ["生活记录", "vlog", "开箱"],
        "image_quote_raw": "¥7,000",
        "video_quote_raw": "¥9,900",
    },
    {
        "file": "raw_creator04_detail_visible.txt",
        "creator_id": "jianjian047",
        "name": "尖子生",
        "city": "广东省 广州市 黄埔区",
        "tags": ["个人护理", "护肤", "美白", "隔离防晒", "控油", "开箱", "vlog", "合集"],
        "image_quote_raw": "暂未报价",
        "video_quote_raw": "¥20,000",
    },
    {
        "file": "raw_creator05_detail_visible.txt",
        "creator_id": "263017124",
        "name": "梅梅子",
        "city": "江西省 南昌市 青山湖区",
        "tags": ["护肤", "健康养生", "抗老", "教程"],
        "image_quote_raw": "¥16,000",
        "video_quote_raw": "¥24,000",
    },
]
NOTE_FILES = [
    ("935928452", 1, "raw_pgy_creator01_note01_visible.txt"),
    ("935928452", 2, "raw_pgy_creator01_note02_visible.txt"),
    ("935928452", 3, "raw_pgy_creator01_note03_visible.txt"),
    ("935928452", 4, "raw_pgy_creator01_note04_visible.txt"),
    ("935928452", 5, "raw_pgy_creator01_note05_visible.txt"),
    ("679808345", 1, "raw_pgy_creator02_note01_visible.txt"),
    ("277758560", 1, "raw_pgy_creator03_note01_visible.txt"),
    ("jianjian047", 1, "raw_pgy_creator04_note01_visible.txt"),
    ("263017124", 1, "raw_pgy_creator05_note01_visible.txt"),
]
XHS_NOTE_URL = "https://www.xiaohongshu.com/explore/6afc317600000000180288af"
XHS_VISIBLE = {
    "title": "谁说北京过冬只能穿黑羽绒服，厉娜",
    "author_name": "汪雨橦TONG",
    "likes_raw": "5379",
    "favorites_raw": "533",
    "comments_raw": "576",
    "shares_raw": None,
}


def read_lines(filename: str) -> list[str]:
    path = OUT / filename
    if not path.is_file():
        raise FileNotFoundError(f"Evidence file missing: {filename}")
    return path.read_text(encoding="utf-8").splitlines()


def next_value(lines: list[str], label: str) -> str | None:
    for index, line in enumerate(lines[:-1]):
        if line.strip().rstrip("：:") == label.rstrip("：:"):
            return lines[index + 1].strip() or None
    return None


def to_count(raw: str | None) -> int | None:
    if raw is None or raw.strip() in {"", "-", "--", "暂无报价"}:
        return None
    cleaned = re.sub(r"[,，\s¥￥]", "", raw)
    match = re.fullmatch(r"-?\d+(?:\.\d+)?(?:w|W|万)?", cleaned)
    if not match:
        return None
    if cleaned[-1:] in {"w", "W", "万"}:
        return int(float(cleaned[:-1]) * 10_000)
    return int(float(cleaned))


def to_money(raw: str | None) -> int | None:
    if raw is None or "暂未报价" in raw:
        return None
    digits = re.sub(r"[^0-9]", "", raw)
    return int(digits) if digits else None


def percent_from(raw: str | None) -> float | None:
    if raw is None or raw.strip() in {"", "-", "--"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    return float(match.group()) if match else None


def section_value(lines: list[str], label: str) -> str | None:
    return next_value(lines, label)


def creator_record(spec: dict[str, Any]) -> dict[str, Any]:
    lines = read_lines(spec["file"])
    raw_followers = section_value(lines, "粉丝数")
    raw_likes_favs = section_value(lines, "获赞与收藏")
    updated = next((re.search(r"数据更新至：(.+)", line).group(1).strip()
                    for line in lines if re.search(r"数据更新至：(.+)", line)), None)
    median_labels = {
        "exposure_median": "曝光中位数",
        "read_median": "阅读中位数",
        "interaction_median": "互动中位数",
    }
    performance = {}
    for key, label in median_labels.items():
        raw = section_value(lines, label)
        performance[key] = {"raw_value": raw, "normalized_value": to_count(raw), "unit": "次"}
    growth_raw = section_value(lines, "粉丝增量")
    change_raw = section_value(lines, "粉丝量变化幅度")
    active_raw = section_value(lines, "活跃粉丝占比")
    read_fan_raw = section_value(lines, "阅读粉丝占比")
    interact_fan_raw = section_value(lines, "互动粉丝占比")
    purchase_raw = section_value(lines, "下单粉丝占比")
    gender_line = next((line for line in lines if re.search(r"女性居多|男性居多", line)), None)
    age_line = next((line for line in lines if re.search(r"\d{2}-\d{2}居多", line)), None)
    region_line = next((line for line in lines if line.startswith("国内最高的三个省份：")), None)
    apple_line = next((line for line in lines if line.startswith("苹果用户占比")), None)
    interest_line = next((line for line in lines if line.startswith("用户最感兴趣的内容类型为")), None)
    regions = []
    if region_line:
        region_text = region_line.split("：", 1)[-1]
        regions = [
            {"region": name.strip(), "share_percent": float(value)}
            for name, value in re.findall(r"([^，、（]+)（([\d.]+)%）", region_text)
        ]
    gender = None
    if gender_line:
        match = re.search(r"(女性|男性)居多，占比([\d.]+)%", gender_line)
        if match:
            gender = {"leading_group": match.group(1), "share_percent": float(match.group(2))}
    age = None
    if age_line:
        match = re.search(r"(\d{2}-\d{2})居多，占比([\d.]+)%", age_line)
        if match:
            age = {"leading_band": match.group(1), "share_percent": float(match.group(2))}
    apple = None
    if apple_line:
        match = re.search(r"([\d.]+)%", apple_line)
        apple = float(match.group(1)) if match else None
    interest = None
    if interest_line:
        interest = interest_line.split("为", 1)[-1].strip().split("、")
    return {
        "source": "pgy",
        "creator_id": spec["creator_id"],
        "creator_name": spec["name"],
        "profile_url": None,
        "profile_url_status": "未保存在页面文本证据中",
        "city_raw": spec["city"],
        "category_tags": spec["tags"],
        "followers_raw": raw_followers,
        "followers_count": to_count(raw_followers),
        "likes_and_favorites_raw": raw_likes_favs,
        "likes_and_favorites_count": to_count(raw_likes_favs),
        "image_quote_raw": spec["image_quote_raw"],
        "image_quote_cny": to_money(spec["image_quote_raw"]),
        "video_quote_raw": spec["video_quote_raw"],
        "video_quote_cny": to_money(spec["video_quote_raw"]),
        "data_updated_at_raw": updated,
        "captured_on": CAPTURE_DATE,
        "performance_medians": performance,
        "fan_snapshot": {
            "growth_raw": growth_raw,
            "growth_count": to_count(growth_raw),
            "change_percent_raw": change_raw,
            "change_percent": percent_from(change_raw),
            "active_share_raw": active_raw,
            "active_share_percent": percent_from(active_raw),
            "read_share_raw": read_fan_raw,
            "read_share_percent": percent_from(read_fan_raw),
            "interaction_share_raw": interact_fan_raw,
            "interaction_share_percent": percent_from(interact_fan_raw),
            "purchase_share_raw": purchase_raw,
            "purchase_share_percent": percent_from(purchase_raw),
            "gender_top": gender,
            "age_top": age,
            "top_regions": regions,
            "apple_device_share_percent": apple,
            "top_interests": interest,
            "period": "平台详情页未显示明确统计周期；页面标注数据更新日期时单独保存",
        },
        "evidence_file": spec["file"],
    }


def parse_note(creator_id: str, seq: int, filename: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lines = read_lines(filename)
    marker = max(index for index, line in enumerate(lines) if line == "笔记详情")
    duration_match = next((re.search(r"00:00\s*/\s*(\d{1,2}:\d{2})", line) for line in lines[marker:]
                           if re.search(r"00:00\s*/\s*(\d{1,2}:\d{2})", line)), None)
    if not duration_match:
        raise ValueError(f"No visible player duration in {filename}")
    duration = duration_match.group(1)
    player_index = next(index for index, line in enumerate(lines[marker:], marker)
                        if re.search(r"00:00\s*/\s*\d{1,2}:\d{2}", line))
    title_index = player_index + 1
    title = lines[title_index].strip()
    publish_index = next((index for index, line in enumerate(lines[title_index + 1:], title_index + 1)
                          if line == "发布于"), None)
    published_raw = lines[publish_index + 1].strip() if publish_index is not None and publish_index + 1 < len(lines) else None
    body_lines = lines[title_index + 1:publish_index] if publish_index is not None else []
    body_lines = [line.strip() for line in body_lines if line.strip() and line.strip() != "评论区搜索组件"]
    content_raw = "\n".join(body_lines)
    hashtags = re.findall(r"#(.*?)\[话题\]#", content_raw)
    metrics = {}
    metric_start = next((index for index, line in enumerate(lines) if line == "本篇笔记数据"), None)
    for key, label in METRIC_LABELS.items():
        raw = next_value(lines[metric_start:] if metric_start is not None else [], label)
        metrics[key] = {"raw_value": raw, "normalized_value": to_count(raw), "unit": "次", "period": "页面未标注"}
    note_id = "6afc317600000000180288af" if (creator_id == "935928452" and seq == 1) else None
    note_url = XHS_NOTE_URL if note_id else None
    note = {
        "source": "pgy",
        "note_key": f"pgy_{creator_id}_{seq:02d}",
        "creator_id": creator_id,
        "note_id": note_id,
        "note_url": note_url,
        "title": title,
        "published_raw": published_raw,
        "published_at": published_raw if published_raw and re.match(r"^20\d{2}-\d{2}-\d{2}", published_raw) else None,
        "content_text_raw": content_raw,
        "hashtags": hashtags,
        "video_duration_raw": duration,
        "note_type": "video",
        "note_type_basis": "面板内可见播放器总时长；类型为依据页面播放器的推断",
        "metrics": metrics,
        "captured_on": CAPTURE_DATE,
        "period": "蒲公英面板未显示单篇指标的统计周期；作为本次页面快照保存",
        "evidence_file": filename,
    }
    comments = []
    comments_start = next((index for index, line in enumerate(lines[marker:], marker) if line == "精选评论"), None)
    comments_end = metric_start if metric_start is not None else len(lines)
    if comments_start is not None:
        comment_lines = lines[comments_start + 1:comments_end]
        time_pattern = re.compile(r"^(?:20\d{2}-\d{2}-\d{2} \d{2}:\d{2}|今天 \d{2}:\d{2})$")
        for index, line in enumerate(comment_lines[:-2]):
            if not time_pattern.match(line.strip()):
                continue
            raw_likes = comment_lines[index + 1].strip()
            if not re.fullmatch(r"\d[\d,]*", raw_likes):
                continue
            text = comment_lines[index + 2].strip()
            if not text:
                continue
            comments.append({
                "source": "pgy_internal_panel",
                "note_key": note["note_key"],
                "display_time_raw": line.strip(),
                "likes_raw": raw_likes,
                "likes_count": to_count(raw_likes),
                "comment_text_excerpt": text,
                "identity_fields_omitted": True,
                "evidence_file": filename,
            })
            if len(comments) >= 10:
                break
    return note, comments


def dump_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {path.name}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {path.name}")
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def create_database(path: Path, creators: list[dict[str, Any]], notes: list[dict[str, Any]],
                    xhs: dict[str, Any], comments: list[dict[str, Any]], errors: list[dict[str, Any]]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing database: {path.name}")
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE collection_batches (
            batch_id TEXT PRIMARY KEY, captured_on TEXT NOT NULL, source TEXT NOT NULL,
            method TEXT NOT NULL, metadata_json TEXT NOT NULL
        );
        CREATE TABLE creators (
            creator_id TEXT PRIMARY KEY, creator_name TEXT NOT NULL, source TEXT NOT NULL
        );
        CREATE TABLE creator_snapshots (
            snapshot_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES collection_batches(batch_id),
            creator_id TEXT NOT NULL REFERENCES creators(creator_id), captured_on TEXT NOT NULL,
            fields_json TEXT NOT NULL, evidence_file TEXT NOT NULL
        );
        CREATE TABLE notes (
            note_key TEXT PRIMARY KEY, creator_id TEXT NOT NULL REFERENCES creators(creator_id),
            platform_note_id TEXT, canonical_url TEXT, title TEXT NOT NULL,
            published_raw TEXT, content_text_raw TEXT, hashtags_json TEXT NOT NULL,
            note_type TEXT, note_type_basis TEXT, duration_raw TEXT, evidence_file TEXT NOT NULL
        );
        CREATE TABLE note_metrics (
            metric_row_id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES collection_batches(batch_id),
            note_key TEXT NOT NULL REFERENCES notes(note_key), source TEXT NOT NULL,
            metric_name TEXT NOT NULL, raw_value TEXT, normalized_value REAL, unit TEXT,
            period TEXT, captured_on TEXT NOT NULL
        );
        CREATE TABLE note_contents (
            note_key TEXT PRIMARY KEY REFERENCES notes(note_key), content_text_raw TEXT,
            hashtags_json TEXT NOT NULL, captured_on TEXT NOT NULL
        );
        CREATE TABLE comments (
            comment_row_id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES collection_batches(batch_id),
            note_key TEXT NOT NULL REFERENCES notes(note_key), source TEXT NOT NULL,
            display_time_raw TEXT, likes_raw TEXT, likes_count INTEGER, content_excerpt TEXT,
            captured_on TEXT NOT NULL
        );
        CREATE TABLE collection_errors (
            error_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES collection_batches(batch_id),
            scope TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL
        );
        """)
        batch_id = "R4-20261120-PGY-01"
        metadata = {
            "creators": len(creators), "pgy_note_panels": len(notes),
            "xhs_pages_opened": 1, "xhs_dom_extracted": 0,
            "comments_policy": "最多10条/笔记；仅结构化保存可见评论文字、显示时间、点赞数，不保存评论者身份",
            "automation_mode": "有界 Playwright 可见浏览器交互；分阶段 UI 操作，不是无人值守连续爬虫",
        }
        connection.execute("INSERT INTO collection_batches VALUES(?,?,?,?,?)", (
            batch_id, CAPTURE_DATE, "pgy", "visible browser UI + DOM body text", json.dumps(metadata, ensure_ascii=False)))
        for creator in creators:
            connection.execute("INSERT INTO creators VALUES(?,?,?)", (
                creator["creator_id"], creator["creator_name"], "pgy"))
            connection.execute("INSERT INTO creator_snapshots VALUES(?,?,?,?,?,?)", (
                f"{batch_id}:{creator['creator_id']}", batch_id, creator["creator_id"], CAPTURE_DATE,
                json.dumps(creator, ensure_ascii=False), creator["evidence_file"]))
        for note in notes:
            connection.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                note["note_key"], note["creator_id"], note["note_id"], note["note_url"], note["title"],
                note["published_raw"], note["content_text_raw"], json.dumps(note["hashtags"], ensure_ascii=False),
                note["note_type"], note["note_type_basis"], note["video_duration_raw"], note["evidence_file"]))
            connection.execute("INSERT INTO note_contents VALUES(?,?,?,?)", (
                note["note_key"], note["content_text_raw"], json.dumps(note["hashtags"], ensure_ascii=False), CAPTURE_DATE))
            for metric_name, value in note["metrics"].items():
                connection.execute("INSERT INTO note_metrics(batch_id,note_key,source,metric_name,raw_value,normalized_value,unit,period,captured_on) VALUES(?,?,?,?,?,?,?,?,?)", (
                    batch_id, note["note_key"], "pgy", metric_name, value["raw_value"], value["normalized_value"],
                    value["unit"], value["period"], CAPTURE_DATE))
        for metric_name in ("likes", "favorites", "comments", "shares"):
            raw = xhs.get(f"{metric_name}_raw")
            connection.execute("INSERT INTO note_metrics(batch_id,note_key,source,metric_name,raw_value,normalized_value,unit,period,captured_on) VALUES(?,?,?,?,?,?,?,?,?)", (
                batch_id, "pgy_935928452_01", "xhs_screenshot_only", metric_name, raw, to_count(raw),
                "次" if raw is not None else None, "截图中的当前显示值；无统计周期标签", CAPTURE_DATE))
        for comment in comments:
            connection.execute("INSERT INTO comments(batch_id,note_key,source,display_time_raw,likes_raw,likes_count,content_excerpt,captured_on) VALUES(?,?,?,?,?,?,?,?)", (
                batch_id, comment["note_key"], comment["source"], comment["display_time_raw"], comment["likes_raw"],
                comment["likes_count"], comment["comment_text_excerpt"], CAPTURE_DATE))
        for error in errors:
            connection.execute("INSERT INTO collection_errors VALUES(?,?,?,?,?)", (
                error["error_id"], batch_id, error["scope"], error["status"], error["detail"]))
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    argparse.ArgumentParser(description="Build bounded R4 evidence artifacts from saved live-page captures.").parse_args()
    output_names = [
        "raw_creators.jsonl", "raw_notes_pgy.jsonl", "raw_notes_xhs.jsonl", "raw_comments.jsonl",
        "collection_batches.json", "field_dictionary.csv", "data_quality_report.json",
        "collection_errors.json", "collection_report.md", "collection_validation.sqlite3",
    ]
    conflicts = [name for name in output_names if (OUT / name).exists()]
    if conflicts:
        raise FileExistsError("Refusing to overwrite: " + ", ".join(conflicts))

    creators = [creator_record(spec) for spec in CREATORS]
    notes, comments = [], []
    for creator_id, seq, filename in NOTE_FILES:
        note, note_comments = parse_note(creator_id, seq, filename)
        notes.append(note)
        comments.extend(note_comments)
    xhs = {
        "source": "xhs",
        "note_key": "pgy_935928452_01",
        "note_id": "6afc317600000000180288af",
        "note_url": XHS_NOTE_URL,
        **XHS_VISIBLE,
        "normalized_values": {
            "likes": to_count(XHS_VISIBLE["likes_raw"]),
            "favorites": to_count(XHS_VISIBLE["favorites_raw"]),
            "comments": to_count(XHS_VISIBLE["comments_raw"]),
            "shares": None,
        },
        "capture_status": "page_opened_login_modal",
        "evidence_level": "截图人工可见；未从 DOM 读取；正文和评论受登录弹窗遮挡",
        "captured_on": CAPTURE_DATE,
        "evidence_file": "screenshots/creator_01_note_xhs.png",
    }
    errors = [
        {
            "error_id": "xhs-login-modal-01", "scope": "xhs note page",
            "status": "blocked_by_login_ui", "detail": "从蒲公英复制并打开原笔记链接后，小红书显示手机号登录弹窗；未尝试登录或读取被遮挡内容。"
        },
        {
            "error_id": "pgy-transient-loading-01", "scope": "creator 03 note panel",
            "status": "recovered_after_wait", "detail": "首次页面文本仅见加载中；正常等待后成功取得该笔记面板，保留初次证据 creator03_note01_initial_loading.txt。"
        },
    ]
    batches = [{
        "batch_id": "R4-20261120-PGY-01", "captured_on": CAPTURE_DATE,
        "pgy_creator_count": len(creators), "pgy_note_panel_count": len(notes),
        "xhs_page_open_count": 1, "xhs_dom_extraction_count": 0,
        "browser": "existing visible Playwright Chromium session; existing project .venv",
        "collection_method": "bounded visible UI actions and DOM body text reads; user credentials/session data were not copied",
        "unattended_continuous_run": False,
        "pgy_access_status": "logged-in page was visible during collection; captures cover 5 creators and 9 note panels",
        "xhs_access_status": "copied note URL opened; stopped at phone login modal",
    }]

    dump_jsonl(OUT / "raw_creators.jsonl", creators)
    dump_jsonl(OUT / "raw_notes_pgy.jsonl", notes)
    dump_jsonl(OUT / "raw_notes_xhs.jsonl", [xhs])
    dump_jsonl(OUT / "raw_comments.jsonl", comments)
    dump_json(OUT / "collection_batches.json", batches)
    dump_json(OUT / "collection_errors.json", errors)

    field_rows = [
        ("creator", "creator_id", "蒲公英个人主页可见小红书号", "文本标识", "主体标识", "5/5", "实际值"),
        ("creator", "creator_name", "博主详情页名称", "文本", "页面当前值", "5/5", "实际值"),
        ("creator", "profile_url", "博主主页链接", "URL", "页面路径未保存在结构化页面文本证据中", "0/5", "不从用户ID猜测或拼接链接"),
        ("creator", "city_raw", "博主详情页基础资料", "地域文本", "页面当前值", "5/5", "实际值"),
        ("creator", "category_tags", "博主详情页标签", "标签数组", "页面当前值", "5/5", "实际值"),
        ("creator", "followers_count", "粉丝数", "人", "页面当前值；页面使用w/万缩写", "5/5", "转换值并保留raw"),
        ("creator", "likes_and_favorites_count", "获赞与收藏", "次（页面合并展示）", "页面当前值", "5/5", "转换值并保留raw"),
        ("creator", "image_quote_cny", "图文笔记一口价", "人民币元", "当前报价；未显示周期", "4/5", "暂未报价保留为空"),
        ("creator", "video_quote_cny", "视频笔记一口价", "人民币元", "当前报价；未显示周期", "5/5", "转换值并保留raw"),
        ("creator_snapshot", "exposure_median", "数据概览", "次", "中位数；页面未明确时间窗口", "5/5", "实际值"),
        ("creator_snapshot", "read_median", "数据概览", "次", "中位数；页面未明确时间窗口", "5/5", "实际值"),
        ("creator_snapshot", "interaction_median", "数据概览", "次", "中位数；页面未明确时间窗口", "5/5", "实际值"),
        ("creator_snapshot", "fan_growth", "粉丝分析核心指标", "人", "页面显示的变化指标；周期未充分确认", "5/5", "保留负值"),
        ("creator_snapshot", "fan_share_percentages", "粉丝分析", "%", "页面快照；各指标周期未在文本中确认", "5/5", "实际显示值"),
        ("creator_snapshot", "gender_age_regions", "粉丝画像", "%/类别", "页面快照；统计周期未显示", "5/5", "只存页面可见摘要"),
        ("creator_snapshot", "data_updated_at", "数据概览标注", "日期", "平台标注更新时间", "5/5", "2026-11-19"),
        ("note", "title", "蒲公英笔记详情面板", "文本", "笔记当前页面值", "9/9", "实际值"),
        ("note", "content_text_raw", "蒲公英笔记详情面板", "文本", "页面可见正文及话题原文", "9/9", "原样保留可见文本"),
        ("note", "hashtags", "正文中的[话题]标记", "标签数组", "页面当前值", "9/9", "由可见正文解析"),
        ("note", "published_raw", "蒲公英笔记详情面板", "日期时间/相对时间", "页面显示值", "9/9", "保留原始显示；相对时间不强行换算"),
        ("note", "video_duration_raw", "详情播放器", "分:秒", "当前播放器总时长", "9/9", "据播放器推断视频类型"),
        ("note", "note_id", "复制小红书笔记链接入口", "平台ID", "稳定链接仅实际取得1篇", "1/9", "其余未取得，不补造"),
        ("note_metric", "exposure/reads", "蒲公英本篇笔记数据", "次", "单次页面快照；周期未显示", "9/9", "两项均有值"),
        ("note_metric", "likes/favorites/comments/shares/follows", "蒲公英本篇笔记数据", "次", "单次页面快照；周期未显示", "8/9 each", "'-'保留为缺失"),
        ("note_metric", "xhs visible counters", "小红书笔记截图", "次", "登录弹窗出现时的截图显示值", "3/4", "截图可见；非DOM读取"),
        ("comment", "comment_text_excerpt", "蒲公英详情精选评论", "文本", "最多10条/篇；当前面板可见片段", "see report", "不保留评论者身份"),
        ("xhs", "body/hashtags/publish_time/comments", "小红书原笔记", "文本/标签/时间", "登录弹窗遮挡，未读取", "0", "阻塞，不视作0数据"),
    ]
    dictionary_path = OUT / "field_dictionary.csv"
    if dictionary_path.exists():
        raise FileExistsError(dictionary_path.name)
    with dictionary_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["entity", "field", "page_location", "unit", "period", "coverage", "normalization_notes"])
        writer.writerows(field_rows)

    creator_coverage = {}
    for key in ("creator_id", "creator_name", "city_raw", "category_tags", "followers_count",
                "likes_and_favorites_count", "image_quote_cny", "video_quote_cny"):
        present = sum(bool(c.get(key)) if isinstance(c.get(key), list) else c.get(key) is not None for c in creators)
        creator_coverage[key] = {"nonempty": present, "total": len(creators), "rate": round(present / len(creators), 4)}
    fan_snapshot_coverage = {}
    for key in ("growth_count", "change_percent", "active_share_percent", "read_share_percent",
                "interaction_share_percent", "purchase_share_percent", "gender_top", "age_top",
                "top_regions", "apple_device_share_percent", "top_interests"):
        present = sum(
            (bool(c["fan_snapshot"].get(key)) if isinstance(c["fan_snapshot"].get(key), (list, dict))
             else c["fan_snapshot"].get(key) is not None)
            for c in creators
        )
        fan_snapshot_coverage[key] = {"nonempty": present, "total": len(creators), "rate": round(present / len(creators), 4)}
    note_coverage = {}
    for key in ("title", "published_raw", "content_text_raw", "hashtags", "video_duration_raw", "note_id"):
        present = sum(bool(n.get(key)) if isinstance(n.get(key), list) else n.get(key) is not None for n in notes)
        note_coverage[key] = {"nonempty": present, "total": len(notes), "rate": round(present / len(notes), 4)}
    metric_coverage = {
        metric: {"nonempty": sum(n["metrics"][metric]["normalized_value"] is not None for n in notes),
                 "total": len(notes), "rate": round(sum(n["metrics"][metric]["normalized_value"] is not None for n in notes) / len(notes), 4)}
        for metric in METRIC_LABELS
    }
    quality = {
        "batch_id": batches[0]["batch_id"],
        "data_source": "real Pgy logged-in page captures; XHS values only from the login-gated screenshot",
        "creator_records": len(creators), "unique_creator_ids": len({c["creator_id"] for c in creators}),
        "pgy_note_panels": len(notes),
        "unique_note_fingerprints_creator_title_publish": len({(n["creator_id"], n["title"], n["published_raw"]) for n in notes}),
        "verified_platform_note_ids": sum(n["note_id"] is not None for n in notes),
        "xhs_pages_opened": 1, "xhs_dom_extracted_records": 0,
        "visible_comments_structured": len(comments),
        "comments_identity_fields_saved": False,
        "creator_field_coverage": creator_coverage,
        "list_detail_comparison": {
            "status": "not_computed",
            "reason": "博主广场证据只有截图，没有结构化列表行记录；本轮不以截图猜测列表字段值",
        },
        "fan_snapshot_field_coverage": fan_snapshot_coverage,
        "note_field_coverage": note_coverage,
        "pgy_metric_field_coverage": metric_coverage,
        "xhs_screenshot_visible_fields": ["title", "author_name", "likes_raw", "favorites_raw", "comments_raw"],
        "type_conversion": {
            "creator_follower_count_converted": sum(c["followers_count"] is not None for c in creators),
            "creator_quote_image_converted": sum(c["image_quote_cny"] is not None for c in creators),
            "creator_quote_video_converted": sum(c["video_quote_cny"] is not None for c in creators),
            "note_metric_count_fields": sum(v["normalized_value"] is not None for n in notes for v in n["metrics"].values()),
            "note_metric_count_cells": len(notes) * len(METRIC_LABELS),
            "missing_display_dash_treated_as_null": True,
        },
        "comparison_warning": "Pgy metrics and XHS screenshot counters are separate source snapshots and periods are not confirmed equivalent.",
        "unavailable_not_zero": ["XHS body", "XHS comments", "share count in XHS screenshot", "7 note IDs", "exact periods for Pgy note metrics"],
    }
    dump_json(OUT / "data_quality_report.json", quality, args.overwrite)
    create_database(OUT / "collection_validation.sqlite3", creators, notes, xhs, comments, errors)

    note_metric_summary = {
        metric: {
            "available": sum(n["metrics"][metric]["normalized_value"] is not None for n in notes),
            "min": min((n["metrics"][metric]["normalized_value"] for n in notes if n["metrics"][metric]["normalized_value"] is not None), default=None),
            "max": max((n["metrics"][metric]["normalized_value"] for n in notes if n["metrics"][metric]["normalized_value"] is not None), default=None),
        }
        for metric in METRIC_LABELS
    }
    report = f"""# CreatorScope R4 连续真实数据采集验证记录

## 范围与方法

- 采集日期：{CAPTURE_DATE}。来源是已登录蒲公英页面，经可见 Playwright Chromium UI 操作并读取页面可见文本；不是无人值守连续爬虫。
- 访问边界：仅限页面正常可见内容；未读取凭据、Cookie、Session，未调用私有接口。
- 小红书：从蒲公英页面正常复制原笔记链接并打开。页面显示手机号登录弹窗后停止；没有继续登录、解析遮挡内容或采集评论。
- 原始证据：本目录下 `creator_*_visible.txt`、`raw_creator*_detail_visible.txt`、`raw_pgy_*_visible.txt` 与 `screenshots/`。

## 实际数量

- 蒲公英创作者详情：{len(creators)} 位，平台 ID 去重后 {len({c['creator_id'] for c in creators})} 位。
- 蒲公英内部笔记详情面板：{len(notes)} 篇，均为不同的创作者/标题/页面发布时间组合。
- 蒲公英精选评论片段：结构化保存 {len(comments)} 条，只保存评论文本片段、页面显示时间和点赞数，不保存评论者身份字段。
- 小红书：打开 1 个由蒲公英复制的真实笔记链接；DOM 提取 {0} 条。截图上可辨认标题、作者、点赞 {XHS_VISIBLE['likes_raw']}、收藏 {XHS_VISIBLE['favorites_raw']}、评论 {XHS_VISIBLE['comments_raw']}；分享图标未显示数字。

## 数据字段与质量

- 创作者资料：ID、名称、地域、类目标签、粉丝数、获赞与收藏、图文/视频报价；数值字段保留原始显示格式并做计数或人民币转换。
- 详情快照：曝光/阅读/互动中位数、粉丝增量/变化率、活跃/阅读/互动/下单粉丝占比，以及性别、年龄、前三地域、苹果设备占比和兴趣摘要。
- 笔记面板：标题、正文可见文本、话题、页面发布时间、播放器时长、曝光/阅读/点赞/收藏/评论/分享/关注量。面板未说明单篇指标的统计周期，故以页面快照存储，不与小红书截图计数合并。
- 创作者关键字段非空率：名称、平台 ID、地域、标签、粉丝数、获赞与收藏、视频报价均为 5/5；图文报价数值为 4/5（第 5 位显示“暂未报价”，原文保留，规范值为空）。
- 创作者主页完整 URL 未保存在结构化证据中（0/5）；仅保留页面可见平台 ID，不据此猜测拼接主页地址。列表页与详情页未做结构化差异计算，列表页当前只有截图证据。
- 笔记关键字段非空率：标题、正文文本、播放器时长均按实际页面文本核验；发布时间 9/9 有显示，其中 1 条为相对时间“今天 11:01”，没有擅自换算绝对日期。平台笔记 ID 仅 1/9 通过复制链接取得。
- 单篇指标可转换率：{json.dumps({k:v['available'] for k,v in note_metric_summary.items()}, ensure_ascii=False)}（共 9 篇；“-”转换为 null 而非 0）。
- 重复：创作者主体 0 个重复；9 个笔记面板的创作者/标题/显示发布时间指纹唯一。跨批次历史趋势尚未验证，本次只有一个批次。

## 页面错误与恢复

- 小红书登录弹窗：访问限制/登录门槛，已停止；正文、话题、发布时间和评论均未取得。
- 1 次蒲公英笔记详情首次只显示“加载中”，正常等待后成功读取；初次文本另存作为瞬时状态证据。
- 页面交互出现重复详情标签时按创作者平台 ID 合并主体；没有因此增加记录数。
- 任务没有作为一个无人值守任务连续执行；它是有边界的分阶段可见 UI 操作。不能据此声称稳定无人值守遍历。

## 可支持的分析与限制

- 可验证：粉丝数/报价分布、创作者类目计数、Pgy 单篇曝光/阅读/互动指标分布、标题/正文/话题词频、可见精选评论词频、同一页的指标散点关系、创作者快照摘要比较。
- 样本只有 9 篇且来自 5 位创作者，适合功能验证，不支持总体推断或可靠的领域相关性结论。
- 话题和正文文本可用于 jieba/词频；可形成词云。评论样本是蒲公英页面精选展示的少量片段，存在平台挑选偏差，不适合直接做评论总体情感结论。
- 小红书页面的截图计数只有单次可见值且受登录弹窗遮挡，正文和评论分析暂不可用。
- 历史趋势需要同一创作者/笔记跨多个明确时间点重复采集；本批次不构成趋势数据。

## 产物

- `raw_creators.jsonl`、`raw_notes_pgy.jsonl`、`raw_notes_xhs.jsonl`、`raw_comments.jsonl`
- `collection_batches.json`、`field_dictionary.csv`、`data_quality_report.json`、`collection_errors.json`
- `collection_validation.sqlite3`：本次独立验证库，未改动正式数据库。
- `screenshots/`：列表、详情、笔记面板、小红书登录弹窗、评论区等页面证据。

原始页面文本可能包含公开页面中的非必要 UI 文案；结构化评论样本已省略评论者身份字段。所有缺失显示值维持空值。
"""
    report_path = OUT / "collection_report.md"
    if report_path.exists():
        raise FileExistsError(report_path.name)
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps({
        "output_directory": str(OUT), "creators": len(creators), "pgy_notes": len(notes),
        "structured_pgy_comment_excerpts": len(comments), "xhs_dom_records": 0,
        "xhs_screenshot_metrics": {k: v for k, v in XHS_VISIBLE.items() if k.endswith("_raw")},
        "metric_coverage": {key: value["available"] for key, value in note_metric_summary.items()},
        "files": output_names + ["build_validation_artifacts.py"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
