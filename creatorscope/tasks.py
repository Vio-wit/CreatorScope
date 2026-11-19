from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .browser import CaptureError, PgyVisibleBrowser
from .db import connection, local_timestamp
from .ingest import ingest_payload, load_baseline


class TaskManager:
    def __init__(
        self,
        db_path: Path,
        baseline_path: Path,
        browser: PgyVisibleBrowser,
    ):
        self.db_path = db_path
        self.baseline_path = baseline_path
        self.browser = browser
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="creatorscope-task")

    def submit_baseline_import(self) -> str:
        return self._submit(
            "baseline_import",
            "蒲公英匿名实测样本",
            self._run_baseline_import,
        )

    def submit_browser_open(self) -> str:
        return self._submit(
            "browser_open",
            "蒲公英可见浏览器",
            lambda _task_id: self.browser.open(),
        )

    def submit_current_page_capture(self) -> str:
        return self._submit(
            "pgy_visible_capture",
            "蒲公英当前可见页面",
            self._run_current_page_capture,
        )

    def submit_browser_close(self) -> str:
        return self._submit(
            "browser_close",
            "蒲公英可见浏览器",
            lambda _task_id: self.browser.close(),
        )

    def _submit(
        self,
        task_type: str,
        source: str,
        runner: Callable[[str], dict[str, Any]],
    ) -> str:
        task_id = uuid4().hex
        with connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO tasks(task_id, task_type, source, status, progress, message, created_at)
                VALUES (?, ?, ?, 'queued', 0, '等待执行', ?)
                """,
                (task_id, task_type, source, local_timestamp()),
            )
        self._executor.submit(self._run, task_id, runner)
        return task_id

    def _run(
        self,
        task_id: str,
        runner: Callable[[str], dict[str, Any]],
    ) -> None:
        with connection(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status='running', progress=10, message='正在执行', started_at=? WHERE task_id=?",
                (local_timestamp(), task_id),
            )
        try:
            result = runner(task_id)
            batch_id = result.get("batch_id")
            with connection(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status='completed', progress=100, message=?, finished_at=?,
                        batch_id=?, result_json=?, error=NULL
                    WHERE task_id=?
                    """,
                    (
                        str(result.get("message") or "任务完成"),
                        local_timestamp(),
                        batch_id,
                        json.dumps(result, ensure_ascii=False),
                        task_id,
                    ),
                )
        except Exception as exc:
            if isinstance(exc, CaptureError):
                message = exc.public_message
            elif isinstance(exc, (ValueError, FileNotFoundError)):
                message = str(exc)
            else:
                message = f"任务失败（{type(exc).__name__}）；请检查本地环境或页面状态"
            with connection(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE tasks SET status='failed', progress=100, message=?,
                        error=?, finished_at=? WHERE task_id=?
                    """,
                    (message, message, local_timestamp(), task_id),
                )

    def _run_baseline_import(self, task_id: str) -> dict[str, Any]:
        payload = load_baseline(self.baseline_path)
        result = ingest_payload(
            self.db_path,
            task_id,
            payload,
            source_override="蒲公英匿名实测基线",
        )
        result["message"] = (
            f"导入完成：{result['record_count']} 条记录，"
            f"{result['parsed_metric_count']} 个数值字段成功转换"
        )
        return result

    def _run_current_page_capture(self, task_id: str) -> dict[str, Any]:
        payload = self.browser.capture_current_page()
        result = ingest_payload(
            self.db_path,
            task_id,
            payload,
            source_override="蒲公英当前可见页面",
        )
        result["message"] = (
            f"当前页采集完成：{result['record_count']} 条记录，"
            f"{result['parsed_metric_count']} 个数值字段成功转换"
        )
        return result

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
