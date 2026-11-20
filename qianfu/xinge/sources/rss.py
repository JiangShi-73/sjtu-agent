"""通用 RSS / Atom 源。

微信公众号没有官方开放接口，社区方案是先用 WeWe RSS 之类的工具
把公众号转成 RSS，再由这里消费。所以这个源同时承担两件事：

  1. 接任意 RSS（博客、新闻、公告）
  2. 作为公众号的落地通道

订阅列表放在 ~/.xinge/config.json：

    "rss_feeds": [
      {"name": "某公众号", "url": "http://127.0.0.1:4000/feeds/xxx.atom", "board": "wechat"}
    ]
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from ..core import FetchReport, Item, register_source

ATOM = "{http://www.w3.org/2005/Atom}"


def _text(node, *tags: str) -> str:
    for tag in tags:
        child = node.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _strip_html(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", raw or "")
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).replace(tzinfo=None)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[: len(fmt) + 6], fmt).replace(tzinfo=None)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def parse_feed(xml_text: str, feed_name: str, board: str) -> list[Item]:
    root = ET.fromstring(xml_text)
    items: list[Item] = []

    # RSS 2.0
    for node in root.iter("item"):
        link = _text(node, "link", "guid")
        title = _text(node, "title")
        if not title:
            continue
        items.append(
            Item(
                source="rss",
                uid=hashlib.sha256((link or title).encode("utf-8")).hexdigest()[:16],
                title=title,
                url=link,
                summary=_strip_html(_text(node, "description"))[:300],
                category=feed_name,
                published=_parse_date(_text(node, "pubDate")),
                extra={"feed": feed_name, "board": board},
            )
        )

    # Atom
    for node in root.iter(ATOM + "entry"):
        title = _text(node, ATOM + "title")
        link_node = node.find(ATOM + "link")
        link = link_node.get("href", "") if link_node is not None else ""
        if not title:
            continue
        items.append(
            Item(
                source="rss",
                uid=hashlib.sha256((link or title).encode("utf-8")).hexdigest()[:16],
                title=title,
                url=link,
                summary=_strip_html(
                    _text(node, ATOM + "summary", ATOM + "content")
                )[:300],
                category=feed_name,
                published=_parse_date(_text(node, ATOM + "updated", ATOM + "published")),
                extra={"feed": feed_name, "board": board},
            )
        )

    return items


def load_feeds() -> list[dict]:
    """配置优先，环境变量兜底。"""
    feeds: list[dict] = []
    cfg_path = os.path.join(os.path.expanduser("~"), ".xinge", "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                feeds = json.load(fh).get("rss_feeds") or []
        except (OSError, json.JSONDecodeError):
            feeds = []
    if not feeds and os.environ.get("XINGE_RSS"):
        feeds = [{"name": "RSS", "url": os.environ["XINGE_RSS"], "board": "custom"}]
    return [f for f in feeds if f.get("url")]


@register_source
class RssSource:
    key = "rss"
    name = "RSS / 公众号"
    auth = None

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)
        feeds = load_feeds()
        if not feeds:
            report.ok = False
            report.error = "还没有配置 RSS 订阅（config.json 的 rss_feeds）"
            return report

        errors: list[str] = []
        with httpx.Client(follow_redirects=True, timeout=20,
                          headers={"User-Agent": "Mozilla/5.0 (Xinge/1.0)"}) as client:
            for feed in feeds:
                name = feed.get("name") or feed["url"]
                board = feed.get("board") or "custom"
                try:
                    resp = client.get(feed["url"])
                    if resp.status_code != 200:
                        errors.append(f"{name}: HTTP {resp.status_code}")
                        continue
                    report.items.extend(parse_feed(resp.text, name, board))
                except (httpx.HTTPError, ET.ParseError) as exc:
                    errors.append(f"{name}: {exc}")

        if errors and not report.items:
            report.ok = False
            report.error = "; ".join(errors[:3])
        elif errors:
            report.error = "; ".join(errors[:3])      # 部分成功也记下来

        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report
