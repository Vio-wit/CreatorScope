from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from .analytics import dashboard_data
from .browser import PgyVisibleBrowser
from .db import connection, initialize_database
from .tasks import TaskManager


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
            os.environ.get("CREATORSCOPE_DB", str(instance_dir / "creatorscope.sqlite3"))
        ),
        BASELINE_PATH=project_root / "data" / "baseline" / "pgy_live_sample_2026-11-19.json",
    )
    if test_config:
        app.config.update(test_config)

    app.config["DATABASE_PATH"] = Path(app.config["DATABASE_PATH"])
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

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/api/dashboard")
    def api_dashboard():
        data = dashboard_data(
            app.config["DATABASE_PATH"],
            batch_id=request.args.get("batch_id") or None,
            record_type=request.args.get("record_type") or None,
        )
        return jsonify(data)

    @app.get("/api/tasks")
    def api_tasks():
        with connection(app.config["DATABASE_PATH"]) as conn:
            rows = conn.execute(
                """
                SELECT task_id, task_type, source, status, progress, message,
                       created_at, started_at, finished_at, batch_id
                FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT 20
                """
            ).fetchall()
        return jsonify([dict(row) for row in rows])

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

    @app.post("/api/browser/open")
    def api_open_browser():
        task_id = tasks.submit_browser_open()
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/browser/capture")
    def api_capture_current_page():
        task_id = tasks.submit_current_page_capture()
        return jsonify({"task_id": task_id}), 202

    @app.post("/api/browser/close")
    def api_close_browser():
        task_id = tasks.submit_browser_close()
        return jsonify({"task_id": task_id}), 202

    @app.get("/api/health")
    def api_health():
        return jsonify({"status": "ok", "mode": "local"})

    return app
