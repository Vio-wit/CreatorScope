from __future__ import annotations

import csv
import io
import json

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
    for row in result["comments"]:
        row.pop("raw_payload", None)
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
        "batch_id", "creator_name", "note_id", "note_title", "comment_text", "cleaned_text",
        "displayed_time", "like_count_raw", "like_count_normalized", "reply_count_raw",
        "is_pinned", "comment_level", "parent_comment_id", "local_comment_id", "captured_at",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
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
