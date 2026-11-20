"""物理实验选课系统。

抓两类：

  1. 教学通知（免登录）
  2. 已选实验（需要 JAccount 登录）

已选实验才是要进日历的东西：实验名、日期、时间、教室。
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import datetime

import httpx

from ..auth import SessionStore
from ..core import FetchReport, Item, register_source

BASE = "http://111.186.40.251/pe"
NOTICE = f"{BASE}/noticeb.aspx?ctype=1"
SELECTED = f"{BASE}/student/select.aspx"

RE_ID = re.compile(r"notice\.aspx\?id=(\d+)")
RE_TITLE = re.compile(r'Label6_\d+">(.*?)</span>', re.S)
RE_DATE = re.compile(r"（\s*(\d{4}-\d{2}-\d{2})\s*）")
RE_SUMMARY = re.compile(r'Label4_\d+">(.*?)</span>', re.S)

RE_ROW = re.compile(r'<tr>(?:(?!</tr>).)*?class="forumRow".*?</tr>', re.S)
RE_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
RE_TIME = re.compile(r"(\d{1,2}):(\d{2})")


def _clean(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", htmllib.unescape(raw)).strip()


@register_source
class PeSource:
    key = "pe"
    name = "物理实验"
    auth = "jaccount"

    def __init__(self, session: SessionStore | None = None) -> None:
        self.session = session or SessionStore()

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)
        errors: list[str] = []

        try:
            report.items.extend(self._fetch_notices())
        except httpx.HTTPError as exc:
            errors.append(f"通知: {exc}")

        try:
            report.items.extend(self._fetch_selected())
        except httpx.HTTPError as exc:
            errors.append(f"已选实验: {exc}")

        if errors and not report.items:
            report.ok = False
        if errors:
            report.error = "; ".join(errors[:3])
        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report

    # ---------- 通知（免登录） ----------

    def _fetch_notices(self) -> list[Item]:
        with httpx.Client(follow_redirects=True, timeout=20) as client:
            resp = client.get(NOTICE, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            raise httpx.HTTPError(f"HTTP {resp.status_code}")

        page = resp.text
        ids = RE_ID.findall(page)
        titles = RE_TITLE.findall(page)
        dates = RE_DATE.findall(page)
        summaries = RE_SUMMARY.findall(page)

        items: list[Item] = []
        for i, nid in enumerate(ids):
            items.append(
                Item(
                    source=self.key,
                    uid=f"notice:{nid}",
                    title=_clean(titles[i]) if i < len(titles) else f"通知 {nid}",
                    url=f"{BASE}/notice.aspx?id={nid}",
                    summary=_clean(summaries[i]) if i < len(summaries) else "",
                    category="通知",
                    published=_parse_date(dates[i]) if i < len(dates) else None,
                    extra={"board": "lab"},
                )
            )
        return items

    # ---------- 已选实验（需登录） ----------

    def _fetch_selected(self) -> list[Item]:
        with self.session.client() as client:
            resp = client.get(SELECTED, timeout=20)
            if "pagenotfound" in str(resp.url) or "用户登录" in resp.text[:3000]:
                raise httpx.HTTPError("未登录或会话已失效")
            if resp.status_code != 200:
                raise httpx.HTTPError(f"HTTP {resp.status_code}")

            items: list[Item] = []
            for block in RE_ROW.findall(resp.text):
                cells = [_clean(c) for c in RE_TD.findall(block)]
                if len(cells) < 6 or not cells[1]:
                    continue

                name, weekday_time, date_text, room = cells[1], cells[3], cells[4], cells[5]
                when = _parse_when(date_text, weekday_time)
                if not when:
                    continue

                items.append(
                    Item(
                        source=self.key,
                        uid=f"exp:{date_text}:{name}",
                        title=name,
                        url=SELECTED,
                        summary=f"{weekday_time} · {room}",
                        category="实验",
                        deadline=when,
                        extra={
                            "board": "lab",
                            "location": room,
                            "week": cells[2],
                            "note": cells[8] if len(cells) > 8 else "",
                        },
                    )
                )
            return items


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _parse_when(date_text: str, weekday_time: str) -> datetime | None:
    """'2026/10/06' + '星期二12:30' -> datetime"""
    m = RE_TIME.search(weekday_time)
    try:
        day = datetime.strptime(date_text.replace("/", "-"), "%Y-%m-%d")
    except ValueError:
        return None
    if not m:
        return day
    return day.replace(hour=int(m.group(1)), minute=int(m.group(2)))
