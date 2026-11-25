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


def _pids_on_port(port: int) -> list[int]:
    """谁在监听这个端口（跨平台，只做尽力而为）。

    为什么需要它：AstrBot 可能是**上一个后端进程**拉起来的，
    现在这个进程的 _proc 是 None，光靠 terminate(_proc) 停不掉它。
    """
    pids: list[int] = []
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=15, errors="ignore"
            ).stdout
            for line in out.splitlines():
                if f":{port} " not in line or "LISTENING" not in line.upper():
                    continue
                tail = line.split()
                if tail and tail[-1].isdigit():
                    pids.append(int(tail[-1]))
        else:
            out = subprocess.run(
                ["ss", "-lptn", f"sport = :{port}"],
                capture_output=True, text=True, timeout=15, errors="ignore",
            ).stdout
            for line in out.splitlines():
                if "pid=" not in line:
                    continue
                for part in line.split("pid=")[1:]:
                    digits = ""
                    for ch in part:
                        if ch.isdigit():
                            digits += ch
                        else:
                            break
                    if digits:
                        pids.append(int(digits))
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(set(pids))


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=15,
                )
            else:
                os.kill(pid, 9)
        except (OSError, subprocess.SubprocessError):
            pass


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

        # AstrBot 默认每 10 分钟往 tickstats.soulter.top 上报一次指标（含机器名），
        # 信鸽这边把它关掉：本地工具不需要任何遥测。
        env = dict(os.environ)
        env["ASTRBOT_DISABLE_METRICS"] = "1"

        _proc = subprocess.Popen(
            [str(py), "main.py"],
            cwd=astrbot_setup.ASTRBOT_DIR,
            stdout=log_file,
            stderr=log_file,
            env=env,
        )

    for _ in range(90):
        time.sleep(1)
        if alive():
            return {"ok": True, "state": "started"}
        if _proc.poll() is not None:
            return {"ok": False, "state": "exited", "code": _proc.returncode}

    return {"ok": False, "state": "timeout"}


def stop() -> dict:
    """停掉 AstrBot —— 自己拉起的那个，以及上一个后端进程留下的孤儿进程。"""
    global _proc
    killed: list[int] = []
    with _lock:
        if _proc and _proc.poll() is None:
            killed.append(_proc.pid)
            _proc.terminate()
            try:
                _proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _proc.kill()
                _proc.wait(timeout=5)
        _proc = None

    # 关键：_proc 为空但端口还在监听，说明是孤儿进程，必须按端口清掉
    others = _pids_on_port(DASHBOARD_PORT)
    if others:
        killed += others
        _kill_pids(others)

    # 等端口真的释放（最多 15 秒）
    for _ in range(30):
        if not _pids_on_port(DASHBOARD_PORT):
            break
        time.sleep(0.5)

    return {"ok": True, "killed": killed}


def restart() -> dict:
    """强制重启：先确保真的停掉，再起。

    注意不能用「start() 发现已存活就直接返回 already-running」那套逻辑，
    否则改了配置点「保存并重启」根本不会生效。
    """
    stop()
    for _ in range(20):
        if not _pids_on_port(DASHBOARD_PORT) and not alive():
            break
        time.sleep(0.5)
    return start()


def apply_config(cfg: dict) -> dict:
    """写入配置并重启 AstrBot。"""
    result = astrbot_setup.sync(cfg)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("reason")}
    astrbot_setup.sync_plugin()
    return {**result, "restart": restart()}


def bootstrap() -> None:
    """后端启动时自动把 AstrBot 拉起来（装了的话）。

    注意：如果 AstrBot 是**上一个后端进程**留下的孤儿进程，它加载的是旧配置。
    这里先 sync 新配置，再强制重启，保证启起来的是最新配置
    （否则会出现「改了配置、重启了 App，QQ 机器人还是旧行为」甚至平台为空的坑）。
    """
    if not astrbot_setup.installed():
        return
    from . import config

    astrbot_setup.sync(config.load())
    astrbot_setup.sync_plugin()

    def _boot() -> None:
        try:
            if _pids_on_port(DASHBOARD_PORT) or _proc is not None:
                restart()
            else:
                start()
        except Exception:                         # noqa: BLE001
            pass

    threading.Thread(target=_boot, daemon=True).start()
