"""知途好课（sjtu.aihaoke.net）。

这个平台不是 jAccount 直连，而是自己的 passport：
    /api/auth/unified/toAuth?type=unified  ->  jAccount SSO  ->  loginByTicket
登录成功后浏览器会带两个东西：
    cookie: haoke-token
    header: Authorization: Bearer <JWT>

拿到这两个之后就能用普通 HTTP 请求了，不用每次开浏览器。
凭据缓存在 ~/.xinge/aihaoke.json，过期了再用浏览器换一次。
"""

from __future__ import annotations

import base64
import json
import os
import time

import httpx

from ..auth import SessionStore
from ..core import FetchReport, Item, register_source

HOST = "https://sjtu.aihaoke.net"
SSO = f"{HOST}/api/auth/unified/toAuth?type=unified&deviceType=1"
CACHE_FILE = os.path.join(os.path.expanduser("~"), ".xinge", "aihaoke.json")


# ---------------- 凭据 ----------------

def _read() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if data.get("exp", 0) < time.time() + 600:
        return {}
    return data


def _write(token: str, cookies: dict) -> None:
    exp = 0
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
    except (IndexError, ValueError):
        pass
    if not exp:
        # 这个 JWT 没有 exp 字段，给个保守的 12 小时
        exp = time.time() + 12 * 3600
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"token": token, "cookies": cookies, "exp": exp}, fh)


def _from_browser(session: SessionStore) -> dict:
    """开一次浏览器走 SSO，把 token 和 cookie 抄下来。"""
    try:
        from playwright.sync_api import sync_playwright

        from ..browser import launch
    except ImportError:
        return {}

    cookies = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}
        for c in session._jar.jar
        if "jaccount" in c.domain
    ]

    captured: dict = {}

    def on_request(req) -> None:
        if "/api/teach/instance/listMyClass" in req.url and "token" not in captured:
            auth = req.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                captured["token"] = auth[7:]

    with sync_playwright() as p:
        try:
            browser = launch(p, headless=True)
        except RuntimeError:
            return {}
        try:
            ctx = browser.new_context()
            if cookies:
                ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.on("request", on_request)
            page.goto(SSO, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(7000)
            jar = {
                c["name"]: c["value"]
                for c in ctx.cookies()
                if "aihaoke" in c["domain"]
            }
        except Exception:
            return {}
        finally:
            browser.close()

    if not captured.get("token"):
        return {}
    _write(captured["token"], jar)
    return {"token": captured["token"], "cookies": jar}


def credentials(session: SessionStore, refresh: bool = False) -> dict:
    if not refresh:
        hit = _read()
        if hit:
            return hit
    return _from_browser(session)


# ---------------- 接口 ----------------

def _post(creds: dict, path: str, payload: dict) -> dict:
    import uuid

    body = {"requestId": str(uuid.uuid4()), **payload}
    resp = httpx.post(
        HOST + path,
        json=body,
        headers={"Authorization": "Bearer " + creds["token"]},
        cookies=creds.get("cookies") or {},
        timeout=25,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_tasks(creds: dict, instance_id, class_id) -> list[dict]:
    """某门课的学习任务（含作业，带截止时间）。"""
    try:
        data = _post(
            creds,
            "/api/learn/task/listTask",
            {
                "instanceId": instance_id,
                "classId": class_id,
                "page": {"pageNo": 1, "pageSize": 100},
            },
        )
    except (httpx.HTTPError, ValueError):
        return []
    return ((data.get("data") or {}).get("rowList")) or []


def fetch_courses(session: SessionStore) -> list[dict]:
    """我的课程。"""
    creds = credentials(session)
    if not creds:
        return []

    try:
        data = _post(creds, "/api/teach/instance/listMyClass",
                     {"instanceName": "", "classId": 0})
    except (httpx.HTTPError, ValueError):
        creds = credentials(session, refresh=True)
        if not creds:
            return []
        try:
            data = _post(creds, "/api/teach/instance/listMyClass",
                         {"instanceName": "", "classId": 0})
        except (httpx.HTTPError, ValueError):
            return []

    rows = ((data.get("data") or {}).get("teachClassResponseList")) or []
    out = []
    for row in rows:
        out.append(
            {
                "instance_id": row.get("instanceId"),
                "class_id": row.get("classId"),
                "name": row.get("instanceName") or "",
                "class_name": row.get("className") or "",
                "teacher": row.get("lecturerName") or row.get("tutorName") or "",
                "cover": row.get("coverUrl") or "",
                "intro": (row.get("introduction") or "").strip(),
                "finished": bool(row.get("finishFlag")),
                "continuing": bool(row.get("continueFlag")),
                "url": f"{HOST}/student/course",
            }
        )
    return out


def _parse_dt(value: str):
    from datetime import datetime

    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return None if dt.year >= 9000 else dt          # 9999 表示"无截止"


@register_source
class AihaokeSource:
    key = "aihaoke"
    name = "知途好课"
    auth = "jaccount"

    def __init__(self, session: SessionStore | None = None) -> None:
        self.session = session or SessionStore()

    def fetch(self) -> FetchReport:
        from datetime import datetime

        started = datetime.now()
        report_ts = started
        report = FetchReport(source=self.key)

        creds = credentials(self.session)
        if not creds:
            report.ok = False
            report.error = "知途好课登录失败（需要浏览器走一次 SSO）"
            return report

        for course in fetch_courses(self.session):
            if course["finished"]:
                continue                            # 只抓进行中的课
            tasks = fetch_tasks(creds, course["instance_id"], course["class_id"])
            for t in tasks:
                deadline = _parse_dt(t.get("endTime"))
                if not deadline:
                    continue                        # 没有截止时间的不是作业
                if deadline < datetime.now():
                    continue                        # 过期的不进信息流
                report.items.append(
                    Item(
                        source=self.key,
                        uid=f"task:{t.get('taskId')}",
                        title=f"{course['name']} · {t.get('taskName')}",
                        url=course["url"],
                        summary=course.get("teacher") or "",
                        category="作业",
                        deadline=deadline,
                        extra={
                            "board": "zhitu",
                            "course": course["name"],
                            "teacher": course["teacher"],
                            "done": bool(t.get("doneFlag")),
                            "task_type": t.get("taskType"),
                            "status": t.get("myStatus"),
                        },
                    )
                )

        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report
