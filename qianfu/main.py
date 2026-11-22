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


def wait_http(url: str, timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if probe(url):
            return True
        time.sleep(1.5)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="信鸽 SJTU")
    parser.add_argument("--port", type=int, default=BACKEND_PORT)
    parser.add_argument("--no-astrbot", action="store_true", help="只起信鸽后端")
    args = parser.parse_args()

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
            [sys.executable, "-m", "app.server", "--port", str(args.port)], cwd=ROOT
        )
        children.append(backend)
        if not wait_http(f"http://127.0.0.1:{args.port}/api/v1/health"):
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
