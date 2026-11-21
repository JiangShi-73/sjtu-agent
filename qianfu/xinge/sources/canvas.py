"""Canvas 信息源。

用 /api/v1/planner/items 一次拿到"公告 + 作业 + 日历事件"的聚合流，
比逐个课程轮询省事，也更贴合"通知"场景。
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from ..auth import SessionStore
from ..core import FetchReport, Item, register_source

HOST = "https://oc.sjtu.edu.cn"
BASE = f"{HOST}/api/v1"


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
                url = f"{BASE}/planner/items"
                params = {"per_page": 100}
                for _ in range(6):
                    resp = client.get(url, params=params)
                    if resp.status_code != 200:
                        report.ok = False
                        report.error = f"HTTP {resp.status_code}"
                        return report
                    for raw in resp.json():
                        item = self._to_item(raw)
                        if item:
                            report.items.append(item)

                    url = _next_link(resp)
                    if not url:
                        break
                    params = None
        except httpx.HTTPError as exc:
            report.ok = False
            report.error = str(exc)
        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report

    @staticmethod
    def _to_item(raw: dict) -> Item | None:
        plannable = raw.get("plannable") or {}
        title = plannable.get("title") or plannable.get("name")
        if not title:
            return None
        kind = raw.get("plannable_type") or "unknown"
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
