from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


PGY_HOST = "pgy.xiaohongshu.com"
PGY_START_URL = "https://pgy.xiaohongshu.com/solar/pre-trade/note/kol"


class CaptureError(Exception):
    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


class PgyVisibleBrowser:
    """A small headed Playwright session controlled only through the visible UI."""

    def __init__(self, profile_dir: Path):
        self.profile_dir = profile_dir
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pgy-browser")
        self._playwright = None
        self._context = None
        self._page = None

    def _call(self, callback):
        return self._executor.submit(callback).result(timeout=75)

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
