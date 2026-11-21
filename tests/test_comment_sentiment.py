from __future__ import annotations

import unittest

from research.r4_3_comment_analysis.sentiment import analyze_comments, manual_review_sample


class CommentSentimentTests(unittest.TestCase):
    def test_negation_intensity_themes_and_source_text_are_exploratory_and_auditable(self) -> None:
        rows = [
            {"batch_id": "b1", "source": "visible", "note_id": "n1", "note_title": "笔记一", "creator_id": "c1", "creator_name": "公开创作者", "comment_id": "p1", "comment_text": "  非常好用，客服发货快  "},
            {"batch_id": "b1", "source": "visible", "note_id": "n1", "note_title": "笔记一", "creator_id": "c1", "creator_name": "公开创作者", "comment_id": "p2", "comment_text": "不是很好用，有点贵"},
            {"batch_id": "b1", "source": "visible", "note_id": "n2", "note_title": "笔记二", "creator_id": "c2", "creator_name": "另一公开创作者", "comment_id": "p3", "comment_text": "一般，没别的反馈"},
            {"batch_id": "b1", "source": "visible", "note_id": "n2", "note_title": "笔记二", "creator_id": "c2", "creator_name": "另一公开创作者", "comment_id": "p4", "comment_text": "喜欢但差"},
        ]
        result = analyze_comments(rows)
        self.assertEqual(result["summary"]["comment_count"], 4)
        self.assertEqual(result["summary"]["sentiment_counts"]["正向倾向"], 1)
        self.assertEqual(result["summary"]["sentiment_counts"]["负向倾向"], 1)
        self.assertEqual(result["summary"]["sentiment_counts"]["混合倾向"], 1)
        self.assertEqual(result["summary"]["sentiment_counts"]["中性 / 未命中词典"], 1)
        self.assertEqual(result["comments"][0]["comment_text"], rows[0]["comment_text"])
        negative = result["comments"][1]["sentiment"]
        self.assertEqual(negative["label"], "负向倾向")
        self.assertIn("产品效果与使用", negative["themes"])
        self.assertIn("价格与购买决策", negative["themes"])
        self.assertIn("服务与履约", result["comments"][0]["sentiment"]["themes"])
        self.assertEqual({item["label"] for item in result["by_note"]}, {"笔记一", "笔记二"})
        self.assertEqual({item["label"] for item in result["by_creator"]}, {"公开创作者", "另一公开创作者"})
        self.assertIn("不能确定个人真实情绪", result["summary"]["scope_notice"])

    def test_manual_review_sample_is_deterministic_and_keeps_label_columns_blank(self) -> None:
        rows = [
            {"batch_id": "b1", "note_id": "n1", "comment_id": f"c{i}", "comment_text": f"原文{i} 很好用"}
            for i in range(30)
        ]
        first = manual_review_sample(rows, limit=12)
        second = manual_review_sample(rows, limit=12)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 12)
        self.assertTrue(all(item["manual_sentiment"] == "" and item["manual_topics"] == "" for item in first))
        self.assertEqual({item["comment_text"] for item in first}, {item["comment_text"] for item in second})


if __name__ == "__main__":
    unittest.main()
