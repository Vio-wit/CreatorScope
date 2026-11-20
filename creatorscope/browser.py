from __future__ import annotations

import hashlib
import subprocess
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


PGY_HOST = "pgy.xiaohongshu.com"
PGY_START_URL = "https://pgy.xiaohongshu.com/solar/pre-trade/note/kol"


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

    def _call(self, callback, timeout: float | None = 75):
        return self._executor.submit(callback).result(timeout=timeout)

    def open(self) -> dict[str, Any]:
        return self._call(self._open)

    def _open(self) -> dict[str, Any]:
        if self._context is not None and self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._safe_state()
            except Exception:
                pass

        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        try:
            self._page.goto(
                PGY_START_URL,
                wait_until="domcontentloaded",
                timeout=45000,
            )
        except Exception:
            # The headed window may remain useful for a normal manual sign-in.
            pass
        return {
            "open": True,
            "domain": PGY_HOST,
            "page_kind": self._route_kind(),
            "message": "请在新窗口中正常登录并手动打开待采集页面",
        }

    def status(self) -> dict[str, Any]:
        return self._call(self._safe_state)

    def _safe_state(self) -> dict[str, Any]:
        if self._page is None:
            return {"open": False, "domain": "", "page_kind": ""}
        try:
            if self._page.is_closed():
                return {"open": False, "domain": "", "page_kind": ""}
            parsed = urlparse(self._page.url)
            if parsed.hostname != PGY_HOST:
                return {
                    "open": True,
                    "domain": parsed.hostname or "",
                    "page_kind": "other",
                }
            return {"open": True, "domain": PGY_HOST, "page_kind": self._route_kind()}
        except Exception:
            return {"open": False, "domain": "", "page_kind": ""}

    def _route_kind(self) -> str:
        if self._page is None:
            return ""
        path = urlparse(self._page.url).path
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
            record = self._capture_list_row()
            if record:
                records.append(record)
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

    def _capture_list_row(self) -> dict[str, Any] | None:
        try:
            rows = self._page.get_by_role("row").all()
            for row in rows[1:]:
                cells = row.get_by_role("cell").all_inner_texts()
                if len(cells) >= 6:
                    fields = {
                        "粉丝数": cells[2].strip(),
                        "阅读中位数（日常）": cells[3].strip(),
                        "互动中位数（日常）": cells[4].strip(),
                        "全部报价": cells[5].strip(),
                    }
                    if any(fields.values()):
                        return {"type": "blogger_list_sample", "fields": fields}
        except Exception:
            return None
        return None

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
            return {"type": "creator_profile_sample", "fields": fields}
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
        period = "近30日" if "近30日" in lines else ""
        return {
            "type": "creator_content_performance",
            "period_displayed": period,
            "fields": fields,
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
        period = "近30日" if "近30日" in lines else ""
        return {
            "type": "fan_analysis",
            "period_selector_displayed": period,
            "fields": fields,
        }

    def _note_detail_record(self, lines: list[str]) -> dict[str, Any] | None:
        try:
            start = lines.index("笔记详情")
        except ValueError:
            return None
        labels = ("发布时间", "曝光量", "阅读量", "点赞量", "收藏量", "评论量", "分享量")
        fields: dict[str, Any] = {}
        for label in labels:
            fields[label] = self._value_after(lines, label, start=start)
        if not any(value is not None for value in fields.values()):
            return None
        return {"type": "note_detail", "fields": fields}

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
        existing_comment_count: int,
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
                existing_comment_count=existing_comment_count,
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
        existing_comment_count: int,
    ) -> dict[str, Any]:
        if self._context is None or self._page is None or self._page.is_closed():
            self._open()
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
        creator_start = max(0, min(creator_limit, int(checkpoint.get("creator_index", 0))))
        note_start = max(0, int(checkpoint.get("note_index", 0)))
        comment_total = existing_comment_count
        dom_rows_added = 0
        creators_visited = 0
        notes_visited = 0
        failures = 0
        if total_rows <= 1:
            self._check_page_blocker(page, pgy=True, force=True)
            raise PauseForUser(self._empty_list_message(page))

        target_end = min(total_rows, creator_limit + 1)
        for creator_index in range(creator_start, target_end - 1):
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
                detail_page, opened_new_page = self._open_creator_row(page, creator_index + 1)
                creator_stage = "确认创作者详情页"
                self._check_page_blocker(detail_page, pgy=True)
                route = urlparse(detail_page.url)
                if route.hostname != PGY_HOST or "/solar/pre-trade/blogger-detail/" not in route.path:
                    raise CaptureError("列表行未打开可识别的蒲公英创作者详情页")
                creator_ref = f"pgy:{route.path}"
                creator_stage = "读取创作者资料和指标"
                creator_records = self._capture_creator_records(detail_page, creator_ref)
                if settings.get("collect_creator", True) and creator_records:
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

                if settings.get("collect_notes", True):
                    creator_stage = "打开笔记数据页签"
                    self._open_notes_tab(detail_page)
                    card_count = detail_page.locator(".note-card-wrapper:visible").count()
                    note_limit = max(1, min(3, int(settings.get("notes_per_creator", 1))))
                    note_end = min(card_count, note_limit)
                    first_note = note_start if creator_index == creator_start else 0
                    for note_index in range(first_note, note_end):
                        check_control()
                        if settings.get("collect_comments", True) and comment_total >= 300:
                            break
                        note_page = None
                        note_stage = "打开笔记详情并复制链接"
                        try:
                            detail_record, note_ref = self._open_and_copy_note(
                                detail_page, note_index, creator_ref
                            )
                            if detail_record:
                                save_records(
                                    {"source": "蒲公英可见笔记详情", "records": [detail_record]}
                                )
                            note_stage = "打开小红书公开笔记页"
                            note_page = self._open_copied_note(note_ref["copied_url"])
                            note_stage = "读取小红书公开笔记正文和话题"
                            public_record = self._capture_public_note(note_page, creator_ref, note_ref["canonical"])
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
                        except (PauseForUser, StopCollection):
                            raise
                        except Exception as exc:
                            self._close_owned_page(note_page)
                            failures += 1
                            report_failure(
                                creator_index + 1,
                                note_index + 1,
                                f"{note_stage}：{self._safe_failure_reason(exc)}",
                            )
                            self._close_visible_note_panel(detail_page)
                            raise PauseForUser(
                                f"第 {creator_index + 1} 位创作者的第 {note_index + 1} 篇笔记采集失败；已保留断点，可查看失败记录后恢复重试"
                            ) from None
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

    def _open_creator_row(self, page, row_index: int):
        before_pages = set(self._context.pages)
        row = page.get_by_role("row").nth(row_index)
        links = row.get_by_role("link")
        images = row.locator("img:visible")
        cells = row.get_by_role("cell")
        candidates = []
        if images.count():
            candidates.append((images.first, "头像"))
        if links.count():
            candidates.append((links.first, "可见链接"))
        if cells.count() and cells.first.is_visible():
            candidates.append((cells.first, "首个可见单元格"))
        if row.is_visible():
            candidates.append((row, "创作者行"))
        if not candidates:
            raise CaptureError("当前创作者行没有可唯一确认的可见点击元素")
        clicked_kinds = []
        for target, target_kind in candidates:
            try:
                target.click(timeout=3000, no_wait_after=True)
                clicked_kinds.append(target_kind)
            except Exception:
                continue
            deadline = time.monotonic() + 3
            detail = None
            while time.monotonic() < deadline:
                detail = next(
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
                if detail or (
                    not page.is_closed()
                    and "/solar/pre-trade/blogger-detail/" in urlparse(page.url).path
                ):
                    break
                page.wait_for_timeout(250)
            if detail:
                try:
                    detail.wait_for_load_state("domcontentloaded", timeout=20000)
                except Exception:
                    pass
                return detail, True
            if not page.is_closed() and "/solar/pre-trade/blogger-detail/" in urlparse(page.url).path:
                return page, False
        opened = [item for item in self._context.pages if item not in before_pages]
        for item in opened:
            if not item.is_closed():
                self._close_owned_page(item)
        if clicked_kinds:
            raise CaptureError("已点击创作者可见列表项，但没有打开蒲公英详情页")
        raise CaptureError("点击创作者头像、链接或列表项失败")

    def _capture_creator_records(self, page, creator_ref: str) -> list[dict[str, Any]]:
        try:
            body = page.locator("body").inner_text(timeout=12000)
        except Exception:
            raise CaptureError("读取创作者可见资料失败") from None
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        records = []
        for record in (
            self._profile_record(lines),
            self._performance_record(lines),
            self._fans_record(lines),
        ):
            if record:
                record["creator_ref"] = creator_ref
                records.append(record)
        return records

    def _open_notes_tab(self, page) -> None:
        tabs = page.get_by_text("笔记数据", exact=True)
        if tabs.count() == 1 and tabs.first.is_visible():
            tabs.first.click(timeout=8000)
            page.wait_for_timeout(500)
        cards = page.locator(".note-card-wrapper:visible")
        if cards.count() == 0:
            self._check_page_blocker(page, pgy=True, force=True)
            raise CaptureError("创作者详情页没有识别到可见笔记案例卡")

    def _open_and_copy_note(self, page, note_index: int, creator_ref: str):
        cards = page.locator(".note-card-wrapper:visible")
        if note_index >= cards.count():
            raise CaptureError("目标笔记案例卡当前不可见")
        card = cards.nth(note_index)
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
            record.update({"type": "note_detail", "creator_ref": creator_ref, "note_ref": canonical})
        return record, {"copied_url": copied, "canonical": canonical}

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

    def _capture_public_note(self, page, creator_ref: str, note_ref: str) -> dict[str, Any] | None:
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
        fields: dict[str, Any] = {}
        if title:
            fields["笔记标题"] = title
        if body:
            fields["笔记正文"] = body
        if published:
            fields["发布时间"] = published
        if topics:
            fields["话题"] = " ".join(topics)
        if not fields:
            raise CaptureError("公开笔记页未识别到可见正文；未保存整页文本")
        return {
            "type": "xhs_public_note",
            "creator_ref": creator_ref,
            "note_ref": note_ref,
            "note_topics": topics,
            "fields": fields,
        }

    def _capture_visible_comments(
        self,
        page,
        note_ref: str,
        limit: int,
        check_control: Callable[[], None],
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
                    if(!el || !visible(el)) return null; const v=(el.innerText||el.textContent||'').trim(); return v||null; };
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
                  return rows.filter(row=>row.text);
                }""",
                {"rootsSelector": roots_selector, "repliesSelector": replies_selector},
            )
            parent_ids: dict[tuple[str, str], str] = {}
            for row in rows:
                if row["level"] == 1:
                    local_id = self._comment_fingerprint(
                        note_ref, row.get("text"), row.get("time"), 1, None
                    )
                    parent_ids[(str(row.get("text") or ""), str(row.get("time") or ""))] = local_id
                    comments.setdefault(
                        local_id,
                        {
                            "local_comment_id": local_id,
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
                        note_ref, row.get("text"), row.get("time"), 2, parent_id
                    )
                    comments.setdefault(
                        local_id,
                        {
                            "local_comment_id": local_id,
                            "comment_text": row["text"],
                            "displayed_time": row.get("time"),
                            "like_count_raw": row.get("likes"),
                            "reply_count_raw": row.get("replies"),
                            "comment_level": 2,
                            "parent_local_comment_id": parent_id,
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
        note_ref: str, text: Any, displayed_time: Any, level: int, parent_id: str | None
    ) -> str:
        material = "\x1f".join(
            (note_ref, str(text or ""), str(displayed_time or ""), str(level), parent_id or "")
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
                            return value[:5000]
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
                        values.append(text[:120])
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
        return {"open": False, "message": "蒲公英浏览器已关闭"}

    def shutdown(self) -> None:
        try:
            self.close()
        except Exception:
            pass
        self._executor.shutdown(wait=False, cancel_futures=True)
