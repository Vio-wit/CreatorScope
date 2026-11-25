"""Visible-browser R4.2 comment investigation and bounded collection runner."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

PROJECT = Path(__file__).resolve().parents[2]
R41 = PROJECT / "research" / "xhs_public_access_probe"
OUT = Path(__file__).resolve().parent
SCREENSHOTS = OUT / "screenshots"
PROFILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "CreatorScope" / "pgy_browser_profile"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(OUT))

from comment_parser import CommentSelectors, dom_outline, extract_comments, local_comment_id, now_local_iso  # noqa: E402
from creatorscope.browser import PgyVisibleBrowser  # noqa: E402

NOTE_IDS = [
    "6af9bf01000000002a02adec",  # R4.1 note 2, creator 1
    "6afc317600000000180288af",  # R4.1 note 1, creator 1
    "6af2dd5f000000001802d246",  # R4.1 note 6, creator 2
]
PGY_START_URL = "https://pgy.xiaohongshu.com/solar/pre-trade/note/kol"


def load_notes() -> dict[str, dict]:
    records = {}
    with (R41 / "raw_notes_xhs.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            note_id = row.get("note_id")
            if note_id in NOTE_IDS:
                records[note_id] = row
    missing = [note_id for note_id in NOTE_IDS if note_id not in records]
    if missing:
        raise RuntimeError(f"R4.1 sample missing note IDs: {missing}")
    return records


def safe_page_state(page) -> dict:
    parsed = urlparse(page.url)
    return {
        "host": parsed.hostname or "",
        "path": parsed.path,
        "title": page.title(),
        "page_count": len(page.context.pages),
    }


def visible_login_prompt(page) -> bool:
    try:
        return page.get_by_text("手机号登录", exact=False).first.is_visible()
    except Exception:
        return False


def phone_prompt_outline(page) -> dict:
    return page.evaluate(
        """() => {
          const candidates=[...document.querySelectorAll('body *')].filter(el => {
            const t=(el.innerText||'').trim(); const r=el.getBoundingClientRect();
            return t==='手机号登录' && r.width>0 && r.height>0;
          }).sort((a,b)=>a.getBoundingClientRect().width*a.getBoundingClientRect().height-
                        b.getBoundingClientRect().width*b.getBoundingClientRect().height);
          const target=candidates[0];
          if(!target) return {visible:false,ancestors:[]};
          let chain=[], root=null, el=target;
          for(let i=0;el&&i<9;i++,el=el.parentElement){
            const r=el.getBoundingClientRect(); const c=typeof el.className==='string'?el.className:'';
            chain.push({tag:el.tagName.toLowerCase(),cls:c.slice(0,160),role:el.getAttribute('role'),
              x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)});
            if(!root && (el.getAttribute('role')==='dialog' || /(modal|login|dialog)/i.test(c)) && r.width>180 && r.height>100 && r.width<1400 && r.height<1200) root=el;
          }
          if(!root) root=target.parentElement?.parentElement||target;
          const controls=[...root.querySelectorAll('button,[role=button],svg,[class*=close],[class*=Close]')].filter(n=>{
            const r=n.getBoundingClientRect(),s=getComputedStyle(n); return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';
          }).slice(0,40).map(n=>{const r=n.getBoundingClientRect(),t=(n.innerText||'').trim();
            const safe=['X','x','×','关闭','close','Close'].includes(t)?t:null;
            return {tag:n.tagName.toLowerCase(),cls:(typeof n.className==='string'?n.className:'').slice(0,150),
              aria:n.getAttribute('aria-label'),title:n.getAttribute('title'),safeText:safe,
              x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};
          });
          const r=root.getBoundingClientRect();
          return {visible:true,ancestors:chain,root:{tag:root.tagName.toLowerCase(),cls:(typeof root.className==='string'?root.className:'').slice(0,160),
            x:Math.max(0,Math.round(r.x)),y:Math.max(0,Math.round(r.y)),w:Math.round(r.width),h:Math.round(r.height)},controls};
        }"""
    )


def close_explicit_phone_prompt(page) -> bool:
    """Click only an explicitly labeled visible close control in a phone prompt."""
    if not visible_login_prompt(page):
        return False
    locators = [
        page.locator("button[aria-label*='关闭']:visible"),
        page.locator("[role='button'][aria-label*='关闭']:visible"),
        page.locator("button[title*='关闭']:visible"),
        page.locator(".icon-btn-wrapper.close-button:visible"),
        page.get_by_role("button", name="关闭", exact=True),
        page.get_by_role("button", name="×", exact=True),
        page.get_by_role("button", name="X", exact=True),
    ]
    for locator in locators:
        try:
            for index in range(min(locator.count(), 5)):
                control = locator.nth(index)
                if control.is_visible():
                    control.click(timeout=3000)
                    try:
                        page.get_by_text("手机号登录", exact=False).first.wait_for(state="hidden", timeout=3500)
                    except Exception:
                        pass
                    return not visible_login_prompt(page)
        except Exception:
            continue
    return False


def pgy_overlay_summary(page) -> list[dict]:
    """Describe only overlay structure and known generic action labels."""
    allowed = {"暂时忽略", "暂不", "关闭", "取消", "知道了", "我知道了", "以后再说", "跳过", "×", "x", "X", "稍后", "不再提醒"}
    result = []
    masks = page.locator(".d-modal-mask:visible")
    for i in range(min(masks.count(), 6)):
        mask = masks.nth(i)
        buttons = mask.locator("button:visible, [role='button']:visible")
        actions = []
        for j in range(min(buttons.count(), 30)):
            button = buttons.nth(j)
            try:
                label = (button.inner_text(timeout=1000) or "").strip()
                if label in allowed:
                    actions.append(label)
            except Exception:
                pass
        close_controls = mask.locator(".close-button:visible, [aria-label*='关闭']:visible, [title*='关闭']:visible, [aria-label*='close' i]:visible, [title*='close' i]:visible").count()
        result.append({"mask_class": mask.get_attribute("class") or "", "visible_button_count": buttons.count(), "known_safe_actions": actions, "explicit_close_control_count": close_controls})
    return result


def dismiss_known_pgy_overlay(page) -> dict:
    allowed = {"暂时忽略", "暂不", "关闭", "取消", "知道了", "我知道了", "以后再说", "跳过", "×", "x", "X", "稍后", "不再提醒"}
    masks = page.locator(".d-modal-mask:visible")
    clicked = []
    for i in range(min(masks.count(), 6)):
        mask = masks.nth(i)
        direct_matches = []
        for label in allowed:
            loc = mask.get_by_text(label, exact=True)
            for j in range(min(loc.count(), 4)):
                try:
                    item = loc.nth(j)
                    if item.is_visible():
                        direct_matches.append((item, label))
                except Exception:
                    pass
        if len(direct_matches) == 1:
            try:
                direct_matches[0][0].click(timeout=5000)
                clicked.append(direct_matches[0][1])
                continue
            except Exception:
                pass
        buttons = masks.nth(i).locator("button:visible, [role='button']:visible")
        matches = []
        for j in range(min(buttons.count(), 30)):
            button = buttons.nth(j)
            try:
                label = (button.inner_text(timeout=1000) or "").strip()
                if label in allowed:
                    matches.append((button, label))
            except Exception:
                pass
        if len(matches) == 1:
            matches[0][0].click(timeout=5000)
            clicked.append(matches[0][1])
        if not matches:
            close = masks.nth(i).locator(".close-button:visible, [aria-label*='关闭']:visible, [title*='关闭']:visible, [aria-label*='close' i]:visible, [title*='close' i]:visible")
            if close.count() == 1:
                close.first.click(timeout=5000)
                clicked.append("explicit_close_control")
    return {"dismissed_actions": clicked, "remaining_overlay_count": page.locator(".d-modal-mask:visible").count()}


def outline_markdown(note_id: str, result: dict) -> str:
    lines = [f"## Note `{note_id}`", "", f"- path: `{result.get('urlPath','')}`", f"- title: {result.get('title','')}", "", "### Comment-related DOM classes", ""]
    lines.extend(f"- `{row['selector']}` × {row['count']}" for row in result.get("commentRelatedClassCounts", []))
    lines.extend(["", "### Scrollable elements", ""])
    lines.extend(
        f"- `{row['tag']}.{row.get('cls','')}` at ({row['x']},{row['y']}) {row['w']}×{row['h']}; {row['scrollTop']}/{row['scrollHeight']-row['clientHeight']}"
        for row in result.get("scrollables", [])
    )
    lines.extend(["", "### Safe UI headings", ""])
    lines.extend(f"- `{row['text']}`: `{row['tag']}.{row.get('cls','')}` y={row['y']}" for row in result.get("headings", []))
    lines.extend(["", "### Text-node shape inside candidate comment rows", ""])
    for row in result.get("textShapes", []):
        lines.append(f"- Root `{row['rootTag']}.{row['rootClass']}` rect={row['rect']}")
        for node in row["textNodes"]:
            chain = " > ".join(f"{part['tag']}.{part['cls']}" for part in node["chain"])
            lines.append(f"  - text length {node['length']}; `{chain}`")
    return "\n".join(lines) + "\n"


COMMENT_SELECTORS = CommentSelectors(
    item=".comments-container .parent-comment > .comment-item:not(.comment-item-sub)",
    text=".content .note-text",
    time=".date > span:not(.location)",
    likes=".interactions .like .count",
    replies=".interactions .reply .count",
    pinned=".labels .top",
)
REPLY_SELECTORS = CommentSelectors(
    item=".comments-container .comment-item.comment-item-sub",
    text=".content .note-text",
    time=".date > span:not(.location)",
    likes=".interactions .like .count",
    replies=".interactions .reply .count",
    pinned=".labels .top",
)


def parse_display_count(value: str | None) -> int | None:
    if value is None:
        return None
    text_value = value.strip().replace(",", "")
    if text_value.isdigit():
        return int(text_value)
    return None


def dedupe_in_batch(records: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    result: list[dict] = []
    duplicates = 0
    for row in records:
        row["local_comment_id"] = local_comment_id(row)
        if row["local_comment_id"] in seen:
            duplicates += 1
            continue
        seen.add(row["local_comment_id"])
        result.append(row)
    return result, duplicates


def _write_json(path: Path, data: object, *, replace: bool = False) -> None:
    if path.exists() and not replace:
        raise FileExistsError(f"Refusing to overwrite existing R4.2 artifact: {path.name}")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict], *, replace: bool = False) -> None:
    if path.exists() and not replace:
        raise FileExistsError(f"Refusing to overwrite existing R4.2 artifact: {path.name}")
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict], fields: list[str], *, replace: bool = False) -> None:
    if path.exists() and not replace:
        raise FileExistsError(f"Refusing to overwrite existing R4.2 artifact: {path.name}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _capture_note(browser: PgyVisibleBrowser, list_page, note: dict, batch_id: str, save_screenshot: bool, expand_replies: bool, detail_cache: dict[str, Any]) -> tuple[list[dict], dict, list[dict]]:
    """Use Pgy visible UI to copy the official note link, then read rendered XHS DOM."""
    events: list[dict] = []
    phase_state = {"name": "navigate_pgy_list"}

    def on_browser(callback):
        return browser._call(callback)

    def click_controlled_pgy_route():
        actions = []
        creator = note.get("creator_name", "")
        detail = detail_cache.get(creator)
        try:
            if detail is not None and detail.is_closed():
                detail = None
        except Exception:
            detail = None
        if detail is None:
            phase_state["name"] = "navigate_pgy_list"
            if urlparse(list_page.url).hostname != "pgy.xiaohongshu.com" or urlparse(list_page.url).path != urlparse(PGY_START_URL).path:
                try:
                    list_page.goto(PGY_START_URL, wait_until="commit", timeout=20000)
                except Exception:
                    if urlparse(list_page.url).hostname != "pgy.xiaohongshu.com" or urlparse(list_page.url).path != urlparse(PGY_START_URL).path:
                        return {"ok": False, "reason": "pgy_list_navigation_failed", "overlay_actions": actions}
            list_page.wait_for_timeout(1800)
            for _ in range(3):
                if not pgy_overlay_summary(list_page):
                    break
                dismissed = dismiss_known_pgy_overlay(list_page)["dismissed_actions"]
                actions.extend(dismissed)
                list_page.wait_for_timeout(1000)
            if pgy_overlay_summary(list_page):
                return {"ok": False, "reason": "pgy_overlay_without_safe_close", "overlay_actions": actions, "overlays": pgy_overlay_summary(list_page)}
            phase_state["name"] = "find_creator_row"
            loc = list_page.get_by_text(creator, exact=True)
            try:
                loc.first.wait_for(state="visible", timeout=15000)
            except Exception:
                return {"ok": False, "reason": "creator_not_found_after_wait", "visible_matches": loc.count(), "overlay_actions": actions}
            matches = [loc.nth(i) for i in range(min(loc.count(), 20)) if loc.nth(i).is_visible()]
            if len(matches) != 1:
                return {"ok": False, "reason": "creator_not_unique_in_visible_list", "visible_matches": len(matches), "overlay_actions": actions}
            pages_before = list(list_page.context.pages)
            phase_state["name"] = "click_creator_row"
            try:
                matches[0].click(timeout=10000)
            except Exception as exc:
                overlays = pgy_overlay_summary(list_page)
                retry_actions = dismiss_known_pgy_overlay(list_page)["dismissed_actions"] if overlays else []
                actions.extend(retry_actions)
                if not retry_actions:
                    return {"ok": False, "reason": "creator_ui_click_failed", "error_type": type(exc).__name__, "overlays": overlays, "overlay_actions": actions}
                list_page.wait_for_timeout(1000)
                try:
                    matches[0].click(timeout=7000)
                except Exception as retry_exc:
                    return {"ok": False, "reason": "creator_ui_click_retry_failed", "error_type": type(retry_exc).__name__, "overlays": pgy_overlay_summary(list_page), "overlay_actions": actions}
            list_page.wait_for_timeout(1500)
            pages_after = list_page.context.pages
            detail = next((p for p in reversed(pages_after) if p not in pages_before), list_page)
            detail_cache[creator] = detail
        else:
            phase_state["name"] = "reload_cached_creator_detail"
            try:
                detail.reload(wait_until="commit", timeout=20000)
            except Exception:
                if urlparse(detail.url).hostname != "pgy.xiaohongshu.com" or "/solar/pre-trade/blogger-detail/" not in urlparse(detail.url).path:
                    return {"ok": False, "reason": "cached_creator_detail_reload_failed", "overlay_actions": actions}
        phase_state["name"] = "wait_creator_detail"
        detail.wait_for_timeout(1600)
        for _ in range(2):
            if not pgy_overlay_summary(detail):
                break
            actions.extend(dismiss_known_pgy_overlay(detail)["dismissed_actions"])
            detail.wait_for_timeout(850)
        if pgy_overlay_summary(detail):
            return {"ok": False, "reason": "creator_detail_overlay_without_safe_close", "overlay_actions": actions}
        title = note.get("title", "")
        phase_state["name"] = "find_note_card"
        title_loc = detail.get_by_text(title, exact=True)
        try:
            title_loc.first.wait_for(state="visible", timeout=15000)
            title_loc.first.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            return {"ok": False, "reason": "note_card_title_not_found_after_wait", "creator_detail_path": urlparse(detail.url).path, "overlay_actions": actions}
        title_matches = [title_loc.nth(i) for i in range(min(title_loc.count(), 20)) if title_loc.nth(i).is_visible()]
        if not title_matches:
            return {"ok": False, "reason": "note_card_title_not_visible", "creator_detail_path": urlparse(detail.url).path, "overlay_actions": actions}
        card = title_matches[0].locator("xpath=ancestor::div[contains(@class,'note-card-wrapper')][1]")
        click_surface = card.locator(".note-card__mask:visible")
        if card.count() != 1 or click_surface.count() != 1:
            return {"ok": False, "reason": "note_card_surface_not_unique", "card_count": card.count(), "surface_count": click_surface.count(), "overlay_actions": actions}
        if pgy_overlay_summary(detail):
            actions.extend(dismiss_known_pgy_overlay(detail)["dismissed_actions"])
            detail.wait_for_timeout(900)
            if pgy_overlay_summary(detail):
                return {"ok": False, "reason": "overlay_remains_before_note_click", "overlay_actions": actions}
        phase_state["name"] = "click_note_card"
        try:
            click_surface.click(timeout=6000)
        except Exception as exc:
            overlays = pgy_overlay_summary(detail)
            retry_actions = dismiss_known_pgy_overlay(detail)["dismissed_actions"] if overlays else []
            actions.extend(retry_actions)
            if not retry_actions:
                return {"ok": False, "reason": "note_card_ui_click_failed", "error_type": type(exc).__name__, "overlays": overlays, "overlay_actions": actions}
            detail.wait_for_timeout(900)
            try:
                click_surface.click(timeout=6000)
            except Exception as retry_exc:
                return {"ok": False, "reason": "note_card_ui_click_retry_failed", "error_type": type(retry_exc).__name__, "overlays": pgy_overlay_summary(detail), "overlay_actions": actions}
        detail.wait_for_timeout(900)
        if pgy_overlay_summary(detail):
            actions.extend(dismiss_known_pgy_overlay(detail)["dismissed_actions"])
            detail.wait_for_timeout(850)
        copy = detail.get_by_text("复制小红书笔记链接", exact=True)
        phase_state["name"] = "click_pgy_copy_link"
        if copy.count() != 1 or not copy.first.is_visible():
            return {"ok": False, "reason": "Pgy_copy_button_not_unique_or_visible", "copy_button_matches": copy.count(), "overlay_actions": actions}
        try:
            copy.click(timeout=8000)
        except Exception as exc:
            return {"ok": False, "reason": "Pgy_copy_button_ui_click_failed", "error_type": type(exc).__name__, "overlays": pgy_overlay_summary(detail), "overlay_actions": actions}
        detail.wait_for_timeout(450)
        phase_state["name"] = "read_copied_link_after_ui_click"
        clip = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"], capture_output=True, text=True, timeout=5).stdout.strip()
        parsed = urlparse(clip)
        keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
        if parsed.hostname == "www.xiaohongshu.com" and not parsed.path.endswith("/" + note["note_id"]):
            copy.click(timeout=5000)
            detail.wait_for_timeout(700)
            clip = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"], capture_output=True, text=True, timeout=5).stdout.strip()
            parsed = urlparse(clip)
            keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
        if parsed.hostname != "www.xiaohongshu.com" or not parsed.path.endswith("/" + note["note_id"]):
            return {"ok": False, "reason": "copied_link_note_id_mismatch", "copied_host": parsed.hostname or "", "copied_path": parsed.path, "overlay_actions": actions}
        return {"ok": True, "url_in_memory": clip, "creator_detail_path": urlparse(detail.url).path, "overlay_actions": actions, "query_present": bool(parsed.query), "query_keys": keys}

    try:
        pgy_result = on_browser(click_controlled_pgy_route)
    except Exception as exc:
        pgy_result = {"ok": False, "reason": "pgy_ui_operation_failed", "phase": phase_state["name"], "error_type": type(exc).__name__}
    events.append({"event": "pgy_ui_link_acquisition", "note_id": note["note_id"], **{k:v for k,v in pgy_result.items() if k != "url_in_memory"}})
    if not pgy_result.get("ok"):
        return [], {"note_id": note["note_id"], "title": note.get("title"), "status": "blocked_before_xhs", "reason": pgy_result.get("reason"), "scroll_attempts": 0}, events

    xhs_page = on_browser(lambda: browser._context.new_page())
    def open_xhs():
        xhs_page.goto(pgy_result["url_in_memory"], wait_until="domcontentloaded", timeout=45000)
        xhs_page.wait_for_timeout(1500)
        prompt_was_visible = visible_login_prompt(xhs_page)
        closed = close_explicit_phone_prompt(xhs_page) if prompt_was_visible else False
        xhs_page.wait_for_timeout(650)
        prompt_remains = visible_login_prompt(xhs_page)
        title = xhs_page.title()
        current_id_matches = note["note_id"] in urlparse(xhs_page.url).path
        try:
            xhs_page.locator(".comments-container").wait_for(state="visible", timeout=7000)
            comments_container_visible = True
        except Exception:
            comments_container_visible = False
        return {"host": urlparse(xhs_page.url).hostname or "", "path": urlparse(xhs_page.url).path, "title": title,
                "page_title_matches_sample": note.get("title", "") in title, "note_id_matches_path": current_id_matches,
                "login_prompt_seen": prompt_was_visible, "login_prompt_closed_by_visible_x": closed,
                "login_prompt_remains": prompt_remains, "comments_container_visible": comments_container_visible}
    page_result = on_browser(open_xhs)
    events.append({"event": "open_xhs_note", "note_id": note["note_id"], **page_result})
    if not (page_result["host"] == "www.xiaohongshu.com" and page_result["note_id_matches_path"] and not page_result["login_prompt_remains"] and page_result["comments_container_visible"]):
        return [], {"note_id": note["note_id"], "title": note.get("title"), "status": "blocked_or_comment_dom_missing", **page_result, "scroll_attempts": 0}, events

    def capture_outline():
        return dom_outline(xhs_page)
    before = on_browser(capture_outline)
    events.append({"event": "dom_before_scroll", "note_id": note["note_id"], "root_items": before.get("commentRelatedClassCounts", [])})
    outline_file = OUT / "dom_structure.md"
    outline_file.write_text((outline_file.read_text(encoding="utf-8") if outline_file.exists() else "# R4.2 Live Comment DOM Structure\n\n") + outline_markdown(note["note_id"], before) + "\n", encoding="utf-8")

    # Exercise one visible reply expansion on the first sample only.
    reply_expansion = {"attempted": False, "expanded": False, "before": 0, "after": 0}
    if expand_replies:
        def expand_one():
            sub = xhs_page.locator(".comments-container .comment-item.comment-item-sub")
            toggles = xhs_page.locator(".comments-container .parent-comment .show-more:visible")
            reply_expansion["before"] = sub.count()
            if toggles.count() > 0:
                reply_expansion["attempted"] = True
                toggles.first.click(timeout=5000)
                xhs_page.wait_for_timeout(900)
            reply_expansion["after"] = sub.count()
            reply_expansion["expanded"] = reply_expansion["after"] > reply_expansion["before"]
            return dict(reply_expansion)
        reply_expansion = on_browser(expand_one)
        events.append({"event": "reply_expand_test", "note_id": note["note_id"], **reply_expansion})

    scrolls = []
    if before.get("scrollables"):
        def scroll_note_area(pixels):
            xhs_page.bring_to_front()
            scroller = xhs_page.locator(".note-scroller")
            previous = scroller.evaluate("el => el.scrollTop")
            box = scroller.bounding_box()
            if box:
                xhs_page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
                xhs_page.mouse.wheel(0, pixels)
                xhs_page.wait_for_timeout(1100)
            current = scroller.evaluate("el => el.scrollTop")
            after_outline = dom_outline(xhs_page)
            roots = xhs_page.locator(COMMENT_SELECTORS.item).count()
            subs = xhs_page.locator(REPLY_SELECTORS.item).count()
            return {"pixels": pixels, "scroll_top_before": previous, "scroll_top_after": current,
                    "root_dom_count": roots, "reply_dom_count": subs,
                    "page_scroll_height": next((x["scrollHeight"] for x in after_outline.get("scrollables", []) if x.get("cls")=="note-scroller"), None)}
        for pixels in (650, 650, 650):
            result = on_browser(lambda pixels=pixels: scroll_note_area(pixels))
            scrolls.append(result)
            events.append({"event": "normal_comment_area_scroll", "note_id": note["note_id"], **result})
            if result["scroll_top_after"] == result["scroll_top_before"]:
                break

    root_limit = 9 if expand_replies else 10
    captured_at = now_local_iso()
    def get_rows():
        rows = extract_comments(xhs_page, note["note_id"], captured_at, COMMENT_SELECTORS, limit=root_limit, comment_level=1)
        replies = extract_comments(xhs_page, note["note_id"], captured_at, REPLY_SELECTORS, limit=1 if expand_replies else 0, comment_level=2)
        return rows, replies
    roots, replies = on_browser(get_rows)
    for root in roots:
        root["local_comment_id"] = local_comment_id(root)
    for reply in replies:
        parent_text = reply.pop("parent_comment_text", None)
        parent_time = reply.pop("parent_displayed_time", None)
        parent = next((r for r in roots if r["comment_text"] == parent_text and r.get("displayed_time") == parent_time), None)
        reply["parent_comment_id"] = parent.get("local_comment_id") if parent else None
    for row in roots:
        row.pop("parent_comment_text", None)
        row.pop("parent_displayed_time", None)
        row["batch_id"] = batch_id
        row["like_count_normalized"] = parse_display_count(row.get("like_count_raw"))
    for row in replies:
        row["batch_id"] = batch_id
        row["like_count_normalized"] = parse_display_count(row.get("like_count_raw"))
    rows, duplicate_count = dedupe_in_batch(roots + replies)

    screenshot_file = None
    if save_screenshot and rows:
        def comment_screenshot():
            if visible_login_prompt(xhs_page):
                close_explicit_phone_prompt(xhs_page)
                xhs_page.wait_for_timeout(500)
            if visible_login_prompt(xhs_page):
                return None
            root = xhs_page.locator(COMMENT_SELECTORS.item).first
            root.scroll_into_view_if_needed(timeout=5000)
            path = SCREENSHOTS / f"comment_excerpt_{note['note_id']}.png"
            mask_selectors = [".author-wrapper", ".author", ".comment-picture", ".location", "img", "a.name"]
            masks = [root.locator(selector) for selector in mask_selectors if root.locator(selector).count()]
            root.screenshot(path=str(path), mask=masks, mask_color="#000000", timeout=10000)
            return str(path)
        screenshot_file = on_browser(comment_screenshot)
        if screenshot_file:
            events.append({"event": "masked_comment_screenshot", "note_id": note["note_id"], "file": Path(screenshot_file).name, "commenter_identity_masked": True})
        else:
            events.append({"event": "comment_screenshot_skipped", "note_id": note["note_id"], "reason": "phone_prompt_remained_visible"})

    note_result = {
        "note_id": note["note_id"], "creator_name": note.get("creator_name"), "title": note.get("title"),
        "status": "opened", "page_title_matches_sample": page_result["page_title_matches_sample"],
        "login_prompt_seen": page_result["login_prompt_seen"], "login_prompt_closed_by_visible_x": page_result["login_prompt_closed_by_visible_x"],
        "root_dom_count_initial": next((x["count"] for x in before.get("commentRelatedClassCounts", []) if x.get("selector") == "div.parent-comment"), 0),
        "reply_dom_count_initial": next((x["count"] for x in before.get("commentRelatedClassCounts", []) if x.get("selector") == "div.comment-item.comment-item-sub"), 0),
        "root_dom_count_before_scroll": scrolls[0]["root_dom_count"] if scrolls else next((x["count"] for x in before.get("commentRelatedClassCounts", []) if x.get("selector") == "div.parent-comment"), 0),
        "reply_dom_count_before_scroll": scrolls[0]["reply_dom_count"] if scrolls else next((x["count"] for x in before.get("commentRelatedClassCounts", []) if x.get("selector") == "div.comment-item.comment-item-sub"), 0),
        "root_records_saved": sum(1 for row in rows if row["comment_level"] == 1),
        "reply_records_saved": sum(1 for row in rows if row["comment_level"] == 2),
        "duplicate_records_removed": duplicate_count,
        "reply_expansion": reply_expansion, "scroll_attempts": len(scrolls), "scrolls": scrolls,
        "screenshot": Path(screenshot_file).name if screenshot_file else None,
    }
    events.append({"event": "comment_rows_extracted", "note_id": note["note_id"], "root_count": note_result["root_records_saved"], "reply_count": note_result["reply_records_saved"], "duplicate_count": duplicate_count})
    return rows, note_result, events


def collect_batch(browser: PgyVisibleBrowser, notes: dict[str, dict], *, replace_generated: bool = False) -> dict:
    batch_id = datetime.now().astimezone().strftime("R42-%Y%m%dT%H%M%S%z")
    # The initial page opened by PgyVisibleBrowser is the normal Pgy creator list.
    list_page = browser._page
    all_rows: list[dict] = []
    notes_results: list[dict] = []
    detail_cache: dict[str, Any] = {}
    events: list[dict] = [{"time": now_local_iso(), "event": "batch_start", "batch_id": batch_id, "target_note_count": len(NOTE_IDS)}]
    failures: list[dict] = []
    for index, note_id in enumerate(NOTE_IDS):
        note = notes[note_id]
        try:
            rows, result, subevents = _capture_note(browser, list_page, note, batch_id, True,
                                                   expand_replies=(note_id == "6afc317600000000180288af"), detail_cache=detail_cache)
            if result.get("reason") in {"pgy_overlay_without_safe_close", "creator_not_found_after_wait"}:
                browser._call(lambda: browser._page.wait_for_timeout(1500))
                retry_rows, retry_result, retry_events = _capture_note(browser, list_page, note, batch_id, True,
                                                                       expand_replies=(note_id == "6afc317600000000180288af"), detail_cache=detail_cache)
                subevents.append({"event": "bounded_pgy_ui_retry", "note_id": note_id, "first_reason": result.get("reason"), "retry_status": retry_result.get("status"), "retry_reason": retry_result.get("reason")})
                rows, result = retry_rows, retry_result
                subevents.extend(retry_events)
        except Exception as exc:
            rows = []
            result = {"note_id": note_id, "title": note.get("title"), "creator_name": note.get("creator_name"), "status": "failed", "reason": type(exc).__name__}
            subevents = [{"event": "note_error", "note_id": note_id, "error_type": type(exc).__name__}]
            failures.append({"note_id": note_id, "error_type": type(exc).__name__})
        all_rows.extend(rows)
        notes_results.append(result)
        events.extend({"time": now_local_iso(), **event} for event in subevents)

    # Remove internal enrichment keys and ensure no commenter-identity fields can persist.
    identity_names = {"commenter_name", "commenter_id", "commenter_account_id", "commenter_avatar", "commenter_profile", "author_name", "author_id", "avatar_url", "profile_url"}
    assert all(not (identity_names & row.keys()) for row in all_rows)
    field_names = ["local_comment_id", "batch_id", "note_id", "source", "comment_text", "displayed_time", "like_count_raw", "like_count_normalized", "reply_count_raw", "is_pinned", "comment_level", "parent_comment_id", "capture_time"]
    for row in all_rows:
        row.setdefault("local_comment_id", local_comment_id(row))
        row.setdefault("batch_id", batch_id)
        row.setdefault("like_count_normalized", parse_display_count(row.get("like_count_raw")))
    clean_rows = []
    for row in all_rows:
        clean = {key: row.get(key) for key in field_names}
        if isinstance(clean.get("comment_text"), str):
            clean["comment_text"] = clean["comment_text"].strip()
        clean_rows.append(clean)
    if len({row["local_comment_id"] for row in clean_rows}) != len(clean_rows):
        raise AssertionError("same-batch duplicate comment IDs remain")

    field_quality = {}
    for field in field_names:
        non_empty = sum(row.get(field) is not None and row.get(field) != "" for row in clean_rows)
        field_quality[field] = {"non_empty": non_empty, "rate_percent": round(100 * non_empty / len(clean_rows), 1) if clean_rows else 0.0}
    scroll_records = [scroll for note_result in notes_results for scroll in note_result.get("scrolls", [])]
    root_items_seen = sum(note_result.get("root_dom_count_before_scroll", note_result.get("root_dom_count_initial", 0)) for note_result in notes_results)
    root_records = sum(row["comment_level"] == 1 for row in clean_rows)
    like_raw_count = sum(row.get("like_count_raw") is not None for row in clean_rows)
    like_normalized_count = sum(row.get("like_count_normalized") is not None for row in clean_rows)
    repeatability_rows, repeatability_duplicates = dedupe_in_batch([dict(row) for row in clean_rows] + [dict(row) for row in clean_rows])
    quality = {
        "batch_id": batch_id,
        "target_note_count": len(NOTE_IDS),
        "notes_opened_successfully": sum(x.get("status") == "opened" for x in notes_results),
        "notes_with_comment_dom": sum(x.get("status") == "opened" and x.get("root_dom_count_before_scroll", 0) > 0 for x in notes_results),
        "actual_comment_records": len(clean_rows),
        "root_comment_records": root_records,
        "reply_records": sum(x["comment_level"] == 2 for x in clean_rows),
        "root_dom_items_seen_before_scroll": root_items_seen,
        "root_text_extraction_rate_percent": round(100 * root_records / root_items_seen, 1) if root_items_seen else 0.0,
        "root_dom_items_without_extractable_text": max(0, root_items_seen - root_records),
        "reply_dom_items_seen_initial": sum(x.get("reply_dom_count_initial", 0) for x in notes_results),
        "exact_like_count_conversion": {"raw_count_values": like_raw_count, "normalized_exact_integers": like_normalized_count, "rate_percent_of_raw_values": round(100 * like_normalized_count / like_raw_count, 1) if like_raw_count else 0.0},
        "by_note": notes_results,
        "field_non_empty": field_quality,
        "duplicate_records_removed": sum(x.get("duplicate_records_removed", 0) for x in notes_results),
        "same_batch_local_id_unique": len({row["local_comment_id"] for row in clean_rows}) == len(clean_rows),
        "local_comment_id_scope": "intra_batch_fingerprint_only",
        "cross_batch_identity_limitations": [
            "displayed_time is relative and can change between captures, so the hash may drift",
            "commenter identity is intentionally omitted, so identical text may collide",
        ],
        "end_to_end_repeat_collection_tested": False,
        "commenter_identity_fields_saved": [],
        "failed_notes": failures,
        "total_scroll_attempts": sum(x.get("scroll_attempts", 0) for x in notes_results),
        "scroll_events_with_position_change": sum(float(x.get("scroll_top_after", 0)) != float(x.get("scroll_top_before", 0)) for x in scroll_records),
        "scroll_events_without_position_change": sum(float(x.get("scroll_top_after", 0)) == float(x.get("scroll_top_before", 0)) for x in scroll_records),
        "total_new_root_dom_items_after_scroll": sum(max(0, (x.get("scrolls", [{}])[-1].get("root_dom_count", 0) if x.get("scrolls") else x.get("root_dom_count_initial", 0)) - x.get("root_dom_count_before_scroll", x.get("root_dom_count_initial", 0))) for x in notes_results),
        "reply_expansion_test": {"notes_attempted": sum(bool(x.get("reply_expansion", {}).get("attempted")) for x in notes_results), "notes_with_new_reply_rows": sum(bool(x.get("reply_expansion", {}).get("expanded")) for x in notes_results)},
        "same_batch_repeatability_check": {"input_rows": len(clean_rows) * 2, "output_rows": len(repeatability_rows), "duplicate_rows_removed": repeatability_duplicates, "passed": len(repeatability_rows) == len(clean_rows) and repeatability_duplicates == len(clean_rows)},
    }
    events.append({"time": now_local_iso(), "event": "batch_finished", "batch_id": batch_id, "records": len(clean_rows), "notes_opened": quality["notes_opened_successfully"]})

    _write_jsonl(OUT / "raw_comments.jsonl", clean_rows, replace=replace_generated)
    _write_csv(OUT / "cleaned_comments.csv", clean_rows, field_names, replace=replace_generated)
    dictionary = [
        {"field": "local_comment_id", "type": "string", "source": "本地内容哈希", "meaning": "同批去重标识，不含评论者身份"},
        {"field": "batch_id", "type": "string", "source": "本次任务", "meaning": "采集批次"},
        {"field": "note_id", "type": "string", "source": "蒲公英复制的小红书链接路径", "meaning": "关联笔记"},
        {"field": "source", "type": "string", "source": "页面域名", "meaning": "固定为 xiaohongshu"},
        {"field": "comment_text", "type": "string", "source": "评论正文 DOM", "meaning": "按页面原文保存；仅清理首尾空白"},
        {"field": "displayed_time", "type": "string|null", "source": "评论日期节点", "meaning": "页面显示时间，不换算绝对时间"},
        {"field": "like_count_raw", "type": "string|null", "source": "评论互动区", "meaning": "保留原始显示值，如 10+"},
        {"field": "like_count_normalized", "type": "integer|null", "source": "like_count_raw 派生", "meaning": "仅纯数字转整数，10+ 保持 null"},
        {"field": "reply_count_raw", "type": "string|null", "source": "评论互动区", "meaning": "保留页面显示值"},
        {"field": "is_pinned", "type": "boolean|null", "source": "置顶标记 DOM", "meaning": "只按明确标签判断"},
        {"field": "comment_level", "type": "integer", "source": "DOM 层级", "meaning": "1 一级评论，2 回复"},
        {"field": "parent_comment_id", "type": "string|null", "source": "本地父评论哈希", "meaning": "回复关联一级评论"},
        {"field": "capture_time", "type": "string", "source": "本地时钟", "meaning": "带时区采集时间"},
    ]
    _write_csv(OUT / "field_dictionary.csv", dictionary, ["field", "type", "source", "meaning"], replace=replace_generated)
    _write_json(OUT / "quality_report.json", quality, replace=replace_generated)
    _write_json(OUT / "collection_log.json", events, replace=replace_generated)
    _write_json(OUT / "access_errors.json", failures, replace=replace_generated)

    report_lines = [
        "# R4.2 小红书评论采集验证报告", "",
        f"- 批次：`{batch_id}`", "- 数据源：小红书正常笔记页面；链接由蒲公英笔记卡片的可见复制按钮取得。",
        f"- 笔记目标：{len(NOTE_IDS)}；成功打开：{quality['notes_opened_successfully']}。",
        f"- 实际评论记录：{len(clean_rows)}（一级 {quality['root_comment_records']}，回复 {quality['reply_records']}）；不代表评论区总量。",
        "- 访问方式：Playwright headed Chromium、正常页面点击、可见弹窗 X、评论滚动；未调用私有接口。",
        "- 个人信息处理：评论者昵称、账号、头像、主页均未提取；截图仅截评论行并遮盖 `.author` 区域。",
        "", "## 逐篇结果", "",
        "| note_id | 标题 | 页面打开 | 一级评论 | 回复 | 回复展开 | 滚动 |",
        "|---|---|---:|---:|---:|---|---:|",
    ]
    for item in notes_results:
        report_lines.append(f"| `{item['note_id']}` | {item.get('title','')} | {item.get('status')} | {item.get('root_records_saved',0)} | {item.get('reply_records_saved',0)} | {item.get('reply_expansion',{}).get('expanded','—')} | {item.get('scroll_attempts',0)} 次 |")
    report_lines += ["", "## 字段质量", "", "| 字段 | 非空 | 非空率 |", "|---|---:|---:|"]
    for field, info in field_quality.items():
        report_lines.append(f"| `{field}` | {info['non_empty']}/{len(clean_rows)} | {info['rate_percent']}% |")
    report_lines += ["", "## 验收", "", "- 正常 UI 取得链接并打开笔记、定位 DOM 评论区、保存真实评论：" + ("PASS" if quality["notes_opened_successfully"] == len(NOTE_IDS) and quality["actual_comment_records"] > 0 else "PARTIAL"),
                     "- 评论区滚动出现新增一级评论：" + ("PASS" if quality["total_new_root_dom_items_after_scroll"] > 0 else "PARTIAL；滚动发生但 DOM 记录数未增加"),
                     "- 回复展开：" + ("PASS" if any(x.get("reply_expansion",{}).get("expanded") for x in notes_results) else "PARTIAL；检查了回复展开控件，但本轮未确认新增回复"),
                     "- 范围限制：每篇最多 10 条；采集的是页面可见样本，不等同全量评论。", "",
                     "## DOM、滚动与去重", "",
                     f"- 评论区容器命中：{quality['notes_with_comment_dom']}/{quality['notes_opened_successfully']} 页；一级评论 DOM 候选 {quality['root_dom_items_seen_before_scroll']} 条，正文成功解析 {quality['root_comment_records']} 条（{quality['root_text_extraction_rate_percent']}%）。",
                     f"- 另有 {quality['root_dom_items_without_extractable_text']} 个一级评论 DOM 候选未读到正文文本，因此未保存；原因不作推断。点赞精确数转换 {quality['exact_like_count_conversion']['normalized_exact_integers']}/{quality['exact_like_count_conversion']['raw_count_values']} 条有计数显示的记录成功，`10+` 保留原文并不转成整数。",
                     f"- 滚轮事件 {quality['total_scroll_attempts']} 次，其中滚动位置变化 {quality['scroll_events_with_position_change']} 次、未变化 {quality['scroll_events_without_position_change']} 次；新增一级评论 DOM 行 {quality['total_new_root_dom_items_after_scroll']} 条。",
                     f"- 回复展开控件尝试 {quality['reply_expansion_test']['notes_attempted']} 篇，新增回复行 {quality['reply_expansion_test']['notes_with_new_reply_rows']} 篇；本批保存的 1 条回复已关联父评论本地 ID。",
                     f"- 批内重复验证：将这批真实记录在内存中复制后重新去重，{quality['same_batch_repeatability_check']['input_rows']} 行归并为 {quality['same_batch_repeatability_check']['output_rows']} 行，重复去除 {quality['same_batch_repeatability_check']['duplicate_rows_removed']} 行；未重复访问网页。",
                     "- 重跑边界：这只是同批去重函数的离线重复输入检查，未再次执行浏览器采集；每次启动生成新的批次号，当前采集器不把重跑数据追加到正式数据库。",
                     "- 评论标识限制：`local_comment_id` 是同批本地指纹，不是平台稳定 ID。相对时间变化会导致跨批 ID 漂移；不采集评论者身份也可能使不同用户的相同文本碰撞。暂不允许按该指纹跨批覆盖或认定主体相同。",
                     "", "## CreatorScope 数据库接入评估", "",
                     "正式 SQLite 当前只有 `batches`、`records`、`metric_values`、`tasks` 等通用表，没有 comments 专表，也没有 note 外键。短期可用 `records.record_type=xhs_comment`、`fields_json.note_id` 和 `batch_id` 存放；但数据库级父评论关系、评论主体去重和多批次互动快照需要后续增加 `comments` 主体表与 `comment_snapshots` 表（或等价结构）。本轮未写入正式数据库。",
                     "", "## 截图证据", "",
                     *[f"- `screenshots/{x['screenshot']}`（评论者头像、昵称与地域区域已遮盖）" for x in notes_results if x.get("screenshot")],
                     "", "## 说明", "", "`10+` 等非精确显示值保留原文并将标准化数值记为 null；纯操作标签（如‘赞’、‘回复’）记为缺失。日期保留页面相对时间，不推算绝对日期。评论者昵称、账号、头像与评论地域均未提取；截图遮盖 `.author-wrapper`、头像、图片和地域节点。", ""]
    if (OUT / "comment_collection_report.md").exists() and not replace_generated:
        raise FileExistsError("Refusing to overwrite existing comment_collection_report.md")
    (OUT / "comment_collection_report.md").write_text("\n".join(report_lines), encoding="utf-8")
    return {"batch_id": batch_id, "quality": quality, "report": str(OUT / "comment_collection_report.md"), "records": len(clean_rows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true", help="keep the headed browser open for DOM inspection")
    parser.add_argument("--collect", action="store_true", help="run the bounded 3-note live comment collection")
    parser.add_argument("--replace-generated", action="store_true", help="replace only the named R4.2 outputs after preserving a prior attempt")
    args = parser.parse_args()
    notes = load_notes()
    copied_urls: dict[str, str] = {}
    browser = PgyVisibleBrowser(PROFILE)
    opened = browser.open()
    print(json.dumps({"event": "browser_open", "result": opened}, ensure_ascii=False))
    page = browser._page
    current_note_id = ""
    call = browser._call
    try:
        if args.collect:
            result = collect_batch(browser, notes, replace_generated=args.replace_generated)
            summary = result["quality"]
            print(json.dumps({"event": "batch_complete", "batch_id": result["batch_id"], "notes_opened": summary["notes_opened_successfully"], "records": result["records"], "root_comments": summary["root_comment_records"], "replies": summary["reply_records"], "report": result["report"]}, ensure_ascii=False))
            return 0
        if args.interactive:
            print("Commands: state | tabs | select INDEX | pgy | pgy-ui | dismiss-pgy | creator NAME | note TITLE | copy NOTE_ID | open NOTE_ID | phone-ui | outline | scroll [PIXELS] | dismiss | quit")
            while True:
                try:
                    command = input("r4.2> ").strip().split(maxsplit=1)
                except EOFError:
                    break
                if not command:
                    continue
                action = command[0].lower()
                argument = command[1].strip() if len(command) > 1 else ""
                if action in {"quit", "close"}:
                    break
                if action == "state":
                    print(json.dumps(call(lambda: safe_page_state(page)), ensure_ascii=False))
                elif action == "tabs":
                    print(json.dumps(call(lambda: [{"index": i, **safe_page_state(tab)} for i, tab in enumerate(browser._context.pages)]), ensure_ascii=False))
                elif action == "select":
                    def select_tab():
                        nonlocal page
                        index = int(argument)
                        pages = browser._context.pages
                        if index < 0 or index >= len(pages):
                            return {"selected": False, "page_count": len(pages)}
                        page = pages[index]
                        browser._page = page
                        return {"selected": True, "index": index, **safe_page_state(page)}
                    print(json.dumps(call(select_tab), ensure_ascii=False))
                elif action == "pgy":
                    def open_pgy():
                        page.goto(PGY_START_URL, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(1600)
                        return {"event": "navigate_pgy_list", **safe_page_state(page)}
                    print(json.dumps(call(open_pgy), ensure_ascii=False))
                elif action == "pgy-ui":
                    print(json.dumps(call(lambda: {"event": "pgy_overlay_state", "overlays": pgy_overlay_summary(page), **safe_page_state(page)}), ensure_ascii=False))
                elif action == "dismiss-pgy":
                    print(json.dumps(call(lambda: {"event": "dismiss_pgy_overlay", **dismiss_known_pgy_overlay(page), **safe_page_state(page)}), ensure_ascii=False))
                elif action == "creator":
                    def open_creator():
                        nonlocal page
                        overlays = pgy_overlay_summary(page)
                        if overlays:
                            return {"event": "open_creator", "blocked_by_visible_overlay": True, "overlays": overlays, **safe_page_state(page)}
                        loc = page.get_by_text(argument, exact=True)
                        visible = [loc.nth(i) for i in range(min(loc.count(), 20)) if loc.nth(i).is_visible()]
                        if len(visible) != 1:
                            return {"event": "open_creator", "matched_visible": len(visible), **safe_page_state(page)}
                        before = len(browser._context.pages)
                        visible[0].click(timeout=10000)
                        page.wait_for_timeout(1400)
                        pages = browser._context.pages
                        if len(pages) > before:
                            page = pages[-1]
                            browser._page = page
                        return {"event": "open_creator", "matched_visible": len(visible), "opened_new_tab": len(pages) > before, **safe_page_state(page)}
                    print(json.dumps(call(open_creator), ensure_ascii=False))
                elif action == "note":
                    def open_pgy_note():
                        nonlocal page
                        overlay_actions = []
                        overlays = pgy_overlay_summary(page)
                        if overlays:
                            overlay_actions = dismiss_known_pgy_overlay(page)["dismissed_actions"]
                            page.wait_for_timeout(1000)
                            if pgy_overlay_summary(page):
                                return {"event": "open_pgy_note", "blocked_by_visible_overlay": True, "overlay_actions_dismissed": overlay_actions, **safe_page_state(page)}
                        loc = page.get_by_text(argument, exact=True)
                        visible = [loc.nth(i) for i in range(min(loc.count(), 20)) if loc.nth(i).is_visible()]
                        if not visible:
                            return {"event": "open_pgy_note", "matched_visible": 0, "overlay_actions_dismissed": overlay_actions, **safe_page_state(page)}
                        card = visible[0].locator("xpath=ancestor::div[contains(@class,'note-card-wrapper')][1]")
                        mask = card.locator(".note-card__mask:visible")
                        if card.count() != 1 or mask.count() != 1:
                            return {"event": "open_pgy_note", "matched_visible": len(visible), "card_count": card.count(), "click_surface_count": mask.count(), "overlay_actions_dismissed": overlay_actions, **safe_page_state(page)}
                        before = len(browser._context.pages)
                        try:
                            mask.click(timeout=6000)
                        except Exception as exc:
                            return {"event": "open_pgy_note", "matched_visible": len(visible), "click_failed": type(exc).__name__, "overlay": pgy_overlay_summary(page), "overlay_actions_dismissed": overlay_actions, **safe_page_state(page)}
                        page.wait_for_timeout(1200)
                        pages = browser._context.pages
                        if len(pages) > before:
                            page = pages[-1]
                            browser._page = page
                        return {"event": "open_pgy_note", "matched_visible": len(visible), "opened_new_tab": len(pages) > before, "overlay_actions_dismissed": overlay_actions, **safe_page_state(page)}
                    print(json.dumps(call(open_pgy_note), ensure_ascii=False))
                elif action == "copy":
                    note_id = argument
                    if note_id not in notes:
                        print(json.dumps({"error": "note_id_not_in_R4.1_sample"}))
                        continue
                    def copy_note_link():
                        if urlparse(page.url).hostname != "pgy.xiaohongshu.com":
                            return {"event": "copy_link", "copied": False, "reason": "not_on_Pgy"}
                        loc = page.get_by_text("复制小红书笔记链接", exact=True)
                        if loc.count() != 1 or not loc.first.is_visible():
                            return {"event": "copy_link", "copied": False, "visible_matches": loc.count(), **safe_page_state(page)}
                        loc.click(timeout=10000)
                        clip = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"], capture_output=True, text=True, timeout=5).stdout.strip()
                        parsed = urlparse(clip)
                        query_keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
                        if parsed.hostname != "www.xiaohongshu.com" or not parsed.path.endswith("/" + note_id):
                            return {"event": "copy_link", "copied": False, "reason": "clipboard_link_does_not_match_selected_note", "host": parsed.hostname or "", "path": parsed.path}
                        copied_urls[note_id] = clip
                        return {"event": "copy_link", "copied": True, "note_id": note_id, "host": parsed.hostname, "path": parsed.path, "query_present": bool(parsed.query), "query_keys": query_keys}
                    print(json.dumps(call(copy_note_link), ensure_ascii=False))
                elif action == "open":
                    if argument not in notes:
                        print(json.dumps({"error": "note_id_not_in_R4.1_sample"}))
                        continue
                    current_note_id = argument
                    url = copied_urls.get(current_note_id)
                    if not url:
                        print(json.dumps({"error": "copy_link_from_Pgy_UI_first", "note_id": current_note_id}))
                        continue
                    def open_note():
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(1800)
                        closed = close_explicit_phone_prompt(page)
                        return {"event": "open_note", "note_id": current_note_id, **safe_page_state(page), "phone_prompt_closed_by_UI": closed, "phone_prompt_visible": visible_login_prompt(page)}
                    print(json.dumps(call(open_note), ensure_ascii=False))
                elif action == "outline":
                    result = call(lambda: dom_outline(page))
                    target = OUT / "dom_structure.md"
                    existing = target.read_text(encoding="utf-8") if target.exists() else "# R4.2 Live Comment DOM Structure\n\n"
                    target.write_text(existing + outline_markdown(current_note_id or "unknown", result) + "\n", encoding="utf-8")
                    print(json.dumps({"event": "outline", "note_id": current_note_id, "structure_file": str(target), "class_counts": result.get("commentRelatedClassCounts", []), "scrollables": result.get("scrollables", []), "candidate_row_count": len(result.get("textShapes", []))}, ensure_ascii=False))
                elif action == "scroll":
                    pixels = max(-1200, min(1200, int(argument or "600")))
                    def scroll_page():
                        structures = dom_outline(page)
                        targets = [x for x in structures.get("scrollables", []) if "comment" in x.get("cls", "").lower() or "note-scroller" in x.get("cls", "").lower()]
                        if targets:
                            target = targets[-1]
                            page.mouse.move(target["x"] + max(5, target["w"] // 2), target["y"] + max(5, target["h"] // 2))
                        else:
                            page.mouse.move(max(10, page.viewport_size["width"] - 80), page.viewport_size["height"] - 120)
                        page.mouse.wheel(0, pixels)
                        page.wait_for_timeout(1400)
                        after = dom_outline(page)
                        return {"event": "wheel_scroll", "pixels": pixels, "note_id": current_note_id, "scrollables": after.get("scrollables", []), "candidate_row_count": len(after.get("textShapes", []))}
                    print(json.dumps(call(scroll_page), ensure_ascii=False))
                elif action == "dismiss":
                    def dismiss():
                        closed = close_explicit_phone_prompt(page)
                        return {"event": "dismiss_prompt", "closed": closed, "still_visible": visible_login_prompt(page)}
                    print(json.dumps(call(dismiss), ensure_ascii=False))
                elif action == "phone-ui":
                    def phone_ui():
                        outline = phone_prompt_outline(page)
                        screenshot = None
                        if outline.get("root"):
                            root = outline["root"]
                            if root["w"] > 100 and root["h"] > 100:
                                name = f"phone_prompt_{current_note_id}.png"
                                target = SCREENSHOTS / name
                                viewport = page.evaluate("() => ({width:innerWidth,height:innerHeight})")
                                page.screenshot(path=str(target), clip={"x": root["x"], "y": root["y"], "width": min(root["w"], viewport["width"]-root["x"]), "height": min(root["h"], viewport["height"]-root["y"])})
                                screenshot = str(target)
                        return {"event": "phone_prompt_ui", "note_id": current_note_id, "outline": outline, "screenshot": screenshot}
                    print(json.dumps(call(phone_ui), ensure_ascii=False))
                else:
                    print(json.dumps({"error": "unknown_command"}))
    finally:
        browser.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
