"""信鸽 SJTU 统一入口。

    python main.py

第一次运行会：

1. 下载 AstrBot（约 40 MB）
2. 建独立虚拟环境并装依赖（约 10 分钟）

之后每次启动：

3. 把信鸽配置同步进 AstrBot（模型 / QQ 平台 / 插件）
4. 起信鸽后端 :8722，后端再把 AstrBot 拉起来 :6185

QQ 机器人的 AppID / Secret 在信鸽界面里填，不用改任何配置文件。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from xinge import astrbot_setup  # noqa: E402

BACKEND_PORT = 8722


def log(msg: str) -> None:
    print(f"[信鸽] {msg}", flush=True)


def probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        return False


def wait_http(url: str, timeout: float = 90, proc: subprocess.Popen | None = None) -> bool:
    """等后端起来。

    proc 传进来的话，子进程提前退出就立刻判失败，不用干等 90 秒。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if probe(url):
            return True
        if proc is not None and proc.poll() is not None:
            log(f"后端进程退出了（退出码 {proc.returncode}），原因在它上面的输出里")
            return False
        time.sleep(1.5)
    return False


def portable_layout() -> bool:
    """是不是便携版目录（zip 解压出来的那种：packages/ + 内置 python/）。"""
    return astrbot_setup.portable()


# 起后端必需的几样东西。缺了基本就是 zip 没解压完。
PORTABLE_REQUIRED = (
    "app/server/__main__.py",
    "app/server/main.py",
    "packages/fastapi/__init__.py",
    "packages/starlette/__init__.py",
    "packages/uvicorn/__init__.py",
    "packages/uvicorn/middleware/__init__.py",
)


def missing_portable_files() -> list[str]:
    return [rel for rel in PORTABLE_REQUIRED if not (ROOT / rel).exists()]


def backend_env() -> dict[str, str]:
    """后端子进程的环境变量。

    子进程不继承父进程的 sys.path，只能靠 ._pth / PYTHONPATH 自己找包：
    嵌入式 Python 认 ._pth，普通 Python 认 PYTHONPATH，两个都给它配好。
    便携版再补 XINGE_PORTABLE，免得子进程又去建 venv。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if portable_layout():
        env["XINGE_PORTABLE"] = "1"
    parts = [str(ROOT / "packages"), str(ROOT), str(ROOT / "astrbot")]
    for old in env.get("PYTHONPATH", "").split(os.pathsep):
        if old and old not in parts:
            parts.append(old)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description="信鸽 SJTU")
    parser.add_argument("--port", type=int, default=BACKEND_PORT)
    parser.add_argument("--no-astrbot", action="store_true", help="只起信鸽后端")
    args = parser.parse_args()

    # 便携版文件不全（zip 没解压完 / 解压出错）先说清楚，别等 Python 吐一坨栈
    if portable_layout():
        missing = missing_portable_files()
        if missing:
            log("便携版文件不完整，缺这些：")
            for rel in missing:
                log(f"  - {rel}")
            log("大概率是 zip 没解压完。重新完整解压一次，再双击 启动.bat")
            sys.exit(1)

    children: list[subprocess.Popen] = []

    def shutdown(*_args) -> None:
        log("正在退出…")
        for proc in children:
            if proc.poll() is None:
                proc.terminate()
        for proc in children:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if not args.no_astrbot:
        try:
            astrbot_setup.ensure_installed(log=log)
            astrbot_setup.sync_plugin()
        except Exception as exc:
            log(f"AstrBot 准备失败：{exc}")
            log("先只启动信鸽后端，修好后再重启")

    if probe(f"http://127.0.0.1:{args.port}/api/v1/health"):
        log(f"信鸽后端已在 :{args.port} 运行，直接复用")
        backend = None
    else:
        log(f"启动信鸽后端 :{args.port}")
        backend = subprocess.Popen(
            [sys.executable, "-m", "app.server", "--port", str(args.port)],
            cwd=ROOT,
            env=backend_env(),
        )
        children.append(backend)
        if not wait_http(f"http://127.0.0.1:{args.port}/api/v1/health", proc=backend):
            log(f"后端没起来，端口 {args.port} 可能被占用")
            shutdown()

    log(f"信鸽  http://127.0.0.1:{args.port}/static/index.html")
    log("AstrBot 由后端托管，面板 http://127.0.0.1:6185")
    log("按 Ctrl+C 全部退出")

    if backend is None:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            shutdown()
    else:
        try:
            backend.wait()
        finally:
            shutdown()


if __name__ == "__main__":
    main()
