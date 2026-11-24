"""好大学在线 CNMOOC。

抓两类东西：

  1. 站内消息（/message/list-N.mooc）—— 老师发的通知
  2. 课程作业与测试（/examTest/stuExamList/<courseId>.mooc）—— 带截止时间

第 2 类才是真正要盯的：作业和测验的 DDL 会直接铺到日历上。

课程列表接口（ajaxMyCourseIndex）会返回 408，所以课程 id 走配置：

    "cnmooc_courses": [
      {"id": "28535", "name": "电子技术实验"}
    ]

登录态有两套 cookie：

  * jAccount（~/.xinge/session.json）—— 扫码登录时留下的
  * cnmooc 自己的会话（~/.xinge/cnmooc.json）—— 必须带 jAccount cookie
    走一次 /oauth/jacAuth.mooc 的 SSO 才会拿到

所以「自动检测课程」「/jaccount/sso/cnmooc」都走真实浏览器：把 jAccount
cookie 注入 Playwright 上下文，打开 SSO 入口让站点自己完成跳转，再把
context 里 cnmooc 域的 cookie 收回来落盘。httpx 直接访问 cnmooc 主页不会
触发 SSO（只会被丢到 /home/login.mooc），这一步必须真浏览器。
"""

from __future__ import annotations

import html as htmllib
import json
import os
import re
import time
from datetime import date, datetime

import httpx

from ..auth import SessionStore
from ..core import FetchReport, Item, register_source

BASE = "https://cnmooc.sjtu.cn"
MESSAGE_LIST = f"{BASE}/message/list-{{page}}.mooc"
EXAM_LIST = f"{BASE}/examTest/stuExamList/{{cid}}.mooc"

SITE = "cnmooc"
SITE_DOMAIN = "cnmooc.sjtu.cn"
SSO_ENTRY = f"{BASE}/oauth/jacAuth.mooc"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEBUG_DIR = os.path.join(
    os.environ.get("TEMP")
    or os.environ.get("TMP")
    or os.path.join(os.path.expanduser("~"), ".xinge"),
    "cnmooc-debug",
)

# 给上层（/cnmooc/discover、/jaccount/sso/cnmooc）用的可读原因
REASON_TEXT = {
    "ok": "",
    "no_playwright": "运行环境缺少 Playwright",
    "no_browser": "找不到可用的浏览器（Edge / Chrome）",
    "no_login": "好大学在线还没登录，先去「订阅」里点登录",
    "expired": "好大学在线登录已过期，已尝试重新登录，请稍后再试",
    "jaccount_expired": "jAccount 登录已过期，请先在信鸽里重新扫码登录，再点「自动检测课程」",
    "error": "自动检测课程时出错",
}

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


# ---------------- 登录态：cookie 来源 ----------------
#
# cnmooc 的会话 cookie 只有两个来源：
#   1. ~/.xinge/cnmooc.json（浏览器 SSO 之后落盘的，首选）
#   2. ~/.xinge/session.json 的全站 jar（老版本扫码时可能带回来过）
# 判定「真的登录了」看 moocsk：匿名访问只会有 keepalive / moocvk / cpstk /
# JSESSIONID，SSO 成功后站点才会补发 moocsk。


class CnmoocLoginExpired(RuntimeError):
    """会话失效：请求被踢回 /home/login.mooc。"""


def reason_text(reason: str) -> str:
    return REASON_TEXT.get(reason) or REASON_TEXT["error"]


def _result(
    reason: str = "ok",
    detail: str = "",
    courses: list[dict] | None = None,
    cookies: int = 0,
) -> dict:
    return {
        "ok": reason == "ok",
        "reason": reason,
        "error": "" if reason == "ok" else reason_text(reason),
        "detail": detail,
        "courses": list(courses or []),
        "cookies": cookies,
    }


def _session_cookies(session: SessionStore | None) -> dict:
    """从双手共用的 SessionStore jar 里捞 cnmooc 域的 cookie（老路径兜底）。"""
    if session is None:
        return {}
    out: dict[str, str] = {}
    try:
        for cookie in session._jar.jar:
            if SITE_DOMAIN in (cookie.domain or ""):
                out[cookie.name] = cookie.value
    except Exception:  # noqa: BLE001 —— 兜底，绝不能让这里拖垮检测
        return {}
    return out


def load_cookies(session: SessionStore | None = None) -> dict:
    """读 cnmooc 会话 cookie。

    优先 ~/.xinge/cnmooc.json；它里面没有登录标志（moocsk）时，再看
    session jar 里有没有更完整的。
    """
    site_jar: dict = {}
    try:
        from .. import jaccount

        site_jar = dict(jaccount.load_site(SITE) or {})
    except Exception:  # noqa: BLE001
        site_jar = {}

    if site_jar.get("moocsk"):
        return site_jar

    session_jar = _session_cookies(session)
    if session_jar.get("moocsk") or not site_jar:
        return session_jar
    return site_jar


def has_login_state(session: SessionStore | None = None) -> bool:
    return bool(load_cookies(session).get("moocsk"))


def save_cookies(jar: dict) -> None:
    from .. import jaccount

    jaccount.save_site(SITE, jar)


def site_client(session: SessionStore | None = None, timeout: float = 25) -> httpx.Client:
    """带 cnmooc 会话 cookie 的 httpx 客户端（抓消息/作业、读课程名用）。"""
    jar = load_cookies(session)
    client = httpx.Client(
        headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True
    )
    for name, value in jar.items():
        if value:
            client.cookies.set(name, value, domain=SITE_DOMAIN, path="/")
    return client


def _check_logged_in(resp: httpx.Response) -> None:
    """响应被踢回登录页就抛 CnmoocLoginExpired。"""
    url = str(getattr(resp, "url", "") or "")
    if "login.mooc" in url or "jalogin" in url or "/home/login" in url:
        raise CnmoocLoginExpired(url)


# ---------------- 登录态：真实浏览器 SSO ----------------


def _jaccount_cookie_records() -> list[dict]:
    """jAccount cookie 明细（带 path）。

    同名 JSESSIONID 在 /oauth2 和 /jaccount 下各有一个，只传 dict 会互相
    覆盖，所以这里要连 path 一起交给 Playwright。
    """
    try:
        from .. import jaccount

        return jaccount.cookie_records()
    except Exception:  # noqa: BLE001
        return []


def _jar_to_pw_cookies(jar: dict) -> list[dict]:
    return [
        {"name": name, "value": value, "url": f"https://{SITE_DOMAIN}/"}
        for name, value in jar.items()
        if value
    ]


def _is_login_url(url: str) -> bool:
    low = url.lower()
    return "login.mooc" in low or "/home/login" in low


def _is_jaccount_url(url: str) -> bool:
    return "jaccount.sjtu.edu.cn" in url


def _dump_evidence(page, tag: str) -> None:
    """登录失败时留证据：HTML + 截图，方便排查是不是要人工点/验证码。"""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        with open(os.path.join(DEBUG_DIR, f"{tag}.html"), "w", encoding="utf-8") as fh:
            fh.write(page.content())
        page.screenshot(path=os.path.join(DEBUG_DIR, f"{tag}.png"), full_page=True)
    except Exception:  # noqa: BLE001
        pass


def _goto(page, url: str, timeout_ms: int = 45000) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:  # noqa: BLE001 —— 跳转本身可能超时，后面按 URL 判断
        pass


def _wait_url_stable(page, quiet_ms: int = 1200, timeout_ms: int = 15000) -> str:
    """轮询 URL 直到连续 quiet_ms 不再变化（SSO 是一长串 302）。"""
    deadline = time.time() + timeout_ms / 1000
    last = page.url
    quiet_since = time.time()
    while time.time() < deadline:
        try:
            page.wait_for_timeout(400)
            current = page.url
        except Exception:  # noqa: BLE001
            break
        if current != last:
            last = current
            quiet_since = time.time()
        elif (time.time() - quiet_since) * 1000 >= quiet_ms:
            break
    return page.url


def _new_context(browser, jar: dict):
    """新上下文：注入 jAccount cookie +（可选的）cnmooc 站点 cookie。"""
    ctx = browser.new_context(user_agent=UA)
    for record in _jaccount_cookie_records() + _jar_to_pw_cookies(jar):
        try:
            ctx.add_cookies([record])
        except Exception:  # noqa: BLE001 —— 单个坏 cookie 不影响其它
            continue
    return ctx


def _close_ctx(ctx) -> None:
    try:
        ctx.close()
    except Exception:  # noqa: BLE001
        pass


def _read_cards(page, wait_ms: int = 15000) -> dict:
    """从当前页读课程卡，返回 {logged_in, url, cards}。"""
    url = page.url
    if _is_login_url(url) or _is_jaccount_url(url):
        return {"logged_in": False, "url": url, "cards": []}

    cards: list[dict] = []
    try:
        page.wait_for_selector("li.view-item", state="attached", timeout=wait_ms)
        cards = parse_course_cards(page.content())
    except Exception:  # noqa: BLE001
        pass

    if not cards:
        # 卡片是 AJAX 灌进来的，有时会慢半拍
        try:
            page.wait_for_timeout(2500)
            cards = parse_course_cards(page.content())
        except Exception:  # noqa: BLE001
            cards = []

    url = page.url
    if _is_login_url(url) or _is_jaccount_url(url):
        return {"logged_in": False, "url": url, "cards": []}
    if not cards and "myCourseIndex" not in url:
        # 停在首页/其它页面：不能算「课程页登录成功」，让上层再跳一次
        return {"logged_in": False, "url": url, "cards": []}
    return {"logged_in": True, "url": url, "cards": cards}


def _open_courses(page, goto_timeout_ms: int = 15000) -> dict:
    """打开课程页并读卡。

    注意：会话失效时这个请求可能被服务端拖住（实测 45s 都不返回），
    所以 goto 超时给短一点，超时后按当前 URL 判断有没有被踢回登录页。
    """
    _goto(page, MY_COURSES, timeout_ms=goto_timeout_ms)
    return _read_cards(page)


def _finish(ctx, state: dict) -> dict:
    jar_now = _cookies_from_context(ctx)
    if jar_now.get("moocsk"):
        save_cookies(jar_now)          # 落盘，下次抓取直接用
    return _result("ok", "", state["cards"], len(jar_now))


def _probe_with_browser(browser, jar: dict) -> dict:
    # 1) 站点 cookie 带登录标志（moocsk）：先直接开课程页，最快
    if jar.get("moocsk"):
        ctx = _new_context(browser, jar)
        try:
            state = _open_courses(ctx.new_page())
            if state["logged_in"]:
                return _finish(ctx, state)
        finally:
            _close_ctx(ctx)

    # 2) 没有登录标志 / 已失效：换一个只带 jAccount cookie 的干净上下文。
    #    不要把过期的 cnmooc cookie 带进去 —— 老会话会让 cnmooc 把
    #    /portal 和 /oauth 的请求都拖到超时，反而登不上。
    if not _jaccount_cookie_records():
        return _result("no_login", "本机没有 jAccount 登录态，没法自动换 cnmooc 会话")

    ctx = _new_context(browser, {})
    try:
        page = ctx.new_page()
        _goto(page, SSO_ENTRY, timeout_ms=30000)
        url = _wait_url_stable(page)
        if _is_jaccount_url(url):
            _dump_evidence(page, "jaccount-login-required")
            return _result(
                "jaccount_expired",
                f"SSO 停在 jAccount 登录页，需要人工扫码：{url}",
            )

        if _is_login_url(url):
            state = {"logged_in": False, "url": url, "cards": []}
        else:
            state = _read_cards(page, wait_ms=8000)
        if not state["logged_in"]:
            state = _open_courses(page)
        if not state["logged_in"]:
            _dump_evidence(page, "login-expired")
            return _result("expired", f"重新 SSO 后课程页仍要求登录：{state['url']}")
        return _finish(ctx, state)
    finally:
        _close_ctx(ctx)



def _browser_probe(jar: dict) -> dict:
    try:
        from playwright.sync_api import sync_playwright

        from ..browser import launch
    except ImportError:
        return _result("no_playwright", "import playwright 失败")

    try:
        with sync_playwright() as p:
            try:
                browser = launch(p, headless=True)
            except RuntimeError as exc:
                return _result("no_browser", str(exc)[:300])
            try:
                return _probe_with_browser(browser, jar)
            finally:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001 —— 浏览器/网络异常都不能让接口 500
        return _result("error", f"{type(exc).__name__}: {exc}"[:300])


def browser_login(session: SessionStore | None = None) -> dict:
    """走真实浏览器完成 cnmooc 的 JAccount SSO，并把站点 cookie 落盘。

    给 /jaccount/sso/cnmooc 用。返回 {ok, reason, error, detail, cookies}。
    """
    jar = load_cookies(session)
    if not jar and not _jaccount_cookie_records():
        return _result("no_login", "本机既没有 cnmooc cookie，也没有 jAccount 登录态")
    return _browser_probe(jar)


def discover_courses(session: SessionStore | None = None) -> dict:
    """用真实浏览器拿「正在进行」的课程。

    课程列表接口会按客户端指纹拦截（httpx / curl_cffi 都是 408），
    只有真浏览器能过，所以这里用 Playwright + 系统 Edge，不额外下载内核。

    cookie 优先取 ~/.xinge/cnmooc.json；没有/过期时自动用 jAccount 会话
    走一次 SSO。返回 {ok, reason, error, detail, courses, cookies}，
    失败原因用 reason_text() 翻成人话，不要只返回空列表。
    """
    jar = load_cookies(session)
    if not jar and not _jaccount_cookie_records():
        return _result("no_login", "本机既没有 cnmooc cookie，也没有 jAccount 登录态")
    return _browser_probe(jar)


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

        jar = load_cookies(self.session)
        if not jar:
            report.ok = False
            report.error = reason_text("no_login")
            report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
            return report

        try:
            with self.session.client() as client:
                # cnmooc 的会话 cookie 不在全站 jar 里，必须手动塞进来
                for name, value in jar.items():
                    if value:
                        client.cookies.set(name, value, domain=SITE_DOMAIN, path="/")

                self._fetch_messages(client, report)

                courses = load_courses()
                if not courses:
                    # 第一次跑：自动检测一次并写回配置
                    result = discover_courses(self.session)
                    found = result.get("courses") or []
                    if found:
                        save_courses(found)
                        courses = found
                    elif not result.get("ok"):
                        report.ok = False
                        report.error = result.get("error") or reason_text("error")

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
        except CnmoocLoginExpired:
            report.ok = False
            report.error = reason_text("expired")
            report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
            return report
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
            _check_logged_in(resp)
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
        _check_logged_in(resp)
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