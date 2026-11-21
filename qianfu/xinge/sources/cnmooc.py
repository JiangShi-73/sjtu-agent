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


CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".xinge", "config.json")
MY_COURSES = f"{BASE}/portal/myCourseIndex/1.mooc"

# 注意：DOM 里属性名会被小写化（ucid），所以用 re.I
RE_CARD = re.compile(
    r'<li[^>]*class="view-item"[^>]*ucId="(\d+)"(.*?)(?=<li[^>]*class="view-item"|$)',
    re.S | re.I,
)
RE_CARD_TITLE = re.compile(r'<h3 class="view-title[^"]*"[^>]*>(.*?)</h3>', re.S)
RE_CARD_ID = re.compile(r"/portal/session/index/(\d+)\.mooc")


def _read_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def load_courses() -> list[dict]:
    return _read_config().get("cnmooc_courses") or []


def save_courses(courses: list[dict]) -> None:
    cfg = _read_config()
    cfg["cnmooc_courses"] = courses
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def parse_course_cards(html: str) -> list[dict]:
    out: list[dict] = []
    for _uc, block in RE_CARD.findall(html):
        cid = RE_CARD_ID.search(block)
        title = RE_CARD_TITLE.search(block)
        if not cid:
            continue
        name = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", title.group(1))).strip() if title else ""
        out.append({"id": cid.group(1), "name": name, "enabled": True})
    return out


def discover_courses(session: SessionStore) -> list[dict]:
    """用真实浏览器拿「正在进行」的课程。

    课程列表接口会按客户端指纹拦截（httpx / curl_cffi 都是 408），
    只有真浏览器能过，所以这里用 Playwright + 系统 Edge，不额外下载内核。
    """
    try:
        from playwright.sync_api import sync_playwright

        from ..browser import launch
    except ImportError:
        return []

    cookies = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}
        for c in session._jar.jar
        if "cnmooc.sjtu.cn" in c.domain
    ]
    if not cookies:
        return []

    with sync_playwright() as p:
        try:
            browser = launch(p, headless=True)
        except RuntimeError:
            return []

        try:
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                )
            )
            ctx.add_cookies(cookies)
            page = ctx.new_page()

            # 直接读渲染后的 DOM：比抓 AJAX 响应稳，页面自己会等接口返回
            for _ in range(2):
                try:
                    page.goto(MY_COURSES, wait_until="domcontentloaded", timeout=45000)
                    # 卡片可能在隐藏的 tab 里，只等挂载不等可见
                    page.wait_for_selector("li.view-item", state="attached", timeout=25000)
                    cards = parse_course_cards(page.content())
                    if cards:
                        return cards
                except Exception:
                    page.wait_for_timeout(3000)
            return []
        finally:
            browser.close()


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

                courses = load_courses()
                if not courses:
                    # 第一次跑：自动检测一次并写回配置
                    found = discover_courses(self.session)
                    if found:
                        save_courses(found)
                        courses = found

                for course in courses:
                    if course.get("enabled") is False:
                        continue
                    cid = str(course.get("id", "")).strip()
                    if not cid.isdigit():
                        continue
                    try:
                        self._fetch_exams(client, cid, course.get("name") or cid, report)
                    except Exception as exc:      # 单门课失败不能拖垮整体
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
        resp = client.get(EXAM_LIST.format(cid=course_id), timeout=15)
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
