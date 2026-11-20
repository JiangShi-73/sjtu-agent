"""Canvas 信息源。

用 /api/v1/planner/items 一次拿到"公告 + 作业 + 日历事件"的聚合流，
比逐个课程轮询省事，也更贴合"通知"场景。
"""

from __future__ import annotations

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
                resp = client.get(f"{BASE}/planner/items", params={"per_page": 50})
                if resp.status_code != 200:
                    report.ok = False
                    report.error = f"HTTP {resp.status_code}"
                    return report
                for raw in resp.json():
                    item = self._to_item(raw)
                    if item:
                        report.items.append(item)
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
        due = raw.get("plannable_date")
        return Item(
            source="canvas",
            uid=f"{kind}:{raw.get('plannable_id')}",
            title=title,
            url=_absolute(raw.get("html_url")),
            category={"announcement": "公告", "assignment": "作业"}.get(kind, kind),
            published=_parse(due),
            extra={"course_id": raw.get("course_id")},
        )


def _absolute(url: str | None) -> str:
    if not url:
        return ""
    return url if url.startswith("http") else f"{HOST}{url}"


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
