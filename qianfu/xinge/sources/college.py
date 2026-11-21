"""学院公告。

35 个学院的站点结构各不相同，所以不写死解析器，用一套通用规则：

    1. 取出所有 <a>，文本长度 8~80 的当候选标题
    2. 在标题前后 400 字符里找日期
    3. 找到日期才算一条公告（导航链接没有日期，自然被过滤）

JS 渲染的站点用 Playwright 打开再取 DOM。
"""

from __future__ import annotations

import hashlib
import html as htmllib
import json
import os
import re
from datetime import datetime
from urllib.parse import urljoin

import httpx

from ..core import FetchReport, Item, register_source
from .college_sites import COLLEGES

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DATE = re.compile(
    r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})|"
    r"(?<!\d)(\d{1,2})[-/](\d{1,2})(?!\d)|"
    r"(?<!\d)(\d{1,2})月(\d{1,2})日"
)
LINK = re.compile(r'<a[^>]*href="([^"#]+)"[^>]*>([\s\S]{2,900}?)</a>')

SKIP = (
    "首页", "更多", "登录", "English", "返回", "下一页", "上一页", "网站地图",
    "联系我们", "加入收藏", "设为首页", "版权所有", "友情链接",
)


# 有些站把日期拆成「日 + 年-月」两块塞进同一个 <a>：
#   <a href="/notice/1974.html">17 2026-11 标题 查看详情</a>
SPLIT_DATE = re.compile(r"^\s*(\d{1,2})\s+(20\d{2})[-/.](\d{1,2})\s+(.+)$", re.S)


def _clean(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw)
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(match: re.Match) -> datetime | None:
    try:
        if match.group(1):
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        # 只有 月-日 的，补当前年份
        if match.group(4):
            month, day = int(match.group(4)), int(match.group(5))
        else:
            month, day = int(match.group(6)), int(match.group(7))
        today = datetime.now()
        guess = datetime(today.year, month, day)
        if guess > today:
            guess = datetime(today.year - 1, month, day)
        return guess
    except ValueError:
        return None


def extract(html: str, page_url: str, college: dict) -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()

    for m in LINK.finditer(html):
        href, raw = m.group(1), m.group(2)
        title = _clean(raw)
        if not (8 <= len(title) <= 80):
            continue
        if any(k in title for k in SKIP):
            continue
        # 至少 4 个汉字，滤掉 "supports HTML5 video" 这类占位链接
        if len(re.findall(r"[\u4e00-\u9fff]", title)) < 4:
            continue
        if href.startswith(("javascript:", "mailto:", "#")):
            continue

        # 先看标题自己是不是「日 年-月 标题」这种结构
        split = SPLIT_DATE.match(title)
        if split:
            day, year, month, rest = split.groups()
            title = rest.replace("查看详情", "").strip()
            if not (8 <= len(title) <= 80):
                continue
            try:
                published = datetime(int(year), int(month), int(day))
            except ValueError:
                continue
            window = ""
            date_match = None
        else:
            window = html[max(0, m.start() - 700): m.end() + 700]
            date_match = DATE.search(window)
            if not date_match:
                continue
            published = _parse_date(date_match)

        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)

        items.append(
            Item(
                source="college",
                uid=hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                title=title,
                url=url,
                summary="",
                category=college["name"],
                published=published,
                extra={"board": "college", "college": college["name"]},
            )
        )

    return items


def _disabled() -> set[str]:
    """用户关掉的学院（config.json 的 college_enabled）。"""
    path = os.path.join(os.path.expanduser("~"), ".xinge", "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh).get("college_enabled") or {}
    except (OSError, json.JSONDecodeError):
        return set()
    return {k for k, v in m.items() if v is False}


def _fetch_html(client: httpx.Client, url: str) -> str:
    resp = client.get(url)
    resp.raise_for_status()
    return resp.text


def _fetch_browser(url: str) -> str:
    from playwright.sync_api import sync_playwright

    from ..browser import launch

    with sync_playwright() as p:
        browser = launch(p, headless=True)
        try:
            page = browser.new_page()
            # 有的站（环境学院）很慢，45 秒会超时
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(5000)
            # 有的站要往下滚才加载
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            return page.content()
        finally:
            browser.close()


@register_source
class CollegeSource:
    key = "college"
    name = "学院公告"
    auth = None

    def __init__(self, only: list[str] | None = None, use_browser: bool = True) -> None:
        self.only = only
        self.use_browser = use_browser

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)
        errors: list[str] = []

        disabled = _disabled()
        sites = [
            c
            for c in COLLEGES
            if (not self.only or c["key"] in self.only) and c["key"] not in disabled
        ]

        with httpx.Client(
            headers={"User-Agent": UA}, timeout=25, follow_redirects=True
        ) as client:
            for college in sites:
                if college["mode"] == "todo":
                    continue                      # 还没摸出列表页，先跳过
                try:
                    if college["mode"] == "browser":
                        if not self.use_browser:
                            continue
                        html = _fetch_browser(college["url"])
                    else:
                        html = _fetch_html(client, college["url"])
                    found = extract(html, college["url"], college)
                    report.items.extend(found)
                    if not found:
                        errors.append(f"{college['name']}: 0 条")
                except Exception as exc:
                    errors.append(f"{college['name']}: {str(exc)[:40]}")

        if errors:
            report.error = "; ".join(errors[:4])
        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report
