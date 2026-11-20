"""好大学在线 CNMOOC · 站内消息。

消息页是服务端渲染的，需要登录态。每条消息带 messageid，可直接做去重。
"""

from __future__ import annotations

import html
import re
from datetime import datetime

import httpx

from ..auth import SessionStore
from ..core import FetchReport, Item, register_source

BASE = "https://cnmooc.sjtu.cn"
LIST = f"{BASE}/message/list-{{page}}.mooc"

RE_ITEM = re.compile(r'<li class="view-item">(.*?)</li>', re.S)
RE_ID = re.compile(r"messageid=(\d+)")
# 标题里可能含 ">"（例如 "2026春>某作业"），会破坏 [^>]*，
# 所以优先取 title 属性；没有属性时再退回取内部文本。
RE_TITLE_ATTR = re.compile(r'class="view-title-text[^"]*"\s+title="([^"]*)"')
RE_TITLE_TEXT = re.compile(r'class="view-title-text[^"]*"[^>]*>(.*?)</span>', re.S)
RE_TIME = re.compile(r'class="view-time">.*?</i>\s*([\d\-: ]+)', re.S)
RE_ABSTRACT = re.compile(r'class="view-abstract email-abstract"[^>]*>(.*?)</div>', re.S)
RE_FROM = re.compile(r'class="link-action message-from"[^>]*>(.*?)</a>', re.S)


def _clean(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


@register_source
class CnmoocSource:
    key = "cnmooc"
    name = "好大学在线 · 站内消息"
    auth = "jaccount"

    def __init__(self, session: SessionStore, pages: int = 2) -> None:
        self.session = session
        self.pages = pages

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)
        try:
            with self.session.client() as client:
                for page in range(1, self.pages + 1):
                    resp = client.get(LIST.format(page=page), timeout=20)
                    if resp.status_code != 200:
                        break
                    items = self._parse(resp.text)
                    if not items:
                        break
                    report.items.extend(items)
        except httpx.HTTPError as exc:
            report.ok = False
            report.error = str(exc)
        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report

    def _parse(self, page: str) -> list[Item]:
        items: list[Item] = []
        for block in RE_ITEM.findall(page):
            mid = RE_ID.search(block)
            title = RE_TITLE_ATTR.search(block) or RE_TITLE_TEXT.search(block)
            if not mid or not title:
                continue
            when = RE_TIME.search(block)
            abstract = RE_ABSTRACT.search(block)
            sender = RE_FROM.search(block)
            items.append(
                Item(
                    source=self.key,
                    uid=mid.group(1),
                    title=_clean(title.group(1)),
                    url=f"{BASE}/message/list-1.mooc",
                    summary=_clean(abstract.group(1)) if abstract else "",
                    category="站内消息",
                    published=_parse(when.group(1)) if when else None,
                    extra={"from": _clean(sender.group(1)) if sender else ""},
                )
            )
        return items


def _parse(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
