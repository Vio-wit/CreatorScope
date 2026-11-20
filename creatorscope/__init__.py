from __future__ import annotations

import os
import csv
import io
import json
import math
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, make_response, render_template, request

from .analytics import analytics_data, creator_library, dashboard_data, wordcloud_png
from .browser import PgyVisibleBrowser
from .db import connection, initialize_database
from .domain import field_dictionary
from .ingest import MAX_UPLOAD_BYTES
from .tasks import TaskManager
from research.r4_3_comment_analysis.comments_web import comment_analysis_bp


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    project_root = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    instance_dir = project_root / "instance"
    instance_dir.mkdir(parents=True, exist_ok=True)
    app.config.update(
        DATABASE_PATH=Path(
            os.environ.get("CREATORSCOPE_DB", str(instance_dir / "creatorscope_unified.sqlite3"))
        ),
        BASELINE_PATH=project_root / "data" / "baseline" / "pgy_live_sample_2026-11-19.json",
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + 64 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    app.config["DATABASE_PATH"] = Path(app.config["DATABASE_PATH"])
    # All CreatorScope modules share one local database, including comment analysis.
    app.config["COMMENT_ANALYSIS_DATABASE_PATH"] = app.config["DATABASE_PATH"]
    app.config["BASELINE_PATH"] = Path(app.config["BASELINE_PATH"])
    initialize_database(app.config["DATABASE_PATH"])

    local_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    browser = PgyVisibleBrowser(local_data / "CreatorScope" / "pgy_browser_profile")
    tasks = TaskManager(
        app.config["DATABASE_PATH"],
        app.config["BASELINE_PATH"],
        browser,
    )
    app.extensions["creatorscope_browser"] = browser
    app.extensions["creatorscope_tasks"] = tasks
    app.register_blueprint(comment_analysis_bp)

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.get("/api/dashboard")
    def api_dashboard():
        data = dashboard_data(
            app.config["DATABASE_PATH"],
            batch_id=request.args.get("batch_id") or None,
            record_type=request.args.get("record_type") or None,
        )
        return jsonify(data)

    @app.get("/api/analytics")
    def api_analytics():
        return jsonify(analytics_data(
            app.config["DATABASE_PATH"],
            batch_id=request.args.get("batch_id") or None,
        ))

    @app.get("/api/wordcloud.png")
    def api_wordcloud():
        try:
            image = wordcloud_png(
                app.config["DATABASE_PATH"],
                batch_id=request.args.get("batch_id") or None,
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
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(data)

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
                    }
                )
            else:
                item.pop("result_json", None)
            result.append(item)
        return jsonify(result)

    @app.get("/api/browser")
    def api_browser_status():
        try:
            return jsonify(browser.status())
        except Exception:
            return jsonify({"open": False, "domain": "", "page_kind": ""})

    @app.post("/api/tasks/import-baseline")
    def api_import_baseline():
        task_id = tasks.submit_baseline_import()
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/tasks/import-file")
    def api_import_file():
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
        }
        handler = handlers.get(action)
        if not handler:
            return jsonify({"error": "不支持的任务操作"}), 404
        if not handler(task_id):
            return jsonify({"error": "任务当前状态不支持此操作"}), 409
        return jsonify({"task_id": task_id, "action": action}), 202

    @app.get("/api/export/json")
    def api_export_json():
        batch_id = request.args.get("batch_id") or None
        with connection(app.config["DATABASE_PATH"]) as conn:
            sql = """
                SELECT r.record_id, r.batch_id, r.record_type, r.captured_at,
                       r.fields_json, r.creator_id, r.note_id, b.source
                FROM records r JOIN batches b ON b.batch_id = r.batch_id
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
                record = {
                    "type": row["record_type"],
                    "captured_at": row["captured_at"],
                    "creator_ref": row["creator_id"],
                    "note_ref": row["note_id"],
                    "fields": json.loads(row["fields_json"]),
                }
                periods = [period[0] for period in metric_period]
                if len(periods) == 1:
                    record["period_displayed"] = periods[0]
                records.append(record)
        payload = {
            "format": "CreatorScope JSON v1",
            "source": "CreatorScope 本地匿名导出",
            "records": records,
        }
        response = make_response(json.dumps(payload, ensure_ascii=False, indent=2))
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="creatorscope_export.json"'
        return response

    @app.get("/api/export/csv")
    def api_export_csv():
        batch_id = request.args.get("batch_id") or None
        with connection(app.config["DATABASE_PATH"]) as conn:
            sql = """
                SELECT r.record_id, r.batch_id, r.record_type, r.captured_at,
                       r.fields_json, r.creator_id, r.note_id
                FROM records r
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
            fieldnames=["record_id", "batch_id", "record_type", "captured_at", "creator_ref", "note_ref", "period_displayed", "fields_json"],
        )
        writer.writeheader()
        for row in rows:
            with connection(app.config["DATABASE_PATH"]) as conn:
                periods = conn.execute(
                    "SELECT DISTINCT period FROM metric_values WHERE record_id = ? AND period <> ''",
                    (row["record_id"],),
                ).fetchall()
            period_values = [str(period[0]) for period in periods]
            writer.writerow(
                {
                    "record_id": row["record_id"],
                    "batch_id": row["batch_id"],
                    "record_type": row["record_type"],
                    "captured_at": row["captured_at"],
                    "creator_ref": row["creator_id"],
                    "note_ref": row["note_id"],
                    "period_displayed": period_values[0] if len(period_values) == 1 else "、".join(period_values),
                    "fields_json": row["fields_json"],
                }
            )
        response = make_response("\ufeff" + stream.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = 'attachment; filename="creatorscope_export.csv"'
        return response

    @app.post("/api/browser/open")
    def api_open_browser():
        task_id = tasks.submit_browser_open()
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/browser/capture")
    def api_capture_current_page():
        payload = request.get_json(silent=True) or {}
        creator_id = str(payload.get("creator_id") or "").strip() or None
        task_id = tasks.submit_current_page_capture(creator_id)
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/browser/close")
    def api_close_browser():
        task_id = tasks.submit_browser_close()
        return jsonify({"task_id": task_id}), 202

    @app.get("/api/health")
    def api_health():
        return jsonify({"status": "ok", "mode": "local"})

    return app
