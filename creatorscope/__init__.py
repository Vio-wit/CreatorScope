from __future__ import annotations

import os
import csv
import io
import json
import math
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, make_response, render_template, request

from .analytics import analytics_data, compare_creators, compare_notes, creator_library, dashboard_data, note_compare_options, notes_data, overview_data, wordcloud_png
from .ai import AIServiceError, build_preview, generated_at, list_analysis_reports, load_analysis_report, provider_status, run_analysis, save_analysis_report, test_connection
from .browser import PgyVisibleBrowser
from .db import SCHEMA_VERSION, connection, initialize_database, schema_version_readonly, verify_database_readonly
from .domain import field_dictionary
from .ingest import MAX_UPLOAD_BYTES
from .history_analytics import HistoryAnalyticsError, analysis_data as history_analysis_data, history_creator_library, history_note_library, list_library_sources as history_library_source_options, list_sources as history_source_options, note_samples as history_note_samples
from .unified_library import unified_creator_library
from .news import NEWS_FEEDS, append_news_items, correlate_news_notes, fetch_feed, load_news_items
from .quality import batch_quality_report, quality_overview
from .report import build_report, render_docx, render_markdown, report_filename
from .tasks import TaskManager
from .migrations import backup_database
from research.r4_3_comment_analysis.comments_web import comment_analysis_bp


def _raw_field(fields: dict[str, Any], names: tuple[str, ...], fallback: Any = None) -> Any:
    for name in names:
        value = fields.get(name)
        if value is not None and str(value).strip():
            return value
    return fallback


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    project_root = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    instance_dir = project_root / "instance"
    instance_dir.mkdir(parents=True, exist_ok=True)
    expanded_history_default = project_root / "_preview" / "v24_history_expanded_final_20261123.sqlite3"
    if not expanded_history_default.is_file():
        expanded_history_default = project_root / "_preview" / "v22_s3_history_final_20261123.sqlite3"
    app.config.update(
        DATABASE_PATH=Path(
            os.environ.get("CREATORSCOPE_DB", str(instance_dir / "creatorscope_unified.sqlite3"))
        ),
        AI_REPORTS_PATH=instance_dir / "ai_reports",
        NEWS_DATA_PATH=instance_dir / "news_items.jsonl",
        MIGRATION_BACKUP_DIR=project_root / "_backup",
        BASELINE_PATH=project_root / "data" / "baseline" / "pgy_live_sample_2026-11-19.json",
        HISTORY_EVIDENCE_DB=Path(
            os.environ.get(
                "CREATORSCOPE_HISTORY_DB",
                str(expanded_history_default),
            )
        ),
        COURSE_SAMPLE_DATABASE=Path(
            os.environ.get(
                "CREATORSCOPE_COURSE_SAMPLE_DB",
                str(project_root / "_preview" / "v2_field_validation_20261123.sqlite3"),
            )
        ),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + 64 * 1024,
        DEMO_MODE=os.environ.get("CREATORSCOPE_DEMO_MODE", "0").strip().casefold() in {"1", "true", "yes"},
    )
    if test_config:
        app.config.update(test_config)

    app.config["DATABASE_PATH"] = Path(app.config["DATABASE_PATH"])
    app.config["AI_REPORTS_PATH"] = Path(app.config["AI_REPORTS_PATH"])
    app.config["NEWS_DATA_PATH"] = Path(app.config["NEWS_DATA_PATH"])
    # All CreatorScope modules share one local database, including comment analysis.
    app.config["COMMENT_ANALYSIS_DATABASE_PATH"] = app.config["DATABASE_PATH"]
    app.config["BASELINE_PATH"] = Path(app.config["BASELINE_PATH"])
    if app.config["HISTORY_EVIDENCE_DB"] is not None:
        app.config["HISTORY_EVIDENCE_DB"] = Path(app.config["HISTORY_EVIDENCE_DB"])
    if app.config["COURSE_SAMPLE_DATABASE"] is not None:
        app.config["COURSE_SAMPLE_DATABASE"] = Path(app.config["COURSE_SAMPLE_DATABASE"])
    if app.config["DEMO_MODE"]:
        verify_database_readonly(app.config["DATABASE_PATH"])
    else:
        database_path = Path(app.config["DATABASE_PATH"])
        current_version = schema_version_readonly(database_path)
        if database_path.is_file() and current_version < SCHEMA_VERSION:
            backup_dir = Path(app.config["MIGRATION_BACKUP_DIR"])
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup_path = backup_dir / f"{database_path.stem}_pre_schema_v{SCHEMA_VERSION}_{stamp}.sqlite3"
            try:
                backup_result = backup_database(database_path, backup_path)
            except Exception as exc:
                raise sqlite3.DatabaseError(
                    f"数据库 schema v{current_version} 迁移前备份失败；已阻止启动和迁移"
                ) from exc
            if backup_result.get("status") not in {"created_and_verified", "already_verified"}:
                raise sqlite3.DatabaseError(
                    f"数据库 schema v{current_version} 迁移前未能创建并验证备份；已阻止启动和迁移"
                )
            print(f"Verified SQLite migration backup: {backup_result['backup']}")
        initialize_database(app.config["DATABASE_PATH"])

    local_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    browser = PgyVisibleBrowser(local_data / "CreatorScope" / "pgy_browser_profile")
    tasks = TaskManager(
        app.config["DATABASE_PATH"],
        app.config["BASELINE_PATH"],
        browser,
        restore_interrupted_collections=not app.config["DEMO_MODE"],
    )
    app.extensions["creatorscope_browser"] = browser
    app.extensions["creatorscope_tasks"] = tasks
    app.register_blueprint(comment_analysis_bp)

    @app.before_request
    def block_demo_writes():
        if app.config["DEMO_MODE"] and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return jsonify({"error": "离线演示数据库只读；请使用筛选、分析和导出"}), 409
        return None

    @app.get("/")
    def home():
        return render_template("index.html", demo_mode=app.config["DEMO_MODE"])

    @app.get("/ai")
    def ai_page():
        return render_template("ai_analysis.html")

    @app.get("/news")
    def news_page():
        return render_template("news.html")

    @app.get("/api/news/options")
    def api_news_options():
        with connection(app.config["DATABASE_PATH"]) as conn:
            batches = [dict(row) for row in conn.execute(
                "SELECT batch_id, source, captured_at FROM batches ORDER BY captured_at DESC, rowid DESC"
            ).fetchall()]
        return jsonify({
            "sources": [{"value": key, "label": value["source_name"], "feed_url": value["feed_url"], "official_source_url": value["official_source_url"]} for key, value in NEWS_FEEDS.items()],
            "batches": batches,
        })

    @app.get("/api/news")
    def api_news():
        source_key = request.args.get("source_key", "").strip()
        items = load_news_items(app.config["NEWS_DATA_PATH"])
        if source_key:
            items = [item for item in items if item.get("source_key") == source_key]
        raw_batch_id = request.args.get("batch_id") or ""
        selected_batch = None if raw_batch_id in {"", "__all__"} else raw_batch_id
        note_rows = notes_data(app.config["DATABASE_PATH"], batch_id=selected_batch)["notes"] if selected_batch else []
        correlation = correlate_news_notes(items, note_rows)
        date_methods: dict[str, int] = {}
        for item in items:
            method = str(item.get("published_at_method") or "missing")
            date_methods[method] = date_methods.get(method, 0) + 1
        published_days = sorted(str(item.get("published_at") or "")[:10] for item in items if item.get("published_at"))
        return jsonify({
            "items": sorted(items, key=lambda item: str(item.get("published_at") or ""), reverse=True),
            "count": len(items), "correlation": correlation,
            "batch_id": selected_batch,
            "date_coverage": {
                "methods": date_methods,
                "earliest": published_days[0] if published_days else None,
                "latest": published_days[-1] if published_days else None,
            },
            "availability": "选择具体笔记批次后计算关联；无选定批次时只显示新闻数据。" if not selected_batch else correlation["method_notice"],
        })

    @app.post("/api/news/collect")
    def api_news_collect():
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示模式不启用网络新闻采集"}), 409
        payload = request.get_json(silent=True) or {}
        source_key = str(payload.get("source_key") or "")
        try:
            items = fetch_feed(source_key)
            saved = append_news_items(app.config["NEWS_DATA_PATH"], items)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 502
        except OSError:
            return jsonify({"error": "无法保存本地 RSS 记录"}), 500
        return jsonify({"status": "completed", "source_key": source_key, **saved})

    @app.get("/api/ai/config")
    def api_ai_config():
        provider = request.args.get("provider", "openai_compatible")
        try:
            return jsonify(provider_status(
                provider,
                base_url=request.args.get("base_url") or None,
                model=request.args.get("model") or None,
            ))
        except AIServiceError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/ai/options")
    def api_ai_options():
        creators = creator_library(app.config["DATABASE_PATH"])
        notes = notes_data(app.config["DATABASE_PATH"])
        with connection(app.config["DATABASE_PATH"]) as conn:
            batches = [dict(row) for row in conn.execute(
                "SELECT batch_id, source, captured_at FROM batches ORDER BY captured_at DESC, rowid DESC"
            ).fetchall()]
        return jsonify({
            "creators": [
                {"value": item["creator_id"], "label": item["pseudonym"]}
                for item in creators["creators"]
            ],
            "notes": [
                {"value": item["note_id"], "label": f"{item['creator_name']} · {item['title']}"}
                for item in notes["notes"]
            ],
            "batches": batches,
        })

    @app.post("/api/ai/preview")
    def api_ai_preview():
        payload = request.get_json(silent=True) or {}
        try:
            result = build_preview(
                app.config["DATABASE_PATH"],
                analysis_type=str(payload.get("analysis_type") or ""),
                creator_ids=payload.get("creator_ids") if isinstance(payload.get("creator_ids"), list) else [],
                note_ids=payload.get("note_ids") if isinstance(payload.get("note_ids"), list) else [],
                batch_id=str(payload.get("batch_id") or ""),
                include_raw_text=payload.get("include_raw_text") is True,
            )
        except AIServiceError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/ai/test")
    def api_ai_test():
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示模式不连接模型服务"}), 409
        payload = request.get_json(silent=True) or {}
        provider = str(payload.get("provider") or "openai_compatible")
        try:
            model = str(payload.get("model") or "")
            base_url = str(payload.get("base_url") or "")
            status = provider_status(provider, base_url=base_url, model=model)
        except AIServiceError as exc:
            return jsonify({"error": str(exc)}), 400
        if not status["key_available"]:
            return jsonify({
                "status": "not_configured",
                "message": f"未配置密钥；请在本机环境变量中设置 {status['key_env']}",
            })
        if not status["model_configured"]:
            return jsonify({"status": "incomplete", "message": "请填写服务商支持的模型 ID"})
        try:
            test_connection(provider=provider, model=model, base_url=base_url)
        except AIServiceError as exc:
            return jsonify({"error": str(exc)}), 503
        return jsonify({"status": "connected", "provider": provider, "provider_label": status["label"], "model": status["model"]})

    @app.post("/api/ai/analyze")
    def api_ai_analyze():
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示模式不连接模型服务"}), 409
        payload = request.get_json(silent=True) or {}
        if payload.get("confirm_upload") is not True:
            return jsonify({"error": "请先检查本机原始数据预览并明确确认数据外发"}), 400
        provider = str(payload.get("provider") or "openai_compatible")
        model = str(payload.get("model") or "")
        base_url = str(payload.get("base_url") or "")
        try:
            status = provider_status(provider, base_url=base_url, model=model)
        except AIServiceError as exc:
            return jsonify({"error": str(exc)}), 400
        if not status["key_available"]:
            return jsonify({
                "status": "not_configured",
                "message": f"未配置密钥；请在本机环境变量中设置 {status['key_env']}",
            })
        if not status["model_configured"]:
            return jsonify({"status": "incomplete", "message": "请填写服务商支持的模型 ID"})
        try:
            preview = build_preview(
                app.config["DATABASE_PATH"],
                analysis_type=str(payload.get("analysis_type") or ""),
                creator_ids=payload.get("creator_ids") if isinstance(payload.get("creator_ids"), list) else [],
                note_ids=payload.get("note_ids") if isinstance(payload.get("note_ids"), list) else [],
                batch_id=str(payload.get("batch_id") or ""),
                include_raw_text=payload.get("include_raw_text") is True,
            )
            output = run_analysis(provider=provider, model=model, base_url=base_url, preview=preview)
        except AIServiceError as exc:
            return jsonify({"error": str(exc)}), 503
        analysis_at = generated_at()
        report = {
            "analysis_type": preview["analysis_type"],
            "analysis_label": preview["analysis_label"],
            "provider": provider,
            "provider_label": status["label"],
            "model": status["model"],
            "input_batch_id": str(payload.get("batch_id") or ""),
            "input_creator_ids": payload.get("creator_ids") if isinstance(payload.get("creator_ids"), list) else [],
            "input_note_ids": payload.get("note_ids") if isinstance(payload.get("note_ids"), list) else [],
            "include_raw_text": preview["include_raw_text"],
            "analysis_at": analysis_at,
            "scope_summary": preview["scope_summary"],
            "facts": preview["data"],
            "model_output": output,
            "output_kind": "模型推断；请结合事实数据复核",
            "report_saved": True,
        }
        try:
            saved_report = save_analysis_report(app.config["AI_REPORTS_PATH"], report)
        except OSError:
            saved_report = {**report, "run_id": "", "report_saved": False}
        return jsonify({
            "status": "completed",
            **saved_report,
        })

    @app.get("/api/ai/reports")
    def api_ai_reports():
        return jsonify({"reports": list_analysis_reports(app.config["AI_REPORTS_PATH"])})

    @app.get("/api/ai/reports/<run_id>")
    def api_ai_report_detail(run_id: str):
        report = load_analysis_report(app.config["AI_REPORTS_PATH"], run_id)
        if report is None:
            return jsonify({"error": "没有找到该本地 AI 分析报告"}), 404
        return jsonify(report)

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.get("/api/dashboard")
    def api_dashboard():
        raw_batch_id = request.args.get("batch_id") or None
        all_batches = raw_batch_id == "__all__"
        data = dashboard_data(
            app.config["DATABASE_PATH"],
            batch_id=None if all_batches else raw_batch_id,
            record_type=request.args.get("record_type") or None,
            all_batches=all_batches,
            source=request.args.get("source", "").strip(),
        )
        return jsonify(data)

    @app.get("/api/overview")
    def api_overview():
        return jsonify(overview_data(app.config["DATABASE_PATH"]))

    @app.get("/api/analytics")
    def api_analytics():
        raw_batch_id = request.args.get("batch_id") or None
        return jsonify(analytics_data(
            app.config["DATABASE_PATH"],
            batch_id=None if raw_batch_id == "__all__" else raw_batch_id,
            source=request.args.get("source", "").strip(),
            creator_id=request.args.get("creator_id", "").strip(),
            note_id=request.args.get("note_id", "").strip(),
            category=request.args.get("category", "").strip(),
            date_from=request.args.get("date_from", "").strip(),
            date_to=request.args.get("date_to", "").strip(),
            note_type=request.args.get("note_type", "").strip(),
            topic=request.args.get("topic", "").strip(),
            query=request.args.get("q", "").strip(),
            word_kind=request.args.get("word_kind", "body").strip(),
        ))

    @app.get("/api/history-analysis/sources")
    def api_history_analysis_sources():
        try:
            return jsonify(history_source_options(app.config["HISTORY_EVIDENCE_DB"]))
        except HistoryAnalyticsError as exc:
            return jsonify({"error": str(exc)}), 503

    @app.get("/api/history-analysis")
    def api_history_analysis():
        try:
            return jsonify(history_analysis_data(
                app.config["HISTORY_EVIDENCE_DB"],
                source_key=request.args.get("source", "EPT").strip() or "EPT",
            ))
        except HistoryAnalyticsError as exc:
            status = 400 if str(exc) == "请选择有效的数据来源" else 503
            return jsonify({"error": str(exc)}), status

    @app.get("/api/history-analysis/note-samples")
    def api_history_analysis_note_samples():
        try:
            return jsonify(history_note_samples(app.config["HISTORY_EVIDENCE_DB"]))
        except HistoryAnalyticsError as exc:
            return jsonify({"error": str(exc)}), 503

    @app.get("/api/history-analysis/note-library")
    def api_history_analysis_note_library():
        try:
            try:
                offset = int(request.args.get("offset", "0"))
                limit = int(request.args.get("limit", "12"))
            except ValueError as exc:
                raise ValueError("分页参数必须是整数") from exc
            if offset < 0 or limit < 1 or limit > 100:
                raise ValueError("笔记分页范围无效（limit 最大为 100）")
            return jsonify(history_note_library(
                app.config["HISTORY_EVIDENCE_DB"],
                query=request.args.get("q", ""),
                offset=offset,
                limit=limit,
            ))
        except HistoryAnalyticsError as exc:
            return jsonify({"error": str(exc)}), 503
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/history-analysis/library-sources")
    def api_history_analysis_library_sources():
        try:
            return jsonify(history_library_source_options(app.config["HISTORY_EVIDENCE_DB"]))
        except HistoryAnalyticsError as exc:
            return jsonify({"error": str(exc)}), 503

    @app.get("/api/history-analysis/library")
    def api_history_analysis_library():
        def optional_number(name: str) -> float | None:
            raw = request.args.get(name, "").strip()
            if not raw:
                return None
            try:
                value = float(raw)
                if not math.isfinite(value):
                    raise ValueError
                return value
            except ValueError as exc:
                raise ValueError(f"筛选参数 {name} 必须是数字") from exc

        try:
            try:
                offset = int(request.args.get("offset", "0"))
                limit = int(request.args.get("limit", "100"))
            except ValueError as exc:
                raise ValueError("分页参数必须是整数") from exc
            if offset < 0 or limit < 1 or limit > 200:
                raise ValueError("分页范围无效（limit 最大为 200）")
            numeric_filters = {
                name: optional_number(name)
                for name in (
                    "followers_min", "followers_max", "daily_read_min", "daily_read_max",
                    "daily_interaction_min", "daily_interaction_max",
                )
            }
            if any(value is not None and value < 0 for value in numeric_filters.values()):
                raise ValueError("数值筛选范围不能小于 0")
            for low_key, high_key in (
                ("followers_min", "followers_max"),
                ("daily_read_min", "daily_read_max"),
                ("daily_interaction_min", "daily_interaction_max"),
            ):
                low, high = numeric_filters[low_key], numeric_filters[high_key]
                if low is not None and high is not None and low > high:
                    raise ValueError("筛选下限不能大于上限")
            data = history_creator_library(
                app.config["HISTORY_EVIDENCE_DB"],
                source_key=request.args.get("source", "EPT").strip() or "EPT",
                query=request.args.get("q", ""),
                category=request.args.get("category", ""),
                location=request.args.get("location", ""),
                offset=offset,
                limit=limit,
                **numeric_filters,
            )
        except HistoryAnalyticsError as exc:
            status = 400 if str(exc) == "请选择有效的数据来源" else 503
            return jsonify({"error": str(exc)}), status
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(data)

    @app.get("/api/wordcloud.png")
    def api_wordcloud():
        try:
            raw_batch_id = request.args.get("batch_id") or None
            image = wordcloud_png(
                app.config["DATABASE_PATH"],
                batch_id=None if raw_batch_id == "__all__" else raw_batch_id,
                kind=request.args.get("kind", "body").strip(),
                source=request.args.get("source", "").strip(),
                creator_id=request.args.get("creator_id", "").strip(),
                note_id=request.args.get("note_id", "").strip(),
                category=request.args.get("category", "").strip(),
                date_from=request.args.get("date_from", "").strip(),
                date_to=request.args.get("date_to", "").strip(),
                note_type=request.args.get("note_type", "").strip(),
                topic=request.args.get("topic", "").strip(),
                query=request.args.get("q", "").strip(),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 503
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        response = make_response(image)
        response.headers["Content-Type"] = "image/png"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/creators")
    def api_creators():
        def optional_number(name: str) -> float | None:
            raw = request.args.get(name, "").strip()
            if not raw:
                return None
            try:
                value = float(raw)
                if not math.isfinite(value):
                    raise ValueError
                return value
            except ValueError as exc:
                raise ValueError(f"筛选参数 {name} 必须是数字") from exc

        try:
            data = creator_library(
                app.config["DATABASE_PATH"],
                query=request.args.get("q", ""),
                category=request.args.get("category", ""),
                followers_min=optional_number("followers_min"),
                followers_max=optional_number("followers_max"),
                quote_min=optional_number("quote_min"),
                quote_max=optional_number("quote_max"),
                location=request.args.get("location", ""),
                sort_by=request.args.get("sort_by", "pseudonym"),
                sort_desc=request.args.get("sort_desc", "true").casefold() != "false",
                batch_id=(request.args.get("batch_id") or None) if request.args.get("batch_id") != "__all__" else None,
                source=request.args.get("source", "").strip(),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(data)

    @app.get("/api/unified-creators")
    def api_unified_creators():
        def optional_nonnegative(name: str) -> float | None:
            raw = request.args.get(name, "").strip()
            if not raw:
                return None
            try:
                value = float(raw)
                if not math.isfinite(value) or value < 0:
                    raise ValueError
                return value
            except ValueError as exc:
                raise ValueError(f"筛选参数 {name} 必须是非负数字") from exc

        try:
            try:
                offset = int(request.args.get("offset", "0"))
                limit = int(request.args.get("limit", "50"))
            except ValueError as exc:
                raise ValueError("分页参数必须是整数") from exc
            lower, upper = optional_nonnegative("followers_min"), optional_nonnegative("followers_max")
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("粉丝下限不能大于上限")
            data = unified_creator_library(
                app.config["DATABASE_PATH"],
                app.config["COURSE_SAMPLE_DATABASE"],
                app.config["HISTORY_EVIDENCE_DB"],
                query=request.args.get("q", ""),
                bucket=request.args.get("bucket", "all"),
                category=request.args.get("category", ""),
                ip_location=request.args.get("ip_location", ""),
                followers_min=lower,
                followers_max=upper,
                offset=offset,
                limit=limit,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(data)

    @app.get("/api/creators/compare")
    def api_creator_compare():
        creator_ids = list(dict.fromkeys(request.args.getlist("creator_id")))
        if not 2 <= len(creator_ids) <= 4:
            return jsonify({"error": "请选择 2 至 4 位创作者进行对比"}), 400
        raw_batch_id = request.args.get("batch_id") or None
        if not raw_batch_id or raw_batch_id == "__all__":
            return jsonify({"error": "请选择一个具体采集批次，以保持对比口径一致"}), 400
        data = compare_creators(
            app.config["DATABASE_PATH"],
            creator_ids,
            batch_id=raw_batch_id,
            source=request.args.get("source", "").strip(),
        )
        return jsonify(data)

    @app.get("/api/notes/compare")
    def api_notes_compare():
        note_ids = list(dict.fromkeys(request.args.getlist("note_id")))
        raw_batch_id = request.args.get("batch_id") or None
        if not raw_batch_id or raw_batch_id == "__all__":
            return jsonify({"error": "请选择一个具体采集批次，以保持对比口径一致"}), 400
        if not 2 <= len(note_ids) <= 4:
            return jsonify({"error": "请选择 2 至 4 篇笔记进行对比"}), 400
        try:
            data = compare_notes(
                app.config["DATABASE_PATH"],
                note_ids,
                batch_id=raw_batch_id,
                source=request.args.get("source", "").strip(),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(data)

    @app.get("/api/notes/compare/options")
    def api_notes_compare_options():
        raw_batch_id = request.args.get("batch_id") or None
        if not raw_batch_id or raw_batch_id == "__all__":
            return jsonify({"error": "请选择一个具体采集批次"}), 400
        return jsonify({"options": note_compare_options(
            app.config["DATABASE_PATH"],
            batch_id=raw_batch_id,
            source=request.args.get("source", "").strip(),
        )})

    @app.get("/api/notes")
    def api_notes():
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        for name, value in (("date_from", date_from), ("date_to", date_to)):
            if value:
                try:
                    from datetime import date
                    date.fromisoformat(value)
                except ValueError:
                    return jsonify({"error": f"{name} 必须是 YYYY-MM-DD 日期"}), 400
        raw_batch_id = request.args.get("batch_id") or None
        data = notes_data(
            app.config["DATABASE_PATH"],
            batch_id=None if raw_batch_id == "__all__" else raw_batch_id,
            creator_id=request.args.get("creator_id", "").strip(),
            note_id=request.args.get("note_id", "").strip(),
            source=request.args.get("source", "").strip(),
            category=request.args.get("category", "").strip(),
            date_from=date_from,
            date_to=date_to,
            note_type=request.args.get("note_type", "").strip(),
            topic=request.args.get("topic", "").strip(),
            query=request.args.get("q", "").strip(),
        )
        return jsonify(data)

    @app.get("/api/notes/export.csv")
    def api_notes_export():
        raw_batch_id = request.args.get("batch_id") or None
        data = notes_data(
            app.config["DATABASE_PATH"],
            batch_id=None if raw_batch_id == "__all__" else raw_batch_id,
            creator_id=request.args.get("creator_id", "").strip(),
            note_id=request.args.get("note_id", "").strip(),
            source=request.args.get("source", "").strip(),
            category=request.args.get("category", "").strip(),
            date_from=request.args.get("date_from", "").strip(),
            date_to=request.args.get("date_to", "").strip(),
            note_type=request.args.get("note_type", "").strip(),
            topic=request.args.get("topic", "").strip(),
            query=request.args.get("q", "").strip(),
        )
        fields = ["local_note_id", "platform_note_id", "source_url", "creator_name", "platform_creator_id", "category", "title", "title_raw", "body", "published_at", "note_type", "topics", "topics_json_raw", "source", "batch_id", "read_count", "like_count", "favorite_count", "interaction_comment_count", "share_count", "exposure_count", "follow_count", "comment_count", "interaction_score", "metrics"]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in data["notes"]:
            writer.writerow({
                **row,
                "topics": "、".join(row["topics"]),
                "metrics": json.dumps(row["metrics"], ensure_ascii=False, sort_keys=True),
            })
        response = make_response("\ufeff" + stream.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="creatorscope_notes.csv"'
        return response

    @app.get("/api/fields")
    def api_fields():
        with connection(app.config["DATABASE_PATH"]) as conn:
            rows = conn.execute(
                "SELECT DISTINCT record_type, fields_json FROM records"
            ).fetchall()
            period_rows = conn.execute(
                """
                SELECT metric_key, period FROM metric_values
                WHERE period <> '' ORDER BY metric_key, period
                """
            ).fetchall()
        observed = {key for row in rows for key in json.loads(row["fields_json"])}
        periods: dict[str, set[str]] = {}
        for row in period_rows:
            periods.setdefault(row["metric_key"], set()).add(row["period"])
        fields = field_dictionary(observed)
        for field in fields:
            actual_periods = sorted(periods.get(field["name"], set()))
            if actual_periods:
                field["period"] = "、".join(actual_periods)
        known = {field["name"] for field in fields}
        fields.extend(
            {
                "name": name,
                "group": "其他实测字段",
                "unit": "原始值（待配置）",
                "period": "页面未标明",
                "observed": True,
            }
            for name in sorted(observed - known)
        )
        return jsonify(fields)

    @app.get("/api/tasks")
    def api_tasks():
        with connection(app.config["DATABASE_PATH"]) as conn:
            rows = conn.execute(
                """
                SELECT task_id, task_type, source, status, progress, message,
                       created_at, started_at, finished_at, batch_id,
                       COALESCE(error_message, error) AS error_message, result_json
                FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT 20
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                task_result = json.loads(item.pop("result_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                task_result = {}
                item.pop("result_json", None)
            if item["task_type"] == "visible_collection":
                item.update(
                    {
                        "failures": task_result.get("failures", [])[:100],
                        "comment_count": int(task_result.get("comment_count", 0) or 0),
                        "dom_rows_added": int(task_result.get("dom_rows_added", 0) or 0),
                        "creators_visited": int(task_result.get("creators_visited", 0) or 0),
                        "notes_visited": int(task_result.get("notes_visited", 0) or 0),
                        "records_added": int(task_result.get("records_added", 0) or 0),
                        "duplicate_records": int(task_result.get("duplicate_records", 0) or 0),
                        "comments_added": int(task_result.get("comments_added", 0) or 0),
                        "duplicate_comments": int(task_result.get("duplicate_comments", 0) or 0),
                    }
                )
            else:
                item.pop("result_json", None)
            result.append(item)
        return jsonify(result)

    @app.get("/api/browser")
    def api_browser_status():
        if app.config["DEMO_MODE"]:
            return jsonify({"open": False, "domain": "", "page_kind": "", "mode": "demo"})
        try:
            return jsonify(browser.status())
        except Exception:
            return jsonify({"open": False, "domain": "", "page_kind": ""})

    @app.post("/api/tasks/import-baseline")
    def api_import_baseline():
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示数据为只读；请使用筛选与导出"}), 409
        task_id = tasks.submit_baseline_import()
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/tasks/import-file")
    def api_import_file():
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示数据为只读；请使用筛选与导出"}), 409
        uploaded = request.files.get("file")
        if uploaded is None:
            return jsonify({"error": "请选择 JSON 或 CSV 文件"}), 400
        try:
            content = uploaded.stream.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                return jsonify({"error": "导入文件超过 2 MB 限制"}), 413
            task_id = tasks.submit_file_import(uploaded.filename or "", content)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/tasks/collect-visible")
    def api_collect_visible():
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示模式不启动浏览器采集"}), 409
        settings = request.get_json(silent=True)
        if not isinstance(settings, dict):
            return jsonify({"error": "请以 JSON 对象提交采集设置"}), 400
        try:
            task_id = tasks.submit_visible_collection(settings)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/tasks/<task_id>/<action>")
    def api_collection_task_action(task_id: str, action: str):
        handlers = {
            "pause": tasks.pause_task,
            "resume": tasks.resume_task,
            "stop": tasks.stop_task,
            "retry-failed": tasks.retry_failed_task,
        }
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示模式不运行采集任务"}), 409
        handler = handlers.get(action)
        if not handler:
            return jsonify({"error": "不支持的任务操作"}), 404
        if not handler(task_id):
            return jsonify({"error": "任务当前状态不支持此操作"}), 409
        return jsonify({"task_id": task_id, "action": action}), 202

    @app.get("/api/batches/<batch_id>/quality")
    def api_batch_quality(batch_id: str):
        report = batch_quality_report(app.config["DATABASE_PATH"], batch_id)
        if report is None:
            return jsonify({"error": "采集批次不存在"}), 404
        return jsonify(report)

    @app.get("/quality")
    def quality_board():
        return render_template("quality.html", demo_mode=app.config["DEMO_MODE"])

    @app.get("/api/quality/overview")
    def api_quality_overview():
        try:
            limit = int(request.args.get("limit") or 20)
        except ValueError:
            return jsonify({"error": "limit 必须为整数"}), 400
        return jsonify(quality_overview(app.config["DATABASE_PATH"], limit=limit))

    def _report_bundle():
        raw_batch_id = (request.args.get("batch_id") or "").strip()
        batch_id = None if raw_batch_id in {"", "__all__"} else raw_batch_id
        try:
            return build_report(app.config["DATABASE_PATH"], batch_id=batch_id)
        except sqlite3.DatabaseError:
            return None

    @app.get("/report")
    def report_page():
        try:
            bundle = _report_bundle()
        except LookupError:
            return jsonify({"error": "采集批次不存在"}), 404
        if bundle is None:
            return render_template("report.html", report=None, demo_mode=app.config["DEMO_MODE"], error="数据库不可读取"), 503
        return render_template("report.html", report=bundle, demo_mode=app.config["DEMO_MODE"], error=None)

    @app.get("/api/report.md")
    def api_report_markdown():
        try:
            bundle = _report_bundle()
        except LookupError:
            return jsonify({"error": "采集批次不存在"}), 404
        if bundle is None:
            return jsonify({"error": "数据库不可读取"}), 503
        response = make_response(render_markdown(bundle))
        response.headers["Content-Type"] = "text/markdown; charset=utf-8"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{report_filename(bundle, "md")}"'
        )
        return response

    @app.get("/api/report.docx")
    def api_report_docx():
        try:
            bundle = _report_bundle()
        except LookupError:
            return jsonify({"error": "采集批次不存在"}), 404
        if bundle is None:
            return jsonify({"error": "数据库不可读取"}), 503
        buffer = io.BytesIO()
        try:
            with tempfile.TemporaryDirectory(prefix="creatorscope-report-") as workspace:
                target = render_docx(bundle, Path(workspace) / "report.docx")
                buffer.write(target.read_bytes())
        except ImportError:
            return jsonify({"error": "缺少 python-docx，请先运行 setup.ps1 安装依赖"}), 503
        buffer.seek(0)
        response = make_response(buffer.read())
        response.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{report_filename(bundle, "docx")}"'
        )
        return response

    @app.get("/api/export/json")
    def api_export_json():
        batch_id = request.args.get("batch_id") or None
        if batch_id == "__all__":
            batch_id = None
        with connection(app.config["DATABASE_PATH"]) as conn:
            sql = """
                SELECT r.record_id, r.batch_id, r.record_type, r.captured_at,
                       r.fields_json, r.creator_id, r.note_id,
                       c.creator_name, c.platform_creator_id, c.pseudonym,
                       n.platform_note_id, n.source_url,
                       COALESCE(NULLIF(r.source, ''), b.source) AS source
                FROM records r JOIN batches b ON b.batch_id = r.batch_id
                LEFT JOIN creators c ON c.creator_id = r.creator_id
                LEFT JOIN notes n ON n.note_id = r.note_id
            """
            params: list[Any] = []
            if batch_id:
                sql += " WHERE r.batch_id = ?"
                params.append(batch_id)
            sql += " ORDER BY b.captured_at, r.record_id"
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
            records = []
            for row in rows:
                metric_period = conn.execute(
                    "SELECT DISTINCT period FROM metric_values WHERE record_id = ? AND period <> ''",
                    (row["record_id"],),
                ).fetchall()
                fields = json.loads(row["fields_json"])
                if not isinstance(fields, dict):
                    fields = {}
                creator_name = _raw_field(
                    fields, ("creator_name", "nickname", "blogger_name", "达人名称", "达人昵称", "博主名称", "博主名", "创作者名称", "昵称", "账号名称", "作者昵称"),
                    row["creator_name"] if row["creator_name"] != row["pseudonym"] else None,
                )
                platform_creator_id = _raw_field(
                    fields, ("platform_creator_id", "creator_id", "blogger_id", "达人ID", "博主ID", "创作者ID", "用户ID", "UID", "小红书号"),
                    row["platform_creator_id"],
                )
                platform_note_id = _raw_field(
                    fields, ("platform_note_id", "note_id", "笔记ID"), row["platform_note_id"]
                )
                source_url = _raw_field(
                    fields, ("source_url", "note_url", "share_url", "share_url_base", "笔记链接"), row["source_url"]
                )
                creator_ref = _raw_field(
                    fields, ("creator_ref", "creator_key", "platform_creator_id", "creator_id"),
                    platform_creator_id or row["creator_id"],
                )
                note_ref = _raw_field(
                    fields, ("note_ref", "platform_note_id", "note_id"),
                    platform_note_id or source_url or row["note_id"],
                )
                record = {
                    "type": row["record_type"],
                    "batch_id": row["batch_id"],
                    "source": row["source"],
                    "captured_at": row["captured_at"],
                    "creator_ref": creator_ref,
                    "note_ref": note_ref,
                    "local_creator_id": row["creator_id"],
                    "local_note_id": row["note_id"],
                    "creator_name": creator_name,
                    "platform_creator_id": platform_creator_id,
                    "platform_note_id": platform_note_id,
                    "source_url": source_url,
                    "fields": fields,
                }
                periods = [period[0] for period in metric_period]
                if len(periods) == 1:
                    record["period_displayed"] = periods[0]
                records.append(record)
        payload = {
            "format": "CreatorScope JSON v1",
            "source": "CreatorScope 本地原始记录导出",
            "records": records,
        }
        response = make_response(json.dumps(payload, ensure_ascii=False, indent=2))
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="creatorscope_export.json"'
        return response

    @app.get("/api/export/csv")
    def api_export_csv():
        batch_id = request.args.get("batch_id") or None
        if batch_id == "__all__":
            batch_id = None
        with connection(app.config["DATABASE_PATH"]) as conn:
            sql = """
                SELECT r.record_id, r.batch_id, r.record_type, r.captured_at,
                       r.fields_json, r.creator_id, r.note_id,
                       c.creator_name, c.platform_creator_id, c.pseudonym,
                       n.platform_note_id, n.source_url,
                       COALESCE(NULLIF(r.source, ''), b.source) AS source
                FROM records r JOIN batches b ON b.batch_id = r.batch_id
                LEFT JOIN creators c ON c.creator_id = r.creator_id
                LEFT JOIN notes n ON n.note_id = r.note_id
            """
            params: list[Any] = []
            if batch_id:
                sql += " WHERE r.batch_id = ?"
                params.append(batch_id)
            sql += " ORDER BY r.captured_at, r.record_id"
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(
            stream,
            fieldnames=["record_id", "batch_id", "record_type", "source", "captured_at", "creator_ref", "note_ref", "local_creator_id", "local_note_id", "creator_name", "platform_creator_id", "platform_note_id", "source_url", "period_displayed", "fields_json"],
        )
        writer.writeheader()
        for row in rows:
            with connection(app.config["DATABASE_PATH"]) as conn:
                periods = conn.execute(
                    "SELECT DISTINCT period FROM metric_values WHERE record_id = ? AND period <> ''",
                    (row["record_id"],),
                ).fetchall()
            period_values = [str(period[0]) for period in periods]
            fields = json.loads(row["fields_json"])
            if not isinstance(fields, dict):
                fields = {}
            creator_name = _raw_field(
                fields, ("creator_name", "nickname", "blogger_name", "达人名称", "达人昵称", "博主名称", "博主名", "创作者名称", "昵称", "账号名称", "作者昵称"),
                row["creator_name"] if row["creator_name"] != row["pseudonym"] else None,
            )
            platform_creator_id = _raw_field(
                fields, ("platform_creator_id", "creator_id", "blogger_id", "达人ID", "博主ID", "创作者ID", "用户ID", "UID", "小红书号"),
                row["platform_creator_id"],
            )
            platform_note_id = _raw_field(fields, ("platform_note_id", "note_id", "笔记ID"), row["platform_note_id"])
            source_url = _raw_field(fields, ("source_url", "note_url", "share_url", "share_url_base", "笔记链接"), row["source_url"])
            creator_ref = _raw_field(fields, ("creator_ref", "creator_key", "platform_creator_id", "creator_id"), platform_creator_id or row["creator_id"])
            note_ref = _raw_field(fields, ("note_ref", "platform_note_id", "note_id"), platform_note_id or source_url or row["note_id"])
            writer.writerow(
                {
                    "record_id": row["record_id"],
                    "batch_id": row["batch_id"],
                    "record_type": row["record_type"],
                    "source": row["source"],
                    "captured_at": row["captured_at"],
                    "creator_ref": creator_ref,
                    "note_ref": note_ref,
                    "local_creator_id": row["creator_id"],
                    "local_note_id": row["note_id"],
                    "creator_name": creator_name,
                    "platform_creator_id": platform_creator_id,
                    "platform_note_id": platform_note_id,
                    "source_url": source_url,
                    "period_displayed": period_values[0] if len(period_values) == 1 else "、".join(period_values),
                    "fields_json": row["fields_json"],
                }
            )
        response = make_response("\ufeff" + stream.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="creatorscope_export.csv"'
        return response

    @app.get("/api/export/metrics.json")
    def api_export_metrics_json():
        batch_id = request.args.get("batch_id") or None
        if batch_id == "__all__":
            batch_id = None
        sql = """
            SELECT m.metric_id, r.record_id, r.batch_id, r.record_type,
                   COALESCE(NULLIF(r.source, ''), b.source) AS source,
                   COALESCE(NULLIF(c.platform_creator_id, ''), r.creator_id) AS creator_ref,
                   COALESCE(NULLIF(n.platform_note_id, ''), NULLIF(n.source_url, ''), r.note_id) AS note_ref,
                   r.creator_id AS local_creator_id, r.note_id AS local_note_id,
                   c.creator_name, c.platform_creator_id,
                   n.platform_note_id, n.source_url,
                   r.captured_at, m.metric_key,
                   m.raw_value, m.numeric_value AS normalized_value, m.unit,
                   m.period, m.parse_status
            FROM metric_values m
            JOIN records r ON r.record_id=m.record_id
            JOIN batches b ON b.batch_id=r.batch_id
            LEFT JOIN creators c ON c.creator_id=r.creator_id
            LEFT JOIN notes n ON n.note_id=r.note_id
        """
        params: list[Any] = []
        if batch_id:
            sql += " WHERE r.batch_id=?"
            params.append(batch_id)
        sql += " ORDER BY r.captured_at, r.record_id, m.metric_id"
        with connection(app.config["DATABASE_PATH"]) as conn:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            raw = row["raw_value"]
            row["missing_status"] = (
                "missing" if raw is None or str(raw).strip() in {"", "-", "—", "–"} else "present"
            )
        payload = {
            "format": "CreatorScope normalized metrics JSON v1",
            "generated_at": generated_at(),
            "batch_id": batch_id or "__all__",
            "metrics": rows,
        }
        response = make_response(json.dumps(payload, ensure_ascii=False, indent=2))
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="creatorscope_metrics.json"'
        return response

    @app.get("/api/export/metrics.csv")
    def api_export_metrics_csv():
        batch_id = request.args.get("batch_id") or None
        if batch_id == "__all__":
            batch_id = None
        sql = """
            SELECT m.metric_id, r.record_id, r.batch_id, r.record_type,
                   COALESCE(NULLIF(r.source, ''), b.source) AS source,
                   COALESCE(NULLIF(c.platform_creator_id, ''), r.creator_id) AS creator_ref,
                   COALESCE(NULLIF(n.platform_note_id, ''), NULLIF(n.source_url, ''), r.note_id) AS note_ref,
                   r.creator_id AS local_creator_id, r.note_id AS local_note_id,
                   c.creator_name, c.platform_creator_id,
                   n.platform_note_id, n.source_url,
                   r.captured_at, m.metric_key, m.raw_value,
                   m.numeric_value AS normalized_value, m.unit, m.period,
                   m.parse_status
            FROM metric_values m
            JOIN records r ON r.record_id=m.record_id
            JOIN batches b ON b.batch_id=r.batch_id
            LEFT JOIN creators c ON c.creator_id=r.creator_id
            LEFT JOIN notes n ON n.note_id=r.note_id
        """
        params: list[Any] = []
        if batch_id:
            sql += " WHERE r.batch_id=?"
            params.append(batch_id)
        sql += " ORDER BY r.captured_at, r.record_id, m.metric_id"
        with connection(app.config["DATABASE_PATH"]) as conn:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        fields = [
            "metric_id", "record_id", "batch_id", "record_type", "source",
            "creator_ref", "note_ref", "local_creator_id", "local_note_id", "creator_name", "platform_creator_id",
            "platform_note_id", "source_url", "captured_at", "metric_key", "raw_value",
            "normalized_value", "unit", "period", "parse_status", "missing_status",
        ]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            raw = row["raw_value"]
            row["missing_status"] = (
                "missing" if raw is None or str(raw).strip() in {"", "-", "—", "–"} else "present"
            )
            writer.writerow(row)
        response = make_response("\ufeff" + stream.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="creatorscope_metrics.csv"'
        return response

    @app.get("/api/export/analytics.json")
    def api_export_analytics_json():
        batch_id = request.args.get("batch_id") or None
        if batch_id == "__all__":
            batch_id = None
        statistics = analytics_data(app.config["DATABASE_PATH"], batch_id=batch_id)
        payload = {
            "format": "CreatorScope analytics JSON v1",
            "generated_at": generated_at(),
            "batch_id": batch_id or "__all__",
            "statistics": statistics,
        }
        response = make_response(json.dumps(payload, ensure_ascii=False, indent=2))
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="creatorscope_analytics.json"'
        return response

    @app.post("/api/browser/open")
    def api_open_browser():
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示模式不启动浏览器采集"}), 409
        task_id = tasks.submit_browser_open()
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/browser/capture")
    def api_capture_current_page():
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示模式不读取实时浏览器页面"}), 409
        payload = request.get_json(silent=True) or {}
        creator_id = str(payload.get("creator_id") or "").strip() or None
        task_id = tasks.submit_current_page_capture(creator_id)
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/browser/close")
    def api_close_browser():
        if app.config["DEMO_MODE"]:
            return jsonify({"error": "离线演示模式不控制浏览器"}), 409
        task_id = tasks.submit_browser_close()
        return jsonify({"task_id": task_id}), 202

    @app.get("/api/health")
    def api_health():
        return jsonify({"status": "ok", "mode": "demo" if app.config["DEMO_MODE"] else "local"})

    return app
