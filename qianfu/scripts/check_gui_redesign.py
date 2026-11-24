"""Smoke-test the current GUI in isolated Chromium; no app server is started.

From qianfu: python -m pip install "playwright>=1.48"
             python -m playwright install chromium
             python scripts/check_gui_redesign.py

Generated artifacts (not source files): build/gui-preview/*.png.
"""

import mimetypes
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"
PREVIEWS = ROOT / "build" / "gui-preview"
HOST = "qianfu-preview.test"
ENTRY = f"http://{HOST}/static/index.html"
API = "/api/v1"

UNREAD = "图书馆春季开放安排"
READ = "校园班车时刻表更新"
CLASS = "数据可视化导论"
PLAN = "小组方案讨论"
ITEMS = [
    {"id": 101, "title": UNREAD, "source": "rss", "board": "news", "read": False,
     "category": "校园公告", "summary": "各阅览室本周按春季时段开放。",
     "published": "2026-03-16T08:30:00"},
    {"id": 102, "title": READ, "source": "rss", "board": "news", "read": True,
     "category": "校园公告", "summary": "工作日晚间增加一班返程车。",
     "published": "2026-03-15T14:00:00"},
    {"id": 103, "title": "交互设计练习：界面走查", "source": "canvas", "board": "course",
     "read": False, "category": "作业", "summary": "提交一份界面走查记录。",
     "published": "2026-03-14T10:00:00", "deadline": "2026-03-20T18:00:00"},
]
EVENTS = [
    {"id": 201, "title": CLASS, "source": "jwc", "event_date": "2026-03-16",
     "start_time": "10:00", "end_time": "11:30", "location": "东上院 101"},
    {"id": 202, "title": PLAN, "source": "manual", "event_date": "2026-03-19",
     "start_time": "15:00", "end_time": "16:00", "location": "图书馆研讨间"},
    {"id": 203, "title": "设计读书会", "source": "manual", "event_date": "2026-03-25",
     "start_time": "18:30", "end_time": "19:30", "location": "学生活动中心"},
]


def mock_routes(context, logged_in, issues):
    """Fulfill static files and read-only fixtures; never forward a request."""
    replies = {
        "/auth/status": {"logged_in": logged_in},
        "/boards": [
            {"key": "news", "name": "校园公告", "unread": 1},
            {"key": "course", "name": "课程作业", "unread": 1},
        ],
        "/jaccount/status": {"session": logged_in, "sites": []},
        "/setup/quick": {"ready": True},
        "/canvas/homework": {
            "total": 1, "groups": [{"course": "交互设计", "count": 1, "items": [ITEMS[2]]}],
        },
    }
    requests = []

    def handle(route):
        request = route.request
        url = urlsplit(request.url)
        if (url.scheme, url.netloc) == ("http", HOST) and request.method == "GET":
            if url.path.startswith(API + "/"):
                endpoint = url.path.removeprefix(API)
                requests.append(endpoint)
                query = parse_qs(url.query)
                if endpoint == "/items":
                    board = query.get("board", [None])[0]
                    unread = query.get("unread") == ["true"]
                    route.fulfill(json={"items": [
                        item for item in ITEMS if (board is None or board == item["board"])
                        and (not unread or not item["read"])
                    ]})
                    return
                if endpoint == "/events":
                    start = query.get("start", [""])[0]
                    end = query.get("end", ["9999-12-31"])[0]
                    route.fulfill(json={"events": [
                        event for event in EVENTS if start <= event["event_date"] <= end
                    ], "derived": []})
                    return
                if endpoint in replies:
                    route.fulfill(json=replies[endpoint])
                    return
            if url.path.startswith("/static/"):
                asset = (WEB / unquote(url.path.removeprefix("/static/"))).resolve()
                if asset.is_relative_to(WEB.resolve()) and asset.is_file():
                    content_type = ("text/javascript" if asset.suffix in {".js", ".mjs"}
                                    else mimetypes.guess_type(asset.name)[0])
                    route.fulfill(path=asset, content_type=content_type or "application/octet-stream")
                    return
        if url.netloc == HOST and url.path == "/favicon.ico" and request.method == "GET":
            route.fulfill(status=204)
            return
        issues.append(f"Unmocked request: {request.method} {request.url}")
        route.abort()

    def handle_ws(socket):
        # A mock connection sends no events and never opens an upstream socket.
        if socket.url != f"ws://{HOST}/ws":
            issues.append(f"Unmocked WebSocket: {socket.url}")
            socket.close()

    context.route("**/*", handle)
    context.route_web_socket("**/*", handle_ws)
    return requests


def check_layout(page, selector, width, height):
    """Check viewport size and horizontal fit without prescribing a layout."""
    expect(page.locator(selector)).to_be_visible()
    metrics = page.locator(selector).evaluate("""element => {
        const {left, right} = element.getBoundingClientRect();
        return {width: innerWidth, height: innerHeight,
                scrollWidth: document.documentElement.scrollWidth, left, right};
    }""")
    assert (metrics["width"], metrics["height"]) == (width, height), metrics
    assert metrics["scrollWidth"] <= width + 1, f"{selector}: horizontal page overflow: {metrics}"
    assert metrics["left"] >= -1 and metrics["right"] <= width + 1, f"{selector}: clipped: {metrics}"


def smoke(browser, width, height, logged_in):
    context = browser.new_context(viewport={"width": width, "height": height},
                                  device_scale_factor=1, locale="zh-CN",
                                  timezone_id="Asia/Shanghai", service_workers="block")
    issues = []
    shots = {}
    size = f"{width}x{height}"
    state = "signed-in" if logged_in else "signed-out"
    try:
        requests = mock_routes(context, logged_in, issues)
        page = context.new_page()
        page.on("pageerror", lambda error: issues.append(f"JavaScript error: {error}"))
        page.clock.set_fixed_time("2026-03-16T10:00:00+08:00")
        page.goto(ENTRY, wait_until="load")

        def capture(name, selector):
            check_layout(page, selector, width, height)
            shots[f"{name}-{size}.png"] = page.screenshot(animations="disabled")

        if logged_in:
            feed = page.locator("#feed-view")
            items = page.locator("#items")
            unread = items.get_by_text(UNREAD, exact=True)
            read = items.get_by_text(READ, exact=True)
            expect(page.locator("#login-page")).to_be_hidden()
            expect(unread).to_be_visible()
            expect(read).to_be_visible()
            expect(page.locator("#rail-label")).to_have_text("2026 年 3 月")
            capture("feed", "#feed-view")

            unread_label = page.locator("label").filter(has=page.locator("#only-unread"))
            unread_label.click()
            expect(unread).to_be_visible()
            expect(read).to_have_count(0)
            unread_label.click()
            expect(read).to_be_visible()

            page.locator("#rail-expand").click()
            calendar = page.locator("#calendar-view")
            expect(calendar).to_be_visible()
            expect(feed).to_be_hidden()
            grid = page.locator("#big-grid")
            lesson = grid.get_by_text(CLASS, exact=False)
            plan = grid.get_by_text(PLAN, exact=False)
            expect(lesson).to_be_visible()
            expect(plan).to_be_visible()
            capture("calendar", "#calendar-view")

            page.locator("#cal-show-class").click()
            expect(lesson).to_have_count(0)
            expect(plan).to_be_visible()
            page.locator("#cal-show-class").click()
            expect(lesson).to_be_visible()
            page.locator("#cal-next").click()
            expect(page.locator("#cal-label")).to_have_text("2026 年 4 月")
            expect(lesson).to_have_count(0)
            page.locator("#cal-prev").click()
            expect(lesson).to_be_visible()
            page.locator("#cal-close").click()
            expect(calendar).to_be_hidden()
            expect(unread).to_be_visible()
        else:
            expect(page.locator("#login-title")).to_be_visible()
            login = page.locator("#login-go")
            expect(login).to_be_in_viewport()
            login.click(trial=True)  # Actionable, but do not start even a mock QR flow.
            expect(page.locator("#login-qr-box")).to_be_hidden()
            capture("signed-out", "#login-page")
            assert "/items" not in requests, "Signed-out page requested the feed"

        assert "/auth/status" in requests, "The mock auth state was not requested"
        assert not issues, "\n".join(issues)
        print(f"PASS {state} {size}")
        return shots
    except Exception as error:
        detail = "\n".join(issues)
        raise RuntimeError(f"{state} {size} failed: {error}\n{detail}") from error
    finally:
        context.close()


def main():
    if not (WEB / "index.html").is_file():
        raise SystemExit(f"Frontend entry not found: {WEB / 'index.html'}")
    shots = {}
    with sync_playwright() as playwright:
        import os

        channel = os.environ.get("XINGE_TEST_BROWSER_CHANNEL")
        browser = playwright.chromium.launch(**({"channel": channel} if channel else {}))
        try:
            for width, height in [(1440, 900), (900, 600)]:
                for logged_in in (True, False):
                    shots.update(smoke(browser, width, height, logged_in))
        finally:
            browser.close()

    PREVIEWS.mkdir(parents=True, exist_ok=True)
    for name, image in shots.items():
        path = PREVIEWS / name
        path.write_bytes(image)
        print(f"ARTIFACT {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
