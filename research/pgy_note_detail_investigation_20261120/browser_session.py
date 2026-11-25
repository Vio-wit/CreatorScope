from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from creatorscope.browser import PgyVisibleBrowser  # noqa: E402

OUT = Path(__file__).resolve().parent
SHOTS = OUT / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
PROFILE = Path(__import__("os").environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "CreatorScope" / "pgy_browser_profile"

browser = PgyVisibleBrowser(PROFILE)
try:
    print(json.dumps({"event": "open", **browser.open()}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({"event": "open_error", "type": type(exc).__name__, "message": str(exc)[:240]}, ensure_ascii=False))


def run_on_page(fn):
    return browser._call(lambda: fn(browser._page))


def safe_location(page):
    u = urlparse(page.url)
    return {"host": u.hostname or "", "path": u.path, "title": page.title()}


def list_visible(page, selector, limit=80):
    out = []
    loc = page.locator(selector)
    for i in range(min(loc.count(), limit)):
        item = loc.nth(i)
        try:
            if not item.is_visible():
                continue
            text = " ".join((item.inner_text(timeout=700) or "").split())[:220]
            href = item.get_attribute("href")
            if href:
                parsed = urlparse(href)
                href = (parsed.scheme + "://" if parsed.scheme else "") + (parsed.hostname or "") + parsed.path
            alt = item.get_attribute("alt") or item.get_attribute("title")
            out.append({"text": text, "href": href, "alt": alt})
        except Exception:
            continue
    return out


def command(line):
    name, _, arg = line.partition(" ")
    name = name.lower().strip()
    arg = arg.strip()
    if name in {"q", "quit", "exit"}:
        return False
    if name == "state":
        print(json.dumps(run_on_page(lambda p: safe_location(p)), ensure_ascii=False))
    elif name == "links":
        print(json.dumps(run_on_page(lambda p: list_visible(p, "a:visible")), ensure_ascii=False))
    elif name == "images":
        print(json.dumps(run_on_page(lambda p: list_visible(p, "img:visible")), ensure_ascii=False))
    elif name == "images-more":
        def image_details(p):
            imgs = p.locator("img:visible")
            out = []
            for i in range(imgs.count()):
                el = imgs.nth(i)
                try:
                    item = el.evaluate("""el => { const r=el.getBoundingClientRect(); let n=el.parentElement, a=[]; for(let j=0;n&&j<6;j++,n=n.parentElement){a.push({tag:n.tagName.toLowerCase(),cls:String(n.className||'').slice(0,90),text:(n.innerText||'').trim().replace(/\\s+/g,' ').slice(0,100),cursor:getComputedStyle(n).cursor})} const u=new URL(el.currentSrc||el.src,location.href); return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height),alt:el.alt,cls:String(el.className||'').slice(0,90),src:u.origin+u.pathname,parents:a} }""")
                    if item and item["w"] >= 60 and item["h"] >= 60 and any(any(k in parent["cls"].lower() for k in ("kol", "note", "case", "cover", "blogger", "creator")) for parent in item["parents"]):
                        out.append({"index": i, **item})
                    if len(out) >= 40:
                        break
                except Exception:
                    continue
            return out
        print(json.dumps(run_on_page(image_details), ensure_ascii=False))
    elif name == "pages":
        print(json.dumps(run_on_page(lambda p: [safe_location(pg) for pg in (browser._context.pages if browser._context else [])]), ensure_ascii=False))
    elif name == "switch-page":
        def switch_page(p):
            pages = browser._context.pages if browser._context else []
            idx = int(arg)
            if idx < 0 or idx >= len(pages):
                return {"switched": False, "page_count": len(pages)}
            browser._page = pages[idx]
            return {"switched": True, "index": idx, **safe_location(browser._page)}
        print(json.dumps(run_on_page(switch_page), ensure_ascii=False))
    elif name == "open-copied-link":
        def open_copied(p):
            import subprocess
            copied = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"], capture_output=True, text=True, timeout=5).stdout.strip()
            parsed = urlparse(copied)
            if parsed.hostname != "www.xiaohongshu.com" or not parsed.path.startswith("/explore/"):
                return {"opened": False, "reason": "剪贴板中没有蒲公英复制的笔记链接", **safe_location(p)}
            tab = browser._context.new_page()
            try:
                tab.goto(copied, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            browser._pgy_page = p
            browser._page = tab
            return {"opened": True, "host": parsed.hostname, "path": parsed.path, "query_redacted": True, "title": tab.title()}
        print(json.dumps(run_on_page(open_copied), ensure_ascii=False))
    elif name == "restore-pgy":
        def restore_pgy(p):
            old = getattr(browser, "_pgy_page", None)
            if old is not None and not old.is_closed():
                browser._page = old
            return safe_location(browser._page)
        print(json.dumps(run_on_page(restore_pgy), ensure_ascii=False))
    elif name == "buttons":
        print(json.dumps(run_on_page(lambda p: list_visible(p, "button:visible")), ensure_ascii=False))
    elif name == "hover-text":
        def hover_text(p):
            loc = p.get_by_text(arg, exact=True)
            count = loc.count()
            loc.first.hover(timeout=10000)
            return {"matches": count, **safe_location(p)}
        print(json.dumps(run_on_page(hover_text), ensure_ascii=False))
    elif name == "inspect-text":
        def inspect_text(p):
            loc = p.get_by_text(arg, exact=True)
            count = loc.count()
            details = loc.evaluate_all("""els => els.slice(0, 8).map(el => {
              let node = el, chain = [];
              for (let i = 0; node && i < 6; i++, node = node.parentElement) {
                const r = node.getBoundingClientRect();
                chain.push({tag: node.tagName.toLowerCase(), cls: String(node.className || '').slice(0, 120),
                  role: node.getAttribute('role'), aria: node.getAttribute('aria-label'),
                  text: (node.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 180),
                  visible: !!(r.width && r.height)});
              }
              return chain;
            })""")
            return {"matches": count, "ancestors": details, **safe_location(p)}
        print(json.dumps(run_on_page(inspect_text), ensure_ascii=False))
    elif name == "bounds-text":
        def bounds_text(p):
            loc = p.get_by_text(arg, exact=True)
            boxes = loc.evaluate_all("els => els.slice(0, 8).map(el => { const r=el.getBoundingClientRect(); return {tag:el.tagName.toLowerCase(), x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height), text:(el.innerText||'').trim().slice(0,80)} })")
            return {"matches": loc.count(), "boxes": boxes, "scrollY": p.evaluate("window.scrollY"), "innerHeight": p.evaluate("window.innerHeight"), **safe_location(p)}
        print(json.dumps(run_on_page(bounds_text), ensure_ascii=False))
    elif name == "text":
        def save_text(p):
            text = p.locator("body").inner_text(timeout=10000)
            path = OUT / (datetime.now().strftime("%H%M%S") + "_visible_text.txt")
            path.write_text(text, encoding="utf-8")
            return {**safe_location(p), "chars": len(text), "file": path.name, "preview": text[:5000]}
        print(json.dumps(run_on_page(save_text), ensure_ascii=False))
    elif name == "shot":
        filename = Path(arg or (datetime.now().strftime("%H%M%S") + ".png")).name
        if not filename.lower().endswith(".png"):
            filename += ".png"
        def save_shot(p):
            path = SHOTS / filename
            p.screenshot(path=str(path), full_page=True)
            return {**safe_location(p), "file": str(path)}
        print(json.dumps(run_on_page(save_shot), ensure_ascii=False))
    elif name == "shot-view":
        filename = Path(arg or (datetime.now().strftime("%H%M%S") + "_view.png")).name
        if not filename.lower().endswith(".png"):
            filename += ".png"
        def save_view(p):
            path = SHOTS / filename
            p.screenshot(path=str(path), full_page=False)
            return {**safe_location(p), "file": str(path)}
        print(json.dumps(run_on_page(save_view), ensure_ascii=False))
    elif name == "click-text":
        def click_text(p):
            target = p.get_by_text(arg, exact=True).first
            count = p.get_by_text(arg, exact=True).count()
            target.click(timeout=10000)
            return {"matches": count, **safe_location(p)}
        print(json.dumps(run_on_page(click_text), ensure_ascii=False))
    elif name == "click-text-last":
        def click_text_last(p):
            loc = p.get_by_text(arg, exact=True)
            count = loc.count()
            loc.last.click(timeout=10000)
            return {"matches": count, "selected": "last", **safe_location(p)}
        print(json.dumps(run_on_page(click_text_last), ensure_ascii=False))
    elif name == "fill-account":
        from getpass import getpass
        account = getpass("Account (hidden): ")
        password = getpass("Password (hidden): ")
        def fill_login(p):
            p.get_by_placeholder("邮箱").fill(account, timeout=10000)
            p.get_by_placeholder("密码").fill(password, timeout=10000)
            return {"filled": True, "values_read_or_logged": False, **safe_location(p)}
        try:
            print(json.dumps(run_on_page(fill_login), ensure_ascii=False))
        finally:
            account = ""
            password = ""
    elif name == "click-role":
        role, sep, label = arg.partition("|")
        if not sep:
            print("Usage: click-role ROLE|NAME")
        else:
            def click_role(p):
                loc = p.get_by_role(role.strip(), name=label.strip(), exact=True)
                count = loc.count()
                loc.first.click(timeout=10000)
                return {"matches": count, **safe_location(p)}
            print(json.dumps(run_on_page(click_role), ensure_ascii=False))
    elif name == "click-css":
        def click_css(p):
            loc = p.locator(arg)
            count = loc.count()
            loc.first.click(timeout=10000)
            return {"matches": count, **safe_location(p)}
        print(json.dumps(run_on_page(click_css), ensure_ascii=False))
    elif name == "click-img":
        def click_img(p):
            idx = int(arg)
            imgs = p.locator("img:visible")
            count = imgs.count()
            imgs.nth(idx).click(timeout=10000)
            p.wait_for_timeout(700)
            return {"index": idx, "visible_image_count": count, "pages": [safe_location(pg) for pg in (browser._context.pages if browser._context else [])], **safe_location(p)}
        print(json.dumps(run_on_page(click_img), ensure_ascii=False))
    elif name == "click-point":
        parts = arg.split()
        if len(parts) != 2:
            print("Usage: click-point X Y")
        else:
            x, y = float(parts[0]), float(parts[1])
            print(json.dumps(run_on_page(lambda p: (p.mouse.click(x, y), safe_location(p))[1]), ensure_ascii=False))
    elif name == "scroll":
        parts = arg.split()
        direction = parts[0] if parts else "down"
        amount = int(parts[1]) if len(parts) > 1 else 650
        def scroll(p):
            p.mouse.wheel(0, amount if direction.lower() == "down" else -amount)
            return safe_location(p)
        print(json.dumps(run_on_page(scroll), ensure_ascii=False))
    elif name == "back":
        print(json.dumps(run_on_page(lambda p: (p.go_back(wait_until="domcontentloaded", timeout=15000), safe_location(p))[1]), ensure_ascii=False))
    elif name == "wait":
        ms = int(float(arg or "1") * 1000)
        print(json.dumps(run_on_page(lambda p: (p.wait_for_timeout(min(ms, 10000)), safe_location(p))[1]), ensure_ascii=False))
    else:
        print("Commands: state, links, images, buttons, text, shot [name], click-text TEXT, click-text-last TEXT, fill-account, click-role ROLE|NAME, click-css CSS, click-point X Y, scroll down|up [pixels], back, wait [seconds], quit")
    return True

print("Commands: state, links, images, buttons, text, shot [name], shot-view [name], click-text TEXT, click-text-last TEXT, hover-text TEXT, inspect-text TEXT, bounds-text TEXT, fill-account, click-role ROLE|NAME, click-css CSS, click-point X Y, scroll down|up [pixels], back, wait [seconds], quit")
while True:
    try:
        if not command(input("pgy> ")):
            break
    except EOFError:
        break
    except Exception as exc:
        print(json.dumps({"event": "action_error", "type": type(exc).__name__, "message": str(exc)[:240]}, ensure_ascii=False))

try:
    browser.close()
finally:
    browser.shutdown()
