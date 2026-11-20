"""物理实验选课系统 · 通知。

列表页与详情页都**不需要登录**，是这个项目里最省事的一个源。
"""

from __future__ import annotations

import html
import re
from datetime import datetime

import httpx

from ..core import FetchReport, Item, register_source

BASE = "http://111.186.40.251/pe"
LIST = f"{BASE}/noticeb.aspx?ctype=1"

RE_ID = re.compile(r"notice\.aspx\?id=(\d+)")
RE_TITLE = re.compile(r'Label6_\d+">(.*?)</span>', re.S)
RE_DATE = re.compile(r"（\s*(\d{4}-\d{2}-\d{2})\s*）")
RE_SUMMARY = re.compile(r'Label4_\d+">(.*?)</span>', re.S)


def _clean(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


@register_source
class PeNoticeSource:
    key = "pe"
    name = "物理实验通知"
    auth = None

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)
        try:
            with httpx.Client(follow_redirects=True, timeout=20) as client:
                resp = client.get(LIST, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                report.ok = False
                report.error = f"HTTP {resp.status_code}"
                return report

            page = resp.text
            ids = RE_ID.findall(page)
            titles = RE_TITLE.findall(page)
            dates = RE_DATE.findall(page)
            summaries = RE_SUMMARY.findall(page)

            for i, nid in enumerate(ids):
                report.items.append(
                    Item(
                        source=self.key,
                        uid=nid,
                        title=_clean(titles[i]) if i < len(titles) else f"通知 {nid}",
                        url=f"{BASE}/notice.aspx?id={nid}",
                        summary=_clean(summaries[i]) if i < len(summaries) else "",
                        category="通知",
                        published=_parse(dates[i]) if i < len(dates) else None,
                    )
                )
        except httpx.HTTPError as exc:
            report.ok = False
            report.error = str(exc)
        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report


def _parse(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
