from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any

import jieba

from .comment_analysis import load_stopwords, tokenize


POSITIVE = {
    "好": 1.0, "好用": 1.0, "不错": 1.0, "喜欢": 1.0, "满意": 1.0,
    "推荐": 1.0, "值得": 1.0, "舒服": 1.0, "方便": 0.8, "有效": 1.0,
    "实用": 0.8, "清晰": 0.8, "惊喜": 1.0, "回购": 1.0, "划算": 0.9,
    "便宜": 0.7, "出色": 1.0, "香": 0.7, "赞": 0.8, "满意度": 1.0,
}
NEGATIVE = {
    "差": 1.0, "不好": 1.0, "不好用": 1.0, "难用": 1.0, "失望": 1.0,
    "踩雷": 1.0, "过敏": 1.0, "刺激": 0.8, "不值": 0.9, "太贵": 1.0,
    "贵": 0.7, "不推荐": 1.0, "不满意": 1.0, "麻烦": 0.6, "后悔": 1.0,
    "虚假": 1.0, "差评": 1.0, "难看": 0.8, "油腻": 0.7,
}
NEGATORS = {"不", "没", "没有", "并不", "并非", "不是", "未", "别", "无", "不太", "不怎么"}
DEGREE = {
    "非常": 1.8, "特别": 1.6, "超级": 1.8, "十分": 1.7, "极其": 1.9,
    "很": 1.35, "挺": 1.25, "比较": 1.15, "太": 1.4, "有点": 0.65,
    "稍微": 0.7, "略微": 0.7,
}
THEMES = {
    "产品效果与使用": ("效果", "好用", "不好用", "肤感", "保湿", "持久", "刺痛", "过敏", "清洁", "上脸", "味道", "气味"),
    "价格与购买决策": ("价格", "贵", "便宜", "性价比", "平替", "入手", "购买", "下单", "回购", "值得"),
    "内容与推荐": ("推荐", "种草", "同款", "链接", "求链接", "怎么买", "哪里买", "博主", "笔记", "视频"),
    "服务与履约": ("客服", "物流", "发货", "快递", "售后", "包装", "退货", "退款"),
    "个人体验反馈": ("体验", "喜欢", "满意", "失望", "踩雷", "好用", "不好用", "舒服", "后悔"),
}


def _tokens(text: str) -> list[str]:
    return [token.strip().casefold() for token in jieba.lcut(text, HMM=True) if token.strip()]


def _sentiment(text: str) -> dict[str, Any]:
    tokens = _tokens(text)
    evidence = []
    positive_score = 0.0
    negative_score = 0.0
    candidates = []
    for polarity, lexicon in ((1, POSITIVE), (-1, NEGATIVE)):
        for phrase in lexicon:
            start = 0
            while (start := text.find(phrase, start)) >= 0:
                candidates.append((start, start + len(phrase), phrase, polarity, lexicon[phrase]))
                start += len(phrase)
    matches = []
    last_end = -1
    for start, end, phrase, polarity, weight in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]), item[3])):
        if start < last_end:
            continue
        matches.append((start, phrase, polarity, weight))
        last_end = end
    for start, phrase, polarity, weight in matches:
        preceding = _tokens(text[:start])[-3:]
        modifiers = [DEGREE[item] for item in preceding if item in DEGREE]
        if modifiers:
            weight *= modifiers[-1]
        negation_count = sum(item in NEGATORS for item in preceding)
        if negation_count % 2:
            polarity *= -1
        signed = polarity * weight
        positive_score += max(0.0, signed)
        negative_score += max(0.0, -signed)
        evidence.append({"token": phrase, "polarity": "positive" if signed > 0 else "negative", "weight": round(weight, 2)})

    score = positive_score - negative_score
    if positive_score and negative_score:
        label = "混合倾向"
    elif score > 0:
        label = "正向倾向"
    elif score < 0:
        label = "负向倾向"
    elif tokens:
        label = "中性 / 未命中词典"
    else:
        label = "未分类（空文本）"
    matched_themes = [theme for theme, keywords in THEMES.items() if any(keyword in text for keyword in keywords)]
    return {
        "label": label,
        "score": round(score, 2),
        "positive_evidence_score": round(positive_score, 2),
        "negative_evidence_score": round(negative_score, 2),
        "evidence": evidence,
        "themes": matched_themes,
        "token_count": len(tokens),
    }


def _tfidf_keywords(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    stopwords = load_stopwords()
    documents = [Counter(tokenize(str(row.get("comment_text") or ""), stopwords)) for row in rows]
    documents = [document for document in documents if document]
    if not documents:
        return []

    document_frequency: Counter[str] = Counter()
    for document in documents:
        document_frequency.update(document.keys())

    scores: Counter[str] = Counter()
    for document in documents:
        token_total = sum(document.values())
        for word, frequency in document.items():
            inverse_document_frequency = math.log((len(documents) + 1) / (document_frequency[word] + 1)) + 1
            scores[word] += (frequency / token_total) * inverse_document_frequency

    return [
        {
            "word": word,
            "score": round(score, 6),
            "document_frequency": document_frequency[word],
            "document_count": len(documents),
        }
        for word, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:max(0, limit)]
    ]


def analyze_comments(rows: list[dict[str, Any]]) -> dict[str, Any]:
    analyzed = []
    sentiment_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    by_note: dict[tuple[str, str], dict[str, Any]] = {}
    by_creator: dict[tuple[str, str], dict[str, Any]] = {}
    note_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    creator_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for row in rows:
        raw_text = str(row.get("comment_text") or "")
        result = _sentiment(raw_text)
        sentiment_counts[result["label"]] += 1
        topic_counts.update(result["themes"])
        item = {
            "comment_id": row.get("comment_id"),
            "local_comment_id": row.get("local_comment_id"),
            "batch_id": row.get("batch_id"),
            "source": row.get("source"),
            "note_id": row.get("note_id"),
            "platform_note_id": row.get("platform_note_id"),
            "note_title": row.get("note_title"),
            "note_source_url": row.get("note_source_url"),
            "creator_id": row.get("creator_id"),
            "creator_name": row.get("creator_name"),
            "comment_text": raw_text,
            "sentiment": result,
        }
        analyzed.append(item)

        note_key = (str(row.get("note_id") or ""), str(row.get("note_title") or ""))
        note_group = by_note.setdefault(note_key, {"note_id": note_key[0], "label": note_key[1], "sample_count": 0, "topics": Counter(), "sentiments": Counter()})
        note_group["sample_count"] += 1
        note_group["topics"].update(result["themes"])
        note_group["sentiments"][result["label"]] += 1
        note_rows.setdefault(note_key, []).append(row)

        creator_key = (str(row.get("creator_id") or ""), str(row.get("creator_name") or ""))
        creator_group = by_creator.setdefault(creator_key, {"creator_id": creator_key[0], "label": creator_key[1], "sample_count": 0, "topics": Counter(), "sentiments": Counter()})
        creator_group["sample_count"] += 1
        creator_group["topics"].update(result["themes"])
        creator_group["sentiments"][result["label"]] += 1
        creator_rows.setdefault(creator_key, []).append(row)

    def groups(source: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        for item_key, item in source.items():
            output.append({
                key: value for key, value in item.items() if key not in {"topics", "sentiments"}
            } | {
                "topics": [{"label": label, "count": count} for label, count in item["topics"].most_common()],
                "sentiments": dict(item["sentiments"]),
                "tfidf_keywords": _tfidf_keywords(
                    note_rows[item_key] if source is by_note else creator_rows[item_key]
                ),
            })
        return sorted(output, key=lambda item: (-item["sample_count"], item["label"]))

    count = len(rows)
    scope_notice = "小样本探索：词典规则不能确定个人真实情绪，需人工抽样复核。" if count < 100 else "词典规则描述当前样本倾向；不能据此断言个人真实情绪，需人工抽样复核。"
    return {
        "summary": {
            "comment_count": count,
            "sentiment_counts": {label: sentiment_counts.get(label, 0) for label in ("正向倾向", "中性 / 未命中词典", "负向倾向", "混合倾向", "未分类（空文本）")},
            "topic_counts": [{"label": label, "count": value} for label, value in topic_counts.most_common()],
            "scope_notice": scope_notice,
            "method": "jieba 分词 + 小型情感词典 + 前置否定词与程度副词规则 + 可解释主题关键词 + 按评论计算并聚合的 TF-IDF 关键词；词典匹配仅作为倾向线索。",
            "tfidf_keywords": _tfidf_keywords(rows),
        },
        "by_note": groups(by_note),
        "by_creator": groups(by_creator),
        "comments": analyzed,
    }


def manual_review_sample(rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        raw_text = str(row.get("comment_text") or "")
        result = _sentiment(raw_text)
        identity = "\0".join((str(row.get("batch_id") or ""), str(row.get("note_id") or ""), str(row.get("comment_id") or row.get("local_comment_id") or ""), raw_text))
        candidates.append((hashlib.sha256(identity.encode("utf-8")).hexdigest(), row, result))
    selected = sorted(candidates, key=lambda item: item[0])[:max(0, min(int(limit), 100))]
    return [
        {
            "batch_id": row.get("batch_id"),
            "source": row.get("source"),
            "note_id": row.get("note_id"),
            "platform_note_id": row.get("platform_note_id"),
            "note_title": row.get("note_title"),
            "comment_id": row.get("comment_id"),
            "comment_text": str(row.get("comment_text") or ""),
            "predicted_sentiment": result["label"],
            "sentiment_evidence": "; ".join(item["token"] for item in result["evidence"]),
            "predicted_topics": "、".join(result["themes"]),
            "manual_sentiment": "",
            "manual_topics": "",
            "manual_notes": "",
        }
        for _, row, result in selected
    ]
