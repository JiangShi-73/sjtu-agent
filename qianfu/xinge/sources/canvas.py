"""Canvas 信息源。

用 /api/v1/planner/items 拿到"公告 + 作业"的聚合流，比逐个课程轮询省事，
也更贴合"通知"场景。

再按 Canvas 网页日历的调用方式补一层 /api/v1/calendar_events：
老师在「日历」里排的课程事件（上课、讲座、课程安排等）都在那里。
注意 calendar_events **必须显式带 context_codes[]**（自己 + 在读课程 + 小组），
否则只会返回用户自己的事件，课程事件一条也拿不到。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import httpx

from ..auth import SessionStore
from ..core import FetchReport, Item, register_source

HOST = "https://oc.sjtu.edu.cn"
BASE = f"{HOST}/api/v1"

# 课程日历事件往后抓 60 天，同时补最近 7 天（和前端日历默认范围一致）
CALENDAR_PAST_DAYS = 7
CALENDAR_FUTURE_DAYS = 60


@register_source
class CanvasSource:
    key = "canvas"
    name = "Canvas 课程平台"
    auth = "jaccount"

    def __init__(self, session: SessionStore, token: str | None = None) -> None:
        self.session = session
        self.token = token

    def _client(self) -> httpx.Client:
        client = self.session.client()
        if self.token:
            client.headers["Authorization"] = f"Bearer {self.token}"
        return client

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)
        try:
            with self._client() as client:
                # 翻页：一页 50 条不够，近两个月的作业会漏掉
                self._fetch_planner(client, report)
                # 日历事件是锦上添花：它挂了也不能把作业/公告一起拖下水
                if report.ok:
                    self._fetch_calendar_events(client, report)
        except httpx.HTTPError as exc:
            report.ok = False
            report.error = str(exc)
        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report

    # ---------- 公告 / 作业 ----------

    def _fetch_planner(self, client: httpx.Client, report: FetchReport) -> None:
        url = f"{BASE}/planner/items"
        params: dict | None = {"per_page": 100}
        for _ in range(6):
            resp = client.get(url, params=params)
            if resp.status_code != 200:
                report.ok = False
                report.error = f"HTTP {resp.status_code}"
                return
            for raw in resp.json():
                item = self._to_item(raw)
                if item:
                    report.items.append(item)

            url = _next_link(resp)
            if not url:
                return
            params = None

    # ---------- 课程日历事件 ----------

    def _fetch_calendar_events(self, client: httpx.Client, report: FetchReport) -> None:
        """拉课程日历事件（type=event），归一化成「课程事件」条目。"""
        try:
            contexts, course_names = self._calendar_contexts(client)

            today = date.today()
            params: list[tuple[str, str]] | None = [
                ("type", "event"),
                ("start_date", (today - timedelta(days=CALENDAR_PAST_DAYS)).isoformat()),
                ("end_date", (today + timedelta(days=CALENDAR_FUTURE_DAYS)).isoformat()),
                ("per_page", "100"),
            ]
            params += [("context_codes[]", code) for code in contexts]

            url = f"{BASE}/calendar_events"
            for _ in range(6):
                resp = client.get(url, params=params)
                if resp.status_code != 200:
                    # 作业已经拿到了，这里失败只在诊断里留个记号，不判整个源失败
                    report.error = f"日历事件 HTTP {resp.status_code}"
                    return
                data = resp.json()
                if not isinstance(data, list):
                    return
                for raw in data:
                    item = self._to_calendar_item(raw, course_names)
                    if item:
                        report.items.append(item)

                url = _next_link(resp)
                if not url:
                    return
                params = None
        except Exception:                       # noqa: BLE001
            # 日历接口不管出什么怪问题（接口变更/脏数据），都不能拖垮作业和公告
            report.error = report.error or "日历事件拉取失败"

    def _calendar_contexts(self, client: httpx.Client) -> tuple[list[str], dict[str, str]]:
        """拼网页日历会带的 context_codes[]：自己 + 在读课程 + 小组。

        Returns:
            (context code 列表, context_code -> 课程名)
        """
        contexts: list[str] = []
        course_names: dict[str, str] = {}

        me = client.get(f"{BASE}/users/self")
        if me.status_code == 200:
            uid = (me.json() or {}).get("id")
            if uid:
                contexts.append(f"user_{uid}")

        courses = client.get(
            f"{BASE}/courses",
            params={"enrollment_state": "active", "per_page": 100},
        )
        if courses.status_code == 200:
            for course in courses.json() or []:
                cid = course.get("id")
                if cid is None:
                    continue
                code = f"course_{cid}"
                contexts.append(code)
                course_names[code] = (
                    course.get("name") or course.get("course_code") or ""
                )

        groups = client.get(f"{BASE}/users/self/groups", params={"per_page": 100})
        if groups.status_code == 200:
            for group in groups.json() or []:
                gid = group.get("id")
                if gid is not None:
                    contexts.append(f"group_{gid}")

        return contexts, course_names

    @staticmethod
    def _to_calendar_item(raw: dict, course_names: dict[str, str]) -> Item | None:
        """把 calendar_events 的返回体（type=event）转成条目。"""
        title = (raw.get("title") or "").strip()
        event_id = raw.get("id")
        if not title or event_id is None:
            return None

        all_day = bool(raw.get("all_day"))
        start = _parse(raw.get("all_day_date")) if all_day else None
        if start is None:
            start = _parse(raw.get("start_at"))
        if start is None:
            return None
        end = _parse(raw.get("all_day_date")) if all_day else _parse(raw.get("end_at"))
        if all_day:
            # 全天事件只关心日期，别让 UTC 的 00:00 变成北京时间 08:00
            start = start.replace(hour=0, minute=0)
            end = end.replace(hour=23, minute=59) if end else start.replace(hour=23, minute=59)

        context_code = raw.get("context_code") or raw.get("effective_context_code") or ""
        course_id = _context_id(context_code) if context_code.startswith("course_") else None
        course_name = raw.get("context_name") or course_names.get(context_code, "")

        return Item(
            source="canvas",
            uid=f"cal:{event_id}",
            title=title,
            url=_absolute(raw.get("html_url")),
            category="课程事件",
            published=start,
            deadline=None,   # 课程事件不是作业，没有截止时间
            extra={
                "cal_start": _fmt(start),
                "cal_end": _fmt(end) if end else "",
                "location": raw.get("location_name") or "",
                "all_day": all_day,
                "course_id": course_id,
                "course_name": course_name,
            },
        )

    # ---------- 通用 ----------

    @staticmethod
    def _to_item(raw: dict) -> Item | None:
        plannable = raw.get("plannable") or {}
        title = plannable.get("title") or plannable.get("name")
        if not title:
            return None
        kind = raw.get("plannable_type") or "unknown"
        if kind == "calendar_event":
            # 课程日历事件由 _fetch_calendar_events 统一抓，这里跳过避免重复
            return None
        when = raw.get("plannable_date")

        # 作业要带 deadline，日历才能自动铺上去；
        # 公告只有发布时间，不当作截止时间。
        due_at = plannable.get("due_at") if kind == "assignment" else None

        return Item(
            source="canvas",
            uid=f"{kind}:{raw.get('plannable_id')}",
            title=title,
            url=_absolute(raw.get("html_url")),
            category={"announcement": "公告", "assignment": "作业"}.get(kind, kind),
            published=_parse(when),
            deadline=_parse(due_at),
            extra={
                "course_id": raw.get("course_id"),
                # 课程名（Canvas 的 context_name），右边栏按它分组
                "course_name": raw.get("context_name") or "",
                "submitted": bool((raw.get("submissions") or {}).get("submitted")),
            },
        )


def _next_link(resp: httpx.Response) -> str:
    """Canvas 用 Link 头分页：<...?page=2>; rel="next"。"""
    link = resp.headers.get("link", "")
    m = re.search(r'<([^>]+)>;\s*rel="next"', link)
    return m.group(1) if m else ""


def _absolute(url: str | None) -> str:
    if not url:
        return ""
    return url if url.startswith("http") else f"{HOST}{url}"


def _context_id(code: str) -> int | None:
    """course_93140 -> 93140。"""
    try:
        return int(code.split("_", 1)[1])
    except (IndexError, ValueError):
        return None


def _fmt(value: datetime | None) -> str:
    """本机时间存成 "YYYY-MM-DD HH:MM"，和 db 里其它时间字段保持一致。"""
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _parse(value: str | None) -> datetime | None:
    """Canvas 给的是 UTC（带 Z），要换成北京时间，否则时间会差 8 小时。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)   # UTC -> 本机时区
    return dt
