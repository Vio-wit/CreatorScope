from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .browser import CaptureError, PauseForUser, PgyVisibleBrowser, StopCollection
from .db import connection, local_timestamp
from .ingest import (
    finish_collection_batch,
    ingest_comment_snapshots,
    ingest_payload,
    load_baseline,
    payload_from_upload,
)


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
        self._lock = threading.RLock()
        self._controls: dict[str, tuple[threading.Event, threading.Event]] = {}
        self._active: set[str] = set()
        self._scheduled: set[str] = set()
        self._restore_interrupted_collections()

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

    def submit_current_page_capture(self, creator_id: str | None = None) -> str:
        return self._submit(
            "pgy_visible_capture",
            "蒲公英当前可见页面",
            lambda task_id: self._run_current_page_capture(task_id, creator_id),
        )

    def submit_file_import(self, filename: str, content: bytes) -> str:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in {".json", ".csv"}:
            raise ValueError("仅支持 .json 或 .csv 文件")
        safe_source = "JSON 文件导入" if suffix == ".json" else "CSV 文件导入"
        return self._submit(
            "file_import",
            safe_source,
            lambda task_id: self._run_file_import(task_id, filename, content, safe_source),
        )

    def submit_browser_close(self) -> str:
        return self._submit(
            "browser_close",
            "蒲公英可见浏览器",
            lambda _task_id: self.browser.close(),
        )

    def submit_visible_collection(self, settings: dict[str, Any]) -> str:
        normalized = self._validate_collection_settings(settings)
        task_id = uuid4().hex
        batch_id = uuid4().hex
        now = local_timestamp()
        parameters = {"settings": normalized, "checkpoint": {"creator_index": 0, "note_index": 0, "comment_count": 0}}
        with connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO tasks
                   (task_id, task_type, source, status, progress, message,
                    parameters_json, created_at, batch_id)
                   VALUES (?, 'visible_collection', ?, 'queued', 0, '等待执行', ?, ?, ?)""",
                (
                    task_id,
                    "蒲公英 → 小红书公开页",
                    json.dumps(parameters, ensure_ascii=False),
                    now,
                    batch_id,
                ),
            )
            conn.execute(
                """INSERT INTO batches
                   (batch_id, task_id, source, captured_at, started_at, status, filters_json)
                   VALUES (?, ?, ?, ?, ?, 'queued', ?)""",
                (
                    batch_id,
                    task_id,
                    "蒲公英与小红书公开可见页面",
                    now,
                    now,
                    json.dumps(normalized, ensure_ascii=False),
                ),
            )
        with self._lock:
            resume_event, stop_event = threading.Event(), threading.Event()
            resume_event.set()
            self._controls[task_id] = (resume_event, stop_event)
            self._scheduled.add(task_id)
        self._executor.submit(self._run, task_id, self._run_visible_collection)
        return task_id

    @staticmethod
    def _validate_collection_settings(settings: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, dict):
            raise ValueError("采集设置格式无效")
        def bounded(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(settings.get(name, default))
            except (TypeError, ValueError):
                raise ValueError(f"{name} 必须是整数") from None
            if value < minimum or value > maximum:
                raise ValueError(f"{name} 范围必须为 {minimum} 到 {maximum}")
            return value

        collect_creator = bool(settings.get("collect_creator", True))
        collect_notes = bool(settings.get("collect_notes", True))
        collect_comments = bool(settings.get("collect_comments", True))
        if not any((collect_creator, collect_notes, collect_comments)):
            raise ValueError("至少选择一种采集类型")
        return {
            "source": "pgy_xhs_visible",
            "collect_creator": collect_creator,
            "collect_notes": collect_notes or collect_comments,
            "collect_comments": collect_comments,
            "creator_count": bounded("creator_count", 10, 1, 10),
            "notes_per_creator": bounded("notes_per_creator", 3, 1, 3),
            "comments_per_note": bounded("comments_per_note", 20, 0, 20),
            "total_comment_cap": 300,
        }

    def pause_task(self, task_id: str) -> bool:
        row = self._collection_task(task_id)
        if not row or row["status"] not in {"queued", "running"}:
            return False
        resume_event, stop_event = self._control(task_id)
        resume_event.clear()
        with connection(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status='paused', message='已暂停；可恢复任务' WHERE task_id=?",
                (task_id,),
            )
            conn.execute("UPDATE batches SET status='paused' WHERE task_id=?", (task_id,))
        return True

    def resume_task(self, task_id: str) -> bool:
        row = self._collection_task(task_id)
        if not row or row["status"] not in {"paused", "stopped", "failed"}:
            return False
        resume_event, stop_event = self._control(task_id)
        stop_event.clear()
        resume_event.set()
        with self._lock:
            active = task_id in self._active
            scheduled = task_id in self._scheduled
        if active:
            with connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE tasks SET status='running', message='正在恢复任务', error=NULL, error_message=NULL WHERE task_id=?",
                    (task_id,),
                )
                conn.execute("UPDATE batches SET status='running', completed_at=NULL WHERE task_id=?", (task_id,))
        elif scheduled:
            with connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE tasks SET status='queued', message='任务恢复排队', error=NULL, error_message=NULL, finished_at=NULL WHERE task_id=?",
                    (task_id,),
                )
                conn.execute("UPDATE batches SET status='queued', completed_at=NULL WHERE task_id=?", (task_id,))
        else:
            with connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE tasks SET status='queued', message='任务恢复排队', error=NULL, error_message=NULL, finished_at=NULL WHERE task_id=?",
                    (task_id,),
                )
                conn.execute("UPDATE batches SET status='queued', completed_at=NULL WHERE task_id=?", (task_id,))
            with self._lock:
                self._scheduled.add(task_id)
            self._executor.submit(self._run, task_id, self._run_visible_collection)
        return True

    def stop_task(self, task_id: str) -> bool:
        row = self._collection_task(task_id)
        if not row or row["status"] not in {"queued", "running", "paused"}:
            return False
        resume_event, stop_event = self._control(task_id)
        stop_event.set()
        resume_event.set()
        with self._lock:
            active = task_id in self._active
            scheduled = task_id in self._scheduled
        if not active and not scheduled and row["status"] == "paused":
            with connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE tasks SET status='stopped', message='已停止；已采集记录保留，可恢复任务', finished_at=? WHERE task_id=?",
                    (local_timestamp(), task_id),
                )
            self._set_collection_batch_status(task_id, "stopped")
            return True
        with connection(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status='stopping', message='正在停止；已采集记录会保留' WHERE task_id=?",
                (task_id,),
            )
        return True

    def _collection_task(self, task_id: str):
        with connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT task_id, task_type, status, batch_id, parameters_json, result_json FROM tasks WHERE task_id=? AND task_type='visible_collection'",
                (task_id,),
            ).fetchone()
            return row

    def _control(self, task_id: str) -> tuple[threading.Event, threading.Event]:
        with self._lock:
            control = self._controls.get(task_id)
            if control is None:
                resume_event, stop_event = threading.Event(), threading.Event()
                resume_event.set()
                control = (resume_event, stop_event)
                self._controls[task_id] = control
            return control

    def _restore_interrupted_collections(self) -> None:
        with connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT task_id FROM tasks WHERE task_type='visible_collection' AND status IN ('running','stopping')"
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE tasks SET status='paused', message='应用重启后已暂停；可从断点恢复' WHERE task_id=?",
                    (row["task_id"],),
                )
                conn.execute("UPDATE batches SET status='paused' WHERE task_id=?", (row["task_id"],))

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
        with self._lock:
            self._scheduled.discard(task_id)
            self._active.add(task_id)
        with connection(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status=CASE WHEN status='paused' THEN status ELSE 'running' END, progress=MAX(progress, 1), message=CASE WHEN status='paused' THEN message ELSE '正在执行' END, started_at=COALESCE(started_at, ?) WHERE task_id=?",
                (local_timestamp(), task_id),
            )
        try:
            result = runner(task_id)
            batch_id = result.get("batch_id")
            with connection(self.db_path) as conn:
                if result.get("collection_batch"):
                    existing = conn.execute(
                        "SELECT result_json FROM tasks WHERE task_id=?", (task_id,)
                    ).fetchone()
                    try:
                        accumulated = json.loads(existing[0] or "{}") if existing else {}
                    except (json.JSONDecodeError, TypeError):
                        accumulated = {}
                    accumulated.update(result)
                    result = accumulated
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
            if result.get("collection_batch"):
                finish_collection_batch(self.db_path, str(batch_id), "completed")
        except PauseForUser as exc:
            with connection(self.db_path) as conn:
                conn.execute(
                    """UPDATE tasks SET status='paused', message=?, error_message=NULL,
                       finished_at=NULL WHERE task_id=?""",
                    (str(exc)[:240], task_id),
                )
            self._set_collection_batch_status(task_id, "paused")
        except StopCollection:
            with connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE tasks SET status='stopped', message='已停止；已采集记录保留，可恢复任务', finished_at=? WHERE task_id=?",
                    (local_timestamp(), task_id),
                )
            self._set_collection_batch_status(task_id, "stopped")
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
            self._set_collection_batch_status(task_id, "failed")
        finally:
            with self._lock:
                self._active.discard(task_id)
                self._scheduled.discard(task_id)

    def _set_collection_batch_status(self, task_id: str, status: str) -> None:
        with connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT batch_id FROM tasks WHERE task_id=? AND task_type='visible_collection'",
                (task_id,),
            ).fetchone()
        if row and row[0]:
            finish_collection_batch(self.db_path, str(row[0]), status)

    def _run_visible_collection(self, task_id: str) -> dict[str, Any]:
        row = self._collection_task(task_id)
        if not row:
            raise ValueError("采集任务不存在")
        parameters = json.loads(row["parameters_json"] or "{}")
        settings = parameters.get("settings") or {}
        checkpoint = parameters.get("checkpoint") or {"creator_index": 0, "note_index": 0, "comment_count": 0}
        batch_id = str(row["batch_id"] or "")
        if not batch_id:
            raise ValueError("采集任务批次缺失")

        def check_control() -> None:
            resume_event, stop_event = self._control(task_id)
            while True:
                if stop_event.is_set():
                    raise StopCollection()
                if resume_event.wait(timeout=0.2):
                    break
            if stop_event.is_set():
                raise StopCollection()

        def save_checkpoint(value: dict[str, Any]) -> None:
            current = self._collection_task(task_id)
            data = json.loads(current["parameters_json"] or "{}")
            data["checkpoint"] = {
                "creator_index": max(0, int(value.get("creator_index", 0))),
                "note_index": max(0, int(value.get("note_index", 0))),
                "comment_count": max(0, min(300, int(value.get("comment_count", 0)))),
            }
            with connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE tasks SET parameters_json=? WHERE task_id=?",
                    (json.dumps(data, ensure_ascii=False), task_id),
                )
            self._append_result(
                task_id,
                comment_count=data["checkpoint"]["comment_count"],
            )

        def save_records(payload: dict[str, Any]) -> None:
            ingest_payload(
                self.db_path,
                task_id,
                payload,
                source_override="蒲公英与小红书可见页面",
                batch_id_override=batch_id,
            )

        def save_comments(creator_ref: str, note_ref: str, comments: list[dict[str, Any]]) -> dict[str, int]:
            return ingest_comment_snapshots(
                self.db_path,
                task_id=task_id,
                batch_id=batch_id,
                creator_ref=creator_ref,
                note_ref=note_ref,
                comments=comments[:20],
            )

        def report_progress(progress: int, message: str, dom_delta: int) -> None:
            with connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE tasks SET progress=MAX(progress, ?), message=? WHERE task_id=?",
                    (max(1, min(95, int(progress))), str(message)[:240], task_id),
                )
            self._append_result(task_id, dom_rows_added=max(0, int(dom_delta)))

        def report_failure(creator_index: int, note_index: int, reason: str) -> None:
            self._append_failure(task_id, creator_index, note_index, reason)
            with connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE batches SET error_count=error_count+1 WHERE batch_id=?",
                    (batch_id,),
                )

        with connection(self.db_path) as conn:
            existing_comment_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM comment_snapshots WHERE batch_id=?", (batch_id,)
                ).fetchone()[0]
            )
        check_control()
        result = self.browser.collect_visible(
            settings,
            checkpoint,
            check_control=check_control,
            save_checkpoint=save_checkpoint,
            save_records=save_records,
            save_comments=save_comments,
            report_progress=report_progress,
            report_failure=report_failure,
            existing_comment_count=existing_comment_count,
            comment_scope=batch_id,
        )
        self._append_result(
            task_id,
            comment_count=int(result.get("comment_count", 0)),
            creators_visited=int(result.get("creators_visited", 0)),
            notes_visited=int(result.get("notes_visited", 0)),
        )
        result["batch_id"] = batch_id
        result["collection_batch"] = True
        return result

    def _append_result(self, task_id: str, **updates: Any) -> None:
        row = self._collection_task(task_id)
        try:
            result = json.loads(row["result_json"] or "{}") if row else {}
        except (json.JSONDecodeError, TypeError):
            result = {}
        for key, value in updates.items():
            if key == "dom_rows_added":
                result[key] = int(result.get(key, 0)) + int(value)
            else:
                result[key] = value
        with connection(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET result_json=? WHERE task_id=?",
                (json.dumps(result, ensure_ascii=False), task_id),
            )

    def _append_failure(self, task_id: str, creator_index: int, note_index: int, reason: str) -> None:
        row = self._collection_task(task_id)
        try:
            result = json.loads(row["result_json"] or "{}") if row else {}
        except (json.JSONDecodeError, TypeError):
            result = {}
        failures = result.setdefault("failures", [])
        if len(failures) < 100:
            failures.append(
                {
                    "creator_index": max(1, int(creator_index)),
                    "note_index": max(0, int(note_index)),
                    "reason": str(reason)[:180],
                }
            )
        with connection(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET result_json=? WHERE task_id=?",
                (json.dumps(result, ensure_ascii=False), task_id),
            )

    def _run_baseline_import(self, task_id: str) -> dict[str, Any]:
        payload = load_baseline(self.baseline_path)
        payload["creator_ref"] = "baseline:pgy-anonymized-2026-11-19"
        profile = next(
            (item for item in payload["records"] if item.get("type") == "creator_profile_sample"),
            None,
        )
        if profile and isinstance(profile.get("fields"), dict):
            profile_json = json.dumps(
                profile["fields"], ensure_ascii=False, sort_keys=True
            )
            with connection(self.db_path) as conn:
                existing = conn.execute(
                    "SELECT creator_id FROM records WHERE record_type = 'creator_profile_sample' AND fields_json = ? ORDER BY record_id DESC LIMIT 1",
                    (profile_json,),
                ).fetchone()
            if existing:
                payload["creator_ref"] = str(existing[0])
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

    def _run_current_page_capture(
        self,
        task_id: str,
        creator_id: str | None = None,
    ) -> dict[str, Any]:
        payload = self.browser.capture_current_page()
        result = ingest_payload(
            self.db_path,
            task_id,
            payload,
            source_override="蒲公英当前可见页面",
            creator_id_override=creator_id,
        )
        result["message"] = (
            f"当前页采集完成：{result['record_count']} 条记录，"
            f"{result['parsed_metric_count']} 个数值字段成功转换"
        )
        return result

    def _run_file_import(
        self,
        task_id: str,
        filename: str,
        content: bytes,
        source: str,
    ) -> dict[str, Any]:
        payload = payload_from_upload(filename, content)
        result = ingest_payload(
            self.db_path,
            task_id,
            payload,
            source_override=source,
        )
        result["message"] = (
            f"{source}完成：{result['record_count']} 条记录，"
            f"去重 {result['duplicate_count']} 条，"
            f"{result['parsed_metric_count']} 个数值字段成功转换"
        )
        return result

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
