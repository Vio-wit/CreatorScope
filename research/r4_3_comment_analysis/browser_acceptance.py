from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:8766"


def main() -> int:
    errors: list[str] = []
    responses: list[dict[str, object]] = []
    results: dict[str, object] = {"started_at": datetime.now().astimezone().isoformat(timespec="seconds"), "base_url": BASE_URL}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: responses.append({"url": response.url.split("?")[0], "status": response.status}) if response.status >= 400 else None)
        home_response = page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector('a[href="/comments"]', timeout=15000)
        results["main_dashboard_status"] = home_response.status if home_response else None
        page.locator('a[href="/comments"]').first.click()
        page.wait_for_url("**/comments")
        response = page.goto(f"{BASE_URL}/comments", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_function("document.querySelector('#commentCount')?.textContent === '29'", timeout=30000)
        page.wait_for_function("document.querySelector('#commentWordcloud')?.naturalWidth > 0", timeout=30000)
        page.wait_for_timeout(700)
        results["page_status"] = response.status if response else None
        results["title"] = page.title()
        results["initial_comment_count"] = page.locator("#commentCount").inner_text()
        results["initial_table_rows"] = page.locator("#commentRows tr").count()
        results["initial_chart_series"] = page.evaluate("""() => {
          const instance = echarts.getInstanceByDom(document.querySelector('#noteCountChart'));
          return instance.getOption().series[0].data;
        }""")
        page.screenshot(path=str(ROOT / "screenshots" / "comment_dashboard.png"), full_page=True)

        note_id = page.locator("#noteFilter option").nth(1).get_attribute("value")
        note_response = page.request.get(
            f"{BASE_URL}/api/comments/analysis", params={"note_id": note_id}
        )
        expected_note_count = str(note_response.json()["summary"]["comment_count"])
        page.select_option("#noteFilter", value=note_id)
        page.wait_for_function(
            "expected => document.querySelector('#commentCount')?.textContent === expected",
            arg=expected_note_count,
            timeout=15000,
        )
        page.wait_for_function(
            "noteId => document.querySelector('#commentWordcloud')?.src.includes(noteId) && document.querySelector('#commentWordcloud')?.complete && document.querySelector('#commentWordcloud')?.naturalWidth > 0",
            arg=note_id,
            timeout=15000,
        )
        results["note_filter_count"] = page.locator("#commentCount").inner_text()
        results["note_filter_rows"] = page.locator("#commentRows tr").count()
        results["filtered_wordcloud_loaded"] = page.locator("#commentWordcloud").evaluate("image => image.complete && image.naturalWidth > 0")
        results["note_filter_chart"] = page.evaluate("""() => {
          const instance = echarts.getInstanceByDom(document.querySelector('#noteCountChart'));
          return instance.getOption().series[0].data;
        }""")
        page.screenshot(path=str(ROOT / "screenshots" / "comment_dashboard_note_filter.png"), full_page=True)

        page.select_option("#noteFilter", value="")
        keyword_response = page.request.get(
            f"{BASE_URL}/api/comments/analysis", params={"q": "链接"}
        )
        expected_keyword_count = str(keyword_response.json()["summary"]["comment_count"])
        page.locator("#keywordFilter").fill("链接")
        page.locator("#applyFilters").click()
        page.wait_for_function(
            "expected => document.querySelector('#commentCount')?.textContent === expected",
            arg=expected_keyword_count,
            timeout=15000,
        )
        results["keyword_filter_count"] = page.locator("#commentCount").inner_text()

        page.locator("#keywordFilter").fill("")
        reply_response = page.request.get(
            f"{BASE_URL}/api/comments/analysis", params={"comment_level": "2"}
        )
        expected_reply_count = str(reply_response.json()["summary"]["comment_count"])
        page.select_option("#levelFilter", value="2")
        page.wait_for_function(
            "expected => document.querySelector('#commentCount')?.textContent === expected",
            arg=expected_reply_count,
            timeout=15000,
        )
        results["reply_filter_count"] = page.locator("#commentCount").inner_text()
        results["reply_row_level"] = page.locator("#commentRows tr").first.inner_text()

        page.locator("#clearFilters").click()
        page.wait_for_function("document.querySelector('#commentCount')?.textContent === '29'", timeout=15000)
        results["cleared_filter_count"] = page.locator("#commentCount").inner_text()
        results["console_errors"] = errors
        results["http_errors"] = responses
        results["charts_rendered"] = page.locator("#topWordsChart canvas").count() > 0 and page.locator("#noteCountChart canvas").count() > 0
        browser.close()

    results["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    results["status"] = "PASS" if results["page_status"] == 200 and results["initial_comment_count"] == "29" and results["note_filter_count"] == expected_note_count and results["keyword_filter_count"] == expected_keyword_count and results["reply_filter_count"] == expected_reply_count and not errors and not responses else "FAIL"
    log_path = ROOT / "reports" / "browser_acceptance.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=True))
    return 0 if results["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
