from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creatorscope import create_app
from creatorscope.db import connection, initialize_database
from creatorscope.news import append_news_items, correlate_news_notes, load_news_items, parse_feed_xml


class NewsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="creatorscope-news-")
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "news-test.sqlite3"
        self.news_path = self.root / "news.jsonl"
        initialize_database(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_rss_and_atom_parsing_preserve_original_news_fields_and_provenance(self) -> None:
        rss = """<?xml version="1.0"?><rss version="2.0"><channel><item>
        <title><![CDATA[城市露营热点原始标题 email@example.test]]></title>
        <link>https://www.xinhuanet.com/story/one.html</link>
        <description><![CDATA[露营装备与周末旅行消费的完整摘要。]]></description>
        <pubDate>Sun, 01 Nov 2026 10:00:00 +0800</pubDate>
        </item></channel></rss>"""
        parsed = parse_feed_xml(rss, "xinhuanet_politics", collected_at="2026-11-01T11:00:00+08:00")
        self.assertEqual(len(parsed), 1)
        item = parsed[0]
        self.assertIn("email@example.test", item["title"])
        self.assertEqual(item["summary"], "露营装备与周末旅行消费的完整摘要。")
        self.assertEqual(item["published_at"], "2026-11-01T10:00:00+08:00")
        self.assertEqual(item["published_at_raw"], "Sun, 01 Nov 2026 10:00:00 +0800")
        self.assertEqual(item["published_at_method"], "rss_field")
        self.assertEqual(item["source_name"], "新华网时政")
        self.assertEqual(item["official_source_url"], "https://www.news.cn/linktous.htm")
        self.assertTrue(item["topic_tags"])

        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>科技热点</title>
        <link href="https://www.xinhuanet.com/story/two.html"/><summary>科技产业话题</summary>
        <updated>2026-11-02T08:30:00+08:00</updated></entry></feed>"""
        self.assertEqual(parse_feed_xml(atom, "xinhuanet_tech")[0]["source_url"], "https://www.xinhuanet.com/story/two.html")
        undated = "<rss><channel><item><title>历史报道</title><link>http://www.news.cn/politics/2022-12/14/c_1.html</link></item></channel></rss>"
        historic = parse_feed_xml(undated, "xinhuanet_politics")[0]
        self.assertEqual(historic["published_at"], "2022-12-14")
        self.assertEqual(historic["published_at_raw"], "")
        self.assertEqual(historic["published_at_method"], "source_url_path_date")

    def test_local_news_store_deduplicates_and_correlation_keeps_source_period_separate(self) -> None:
        news = {
            "news_id": "news-1", "source_name": "新华网时政", "source_key": "xinhuanet_politics",
            "title": "露营消费升温", "summary": "露营装备话题", "published_at": "2026-11-01T10:00:00+08:00",
            "collected_at": "2026-11-01T11:00:00+08:00", "source_url": "https://www.xinhuanet.com/story/one.html",
            "topic_tags": ["露营", "装备"],
        }
        first = append_news_items(self.news_path, [news, news])
        second = append_news_items(self.news_path, [news])
        self.assertEqual(first, {"received": 2, "added": 1, "total": 1})
        self.assertEqual(second, {"received": 1, "added": 0, "total": 1})
        self.assertEqual(len(load_news_items(self.news_path)), 1)

        notes = [
            {"note_id": "note-before", "title": "露营装备分享", "topics": ["#露营"], "source": "pgy_visible", "published_at": "2026-10-30", "metrics": {key: {"value": value, "period": "累计"} for key, value in (("点赞量", 10), ("收藏量", 5), ("评论量", 2), ("分享量", 1))}},
            {"note_id": "note-after", "title": "周末露营装备", "topics": ["#露营"], "source": "pgy_visible", "published_at": "2026-11-03", "metrics": {key: {"value": value, "period": "累计"} for key, value in (("点赞量", 20), ("收藏量", 10), ("评论量", 5), ("分享量", 5))}},
            {"note_id": "note-other-source", "title": "露营装备另一平台", "topics": ["#露营"], "source": "xhs_public", "published_at": "2026-11-03", "metrics": {key: {"value": value, "period": "累计"} for key, value in (("点赞量", 3), ("收藏量", 2), ("评论量", 1), ("分享量", 0))}},
        ]
        result = correlate_news_notes([news], notes)
        event = result["events"][0]
        self.assertEqual(event["matched_note_count"], 3)
        groups = {group["source"]: group for group in event["by_source_and_period"]}
        self.assertEqual(groups["pgy_visible"]["before_count"], 1)
        self.assertEqual(groups["pgy_visible"]["after_count"], 1)
        self.assertEqual(groups["pgy_visible"]["before_mean_interactions"], 18)
        self.assertEqual(groups["pgy_visible"]["after_mean_interactions"], 40)
        self.assertIn("不表示新闻导致", result["method_notice"])

    def test_legacy_news_rows_derive_and_label_url_date_without_fabricating_rss_date(self) -> None:
        legacy = {
            "news_id": "legacy-news", "source_key": "xinhuanet_politics",
            "title": "保留完整原始标题", "published_at": "",
            "source_url": "https://www.xinhuanet.com/politics/2022-12/08/c_1.html",
        }
        self.news_path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
        loaded = load_news_items(self.news_path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["title"], "保留完整原始标题")
        self.assertEqual(loaded[0]["published_at"], "2022-12-08")
        self.assertEqual(loaded[0]["published_at_raw"], "")
        self.assertEqual(loaded[0]["published_at_method"], "source_url_path_date")

    def test_news_api_collect_uses_fixture_without_live_request_or_formal_database(self) -> None:
        app = create_app({
            "DATABASE_PATH": self.db_path,
            "NEWS_DATA_PATH": self.news_path,
            "AI_REPORTS_PATH": self.root / "ai-reports",
            "BASELINE_PATH": self.root / "missing.json",
            "TESTING": True,
        })
        client = app.test_client()
        fixture = [{"news_id": "fixture-news", "source_key": "xinhuanet_tech", "source_name": "新华网科技", "title": "完整原文标题", "summary": "完整原文摘要", "published_at": "2026-11-01T12:00:00+08:00", "collected_at": "2026-11-01T12:10:00+08:00", "source_url": "https://www.xinhuanet.com/story/fixture", "topic_tags": ["科技"]}]
        with patch("creatorscope.fetch_feed", return_value=fixture):
            response = client.post("/api/news/collect", json={"source_key": "xinhuanet_tech"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["added"], 1)
        data = client.get("/api/news?source_key=xinhuanet_tech").get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["title"], "完整原文标题")
        self.assertEqual(client.get("/news").status_code, 200)
        with connection(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 0)
        self.assertEqual(json.loads(self.news_path.read_text(encoding="utf-8").strip())["news_id"], "fixture-news")


if __name__ == "__main__":
    unittest.main()
