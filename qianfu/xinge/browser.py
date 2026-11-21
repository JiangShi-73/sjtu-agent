"""找一个能用的浏览器。

有些站点（CNMOOC 课程列表、校园活动平台详情页）只能被真浏览器访问，
所以需要 Playwright 驱动本机浏览器。但**不能假设用户装了 Chromium**——
Windows 上 Edge 是系统自带的，macOS/Linux 上常见的是 Chrome。

查找顺序：

    1. Edge（Windows 必装）
    2. Chrome
    3. Playwright 自带 chromium（如果跑过 playwright install）
    4. 手动扫常见安装路径

都找不到就抛 RuntimeError，调用方降级处理。
"""

from __future__ import annotations

import os
import shutil
import sys


def _candidates() -> list[str]:
    """常见安装路径。"""
    paths: list[str] = []

    if sys.platform == "win32":
        roots = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for root in filter(None, roots):
            paths += [
                os.path.join(root, r"Microsoft\Edge\Application\msedge.exe"),
                os.path.join(root, r"Google\Chrome\Application\chrome.exe"),
            ]
    elif sys.platform == "darwin":
        paths += [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    else:
        for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                paths.append(found)

    return [p for p in paths if p and os.path.exists(p)]


def find_executable() -> str:
    """返回本机浏览器可执行文件路径，找不到返回空串。"""
    hits = _candidates()
    return hits[0] if hits else ""


def launch(playwright, headless: bool = True):
    """按优先级启动一个浏览器，返回 Browser 对象。

    调用方负责 close()。全都失败时抛 RuntimeError。
    """
    errors: list[str] = []

    for channel in ("msedge", "chrome"):
        try:
            return playwright.chromium.launch(channel=channel, headless=headless)
        except Exception as exc:
            errors.append(channel + ": " + str(exc).splitlines()[0][:80])

    exe = find_executable()
    if exe:
        try:
            return playwright.chromium.launch(executable_path=exe, headless=headless)
        except Exception as exc:
            errors.append(os.path.basename(exe) + ": " + str(exc).splitlines()[0][:80])

    try:
        return playwright.chromium.launch(headless=headless)
    except Exception as exc:
        errors.append("bundled: " + str(exc).splitlines()[0][:80])

    raise RuntimeError(
        "找不到可用的浏览器。请安装 Microsoft Edge 或 Google Chrome，"
        "或者执行 python -m playwright install chromium。"
        "（尝试过：" + "；".join(errors) + "）"
    )


def status() -> dict:
    """给诊断页用：这台机器上浏览器情况如何。"""
    exe = find_executable()
    info = {
        "executable": exe,
        "found": bool(exe),
        "platform": sys.platform,
    }
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        info["playwright"] = False
        return info

    info["playwright"] = True
    with sync_playwright() as p:
        try:
            browser = launch(p, headless=True)
        except RuntimeError as exc:
            info["launch_error"] = str(exc)
            return info
        try:
            info["version"] = browser.version
        finally:
            browser.close()
    return info
