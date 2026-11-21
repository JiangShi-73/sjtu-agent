"""把活动详情页截成图，给 QQ 机器人发图用。

平台是纯前端渲染的，拿 HTML 没用，只能用真浏览器打开再截图。
截图缓存在 ~/.xinge/shots/，10 分钟内复用，避免每次都开浏览器。
"""

from __future__ import annotations

import hashlib
import os
import time

from xinge.auth import SessionStore

SHOT_DIR = os.path.join(os.path.expanduser("~"), ".xinge", "shots")
TTL_SECONDS = 600

# 详情页是桌面布局，宽度太窄会把右侧信息切掉
VIEWPORT = {"width": 1000, "height": 1200}


def _cache_path(url: str) -> str:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(SHOT_DIR, f"{key}.png")


def cached(url: str) -> bytes | None:
    path = _cache_path(url)
    try:
        if time.time() - os.path.getmtime(path) < TTL_SECONDS:
            with open(path, "rb") as fh:
                return fh.read()
    except OSError:
        pass
    return None


def capture(url: str, session: SessionStore | None = None, force: bool = False) -> bytes:
    """打开 url 截一张全页图，返回 PNG 字节。失败抛 RuntimeError。"""
    if not force:
        hit = cached(url)
        if hit:
            return hit

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("没有安装 playwright，无法截图") from exc

    session = session or SessionStore()
    cookies = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}
        for c in session._jar.jar
        if "jaccount" in c.domain or "activity" in c.domain
    ]

    with sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = (
                    p.chromium.launch(channel=channel, headless=True)
                    if channel
                    else p.chromium.launch(headless=True)
                )
                break
            except Exception:
                continue
        if browser is None:
            raise RuntimeError("找不到可用的浏览器（Edge / Chrome）")

        try:
            ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
            if cookies:
                ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            # 等主标题渲染出来，避免截到空白
            try:
                page.wait_for_selector("h1, h2, .activity-detail", timeout=8000)
            except Exception:
                pass
            png = page.screenshot(full_page=True)
        finally:
            browser.close()

    os.makedirs(SHOT_DIR, exist_ok=True)
    with open(_cache_path(url), "wb") as fh:
        fh.write(png)
    return png
