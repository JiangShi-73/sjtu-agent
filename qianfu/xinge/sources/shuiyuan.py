"""水源社区（SJTU BBS）信息源。

抓最新话题进信息流，每个话题一条。
需要先登录——没登录就返回 0 条，不报错，避免拖垮整轮抓取。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from .. import shuiyuan
from ..core import FetchReport, Item, register_source


def _parse(ts: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return None


@register_source
class ShuiyuanSource:
    key = "shuiyuan"
    name = "水源社区"
    auth = "shuiyuan"

    def __init__(self, limit: int = 30) -> None:
        self.limit = limit

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)

        if not shuiyuan.logged_in():
            report.error = "还没登录水源"
            return report

        try:
            topics = shuiyuan.latest(self.limit)
        except shuiyuan.ShuiyuanError as exc:
            report.error = str(exc)[:120]
            return report

        for t in topics:
            tid = t.get("id")
            if not tid:
                continue
            report.items.append(
                Item(
                    source=self.key,
                    uid=hashlib.sha256(str(tid).encode()).hexdigest()[:16],
                    title=t.get("title") or f"话题 {tid}",
                    url=f"{shuiyuan.ORIGIN}/t/{tid}",
                    summary=t.get("excerpt") or "",
                    category="水源",
                    published=_parse(t.get("created_at") or ""),
                    extra={
                        "board": "shuiyuan",
                        "topic_id": tid,
                        "posts": t.get("posts_count"),
                        "views": t.get("views"),
                    },
                )
            )

        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report
