"""AstrBot 进程管理。

信鸽后端负责拉起 / 停掉 AstrBot，这样前端才能在配置改完后直接重启它，
用户不用去碰命令行。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from xinge import astrbot_setup

DASHBOARD_PORT = 6185

_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def alive() -> bool:
    """AstrBot 面板有没有响应。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{DASHBOARD_PORT}/", timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        return False


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def start() -> dict:
    """起 AstrBot。已经在跑就直接返回。"""
    global _proc

    with _lock:
        if alive():
            return {"ok": True, "state": "already-running"}
        if not astrbot_setup.installed():
            return {"ok": False, "state": "not-installed"}

        py = astrbot_setup.python_for_astrbot()

        # 别把输出丢掉——出问题时这是唯一能看到 QQ 连没连上的地方
        log_path = astrbot_setup.ASTRBOT_DIR / "data" / "xinge-astrbot.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "ab", buffering=0)
        except OSError:
            log_file = subprocess.DEVNULL

        _proc = subprocess.Popen(
            [str(py), "main.py"],
            cwd=astrbot_setup.ASTRBOT_DIR,
            stdout=log_file,
            stderr=log_file,
        )

    for _ in range(90):
        time.sleep(1)
        if alive():
            return {"ok": True, "state": "started"}
        if _proc.poll() is not None:
            return {"ok": False, "state": "exited", "code": _proc.returncode}

    return {"ok": False, "state": "timeout"}


def stop() -> dict:
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None
    return {"ok": True}


def restart() -> dict:
    stop()
    time.sleep(2)
    return start()


def apply_config(cfg: dict) -> dict:
    """写入配置并重启 AstrBot。"""
    result = astrbot_setup.sync(cfg)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("reason")}
    astrbot_setup.sync_plugin()
    return {**result, "restart": restart()}


def bootstrap() -> None:
    """后端启动时自动把 AstrBot 拉起来（装了的话）。"""
    if not astrbot_setup.installed():
        return
    from . import config

    astrbot_setup.sync(config.load())
    astrbot_setup.sync_plugin()
    threading.Thread(target=start, daemon=True).start()
