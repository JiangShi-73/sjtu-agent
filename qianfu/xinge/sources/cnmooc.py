"""好大学在线 CNMOOC。

抓两类东西：

  1. 站内消息（/message/list-N.mooc）—— 老师发的通知
  2. 课程作业与测试（/examTest/stuExamList/<courseId>.mooc）—— 带截止时间

第 2 类才是真正要盯的：作业和测验的 DDL 会直接铺到日历上。

课程列表接口（ajaxMyCourseIndex）会返回 408，所以课程 id 走配置：

    "cnmooc_courses": [
      {"id": "28535", "name": "电子技术实验"}
    ]
"""

from __future__ import annotations

import html as htmllib
import json
import os
import re
from datetime import date, datetime

import httpx

from ..auth import SessionStore
from ..core import FetchReport, Item, register_source

BASE = "https://cnmooc.sjtu.cn"
MESSAGE_LIST = f"{BASE}/message/list-{{page}}.mooc"
EXAM_LIST = f"{BASE}/examTest/stuExamList/{{cid}}.mooc"

RE_ITEM = re.compile(r'<li class="view-item">(.*?)</li>', re.S)
RE_ID = re.compile(r"messageid=(\d+)")
RE_TITLE_ATTR = re.compile(r'class="view-title-text[^"]*"\s+title="([^"]*)"')
RE_TITLE_TEXT = re.compile(r'class="view-title-text[^"]*"[^>]*>(.*?)</span>', re.S)
RE_TIME = re.compile(r'class="view-time">.*?</i>\s*([\d\-: ]+)', re.S)
RE_ABSTRACT = re.compile(r'class="view-abstract email-abstract"[^>]*>(.*?)</div>', re.S)
RE_FROM = re.compile(r'class="link-action message-from"[^>]*>(.*?)</a>', re.S)

RE_CHAPTER = re.compile(r'<tr class="tr-chapter[^"]*">\s*<td[^>]*>\s*([^<]+)', re.S)
RE_ROW = re.compile(r'<td class="td1"[^>]*>\s*([^<]+?)\s*</td>(.*?)</tr>', re.S)
RE_DEADLINE = re.compile(r"截止：\s*([\d]{4}-[\d]{2}-[\d]{2}\s+[\d]{2}:[\d]{2})")
RE_ITEM_ID = re.compile(r'itemId="(\d+)"')
RE_COURSE_OPEN = re.compile(r'id="courseOpenId" value="(\d+)"')
RE_COURSE_TITLE = re.compile(r'<h3 class="model-title[^"]*"[^>]*title="([^"]+)"')


def _clean(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", htmllib.unescape(raw)).strip()


def load_courses() -> list[dict]:
    path = os.path.join(os.path.expanduser("~"), ".xinge", "config.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("cnmooc_courses") or []
    except (OSError, json.JSONDecodeError):
        return []


@register_source
class CnmoocSource:
    key = "cnmooc"
    name = "好大学在线 · 消息与作业"
    auth = "jaccount"

    def __init__(self, session: SessionStore, pages: int = 2) -> None:
        self.session = session
        self.pages = pages

    # ---------- 抓取 ----------

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)
        errors: list[str] = []

        try:
            with self.session.client() as client:
                self._fetch_messages(client, report)
                for course in load_courses():
                    if course.get("enabled") is False:
                        continue
                    cid = str(course.get("id", "")).strip()
                    if not cid.isdigit():
                        continue
                    try:
                        self._fetch_exams(client, cid, course.get("name") or cid, report)
                    except httpx.HTTPError as exc:
                        errors.append(f"{course.get('name') or cid}: {exc}")
        except httpx.HTTPError as exc:
            report.ok = False
            report.error = str(exc)
            return report

        if errors:
            report.error = "; ".join(errors[:3])
        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report

    def _fetch_messages(self, client: httpx.Client, report: FetchReport) -> None:
        for page in range(1, self.pages + 1):
            resp = client.get(MESSAGE_LIST.format(page=page), timeout=20)
            if resp.status_code != 200:
                break
            items = self._parse_messages(resp.text)
            if not items:
                break
            report.items.extend(items)

    def _fetch_exams(
        self, client: httpx.Client, course_id: str, course_name: str, report: FetchReport
    ) -> None:
        resp = client.get(EXAM_LIST.format(cid=course_id), timeout=25)
        if resp.status_code != 200:
            raise httpx.HTTPError(f"HTTP {resp.status_code}")

        # 页面标题才是课程的正式名（配置里的可能只是简称）
        m = RE_COURSE_TITLE.search(resp.text)
        if m:
            course_name = _clean(m.group(1))

        report.items.extend(self._parse_exams(resp.text, course_id, course_name))

    # ---------- 解析 ----------

    def _parse_messages(self, page: str) -> list[Item]:
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
                    uid="msg:" + mid.group(1),
                    title=_clean(title.group(1)),
                    url=f"{BASE}/message/list-1.mooc",
                    summary=_clean(abstract.group(1)) if abstract else "",
                    category="站内消息",
                    published=_parse_dt(when.group(1)) if when else None,
                    extra={"board": "mooc", "from": _clean(sender.group(1)) if sender else ""},
                )
            )
        return items

    def _parse_exams(self, page: str, course_id: str, course_name: str) -> list[Item]:
        """作业/测验。

        两条过滤规则：
          1. 没有"截止"时间的不是日程，丢掉
          2. 已经过期的也丢掉——这样"课程是否还在进行"等价于"还有没有未截止的作业"
        """
        # 章节按出现顺序对齐到后面的行
        chapters = [(m.start(), _clean(m.group(1))) for m in RE_CHAPTER.finditer(page)]

        items: list[Item] = []
        for m in RE_ROW.finditer(page):
            title = _clean(m.group(1))
            rest = m.group(2)
            dl = RE_DEADLINE.search(rest)
            if not dl:
                continue
            item_id = RE_ITEM_ID.search(rest)
            if not item_id:
                continue

            chapter = ""
            for pos, name in chapters:
                if pos < m.start():
                    chapter = name

            deadline = _parse_dt(dl.group(1))
            if not deadline or deadline.date() < date.today():
                continue

            items.append(
                Item(
                    source=self.key,
                    uid=f"exam:{course_id}:{item_id.group(1)}",
                    title=f"{course_name} · {title}",
                    url=EXAM_LIST.format(cid=course_id),
                    summary=chapter,
                    category="作业/测验",
                    deadline=deadline,
                    extra={
                        "board": "mooc",
                        "course": course_name,
                        "chapter": chapter,
                        "submitted": "未提交" not in rest,
                    },
                )
            )
        return items


def _parse_dt(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
