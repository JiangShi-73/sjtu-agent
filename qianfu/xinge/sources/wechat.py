"""微信公众号单篇文章。

公众号没有公开的文章列表接口（历史消息要微信客户端会话），
所以这里按「文章链接」订阅：给一个 mp.weixin.qq.com/s/... 就抓一篇。

从文章页能稳定拿到：
    公众号名   <a id="js_name">
    标题       <meta property="og:title">
    发布时间   var ct = <unix 时间戳>
    __biz      var biz = "Mzk4ODI2Mzk4MA=="   ← 同号文章的标识
    gh_ id     var user_name = "gh_xxx"
正文在 <div id="js_content"> 里。

历史消息接口 /mp/profile_ext?action=getmsg 会返回 no session，
没有微信客户端会话就拿不到列表——这就是为什么这里只能按链接订阅。
"""

from __future__ import annotations

import html as htmllib
import json
import os
import re
from datetime import datetime

import httpx

from ..core import FetchReport, Item, register_source

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".xinge", "config.json")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

RE_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')
RE_ACCOUNT = re.compile(r'<a[^>]*id="js_name"[^>]*>\s*([^<]+)', re.S)
RE_BIZ = re.compile(r'var biz = "([^"]*)"')
RE_GH = re.compile(r'var user_name = "([^"]*)"')
RE_CT = re.compile(r'var ct = "?(\d+)"?')
RE_SN = re.compile(r'var sn = "([^"]*)"')
RE_CONTENT = re.compile(r'<div class="rich_media_content[^"]*"[^>]*id="js_content"[^>]*>(.*?)</div>\s*<script', re.S)


def load_urls() -> list[str]:
    if not os.path.exists(CONFIG_PATH):
        return []
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh).get("wechat_articles") or []
    except (OSError, json.JSONDecodeError):
        return []


def _text(raw: str) -> str:
    raw = re.sub(r"<[^>]+>", " ", raw or "")
    raw = htmllib.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def parse(html: str, url: str) -> Item | None:
    title = RE_TITLE.search(html)
    if not title:
        return None

    account = RE_ACCOUNT.search(html)
    biz = RE_BIZ.search(html)
    gh = RE_GH.search(html)
    ct = RE_CT.search(html)
    sn = RE_SN.search(html)
    body = RE_CONTENT.search(html)

    published = None
    if ct:
        try:
            published = datetime.fromtimestamp(int(ct.group(1)))
        except (ValueError, OSError):
            published = None

    uid = sn.group(1) if sn else (url.rsplit("/", 1)[-1][:40])

    return Item(
        source="wechat",
        uid=f"wx:{uid}",
        title=htmllib.unescape(title.group(1)).strip(),
        url=url,
        summary=_text(body.group(1))[:240] if body else "",
        category=account.group(1).strip() if account else "公众号",
        published=published,
        extra={
            "board": "wechat",
            "account": account.group(1).strip() if account else "",
            "biz": biz.group(1) if biz else "",
            "gh": gh.group(1) if gh else "",
        },
    )


@register_source
class WechatArticleSource:
    key = "wechat"
    name = "公众号文章"
    auth = None

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)
        urls = load_urls()
        if not urls:
            report.ok = False
            report.error = "还没有添加公众号文章链接"
            return report

        errors: list[str] = []
        with httpx.Client(follow_redirects=True, timeout=30,
                          headers={"User-Agent": UA}) as client:
            for url in urls:
                try:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        errors.append(f"{url[-20:]}: HTTP {resp.status_code}")
                        continue
                    item = parse(resp.text, url)
                    if item:
                        report.items.append(item)
                    else:
                        errors.append(f"{url[-20:]}: 解析不到标题")
                except httpx.HTTPError as exc:
                    errors.append(f"{url[-20:]}: {exc}")

        if errors:
            report.error = "; ".join(errors[:3])
        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report
