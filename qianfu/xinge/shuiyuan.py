"""水源社区（shuiyuan.sjtu.edu.cn）客户端。

水源是 Discourse，接口都是现成的 JSON：

    /latest.json              最新话题
    /categories.json          分类
    /search.json?q=           搜索
    /t/{id}.json              话题详情（含楼层）
    /t/{id}/posts.json        楼层列表

但**必须登录**——未登录一律返回
{"errors":["您需要登录才能执行此操作。"],"error_type":"not_logged_in"}

所以走浏览器登录一次、存 cookie 的路子，和 Canvas / 慕课一致。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

ORIGIN = "https://shuiyuan.sjtu.edu.cn"
COOKIE_PATH = Path(os.path.expanduser("~")) / ".xinge" / "shuiyuan.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ShuiyuanError(RuntimeError):
    pass


def load_cookies() -> dict:
    if not COOKIE_PATH.exists():
        return {}
    try:
        return json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cookies(cookies: dict) -> None:
    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_PATH.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")


def logged_in() -> bool:
    cookies = load_cookies()
    return bool(cookies.get("_t"))


def login(timeout: int = 300) -> dict:
    """开一个真浏览器让用户 jAccount 登录，登录完把 cookie 抄下来。"""
    from playwright.sync_api import sync_playwright

    from .browser import launch

    with sync_playwright() as p:
        browser = launch(p, headless=False)
        try:
            ctx = browser.new_context(viewport={"width": 1200, "height": 820})
            page = ctx.new_page()
            page.goto(ORIGIN, wait_until="domcontentloaded", timeout=45000)

            deadline = time.time() + timeout
            while time.time() < deadline:
                page.wait_for_timeout(2000)
                jar = {c["name"]: c["value"] for c in ctx.cookies() if "shuiyuan" in c["domain"]}
                if jar.get("_t"):
                    save_cookies(jar)
                    return {"ok": True, "cookies": len(jar)}
            return {"ok": False, "error": "等待登录超时"}
        finally:
            browser.close()


def _client() -> httpx.Client:
    cookies = load_cookies()
    if not cookies.get("_t"):
        raise ShuiyuanError("还没登录水源，先在「订阅」里点「登录水源」")
    return httpx.Client(
        headers={"User-Agent": UA, "Accept": "application/json"},
        cookies=cookies,
        timeout=25,
        follow_redirects=True,
    )


def _get(path: str) -> dict:
    with _client() as client:
        resp = client.get(ORIGIN + path)
        if resp.status_code == 403:
            raise ShuiyuanError("登录已失效，重新登录一次")
        resp.raise_for_status()
        return resp.json()


def _slim_topic(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "title": t.get("title") or "",
        "excerpt": (t.get("excerpt") or "").replace("\n", " ")[:160],
        "category_id": t.get("category_id"),
        "posts_count": t.get("posts_count"),
        "views": t.get("views"),
        "like_count": t.get("like_count"),
        "created_at": (t.get("created_at") or "")[:10],
        "last_posted_at": (t.get("last_posted_at") or "")[:16],
        "pinned": bool(t.get("pinned")),
        "closed": bool(t.get("closed")),
    }


def latest(limit: int = 20, category: str = "") -> list[dict]:
    """最新话题。category 形如 "c/xx/12"。"""
    path = f"/{category}.json" if category else "/latest.json"
    data = _get(path)
    topics = (data.get("topic_list") or {}).get("topics") or []
    return [_slim_topic(t) for t in topics[:limit]]


def categories() -> list[dict]:
    data = _get("/categories.json")
    cats = (data.get("category_list") or {}).get("categories") or []
    return [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "slug": c.get("slug"),
            "topics": c.get("topic_count"),
        }
        for c in cats
    ]


def search(query: str, limit: int = 10) -> list[dict]:
    from urllib.parse import quote

    data = _get(f"/search.json?q={quote(query)}")
    topics = data.get("topics") or []
    return [_slim_topic(t) for t in topics[:limit]]


def topic(topic_id: int, max_posts: int = 20) -> dict:
    """话题详情，带楼层正文。"""
    data = _get(f"/t/{topic_id}.json")
    posts = []
    for p in (data.get("post_stream") or {}).get("posts") or []:
        posts.append(
            {
                "floor": p.get("post_number"),
                "user": p.get("username"),
                "at": (p.get("created_at") or "")[:16],
                "text": _strip_html(p.get("cooked") or "")[:800],
            }
        )
        if len(posts) >= max_posts:
            break

    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "posts_count": data.get("posts_count"),
        "created_at": (data.get("created_at") or "")[:16],
        "url": f"{ORIGIN}/t/{topic_id}",
        "posts": posts,
    }


def _strip_html(raw: str) -> str:
    import re

    text = re.sub(r"<br\s*/?>", "\n", raw)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n{3,}", "\n\n", text).strip()
