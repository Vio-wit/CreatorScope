from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SHOTS = OUT / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PROJECT_ROOT))
from creatorscope.browser import PgyVisibleBrowser  # noqa: E402

PROFILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "CreatorScope" / "pgy_browser_profile"
browser = PgyVisibleBrowser(PROFILE)
copied_urls: dict[str, str] = {}
events: list[dict] = []


def log(event: str, **details):
    parsed = urlparse(str(details.get("url", "")))
    if parsed.scheme and parsed.hostname:
        details["url"] = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
    events.append({"time_local": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event, **details})
    (OUT / "run_log.json").write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page_state(page):
    parsed = urlparse(page.url)
    title = page.title()
    return {"host": parsed.hostname or "", "path": parsed.path, "title": title, "page_count": len(browser._context.pages)}


def on_page(callback):
    return browser._call(lambda: callback(browser._page))


def phone_login_visible(page) -> bool:
    try:
        return page.get_by_text("手机号登录", exact=True).first.is_visible()
    except Exception:
        try:
            return page.get_by_text("登录", exact=True).first.is_visible()
        except Exception:
            return False


def safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname != "www.xiaohongshu.com" or not parsed.path.startswith("/explore/"):
        raise ValueError("链接不是蒲公英正常复制的小红书笔记链接")
    if parsed.fragment:
        raise ValueError("复制链接包含 fragment；本实验不使用 fragment")
    return url


def public_url_summary(url: str) -> dict:
    parsed = urlparse(url)
    query_keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    return {"url": f"https://{parsed.hostname}{parsed.path}", "query_present": bool(parsed.query),
            "query_keys": query_keys}


def action(command: str, arg: str):
    if command == "quit":
        return False
    if command == "state":
        result = on_page(lambda p: page_state(p))
        print(json.dumps(result, ensure_ascii=False))
    elif command == "tabs":
        def tabs(_):
            result = []
            for index, page in enumerate(browser._context.pages):
                item = page_state(page)
                item["index"] = index
                result.append(item)
            return result
        print(json.dumps(on_page(tabs), ensure_ascii=False))
    elif command == "select":
        def select(_):
            index = int(arg)
            pages = browser._context.pages
            if index < 0 or index >= len(pages):
                return {"selected": False, "count": len(pages)}
            browser._page = pages[index]
            return {"selected": True, "index": index, **page_state(browser._page)}
        print(json.dumps(on_page(select), ensure_ascii=False))
    elif command == "text":
        name = Path(arg).name or "current.txt"
        if not name.endswith(".txt"):
            name += ".txt"
        def save_text(page):
            state = page_state(page)
            if state["host"] == "www.xiaohongshu.com" and phone_login_visible(page):
                return {**state, "saved": False, "reason": "phone_login_modal_visible; body text was not read"}
            text = page.locator("body").inner_text(timeout=15000)
            target = OUT / name
            target.write_text(text, encoding="utf-8")
            log("save_visible_body", url=page.url, file=name, characters=len(text), login_modal=False)
            return {**state, "saved": True, "file": name, "characters": len(text), "preview": text[:1200]}
        print(json.dumps(on_page(save_text), ensure_ascii=False))
    elif command == "shot":
        name = Path(arg).name or (datetime.now().strftime("%H%M%S") + ".png")
        if not name.lower().endswith(".png"):
            name += ".png"
        def screenshot(page):
            target = SHOTS / name
            page.screenshot(path=str(target), full_page=False)
            log("screenshot", url=page.url, file=f"screenshots/{name}")
            return {**page_state(page), "file": f"screenshots/{name}"}
        print(json.dumps(on_page(screenshot), ensure_ascii=False))
    elif command == "buttons":
        def buttons(page):
            loc = page.locator("button:visible, [role='button']:visible")
            values = []
            for i in range(min(loc.count(), 80)):
                el = loc.nth(i)
                try:
                    values.append(el.evaluate("el => { const r=el.getBoundingClientRect(); return {index:0,text:(el.innerText||'').trim(),aria:el.getAttribute('aria-label'),title:el.getAttribute('title'),x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)} }"))
                    values[-1]["index"] = i
                except Exception:
                    continue
            return {**page_state(page), "phone_login_modal": phone_login_visible(page), "buttons": values}
        print(json.dumps(on_page(buttons), ensure_ascii=False))
    elif command == "images":
        def images(page):
            loc = page.locator("img:visible")
            items = []
            for index in range(min(loc.count(), 120)):
                try:
                    item = loc.nth(index).evaluate("""el => { const r=el.getBoundingClientRect(); let n=el.parentElement, chain=[]; for(let j=0;n&&j<6;j++,n=n.parentElement){const b=n.getBoundingClientRect();chain.push({tag:n.tagName.toLowerCase(),cls:String(n.className||'').slice(0,100),text:(n.innerText||'').trim().replace(/\\s+/g,' ').slice(0,120),cursor:getComputedStyle(n).cursor,x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)})} return {alt:el.alt||'',title:el.title||'',x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height),chain} }""")
                    if item["w"] >= 50 and item["h"] >= 50:
                        items.append({"index": index, **item})
                except Exception:
                    continue
            return {**page_state(page), "images": items[:60]}
        print(json.dumps(on_page(images), ensure_ascii=False))
    elif command == "inspect-text":
        def inspect_text(page):
            loc = page.get_by_text(arg, exact=True)
            chains = loc.evaluate_all("""els => els.slice(0,8).map(el => {let n=el,a=[];for(let i=0;n&&i<7;i++,n=n.parentElement){const r=n.getBoundingClientRect();a.push({tag:n.tagName.toLowerCase(),cls:String(n.className||'').slice(0,120),role:n.getAttribute('role'),aria:n.getAttribute('aria-label'),text:(n.innerText||'').trim().replace(/\\s+/g,' ').slice(0,180),cursor:getComputedStyle(n).cursor,x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)})}return a})""")
            return {**page_state(page), "matches": loc.count(), "ancestor_chains": chains}
        print(json.dumps(on_page(inspect_text), ensure_ascii=False))
    elif command == "card-list":
        def card_list(page):
            cards = page.locator(".note-card-wrapper:visible")
            items = []
            for index in range(min(cards.count(), 30)):
                card = cards.nth(index)
                items.append({"index": index, "text": card.inner_text(timeout=5000).strip()[:500],
                              "visible": card.is_visible()})
            return {**page_state(page), "cards": items}
        print(json.dumps(on_page(card_list), ensure_ascii=False))
    elif command == "click-card":
        def click_card(page):
            title = page.get_by_text(arg, exact=True)
            count = title.count()
            target = None
            for index in range(count):
                candidate = title.nth(index)
                if candidate.is_visible():
                    target = candidate
                    break
            if target is None:
                raise RuntimeError("no visible exact note title")
            card = target.locator("xpath=ancestor::div[contains(@class,'note-card-wrapper')][1]")
            mask = card.locator(".note-card__mask:visible")
            if card.count() != 1 or mask.count() != 1:
                raise RuntimeError("note card or visible click surface was not unique")
            before = page.url
            mask.click(timeout=12000)
            page.wait_for_timeout(1200)
            return {"clicked_title": arg, "matches": count, "url_before": before,
                    "card_text": card.inner_text(timeout=5000).strip()[:500], **page_state(page)}
        result = on_page(click_card)
        log("ui_click_note_card_by_title", note_title=arg, **result)
        print(json.dumps(result, ensure_ascii=False))
    elif command == "click-img":
        def click_image(page):
            index = int(arg)
            imgs = page.locator("img:visible")
            count = imgs.count()
            imgs.nth(index).click(timeout=10000)
            return {"clicked_index": index, "visible_images": count, **page_state(page)}
        result = on_page(click_image)
        log("ui_click_visible_image", **result)
        print(json.dumps(result, ensure_ascii=False))
    elif command == "click-text":
        def click_text(page):
            loc = page.get_by_text(arg, exact=True)
            count = loc.count()
            target = None
            for index in range(count):
                candidate = loc.nth(index)
                if candidate.is_visible():
                    target = candidate
                    break
            if target is None:
                raise RuntimeError("no visible exact-text target")
            target.click(timeout=10000)
            return {"matches": count, **page_state(page)}
        result = on_page(click_text)
        log("ui_click_text", text=arg, **result)
        print(json.dumps(result, ensure_ascii=False))
    elif command == "click-role":
        role, sep, label = arg.partition("|")
        if not sep:
            print("Usage: click-role ROLE|NAME")
        else:
            def click_role(page):
                loc = page.get_by_role(role.strip(), name=label.strip(), exact=True)
                count = loc.count()
                loc.first.click(timeout=10000)
                return {"matches": count, **page_state(page)}
            result = on_page(click_role)
            log("ui_click_role", role=role.strip(), label=label.strip(), **result)
            print(json.dumps(result, ensure_ascii=False))
    elif command == "click-point":
        parts = arg.split()
        if len(parts) != 2:
            print("Usage: click-point X Y (only after a fresh screenshot identifies the visible close control)")
        else:
            x, y = map(float, parts)
            result = on_page(lambda p: (p.mouse.click(x, y), page_state(p))[1])
            log("ui_click_point", x=x, y=y, **result)
            print(json.dumps(result, ensure_ascii=False))
    elif command == "dismiss-login":
        def dismiss(page):
            if not phone_login_visible(page):
                return {**page_state(page), "dismissed": False, "reason": "phone login modal not detected"}
            locator = page.locator("button:visible, [role='button']:visible")
            candidates = []
            for index in range(min(locator.count(), 100)):
                el = locator.nth(index)
                try:
                    info = el.evaluate("el => ({text:(el.innerText||'').trim(),aria:el.getAttribute('aria-label')||'',title:el.getAttribute('title')||''})")
                    if re.search(r"关闭|close", " ".join(info.values()), re.I):
                        candidates.append(index)
                except Exception:
                    continue
            if len(candidates) != 1:
                return {**page_state(page), "dismissed": False, "reason": "close control was not uniquely identified", "candidate_count": len(candidates)}
            locator.nth(candidates[0]).click(timeout=5000)
            return {**page_state(page), "dismissed": True, "phone_login_modal_after_click": phone_login_visible(page)}
        result = on_page(dismiss)
        log("dismiss_login_modal", **result)
        print(json.dumps(result, ensure_ascii=False))
    elif command == "copy-link":
        captured_url = None
        key = arg or f"link_{len(copied_urls)+1}"
        def copy_link(page):
            nonlocal captured_url
            if urlparse(page.url).hostname != "pgy.xiaohongshu.com":
                return {"copied": False, "reason": "not on Pgy page", **page_state(page)}
            loc = page.get_by_text("复制小红书笔记链接", exact=True)
            count = loc.count()
            if count != 1:
                return {"copied": False, "reason": "copy control not unique", "matches": count, **page_state(page)}
            loc.click(timeout=10000)
            clip = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                                  capture_output=True, text=True, timeout=5).stdout.strip()
            url = safe_url(clip)
            captured_url = url
            return {"copied": True, **public_url_summary(url), **page_state(page)}
        result = on_page(copy_link)
        if result.get("copied"):
            copied_urls[key] = captured_url
            result["link_key"] = key
            log("pgy_ui_copy_link", link_key=key, url=result["url"], title=result.get("title"))
        print(json.dumps(result, ensure_ascii=False))
    elif command == "open-link":
        if arg not in copied_urls:
            print(json.dumps({"opened": False, "reason": "key was not obtained through the Pgy copy control in this session"}, ensure_ascii=False))
        else:
            url = copied_urls[arg]
            def open_tab(_):
                tab = browser._context.new_page()
                browser._page = tab
                try:
                    tab.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as exc:
                    return {"opened": True, "navigation_error": type(exc).__name__, "url": url, **page_state(tab)}
                return {"opened": True, "url": url, **page_state(tab)}
            result = on_page(open_tab)
            if "url" in result:
                result.update(public_url_summary(url))
            log("open_copied_link_new_tab", link_key=arg, **result)
            print(json.dumps(result, ensure_ascii=False))
    elif command == "open-current":
        if arg not in copied_urls:
            print(json.dumps({"opened": False, "reason": "key was not obtained through the Pgy copy control in this session"}, ensure_ascii=False))
        else:
            url = copied_urls[arg]
            def open_current(_):
                page = browser._page
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    return {"opened": True, **public_url_summary(url), **page_state(page)}
                except Exception as exc:
                    return {"opened": True, "navigation_error": type(exc).__name__,
                            **public_url_summary(url), **page_state(page)}
            result = on_page(open_current)
            log("open_copied_link_current_tab", link_key=arg, **result)
            print(json.dumps(result, ensure_ascii=False))
    elif command == "close-tab":
        def close_tab(_):
            index = int(arg)
            pages = browser._context.pages
            if index < 0 or index >= len(pages):
                return {"closed": False, "reason": "tab index out of range", "page_count": len(pages)}
            closing = pages[index]
            was_active = closing is browser._page
            closing.close()
            remaining = browser._context.pages
            if was_active and remaining:
                browser._page = remaining[min(index, len(remaining) - 1)]
            return {"closed": True, "closed_index": index, "active_was_closed": was_active,
                    "remaining_tabs": [page_state(page) for page in remaining]}
        result = on_page(close_tab)
        log("close_one_tab", **result)
        print(json.dumps(result, ensure_ascii=False))
    elif command == "scroll":
        pixels = int(arg or "650")
        result = on_page(lambda p: (p.mouse.wheel(0, pixels), page_state(p))[1])
        log("normal_page_scroll", pixels=pixels, **result)
        print(json.dumps(result, ensure_ascii=False))
    elif command == "wait":
        ms = min(10000, max(0, int(arg or "1000")))
        result = on_page(lambda p: (p.wait_for_timeout(ms), page_state(p))[1])
        print(json.dumps({"waited_ms": ms, **result}, ensure_ascii=False))
    elif command == "close":
        print(json.dumps(browser.close(), ensure_ascii=False))
        browser.shutdown()
        return False
    else:
        print("Commands: state, tabs, select INDEX, text FILE, shot FILE, buttons, images, inspect-text TEXT, card-list, click-card TITLE, click-img INDEX, click-text TEXT, click-role ROLE|NAME, click-point X Y, dismiss-login, copy-link KEY, open-link KEY, open-current KEY, close-tab INDEX, scroll PIXELS, wait MS, close")
    return True


try:
    print(json.dumps({"event": "open", **browser.open()}, ensure_ascii=False))
    while True:
        try:
            line = input("probe> ").strip()
        except EOFError:
            break
        if not line:
            continue
        command, _, argument = line.partition(" ")
        try:
            if not action(command.lower(), argument.strip()):
                break
        except Exception as exc:
            log("action_error", command=command.lower(), error_type=type(exc).__name__)
            print(json.dumps({"event": "action_error", "command": command.lower(),
                              "error_type": type(exc).__name__,
                              "message": str(exc).splitlines()[0][:180]}, ensure_ascii=False))
finally:
    try:
        browser.shutdown()
    except Exception:
        pass
