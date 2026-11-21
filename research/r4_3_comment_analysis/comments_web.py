from __future__ import annotations

import csv
import io
import json
import sqlite3

from flask import Blueprint, Response, current_app, jsonify, make_response, render_template, request


comment_analysis_bp = Blueprint("comment_analysis", __name__)


def _filters() -> dict[str, str]:
    return {
        "batch_id": request.args.get("batch_id", ""),
        "creator_id": request.args.get("creator_id", ""),
        "note_id": request.args.get("note_id", ""),
        "comment_level": request.args.get("comment_level", ""),
        "is_pinned": request.args.get("is_pinned", ""),
        "q": request.args.get("q", ""),
    }


def _service():
    from .comment_analysis import DEFAULT_DB, fetch_comments, filter_options, make_wordcloud_png, summarize

    db_path = current_app.config.get("COMMENT_ANALYSIS_DATABASE_PATH", DEFAULT_DB)
    return db_path, fetch_comments, filter_options, make_wordcloud_png, summarize


@comment_analysis_bp.get("/comments")
def comments_page():
    return render_template("comments_analysis.html")


@comment_analysis_bp.get("/api/comments/options")
def comments_options():
    db_path, _, options_fn, _, _ = _service()
    return jsonify(options_fn(db_path))


@comment_analysis_bp.get("/api/comments/analysis")
def comments_analysis():
    db_path, fetch_fn, _, _, summarize_fn = _service()
    rows = fetch_fn(db_path, _filters())
    result = summarize_fn(rows)
    return jsonify(result)


@comment_analysis_bp.get("/api/comments/sentiment")
def comments_sentiment():
    db_path, fetch_fn, _, _, _ = _service()
    from .sentiment import analyze_comments, manual_review_sample

    rows = fetch_fn(db_path, _filters())
    result = analyze_comments(rows)
    result["review_sample_count"] = len(manual_review_sample(rows, limit=20))
    return jsonify(result)


@comment_analysis_bp.get("/api/comments/sentiment/review.csv")
def comments_sentiment_review():
    db_path, fetch_fn, _, _, _ = _service()
    from .sentiment import manual_review_sample

    rows = fetch_fn(db_path, _filters())
    sample = manual_review_sample(rows, limit=20)
    fields = [
        "batch_id", "source", "note_id", "platform_note_id", "note_title", "comment_id",
        "comment_text", "predicted_sentiment", "sentiment_evidence", "predicted_topics",
        "manual_sentiment", "manual_topics", "manual_notes",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(sample)
    response = make_response("\ufeff" + stream.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="comment_sentiment_manual_review.csv"'
    return response


@comment_analysis_bp.get("/api/comments/word-change")
def comments_word_change():
    db_path, fetch_fn, _, _, _ = _service()
    baseline_id = request.args.get("baseline_batch_id", "").strip()
    comparison_id = request.args.get("comparison_batch_id", "").strip()
    if not baseline_id or not comparison_id or baseline_id == comparison_id:
        return jsonify({"error": "请选择两个不同的评论批次"}), 400
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        sources = dict(conn.execute(
            "SELECT batch_id, source FROM batches WHERE batch_id IN (?, ?)",
            (baseline_id, comparison_id),
        ).fetchall())
    finally:
        conn.close()
    if len(sources) != 2:
        return jsonify({"error": "所选批次不存在"}), 404
    if sources[baseline_id] != sources[comparison_id]:
        return jsonify({"error": "只能比较相同采集来源的批次"}), 400

    filters = _filters()
    filters["batch_id"] = baseline_id
    baseline_rows = fetch_fn(db_path, filters)
    filters["batch_id"] = comparison_id
    comparison_rows = fetch_fn(db_path, filters)
    from .comment_analysis import compare_word_frequencies

    result = compare_word_frequencies(baseline_rows, comparison_rows)
    result["source"] = sources[baseline_id]
    result["baseline_batch_id"] = baseline_id
    result["comparison_batch_id"] = comparison_id
    return jsonify(result)


@comment_analysis_bp.get("/api/comments/wordcloud.png")
def comments_wordcloud():
    db_path, fetch_fn, _, wordcloud_fn, _ = _service()
    rows = fetch_fn(db_path, _filters())
    response = make_response(wordcloud_fn(rows))
    response.headers["Content-Type"] = "image/png"
    response.headers["Cache-Control"] = "no-store"
    return response


@comment_analysis_bp.get("/api/comments/export.csv")
def comments_export_csv():
    db_path, fetch_fn, _, _, _ = _service()
    rows = fetch_fn(db_path, _filters())
    fields = [
        "batch_id", "creator_name", "creator_platform_id", "note_id", "platform_note_id",
        "note_source_url", "note_title", "comment_text", "cleaned_text", "displayed_time",
        "like_count_raw", "like_count_normalized", "reply_count_raw", "raw_payload", "is_pinned",
        "comment_level", "comment_id", "local_comment_id", "parent_comment_id", "parent_local_comment_id",
        "local_comment_key", "parent_local_comment_key", "local_comment_id_hash", "parent_comment_id_hash",
        "local_comment_id_origin", "parent_comment_id_origin", "captured_at",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        row["raw_payload"] = json.dumps(row.get("raw_payload") or {}, ensure_ascii=False, sort_keys=True)
        writer.writerow(row)
    response = make_response("\ufeff" + stream.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="comments_export.csv"'
    return response


@comment_analysis_bp.get("/api/comments/export.jsonl")
def comments_export_jsonl():
    db_path, fetch_fn, _, _, _ = _service()
    rows = fetch_fn(db_path, _filters())
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    response = Response(payload, mimetype="application/x-ndjson; charset=utf-8")
    response.headers["Content-Disposition"] = 'attachment; filename="comments_export.jsonl"'
    return response
