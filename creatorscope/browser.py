from __future__ import annotations

import hashlib
import subprocess
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .db import local_timestamp


PGY_HOST = "pgy.xiaohongshu.com"
PGY_START_URL = "https://pgy.xiaohongshu.com/solar/pre-trade/note/kol"
NOTE_PANEL_FIELDS = (
    "笔记标题", "发布时间", "曝光量", "阅读量", "点赞量", "收藏量",
    "评论量", "分享量", "新增关注", "视频时长",
)
PUBLIC_NOTE_FIELDS = (
    "笔记标题", "笔记正文", "发布时间", "话题", "点赞量", "收藏量", "评论量", "分享量",
)
COMMENT_FIELDS = (
    "comment_text", "displayed_time", "like_count_raw", "reply_count_raw",
    "comment_level", "is_pinned",
)
CREATOR_LIST_FIELDS = (
    "创作者名称", "平台公开ID", "粉丝数", "阅读中位数（日常）",
    "互动中位数（日常）", "全部报价", "合作报价", "图文报价", "视频报价",
    "内容领域", "地域", "人设", "标签",
)


class CaptureError(Exception):
    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


class PauseForUser(Exception):
    """A visible page needs a normal user action before collection can continue."""


class StopCollection(Exception):
    pass


class PgyVisibleBrowser:
    """A small headed Playwright session controlled only through the visible UI."""

    def __init__(self, profile_dir: Path):
        self.profile_dir = profile_dir
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pgy-browser")
        self._playwright = None
        self._context = None
        self._page = None
        self._operation_busy = threading.Event()
        self._last_status: dict[str, Any] = {
            "open": False,
            "domain": "",
            "page_kind": "",
            "tab_count": 0,
            "tabs": [],
        }

    def _call(self, callback, timeout: float | None = 75):
        self._operation_busy.set()
        try:
            return self._executor.submit(callback).result(timeout=timeout)
        finally:
            self._operation_busy.clear()

    def open(self) -> dict[str, Any]:
        return self._call(self._open)

    def _open(self) -> dict[str, Any]:
        if self._context is not None:
            state = self._safe_state()
            if state.get("open"):
                self._last_status = state
                return state
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
            self._page = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=False,
                no_viewport=True,
                args=["--start-maximized"],
            )
        except Exception:
            playwright.stop()
            raise CaptureError(
                "无法启动独立可见浏览器；请确认该配置目录没有被其他浏览器进程占用"
            ) from None
        self._playwright = playwright
        self._context = context
        context.on("page", self._on_page_created)
        pages = list(context.pages)
        for item in pages:
            self._watch_page(item)
        pgy_pages = [item for item in pages if urlparse(item.url).hostname == PGY_HOST]
        self._page = pgy_pages[-1] if pgy_pages else (pages[0] if pages else context.new_page())
        try:
            if urlparse(self._page.url).hostname != PGY_HOST:
                self._page.goto(PGY_START_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            # The headed window may remain useful for a normal manual sign-in.
            pass
        self._remember_status()
        return {
            **self._last_status,
            "message": "请在新窗口中正常登录并手动打开待采集页面",
        }

    def status(self) -> dict[str, Any]:
        if self._operation_busy.is_set():
            return {**self._last_status, "busy": True}
        state = self._call(self._safe_state)
        self._last_status = state
        return state

    def _on_page_created(self, page) -> None:
        self._page = page
        self._watch_page(page)
        self._remember_status()

    def _watch_page(self, page) -> None:
        page.on("close", lambda: self._remember_status())
        page.on("framenavigated", lambda _frame: self._remember_status())

    def _remember_status(self) -> None:
        try:
            self._last_status = self._safe_state()
        except Exception:
            pass

    def _safe_state(self) -> dict[str, Any]:
        try:
            pages = [page for page in (self._context.pages if self._context else []) if not page.is_closed()]
            if self._page is None or self._page.is_closed():
                pgy_pages = [page for page in pages if urlparse(page.url).hostname == PGY_HOST]
                self._page = pgy_pages[-1] if pgy_pages else (pages[-1] if pages else None)
            if self._page is None:
                return {"open": False, "domain": "", "page_kind": "", "tab_count": 0, "tabs": []}
            parsed = urlparse(self._page.url)
            tabs = []
            for page in pages:
                tab_url = urlparse(page.url)
                tabs.append({
                    "domain": tab_url.hostname or "",
                    "page_kind": self._route_kind(page),
                })
            return {
                "open": True,
                "domain": parsed.hostname or "",
                "page_kind": self._route_kind(self._page),
                "tab_count": len(pages),
                "tabs": tabs,
            }
        except Exception:
            return {"open": False, "domain": "", "page_kind": "", "tab_count": 0, "tabs": []}

    def _route_kind(self, page=None) -> str:
        page = page or self._page
        if page is None:
            return ""
        parsed = urlparse(page.url)
        if parsed.hostname != PGY_HOST:
            return "other"
        path = parsed.path
        if path.endswith("/solar/pre-trade/note/kol"):
            return "博主广场"
        if "/solar/pre-trade/blogger-detail/" in path:
            return "博主详情"
        if "/microapp/creativity/inspire" in path:
            return "内容广场"
        return "蒲公英页面"

    def capture_current_page(self) -> dict[str, Any]:
        return self._call(self._capture_current_page)

    def _capture_current_page(self) -> dict[str, Any]:
        if self._page is None or self._page.is_closed():
            raise CaptureError("请先打开独立蒲公英浏览器")
        parsed = urlparse(self._page.url)
        if parsed.hostname != PGY_HOST:
            raise CaptureError("当前页面不是蒲公英域名；未读取或保存页面内容")
        try:
            body = self._page.locator("body").inner_text(timeout=15000)
        except Exception:
            raise CaptureError("读取当前页面失败；请确认页面已加载后重试") from None

        lines = [line.strip() for line in body.splitlines() if line.strip()]
        records: list[dict[str, Any]] = []
        if parsed.path.endswith("/solar/pre-trade/note/kol"):
            records.extend(self._capture_list_rows(self._page))
        else:
            profile = self._profile_record(lines)
            if profile:
                records.append(profile)
            performance = self._performance_record(lines)
            if performance:
                records.append(performance)
            fans = self._fans_record(lines)
            if fans:
                records.append(fans)
            note = self._note_detail_record(lines)
            if note:
                records.append(note)

        if not records:
            raise CaptureError(
                "当前页没有识别到支持的可见字段；请打开博主列表、博主详情或笔记详情面板"
            )
        return {
            "source": "蒲公英当前可见页面",
            "captured_at_local": __import__("datetime").datetime.now()
            .astimezone()
            .isoformat(timespec="seconds"),
            "records": records,
        }

    def _capture_list_rows(self, page) -> list[dict[str, Any]]:
        """Read visible creator rows by their rendered column headers."""
        try:
            headers = page.get_by_role("columnheader").all_inner_texts()
            if not headers:
                headers = page.locator("thead th").all_inner_texts()
            headers = [str(header or "").strip() for header in headers]
            if not headers:
                raise CaptureError("未找到创作者列表表头；为避免列错位，已停止读取")
            rows = page.get_by_role("row").all()
            parsed: list[dict[str, Any]] = []
            for visible_row_index, row in enumerate(rows):
                cells = row.get_by_role("cell").all_inner_texts()
                if not cells:
                    cells = row.get_by_role("gridcell").all_inner_texts()
                if not cells:
                    cells = row.locator("td").all_inner_texts()
                if not cells:
                    continue
                metadata = row.evaluate(
                    """el => ({
                      links: Array.from(el.querySelectorAll('a[href]')).map(a => ({
                        text: (a.innerText || a.textContent || '').trim(), href: a.href
                      })),
                      rowKey: el.getAttribute('data-row-key') || el.getAttribute('data-key') || ''
                    })"""
                )
                record = self._parse_creator_list_row(headers, cells, metadata)
                if record:
                    record["visible_row_index"] = visible_row_index
                    parsed.append(record)
            return parsed
        except CaptureError:
            raise
        except Exception:
            raise CaptureError("读取创作者列表失败；请确认列表已加载后重试") from None

    @staticmethod
    def _creator_list_field_for_header(header: str) -> str | None:
        normalized = re.sub(r"[\s\u3000:：()（）·/_-]+", "", str(header)).casefold()
        if not normalized:
            return None
        if normalized in {"博主信息", "达人信息", "创作者信息", "博主名称", "达人名称", "创作者名称"}:
            return "creator_display"
        if any(token in normalized for token in ("小红书号", "平台公开id", "公开平台id", "用户id", "达人id", "博主id", "创作者id", "账号id", "uid")):
            return "平台公开ID"
        if "粉丝" in normalized and any(token in normalized for token in ("数", "量", "人数")):
            return "粉丝数"
        if "阅读" in normalized and "中位数" in normalized:
            return "阅读中位数（日常）" if "日常" in normalized else "阅读中位数"
        if "互动" in normalized and "中位数" in normalized:
            return "互动中位数（日常）" if "日常" in normalized else "互动中位数"
        if "图文" in normalized and any(token in normalized for token in ("报价", "一口价")):
            return "图文报价"
        if "视频" in normalized and any(token in normalized for token in ("报价", "一口价")):
            return "视频报价"
        if "全部报价" in normalized:
            return "全部报价"
        if "报价" in normalized:
            return "合作报价"
        if any(token in normalized for token in ("内容领域", "内容类目", "博主类目", "领域")):
            return "内容领域"
        if any(token in normalized for token in ("地域", "地区", "所在地")):
            return "地域"
        if "人设" in normalized:
            return "人设"
        if "标签" in normalized:
            return "标签"
        return None

    @staticmethod
    def _parse_creator_list_row(
        headers: list[str], cells: list[str], metadata: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Map a rendered creator row without depending on table column order."""
        fields: dict[str, Any] = {}
        metadata = metadata if isinstance(metadata, dict) else {}
        for header, raw_value in zip(headers, cells):
            value = re.sub(r"\s+", " ", str(raw_value or "")).strip()
            field_name = PgyVisibleBrowser._creator_list_field_for_header(header)
            if not value or not field_name:
                continue
            if field_name == "creator_display":
                display_lines = [line.strip() for line in str(raw_value).splitlines() if line.strip()]
                if display_lines:
                    fields.setdefault("创作者名称", display_lines[0])
                    fields["博主信息原始显示"] = str(raw_value).strip()
                    for line in display_lines:
                        id_match = re.match(
                            r"^(?:小红书号|公开ID|平台公开ID|公开平台ID|平台ID|用户ID|UID)\s*[:：]\s*(\S+)$",
                            line,
                            flags=re.IGNORECASE,
                        )
                        if id_match:
                            fields.setdefault("平台公开ID", id_match.group(1))
            else:
                fields[field_name] = value
        if not fields:
            return None

        detail_href = ""
        for link in metadata.get("links", []):
            if not isinstance(link, dict):
                continue
            parsed_link = urlparse(str(link.get("href") or ""))
            if parsed_link.hostname == PGY_HOST and "/solar/pre-trade/blogger-detail/" in parsed_link.path:
                detail_href = parsed_link.path
                break
        creator_ref = f"pgy:{detail_href}" if detail_href else ""
        if not creator_ref and fields.get("平台公开ID"):
            creator_ref = f"pgy:id:{fields['平台公开ID']}"
        if not creator_ref and metadata.get("rowKey"):
            creator_ref = f"pgy:row:{metadata['rowKey']}"
        if not creator_ref and fields.get("创作者名称"):
            creator_ref = f"pgy:name:{fields['创作者名称']}"

        field_statuses = {
            name: "captured" if fields.get(name) else "not_displayed"
            for name in CREATOR_LIST_FIELDS
        }
        record = {
            "type": "blogger_list_sample",
            "source": "蒲公英创作者列表",
            "fields": fields,
            "field_statuses": field_statuses,
        }
        if creator_ref:
            record["creator_ref"] = creator_ref
        if detail_href:
            record["creator_detail_path"] = detail_href
        return record

    def _capture_list_row(self) -> dict[str, Any] | None:
        """Compatibility helper for callers expecting one visible list row."""
        rows = self._capture_list_rows(self._page)
        return rows[0] if rows else None

    @staticmethod
    def _value_after(lines: list[str], label: str, start: int = 0) -> str | None:
        labels = {
            "粉丝数",
            "获赞与收藏",
            "图文笔记一口价",
            "视频笔记一口价",
            "发布笔记",
            "内容类目",
            "曝光中位数",
            "阅读中位数",
            "互动中位数",
            "中位点赞量",
            "中位收藏量",
            "中位评论量",
            "中位分享量",
            "中位关注量",
            "互动率",
            "视频完播率",
            "图文3秒阅读率",
            "粉丝量变化幅度",
            "活跃粉丝占比",
            "阅读粉丝占比",
            "互动粉丝占比",
            "下单粉丝占比",
            "发布时间",
            "曝光量",
            "阅读量",
            "点赞量",
            "收藏量",
            "评论量",
            "分享量",
            "关注量",
            "新增关注",
            "视频时长",
            "时长",
        }
        for index in range(start, len(lines)):
            line = lines[index]
            if line == label:
                for candidate in lines[index + 1 :]:
                    if candidate in labels:
                        return None
                    if candidate:
                        return candidate
            if line.startswith(label + " ") or line.startswith(label + "：") or line.startswith(label + ":"):
                value = re.sub(r"^" + re.escape(label) + r"\s*[:：]?\s*", "", line).strip()
                if value:
                    return value
        return None

    @staticmethod
    def _inline_count(text: str, pattern: str) -> str | None:
        match = re.search(pattern, text)
        return match.group(1) if match else None

    def _profile_record(self, lines: list[str]) -> dict[str, Any] | None:
        fields: dict[str, Any] = {}
        for label in ("粉丝数", "获赞与收藏", "图文笔记一口价", "视频笔记一口价"):
            value = self._value_after(lines, label)
            if value:
                fields[label] = value
        published = self._inline_count("\n".join(lines), r"发布笔记\s*(\d+)\s*篇")
        if published:
            fields["发布笔记数"] = int(published)
        category = self._value_after(lines, "内容类目")
        if category:
            fields["内容类目"] = category
        if fields:
            expected = ("粉丝数", "获赞与收藏", "图文笔记一口价", "视频笔记一口价", "发布笔记数", "内容类目")
            field_statuses = {name: "captured" if name in fields else "not_displayed" for name in expected}
            field_statuses.update({"地域": "not_attempted", "人设及标签": "not_attempted"})
            return {"type": "creator_profile_sample", "fields": fields, "field_statuses": field_statuses}
        return None

    def _performance_record(self, lines: list[str]) -> dict[str, Any] | None:
        labels = (
            "曝光中位数",
            "阅读中位数",
            "互动中位数",
            "中位点赞量",
            "中位收藏量",
            "中位评论量",
            "中位分享量",
            "中位关注量",
            "互动率",
            "视频完播率",
            "图文3秒阅读率",
        )
        fields = {}
        for label in labels:
            value = self._value_after(lines, label)
            if value is not None:
                fields[label] = value
        if not fields:
            return None
        field_statuses = {name: "captured" if name in fields else "not_displayed" for name in labels}
        period = "近30日" if "近30日" in lines else ""
        return {
            "type": "creator_content_performance",
            "period_displayed": period,
            "fields": fields,
            "field_statuses": field_statuses,
        }

    def _fans_record(self, lines: list[str]) -> dict[str, Any] | None:
        labels = (
            "粉丝量变化幅度",
            "活跃粉丝占比",
            "阅读粉丝占比",
            "互动粉丝占比",
            "下单粉丝占比",
        )
        fields = {}
        for label in labels:
            value = self._value_after(lines, label)
            if value is not None:
                fields[label] = value
        if not fields:
            return None
        field_statuses = {name: "captured" if name in fields else "not_displayed" for name in labels}
        field_statuses.update(
            {
                "粉丝性别分布": "not_attempted",
                "粉丝年龄分布": "not_attempted",
                "粉丝地域分布": "not_attempted",
                "粉丝兴趣分布": "not_attempted",
                "粉丝设备分布": "not_attempted",
            }
        )
        period = "近30日" if "近30日" in lines else ""
        return {
            "type": "fan_analysis",
            "period_selector_displayed": period,
            "fields": fields,
            "field_statuses": field_statuses,
        }

    def _note_detail_record(self, lines: list[str]) -> dict[str, Any] | None:
        try:
            start = lines.index("笔记详情")
        except ValueError:
            return None
        labels = (
            "发布时间",
            "曝光量",
            "阅读量",
            "点赞量",
            "收藏量",
            "评论量",
            "分享量",
            "关注量",
            "新增关注",
            "视频时长",
            "时长",
        )
        fields: dict[str, Any] = {}
        for label in labels:
            fields[label] = self._value_after(lines, label, start=start)
        if not any(value is not None for value in fields.values()):
            return None
        field_statuses = {name: "captured" if value is not None else "not_displayed" for name, value in fields.items()}
        return {"type": "note_detail", "fields": fields, "field_statuses": field_statuses}

    def collect_visible(
        self,
        settings: dict[str, Any],
        checkpoint: dict[str, Any],
        *,
        check_control: Callable[[], None],
        save_checkpoint: Callable[[dict[str, Any]], None],
        save_records: Callable[[dict[str, Any]], None],
        save_comments: Callable[[str, str, list[dict[str, Any]]], dict[str, int]],
        report_progress: Callable[[int, str, int], None],
        report_failure: Callable[[int, int, str], None],
        report_note_success: Callable[[int, int], None],
        report_field_status: Callable[[str, str, str, tuple[str, ...], str, str], None],
        existing_comment_count: int,
        comment_scope: str = "",
    ) -> dict[str, Any]:
        """Collect a small, resumable sample only through visible page controls."""
        return self._call(
            lambda: self._collect_visible(
                settings,
                checkpoint,
                check_control=check_control,
                save_checkpoint=save_checkpoint,
                save_records=save_records,
                save_comments=save_comments,
                report_progress=report_progress,
                report_failure=report_failure,
                report_note_success=report_note_success,
                report_field_status=report_field_status,
                existing_comment_count=existing_comment_count,
                comment_scope=comment_scope,
            ),
            timeout=None,
        )

    def _collect_visible(
        self,
        settings: dict[str, Any],
        checkpoint: dict[str, Any],
        *,
        check_control: Callable[[], None],
        save_checkpoint: Callable[[dict[str, Any]], None],
        save_records: Callable[[dict[str, Any]], None],
        save_comments: Callable[[str, str, list[dict[str, Any]]], dict[str, int]],
        report_progress: Callable[[int, str, int], None],
        report_failure: Callable[[int, int, str], None],
        report_note_success: Callable[[int, int], None],
        report_field_status: Callable[[str, str, str, tuple[str, ...], str, str], None],
        existing_comment_count: int,
        comment_scope: str = "",
    ) -> dict[str, Any]:
        if self._context is None or self._page is None or self._page.is_closed():
            self._open()
        pgy_pages = [
            item
            for item in (self._context.pages if self._context else [])
            if not item.is_closed() and urlparse(item.url).hostname == PGY_HOST
        ]
        if pgy_pages and (
            self._page is None
            or self._page.is_closed()
            or urlparse(self._page.url).hostname != PGY_HOST
        ):
            self._page = pgy_pages[-1]
        page = self._page
        if page is None:
            raise CaptureError("无法打开蒲公英可见浏览器")
        if urlparse(page.url).hostname != PGY_HOST:
            raise PauseForUser("请在蒲公英窗口中正常登录，并停留在博主广场后恢复任务")
        if not urlparse(page.url).path.endswith("/solar/pre-trade/note/kol"):
            try:
                page.goto(PGY_START_URL, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
        self._check_page_blocker(page, pgy=True)
        row_locator = page.get_by_role("row")
        total_rows = row_locator.count()
        for _ in range(30):
            if total_rows > 1:
                break
            check_control()
            self._check_page_blocker(page, pgy=True)
            page.wait_for_timeout(500)
            total_rows = row_locator.count()
        creator_limit = max(1, min(10, int(settings.get("creator_count", 1))))
        creator_list_records = self._capture_list_rows(page)
        if not creator_list_records:
            raise CaptureError("当前列表没有可按表头识别的创作者记录；未启动批量采集")
        creator_limit = min(creator_limit, len(creator_list_records))
        creator_start = max(0, min(creator_limit, int(checkpoint.get("creator_index", 0))))
        note_start = max(0, int(checkpoint.get("note_index", 0)))
        comment_total = existing_comment_count
        dom_rows_added = 0
        creators_visited = 0
        notes_visited = 0
        failures = 0
        if total_rows <= 1 or creator_limit <= 0:
            self._check_page_blocker(page, pgy=True, force=True)
            raise PauseForUser(self._empty_list_message(page))

        target_end = creator_limit
        retry_targets = {
            (int(item.get("creator_index", 0)), int(item.get("note_index", 0)))
            for item in settings.get("retry_notes", [])
            if isinstance(item, dict)
        }
        for creator_index in range(creator_start, target_end - 1):
            if retry_targets and not any(target_creator == creator_index + 1 for target_creator, _ in retry_targets):
                continue
            check_control()
            report_progress(
                min(90, 10 + int(75 * (creator_index / max(1, creator_limit)))),
                f"处理第 {creator_index + 1} 位创作者",
                0,
            )
            detail_page = None
            opened_new_page = False
            creator_stage = "打开创作者详情"
            try:
                list_record = creator_list_records[creator_index]
                list_row_index = int(list_record.get("visible_row_index", creator_index + 1))
                detail_page, opened_new_page = self._open_creator_row(
                    page, list_row_index, expected_record=list_record
                )
                creator_stage = "确认创作者详情页"
                self._check_page_blocker(detail_page, pgy=True)
                route = urlparse(detail_page.url)
                if route.hostname != PGY_HOST or "/solar/pre-trade/blogger-detail/" not in route.path:
                    raise CaptureError("列表行未打开可识别的蒲公英创作者详情页")
                creator_ref = f"pgy:{route.path}"
                list_record["creator_ref"] = creator_ref
                list_record.pop("visible_row_index", None)
                save_records(
                    {
                        "source": "蒲公英创作者列表",
                        "records": [list_record],
                    }
                )
                creator_stage = "读取创作者资料和指标"
                if settings.get("collect_creator", True):
                    creator_records = self._capture_creator_records(detail_page, creator_ref)
                    if creator_records:
                        save_records(
                            {
                                "source": "蒲公英可见页面",
                                "records": creator_records,
                            }
                        )
                creators_visited += 1
                save_checkpoint(
                    {"creator_index": creator_index, "note_index": note_start if creator_index == creator_start else 0,
                     "comment_count": comment_total}
                )

                if settings.get("collect_notes", True) or settings.get("collect_comments", False):
                    creator_stage = "打开笔记数据页签"
                    self._open_notes_tab(detail_page)
                    card_count = detail_page.locator(".note-card-wrapper:visible").count()
                    note_limit = max(1, min(3, int(settings.get("notes_per_creator", 1))))
                    note_end = min(card_count, note_limit)
                    first_note = note_start if creator_index == creator_start else 0
                    for note_index in range(first_note, note_end):
                        if retry_targets and (creator_index + 1, note_index + 1) not in retry_targets:
                            continue
                        check_control()
                        if settings.get("collect_comments", True) and comment_total >= 300:
                            break
                        note_page = None
                        note_ref = None
                        note_stage = "打开笔记详情并复制链接"
                        try:
                            detail_record, note_ref = self._open_and_copy_note(
                                detail_page, note_index, creator_ref
                            )
                            if settings.get("collect_notes", True) and detail_record:
                                save_records(
                                    {"source": "蒲公英可见笔记详情", "records": [detail_record]}
                                )
                            note_stage = "打开小红书公开笔记页"
                            note_page = self._open_copied_note(note_ref["copied_url"])
                            if settings.get("collect_notes", True):
                                note_stage = "读取小红书公开笔记正文和话题"
                                public_record = self._capture_public_note(
                                    note_page,
                                    creator_ref,
                                    note_ref["canonical"],
                                    expected_title=note_ref.get("expected_title"),
                                )
                                if public_record:
                                    save_records(
                                        {"source": "小红书公开笔记页", "records": [public_record]}
                                    )
                            notes_visited += 1
                            if settings.get("collect_comments", True):
                                note_stage = "滚动并提取可见评论"
                                cap = max(0, min(20, int(settings.get("comments_per_note", 20))))
                                cap = min(cap, 300 - comment_total)
                                comments, added = self._capture_visible_comments(
                                    note_page,
                                    note_ref["canonical"],
                                    cap,
                                    check_control,
                                    comment_scope,
                                    lambda delta: report_progress(
                                        min(95, 30 + notes_visited * 4),
                                        f"采集评论：创作者 {creator_index + 1} / 笔记 {note_index + 1}",
                                        delta,
                                    ),
                                )
                                dom_rows_added += added
                                if comments:
                                    outcome = save_comments(
                                        creator_ref,
                                        note_ref["canonical"],
                                        comments,
                                    )
                                    comment_total += int(outcome.get("inserted", 0))
                                report_progress(
                                    min(95, 30 + notes_visited * 4),
                                    f"已读取 {len(comments)} 条可见评论；DOM 新增 {added} 行",
                                    0,
                                )
                            self._close_owned_page(note_page)
                            self._close_visible_note_panel(detail_page)
                            note_start = note_index + 1
                            save_checkpoint(
                                {
                                    "creator_index": creator_index,
                                    "note_index": note_start,
                                    "comment_count": comment_total,
                                }
                            )
                            report_note_success(creator_index + 1, note_index + 1)
                        except PauseForUser as exc:
                            reason = str(exc)
                            if any(token in reason for token in ("登录", "验证码", "安全验证", "权限", "访问受限")):
                                status = "access_denied"
                                attempt_ref = str((note_ref or {}).get("canonical") or f"{creator_ref}#note-index-{note_index + 1}")
                                report_field_status(
                                    "note", attempt_ref, "xhs_public_note", PUBLIC_NOTE_FIELDS,
                                    status, "小红书公开笔记页",
                                )
                                if settings.get("collect_comments", True):
                                    report_field_status(
                                        "comment", attempt_ref, "comment_capture", COMMENT_FIELDS,
                                        status, "小红书公开评论区",
                                    )
                            raise
                        except StopCollection:
                            raise
                        except Exception as exc:
                            self._close_owned_page(note_page)
                            failures += 1
                            report_failure(
                                creator_index + 1,
                                note_index + 1,
                                f"{note_stage}：{self._safe_failure_reason(exc)}",
                            )
                            status = "parse_failed" if isinstance(exc, CaptureError) else "not_loaded"
                            attempt_ref = str((note_ref or {}).get("canonical") or f"{creator_ref}#note-index-{note_index + 1}")
                            if "评论" in note_stage:
                                report_field_status(
                                    "comment", attempt_ref, "comment_capture", COMMENT_FIELDS,
                                    status, "小红书公开评论区",
                                )
                            elif "小红书" in note_stage:
                                report_field_status(
                                    "note", attempt_ref, "xhs_public_note", PUBLIC_NOTE_FIELDS,
                                    status, "小红书公开笔记页",
                                )
                            else:
                                report_field_status(
                                    "note", attempt_ref, "note_detail", NOTE_PANEL_FIELDS,
                                    status, "蒲公英笔记详情",
                                )
                            try:
                                self._close_visible_note_panel(detail_page)
                            except (PauseForUser, StopCollection):
                                raise
                            except Exception:
                                raise PauseForUser(
                                    "笔记失败后无法恢复创作者详情页；请检查可见窗口后恢复任务"
                                ) from None
                            note_start = note_index + 1
                            save_checkpoint(
                                {
                                    "creator_index": creator_index,
                                    "note_index": note_start,
                                    "comment_count": comment_total,
                                }
                            )
                            report_progress(
                                min(95, 30 + (notes_visited + failures) * 4),
                                f"笔记 {note_index + 1} 失败，继续处理后续笔记",
                                0,
                            )
                if opened_new_page:
                    self._close_owned_page(detail_page)
                else:
                    try:
                        page.go_back(wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        raise PauseForUser("无法返回蒲公英博主列表；请检查可见窗口后恢复任务")
                note_start = 0
                save_checkpoint(
                    {"creator_index": creator_index + 1, "note_index": 0,
                     "comment_count": comment_total}
                )
            except (PauseForUser, StopCollection):
                raise
            except Exception as exc:
                failures += 1
                report_failure(
                    creator_index + 1,
                    0,
                    f"{creator_stage}：{self._safe_failure_reason(exc)}",
                )
                try:
                    if opened_new_page and detail_page is not None:
                        self._close_owned_page(detail_page)
                    elif page.is_closed() or not urlparse(page.url).path.endswith("/solar/pre-trade/note/kol"):
                        page.goto(PGY_START_URL, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    raise PauseForUser("无法恢复蒲公英博主列表；请检查可见窗口后恢复任务")
                raise PauseForUser(
                    f"第 {creator_index + 1} 位创作者采集失败；已保留断点，可查看失败记录后恢复重试"
                )

        return {
            "creators_visited": creators_visited,
            "notes_visited": notes_visited,
            "comment_count": comment_total,
            "dom_rows_added": dom_rows_added,
            "failure_count": failures,
            "message": (
                f"可见采集完成：创作者 {creators_visited} 位、笔记 {notes_visited} 篇、"
                f"评论 {comment_total} 条；失败点 {failures} 处"
            ),
        }

    def _open_creator_row(self, page, row_index: int, *, expected_record: dict[str, Any] | None = None):
        before_pages = set(self._context.pages)
        row = page.get_by_role("row").nth(row_index)
        if expected_record:
            expected_fields = expected_record.get("fields", {})
            expected_id = str(expected_fields.get("平台公开ID") or "").strip()
            expected_name = str(expected_fields.get("创作者名称") or "").strip()
            try:
                row_text = re.sub(r"\s+", " ", row.inner_text(timeout=5000)).strip()
            except Exception:
                raise CaptureError("无法复核目标创作者列表行；未执行点击") from None
            if expected_id and expected_id not in row_text:
                raise CaptureError("目标创作者公开 ID 与列表行不匹配；为避免串位已停止")
            if expected_name and expected_name not in row_text:
                raise CaptureError("目标创作者名称与列表行不匹配；为避免串位已停止")
        links = row.locator("a:visible, [role='link']:visible")
        images = row.locator("img:visible")
        avatars = row.locator("[class*='avatar']:visible")
        buttons = row.locator("button:visible, [role='button']:visible")
        cells = row.get_by_role("cell")
        candidates = []
        if images.count():
            candidates.append((images.first, "头像"))
        if avatars.count():
            candidates.append((avatars.first, "头像容器"))
        if links.count():
            candidates.append((links.first, "可见链接"))
        if buttons.count():
            candidates.append((buttons.first, "可见按钮"))
        if cells.count() and cells.first.is_visible():
            candidates.append((cells.first, "首个可见单元格"))
        if row.is_visible():
            candidates.append((row, "创作者行"))
        if not candidates:
            raise CaptureError("当前创作者行没有可唯一确认的可见点击元素")
        click_errors: list[str] = []

        def opened_detail_page():
            return next(
                (
                    item
                    for item in self._context.pages
                    if item not in before_pages
                    and not item.is_closed()
                    and urlparse(item.url).hostname == PGY_HOST
                    and "/solar/pre-trade/blogger-detail/" in urlparse(item.url).path
                ),
                None,
            )

        def return_opened_detail(detail):
            try:
                detail.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            return detail, True

        for target, target_kind in candidates:
            try:
                target.scroll_into_view_if_needed(timeout=5000)
            except Exception as exc:
                click_errors.append(
                    f"{target_kind}:{type(exc).__name__}[target_summary_unavailable]"
                )
                continue
            target_summary = self._safe_click_target_summary(target)
            if self._is_modal_overlay_interception(target_summary):
                raise PauseForUser(
                    "蒲公英列表被页面弹层遮挡；请在可见浏览器中确认并关闭后恢复任务"
                )
            try:
                target.click(timeout=8000, no_wait_after=True)
            except Exception as exc:
                detail = opened_detail_page()
                if detail:
                    return return_opened_detail(detail)
                if (
                    not page.is_closed()
                    and "/solar/pre-trade/blogger-detail/" in urlparse(page.url).path
                ):
                    return page, False
                click_errors.append(
                    f"{target_kind}:{type(exc).__name__}[{target_summary['summary']}]"
                )
                continue
            deadline = time.monotonic() + 8
            detail = None
            while time.monotonic() < deadline:
                detail = opened_detail_page()
                if detail or (
                    not page.is_closed()
                    and "/solar/pre-trade/blogger-detail/" in urlparse(page.url).path
                ):
                    break
                page.wait_for_timeout(250)
            if detail:
                return return_opened_detail(detail)
            if not page.is_closed() and "/solar/pre-trade/blogger-detail/" in urlparse(page.url).path:
                return page, False
        opened = [item for item in self._context.pages if item not in before_pages]
        for item in opened:
            if not item.is_closed():
                self._close_owned_page(item)
        if click_errors:
            details = "、".join(click_errors[:5])
            raise CaptureError(f"创作者行可见点击未成功（{details[:240]}）")
        raise CaptureError("点击创作者头像、链接或列表项失败")

    @staticmethod
    def _safe_click_target_summary(target) -> dict[str, Any]:
        try:
            info = target.evaluate(
                """el => {
                  const r=el.getBoundingClientRect();
                  const x=Math.round(r.left+r.width/2), y=Math.round(r.top+r.height/2);
                  const hit=document.elementFromPoint(x,y);
                  const compact=n=>n?`${n.tagName.toLowerCase()}.${String(n.className||'').trim().split(/\\s+/).slice(0,2).join('.')}`:'none';
                  const style=getComputedStyle(el);
                  return {tag:compact(el), width:Math.round(r.width), height:Math.round(r.height),
                    pointer:style.pointerEvents, visibility:style.visibility,
                    hit:compact(hit), containsHit:!!(hit&&el.contains(hit))};
                }"""
            )
            info["summary"] = (
                f"{info['tag']}:{info['width']}x{info['height']}:"
                f"{info['pointer']}/{info['visibility']}:hit={info['hit']}:"
                f"inside={int(info['containsHit'])}"
            )[:150]
            return info
        except Exception:
            return {"hit": "none", "containsHit": False, "summary": "target_summary_unavailable"}

    @staticmethod
    def _is_modal_overlay_interception(target_summary: dict[str, Any]) -> bool:
        hit = str(target_summary.get("hit") or "")
        return (
            not bool(target_summary.get("containsHit"))
            and bool(re.search(r"modal|overlay|mask", hit, flags=re.IGNORECASE))
        )

    def _capture_creator_records(self, page, creator_ref: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        def capture_visible() -> list[str]:
            try:
                body = page.locator("body").inner_text(timeout=12000)
            except Exception:
                raise CaptureError("读取创作者可见资料失败") from None
            return [line.strip() for line in body.splitlines() if line.strip()]

        def add(record: dict[str, Any] | None, source: str = "蒲公英可见页面") -> None:
            if record:
                record["creator_ref"] = creator_ref
                record["source"] = source
                record["captured_at"] = local_timestamp()
                records.append(record)

        overview_tab = page.get_by_text("数据概览", exact=True)
        if overview_tab.count() == 1 and overview_tab.first.is_visible():
            try:
                overview_tab.first.click(timeout=5000)
                page.wait_for_timeout(350)
            except Exception:
                pass
        lines = capture_visible()
        add(self._profile_record(lines))
        add(self._performance_record(lines))

        fans_tab = page.get_by_text("粉丝分析", exact=True)
        if fans_tab.count() == 1 and fans_tab.first.is_visible():
            try:
                fans_tab.first.click(timeout=8000)
                page.wait_for_timeout(450)
                add(self._fans_record(capture_visible()))
            except CaptureError:
                raise
            except Exception:
                # Keep the already captured overview and let the task report missing fields.
                pass
        return records

    def _open_notes_tab(self, page) -> None:
        tabs = page.get_by_text("笔记数据", exact=True)
        if tabs.count() == 1 and tabs.first.is_visible():
            tabs.first.click(timeout=8000)
            page.wait_for_timeout(500)
        cards = page.locator(".note-card-wrapper:visible")
        if cards.count() == 0:
            try:
                page.locator(".note-card-wrapper").first.wait_for(state="visible", timeout=8000)
            except Exception:
                pass
            cards = page.locator(".note-card-wrapper:visible")
        if cards.count() == 0:
            self._check_page_blocker(page, pgy=True, force=True)
            raise CaptureError("创作者详情页没有识别到可见笔记案例卡")

    @staticmethod
    def _note_card_title_from_text(text: str) -> str | None:
        excluded = re.compile(
            r"(?:阅读量|曝光量|点赞量|收藏量|评论量|分享量|关注量|播放量|发布于|发布时间|"
            r"含推广流量|推广笔记|视频笔记|图文笔记|视频|图文|广告)",
            re.I,
        )
        for value in text.splitlines():
            line = re.sub(r"\s+", " ", value).strip(" ·|\t")
            if len(line) < 4 or len(line) > 240 or excluded.search(line):
                continue
            if re.fullmatch(r"[\d.,+%¥￥元万wW:：—–-]+", line):
                continue
            return line
        return None

    @staticmethod
    def _titles_match(expected: str | None, actual: str | None) -> bool:
        normalize = lambda value: re.sub(r"[^\w\u3400-\u9fff]+", "", str(value or "").casefold())
        left, right = normalize(expected), normalize(actual)
        if not left or not right:
            return False
        if left == right:
            return True
        shorter, longer = sorted((left, right), key=len)
        return len(shorter) >= 5 and longer.startswith(shorter)

    def _open_and_copy_note(self, page, note_index: int, creator_ref: str):
        cards = page.locator(".note-card-wrapper:visible")
        if note_index >= cards.count():
            raise CaptureError("目标笔记案例卡当前不可见")
        card = cards.nth(note_index)
        try:
            expected_title = self._note_card_title_from_text(card.inner_text(timeout=5000))
        except Exception:
            expected_title = None
        if not expected_title:
            raise CaptureError("无法从可见笔记卡确认标题；已跳过该笔记")
        mask = card.locator(".note-card__mask:visible")
        if mask.count() != 1:
            raise CaptureError("笔记卡片可见点击区域不唯一；已跳过该笔记")
        mask.click(timeout=12000)
        page.wait_for_timeout(700)
        copy_control = page.get_by_text("复制小红书笔记链接", exact=True)
        if copy_control.count() != 1 or not copy_control.first.is_visible():
            raise CaptureError("笔记详情面板没有唯一的可见复制链接按钮")
        try:
            page.context.grant_permissions(
                ["clipboard-read", "clipboard-write"], origin="https://pgy.xiaohongshu.com"
            )
        except Exception:
            pass
        clipboard_before = self._read_clipboard(page)
        copy_control.first.click(timeout=10000)
        copied = self._read_clipboard(page)
        if copied and copied == clipboard_before:
            confirmation = page.get_by_text(re.compile(r"复制成功|已复制"))
            if not any(
                confirmation.nth(index).is_visible()
                for index in range(min(confirmation.count(), 5))
            ):
                raise CaptureError("复制后的剪贴板内容未变化，无法排除旧链接；已跳过")
        if not copied:
            raise CaptureError("无法读取刚复制的小红书链接；未打开其他地址")
        parsed = urlparse(copied)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"}
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.fragment
            or not parsed.path.startswith("/explore/")
            or not parsed.path.removeprefix("/explore/").strip("/")
        ):
            raise CaptureError("复制结果不是已验证的小红书公开笔记链接；未打开其他地址")
        canonical = f"https://www.xiaohongshu.com{parsed.path}"
        body = page.locator("body").inner_text(timeout=8000)
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        record = self._note_detail_record(lines)
        if record:
            record.setdefault("fields", {})
            record["fields"].setdefault("笔记标题", expected_title)
            record.update(
                {
                    "type": "note_detail",
                    "source": "蒲公英可见笔记详情",
                    "captured_at": local_timestamp(),
                    "creator_ref": creator_ref,
                    "note_ref": canonical,
                }
            )
        return record, {
            "copied_url": copied,
            "canonical": canonical,
            "expected_title": expected_title,
        }

    @staticmethod
    def _read_clipboard(page) -> str:
        try:
            value = str(page.evaluate("() => navigator.clipboard.readText()") or "").strip()
            if value:
                return value
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def _open_copied_note(self, copied_url: str):
        parsed = urlparse(copied_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"}
            or not parsed.path.startswith("/explore/")
        ):
            raise CaptureError("复制链接校验失败；未导航到小红书页面")
        tab = self._context.new_page()
        try:
            tab.goto(copied_url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        tab.wait_for_timeout(900)
        self._check_page_blocker(tab, pgy=False)
        self._close_phone_login_prompt(tab)
        return tab

    def _capture_public_note(
        self,
        page,
        creator_ref: str,
        note_ref: str,
        *,
        expected_title: str | None = None,
    ) -> dict[str, Any] | None:
        title = self._visible_first_text(
            page,
            (".note-content .title", ".note-detail-mask .title", "h1"),
        )
        body = self._visible_first_text(
            page,
            (".note-content .desc", ".note-detail-mask .desc", ".note-content .note-text"),
        )
        topics = self._visible_text_list(
            page,
            (
                ".note-content .tag",
                ".note-detail-mask .tag",
                '.note-content a[href*="search_result"]',
                '.note-detail-mask a[href*="search_result"]',
            ),
        )
        published = self._visible_first_text(
            page,
            (".note-content .date", ".note-detail-mask .date", "time"),
        )
        if expected_title and not self._titles_match(expected_title, title):
            raise CaptureError("小红书页面标题与刚复制的蒲公英笔记卡不匹配；已跳过该页面")
        fields: dict[str, Any] = {}
        if title:
            fields["笔记标题"] = title
        if body:
            fields["笔记正文"] = body
        if published:
            fields["发布时间"] = published
        if topics:
            fields["话题"] = " ".join(topics)
        metric_selectors = {
            "点赞量": (
                ".interact-container .like-wrapper .count",
                ".interact-container .like-count",
                "[class*='like-wrapper'] [class*='count']",
            ),
            "收藏量": (
                ".interact-container .collect-wrapper .count",
                ".interact-container .collect-count",
                "[class*='collect-wrapper'] [class*='count']",
            ),
            "评论量": (
                ".interact-container .chat-wrapper .count",
                ".interact-container .comment-count",
                "[class*='chat-wrapper'] [class*='count']",
            ),
            "分享量": (
                ".interact-container .share-wrapper .count",
                ".interact-container .share-count",
                "[class*='share-wrapper'] [class*='count']",
            ),
        }
        for key, selectors in metric_selectors.items():
            value = self._visible_first_text(page, selectors)
            if value is not None:
                fields[key] = value
        if "评论量" not in fields:
            try:
                visible_text = page.locator("body").inner_text(timeout=5000)
            except Exception:
                visible_text = ""
            comment_total = self._inline_count(
                visible_text,
                r"共\s*([\d.,]+\s*(?:万|w|千)?\+?)\s*条评论",
            )
            if comment_total:
                fields["评论量"] = comment_total
        if not fields:
            try:
                visible_text = page.locator("body").inner_text(timeout=5000)
            except Exception:
                visible_text = ""
            if re.search(r"登录后查看|登录后浏览|登录后查看笔记|登录查看完整内容", visible_text):
                raise PauseForUser("小红书笔记内容要求登录才能查看；受限内容未采集")
            raise CaptureError("公开笔记页未识别到可见字段；未保存整页文本")
        expected_public_fields = ("笔记标题", "笔记正文", "发布时间", "话题", "点赞量", "收藏量", "评论量", "分享量")
        field_statuses = {
            name: "captured" if name in fields else "not_displayed"
            for name in expected_public_fields
        }
        field_statuses.update(
            {
                "曝光量": "not_applicable",
                "阅读量": "not_applicable",
                "新增关注量": "not_applicable",
                "视频时长": "not_attempted",
                "笔记类型": "not_attempted",
            }
        )
        return {
            "type": "xhs_public_note",
            "source": "小红书公开笔记页",
            "captured_at": local_timestamp(),
            "creator_ref": creator_ref,
            "note_ref": note_ref,
            "note_topics": topics,
            "fields": fields,
            "field_statuses": field_statuses,
        }

    def _capture_visible_comments(
        self,
        page,
        note_ref: str,
        limit: int,
        check_control: Callable[[], None],
        comment_scope: str,
        report_dom_delta: Callable[[int], None],
    ) -> tuple[list[dict[str, Any]], int]:
        if limit <= 0:
            return [], 0
        roots_selector = ".comments-container .parent-comment > .comment-item:not(.comment-item-sub)"
        replies_selector = ".comments-container .comment-item.comment-item-sub"
        comments: dict[str, dict[str, Any]] = {}
        dom_rows_added = 0

        def harvest() -> int:
            rows = page.evaluate(
                """({rootsSelector,repliesSelector}) => {
                  const visible = (el) => { const r=el.getBoundingClientRect(), s=getComputedStyle(el);
                    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'; };
                  const value = (root, selector) => { const el=root.querySelector(selector);
                    if(!el || !visible(el)) return null; const v=(el.innerText||el.textContent||''); return v.length?v:null; };
                  const rows=[];
                  for (const el of document.querySelectorAll(rootsSelector)) if(visible(el)) rows.push({
                    text:value(el,'.content .note-text'), time:value(el,'.date > span:not(.location)'),
                    likes:value(el,'.interactions .like .count'), replies:value(el,'.interactions .reply .count'),
                    pinned:!!el.querySelector('.labels .top'), level:1, parent_text:null, parent_time:null});
                  for (const el of document.querySelectorAll(repliesSelector)) if(visible(el)) {
                    const parent=el.closest('.parent-comment')?.querySelector(':scope > .comment-item:not(.comment-item-sub)');
                    rows.push({text:value(el,'.content .note-text'), time:value(el,'.date > span:not(.location)'),
                      likes:value(el,'.interactions .like .count'), replies:value(el,'.interactions .reply .count'),
                      pinned:null, level:2, parent_text:parent?value(parent,'.content .note-text'):null,
                      parent_time:parent?value(parent,'.date > span:not(.location)'):null});
                  }
                  return rows.filter(row=>row.text && row.text.trim());
                }""",
                {"rootsSelector": roots_selector, "repliesSelector": replies_selector},
            )
            parent_ids: dict[tuple[str, str], str] = {}
            for row in rows:
                if row["level"] == 1:
                    local_id = self._comment_fingerprint(
                        comment_scope, note_ref, row.get("text"), row.get("time"), 1, None
                    )
                    parent_ids[(str(row.get("text") or ""), str(row.get("time") or ""))] = local_id
                    comments.setdefault(
                        local_id,
                        {
                            "local_comment_key": local_id,
                            "local_comment_key_origin": "generated_fingerprint",
                            "comment_text": row["text"],
                            "displayed_time": row.get("time"),
                            "like_count_raw": row.get("likes"),
                            "reply_count_raw": row.get("replies"),
                            "comment_level": 1,
                            "is_pinned": row.get("pinned"),
                        },
                    )
                else:
                    parent_id = parent_ids.get(
                        (str(row.get("parent_text") or ""), str(row.get("parent_time") or ""))
                    )
                    local_id = self._comment_fingerprint(
                        comment_scope, note_ref, row.get("text"), row.get("time"), 2, parent_id
                    )
                    comments.setdefault(
                        local_id,
                        {
                            "local_comment_key": local_id,
                            "local_comment_key_origin": "generated_fingerprint",
                            "comment_text": row["text"],
                            "displayed_time": row.get("time"),
                            "like_count_raw": row.get("likes"),
                            "reply_count_raw": row.get("replies"),
                            "comment_level": 2,
                            "parent_local_comment_key": parent_id,
                            "parent_comment_id_origin": "generated_fingerprint",
                            "is_pinned": None,
                        },
                    )
            return len(rows)

        def dom_row_count() -> int:
            return int(
                page.evaluate(
                    """({rootsSelector,repliesSelector}) =>
                      document.querySelectorAll(`${rootsSelector},${repliesSelector}`).length""",
                    {"rootsSelector": roots_selector, "repliesSelector": replies_selector},
                )
            )

        harvest()
        before_count = dom_row_count()
        prior_count = before_count
        no_growth = 0
        scroller = page.locator(".note-scroller:visible")
        comments_container = page.locator(".comments-container:visible")
        for _ in range(20):
            check_control()
            if len(comments) >= limit:
                break
            if scroller.count() == 1:
                scroller.evaluate("el => { el.scrollTop = Math.min(el.scrollTop + Math.max(250, el.clientHeight * .8), el.scrollHeight); }")
            elif comments_container.count() == 1:
                comments_container.evaluate("el => { el.scrollTop = Math.min(el.scrollTop + Math.max(250, el.clientHeight * .8), el.scrollHeight); }")
            else:
                break
            page.wait_for_timeout(550)
            harvest()
            after_count = dom_row_count()
            delta = max(0, after_count - prior_count)
            dom_rows_added += delta
            report_dom_delta(delta)
            if delta:
                no_growth = 0
            else:
                no_growth += 1
            prior_count = after_count
            if no_growth >= 3:
                break

        if len(comments) < limit:
            toggles = page.locator(".comments-container .parent-comment .show-more:visible")
            for index in range(min(toggles.count(), limit)):
                check_control()
                if len(comments) >= limit:
                    break
                toggle = page.locator(".comments-container .parent-comment .show-more:visible").nth(index)
                try:
                    toggle.click(timeout=2500)
                    page.wait_for_timeout(250)
                except Exception:
                    continue
                harvest()
                after_count = dom_row_count()
                delta = max(0, after_count - prior_count)
                dom_rows_added += delta
                report_dom_delta(delta)
                prior_count = after_count

        roots = [item for item in comments.values() if item.get("comment_level") == 1]
        replies = [item for item in comments.values() if item.get("comment_level") == 2]
        rows = (roots + replies)[:limit]
        return rows, dom_rows_added

    @staticmethod
    def _comment_fingerprint(
        batch_scope: str,
        note_ref: str,
        text: Any,
        displayed_time: Any,
        level: int,
        parent_id: str | None,
    ) -> str:
        material = "\x1f".join(
            (
                batch_scope,
                note_ref,
                str(text or ""),
                str(displayed_time or ""),
                str(level),
                parent_id or "",
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _visible_first_text(page, selectors: tuple[str, ...]) -> str | None:
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 8)):
                item = locator.nth(index)
                try:
                    if item.is_visible():
                        value = re.sub(r"\s+", " ", item.inner_text(timeout=1000)).strip()
                        if value:
                            return value
                except Exception:
                    continue
        return None

    @staticmethod
    def _visible_text_list(page, selectors: tuple[str, ...]) -> list[str]:
        values: list[str] = []
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 60)):
                try:
                    item = locator.nth(index)
                    if not item.is_visible():
                        continue
                    text = re.sub(r"\s+", " ", item.inner_text(timeout=1000)).strip()
                    if text and text not in values:
                        values.append(text)
                except Exception:
                    continue
        return values

    def _close_phone_login_prompt(self, page) -> None:
        self._check_page_blocker(page, pgy=False)
        prompt = page.get_by_text("手机号登录", exact=True)
        visible_prompt = False
        try:
            visible_prompt = prompt.count() > 0 and prompt.first.is_visible()
            if not visible_prompt:
                login_label = page.get_by_text("登录", exact=True)
                visible_prompt = login_label.count() > 0 and login_label.first.is_visible()
        except Exception:
            visible_prompt = False
        if not visible_prompt:
            return
        selectors = (
            "button[aria-label*='关闭']:visible",
            "[role='button'][aria-label*='关闭']:visible",
            "button[title*='关闭']:visible",
            ".icon-btn-wrapper.close-button:visible",
            "button:visible",
            "[role='button']:visible",
        )
        candidates = []
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 80)):
                item = locator.nth(index)
                try:
                    label = item.evaluate(
                        "el => [el.getAttribute('aria-label')||'',el.getAttribute('title')||'',(el.innerText||'').trim()].join(' ')"
                    )
                    if re.search(r"关闭|close|^[×xX]$", label, re.I):
                        handle = item.element_handle()
                        if handle and all(handle != prior for prior in candidates):
                            candidates.append(handle)
                except Exception:
                    continue
        if len(candidates) != 1:
            raise PauseForUser("小红书显示手机号登录提示，但没有唯一明确的关闭按钮；请人工处理后恢复")
        candidates[0].click(timeout=5000)
        page.wait_for_timeout(300)
        self._check_page_blocker(page, pgy=False)

    @staticmethod
    def _check_page_blocker(page, *, pgy: bool, force: bool = False) -> None:
        try:
            body = page.locator("body").inner_text(timeout=5000)
        except Exception:
            body = ""
        if re.search(r"验证码|安全验证|访问异常|请完成验证|人机验证", body):
            raise PauseForUser("页面要求验证码或安全验证，任务已暂停；请在可见浏览器中按正常流程处理")
        if pgy and re.search(r"请先登录|登录后查看|账号登录", body):
            raise PauseForUser("蒲公英需要正常登录后才能采集；请在可见浏览器中登录后恢复任务")

    @staticmethod
    def _empty_list_message(page) -> str:
        try:
            body = page.locator("body").inner_text(timeout=3000)
        except Exception:
            body = ""
        if re.search(r"登录蒲公英|手机号登录|密码登录|请登录|登录后", body):
            return "蒲公英需要正常登录后才能采集；请在可见浏览器中登录后恢复任务"
        if re.search(r"暂无数据|没有符合条件|无匹配结果", body):
            return "当前蒲公英筛选结果为空；请在页面调整筛选后恢复任务"
        return "蒲公英博主列表等待 15 秒后仍未加载可采集行；请检查页面、登录状态和网络后恢复任务"

    @staticmethod
    def _safe_failure_reason(exc: Exception) -> str:
        if isinstance(exc, CaptureError):
            return exc.public_message[:180]
        return f"页面操作失败（{type(exc).__name__}）；请检查当前可见页面后重试"

    @staticmethod
    def _close_owned_page(page) -> None:
        try:
            if page is not None and not page.is_closed():
                page.close()
        except Exception:
            pass

    def _close_visible_note_panel(self, page) -> None:
        if page is None or page.is_closed():
            return
        copy_control = page.get_by_text("复制小红书笔记链接", exact=True)
        if copy_control.count() == 0 or not copy_control.first.is_visible():
            return
        for selector in (
            "button[aria-label*='关闭']:visible",
            "[role='button'][aria-label*='关闭']:visible",
            "button[title*='关闭']:visible",
            ".icon-btn-wrapper.close-button:visible",
        ):
            locator = page.locator(selector)
            if locator.count() == 1:
                try:
                    locator.first.click(timeout=3000)
                    return
                except Exception:
                    continue
        for label in ("关闭", "×", "X"):
            locator = page.get_by_role("button", name=label, exact=True)
            if locator.count() == 1 and locator.first.is_visible():
                try:
                    locator.first.click(timeout=3000)
                    return
                except Exception:
                    continue
        raise PauseForUser("无法唯一确认笔记详情面板的关闭按钮；任务已暂停等待人工处理")

    def close(self) -> dict[str, Any]:
        return self._call(self._close)

    def _close(self) -> dict[str, Any]:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None
        self._page = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self._last_status = {
            "open": False,
            "domain": "",
            "page_kind": "",
            "tab_count": 0,
            "tabs": [],
        }
        return {"open": False, "message": "蒲公英浏览器已关闭"}

    def shutdown(self) -> None:
        try:
            self.close()
        except Exception:
            pass
        self._executor.shutdown(wait=False, cancel_futures=True)
