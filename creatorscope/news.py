from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import jieba


NEWS_FEEDS: dict[str, dict[str, str]] = {
    "xinhuanet_politics": {
        "source_name": "新华网时政",
        "feed_url": "https://www.xinhuanet.com/politics/news_politics.xml",
        "official_source_url": "https://www.news.cn/linktous.htm",
    },
    "xinhuanet_tech": {
        "source_name": "新华网科技",
        "feed_url": "https://www.xinhuanet.com/tech/news_tech.xml",
        "official_source_url": "https://www.news.cn/linktous.htm",
    },
}
MAX_FEED_BYTES = 2_000_000


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _child_text(node: ET.Element, names: set[str]) -> str:
    for child in list(node):
        if _local_name(child.tag) in names:
            if child.attrib.get("href"):
                return child.attrib["href"].strip()
            return "".join(child.itertext()).strip()
    return ""


def _published_iso(value: str, source_url: str) -> tuple[str, str]:
    raw = value.strip()
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat(timespec="seconds"), "rss_field"
        return raw, "rss_field_unparsed"
    path_match = re.search(r"/(20\d{2})-(\d{1,2})/(\d{1,2})/", urllib.parse.urlsplit(source_url).path)
    if path_match:
        year, month, day = map(int, path_match.groups())
        try:
            return date(year, month, day).isoformat(), "source_url_path_date"
        except ValueError:
            pass
    return "", "missing"


def _topic_tags(title: str, summary: str) -> list[str]:
    words = []
    seen = set()
    for token in jieba.lcut(f"{title} {summary}", HMM=True):
        value = token.strip().lstrip("#＃").casefold()
        if len(value) < 2 or value in seen or not any("\u4e00" <= char <= "\u9fff" for char in value):
            continue
        seen.add(value)
        words.append(value)
    return words[:20]


def parse_feed_xml(payload: bytes | str, source_key: str, *, collected_at: str | None = None) -> list[dict[str, Any]]:
    if source_key not in NEWS_FEEDS:
        raise ValueError("不支持的官方新闻源")
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if len(raw) > MAX_FEED_BYTES:
        raise ValueError("RSS 文件超过 2 MB 安全上限")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("RSS/Atom XML 格式无效") from exc
    source = NEWS_FEEDS[source_key]
    captured = collected_at or datetime.now().astimezone().isoformat(timespec="seconds")
    nodes = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    output = []
    for node in nodes:
        title = _child_text(node, {"title"})
        link = _child_text(node, {"link"})
        summary = _child_text(node, {"description", "summary", "content"})
        published = _child_text(node, {"pubdate", "published", "updated", "date"})
        if not title or not link:
            continue
        absolute_url = urllib.parse.urljoin(source["feed_url"], link)
        parsed_url = urllib.parse.urlsplit(absolute_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username or parsed_url.password:
            continue
        identity = "\0".join((source_key, absolute_url, title, published))
        published_at, published_at_method = _published_iso(published, absolute_url)
        output.append({
            "news_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "source_name": source["source_name"],
            "source_key": source_key,
            "title": title,
            "summary": summary,
            "published_at": published_at,
            "published_at_raw": published,
            "published_at_method": published_at_method,
            "collected_at": captured,
            "source_url": absolute_url,
            "topic_tags": _topic_tags(title, summary),
            "feed_url": source["feed_url"],
            "official_source_url": source["official_source_url"],
        })
    return output


def fetch_feed(source_key: str) -> list[dict[str, Any]]:
    source = NEWS_FEEDS.get(source_key)
    if source is None:
        raise ValueError("不支持的官方新闻源")
    request = urllib.request.Request(
        source["feed_url"],
        headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml", "User-Agent": "CreatorScope/1.2 (course analytics)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            final_url = urllib.parse.urlsplit(response.geturl())
            hostname = (final_url.hostname or "").casefold()
            if final_url.scheme != "https" or not (hostname == "xinhuanet.com" or hostname.endswith(".xinhuanet.com")):
                raise ValueError("RSS 跳转到了不受信任的地址")
            payload = response.read(MAX_FEED_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"新闻源返回 HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("无法连接官方 RSS 新闻源") from None
    return parse_feed_xml(payload, source_key)


def append_news_items(path: Path, items: list[dict[str, Any]]) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {item.get("news_id") for item in load_news_items(path)}
    existing_count = len(existing)
    new_items = []
    for item in items:
        news_id = item.get("news_id")
        if news_id and news_id not in existing:
            new_items.append(item)
            existing.add(news_id)
    if new_items:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            for item in new_items:
                stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"received": len(items), "added": len(new_items), "total": existing_count + len(new_items)}


def load_news_items(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    output = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("news_id"):
                if "published_at_method" not in item:
                    item["published_at_raw"] = item.get("published_at_raw", "")
                    item["published_at"], item["published_at_method"] = _published_iso(
                        str(item.get("published_at_raw") or ""), str(item.get("source_url") or "")
                    )
                output.append(item)
    return output


def _tokens(value: str) -> set[str]:
    return {
        token.strip().lstrip("#＃").casefold()
        for token in jieba.lcut(value, HMM=True)
        if len(token.strip().lstrip("#＃")) >= 2
    }


def _day(value: Any) -> date | None:
    raw = str(value or "").strip()
    try:
        if "T" in raw or " " in raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        return date.fromisoformat(raw[:10])
    except ValueError:
        try:
            return parsedate_to_datetime(raw).date()
        except (TypeError, ValueError, OverflowError):
            return None


def correlate_news_notes(news_items: list[dict[str, Any]], notes: list[dict[str, Any]], *, window_days: int = 7) -> dict[str, Any]:
    rows = []
    for news in news_items:
        event_day = _day(news.get("published_at"))
        keywords = _tokens(" ".join((str(news.get("title") or ""), str(news.get("summary") or ""), " ".join(news.get("topic_tags") or []))))
        matched = []
        for note in notes:
            note_keywords = _tokens(" ".join((str(note.get("title") or ""), " ".join(note.get("topics") or []))))
            common = sorted(keywords & note_keywords)
            if not common:
                continue
            note_day = _day(note.get("published_at"))
            if event_day is None or note_day is None:
                relative = "日期缺失"
            elif event_day - (date.resolution * window_days) <= note_day < event_day:
                relative = "热点前"
            elif note_day == event_day:
                relative = "热点当天"
            elif event_day < note_day <= event_day + (date.resolution * window_days):
                relative = "热点后"
            else:
                relative = "窗口外"
            metric_map = note.get("metrics") or {}
            component_keys = (("点赞量", "点赞数"), ("收藏量", "收藏数"), ("评论量", "评论数"), ("分享量", "分享数"))
            components = [next((metric_map[key] for key in aliases if key in metric_map), {}) for aliases in component_keys]
            values = [component.get("value") for component in components]
            periods = {str(component.get("period") or "") for component in components}
            interaction_total = sum(float(value) for value in values) if all(value is not None for value in values) and len(periods) == 1 and "" not in periods else None
            matched.append({
                "note_id": note.get("note_id"), "platform_note_id": note.get("platform_note_id"),
                "creator_name": note.get("creator_name"), "platform_creator_id": note.get("platform_creator_id"),
                "title": note.get("title"), "source": note.get("source"), "source_url": note.get("source_url"),
                "published_at": note.get("published_at"), "relative_window": relative,
                "matched_keywords": common, "interaction_total": interaction_total,
                "metric_period": next(iter(periods)) if len(periods) == 1 else "口径不一致",
            })
        by_group: dict[tuple[str, str], dict[str, Any]] = {}
        for note in matched:
            group_key = (str(note.get("source") or "未知来源"), str(note.get("metric_period") or ""))
            group = by_group.setdefault(group_key, {"source": group_key[0], "metric_period": group_key[1], "before_count": 0, "event_day_count": 0, "after_count": 0, "before_interaction_values": [], "after_interaction_values": []})
            relative_key = {"热点前": "before_count", "热点当天": "event_day_count", "热点后": "after_count"}.get(note["relative_window"])
            if relative_key:
                group[relative_key] += 1
            if note["interaction_total"] is not None and note["relative_window"] in {"热点前", "热点后"}:
                group[f"{'before' if note['relative_window'] == '热点前' else 'after'}_interaction_values"].append(note["interaction_total"])
        groups = []
        for group in by_group.values():
            before_values = group.pop("before_interaction_values")
            after_values = group.pop("after_interaction_values")
            group["before_mean_interactions"] = round(sum(before_values) / len(before_values), 2) if before_values else None
            group["before_interaction_sample_count"] = len(before_values)
            group["after_mean_interactions"] = round(sum(after_values) / len(after_values), 2) if after_values else None
            group["after_interaction_sample_count"] = len(after_values)
            groups.append(group)
        rows.append({
            "news_id": news.get("news_id"), "source_name": news.get("source_name"), "title": news.get("title"),
            "published_at": news.get("published_at"), "source_url": news.get("source_url"),
            "topic_tags": news.get("topic_tags") or [], "matched_note_count": len(matched), "matched_notes": matched,
            "by_source_and_period": groups,
        })
    return {
        "news_count": len(news_items), "matched_event_count": sum(bool(row["matched_note_count"]) for row in rows),
        "window_days": window_days, "events": rows,
        "method_notice": "按标题、摘要与笔记标题/话题的 jieba 词项交集匹配；发布数量按新闻时间前后各 7 天统计。互动均值仅合并同笔记来源、同指标周期且四项完整值；结果为描述性时间关联，不表示新闻导致内容或互动变化。",
    }
