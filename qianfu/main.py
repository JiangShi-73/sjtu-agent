"""信鸽 SJTU 统一入口。

和 AstrBot 一样的启动方式：

    python main.py

它会做四件事：

1. 检查 AstrBot 在不在，不在就 clone 下来
2. 检查 AstrBot 的虚拟环境，不在就建 + 装依赖
3. 把信鸽插件同步到 AstrBot 的插件目录
4. 先起信鸽后端（8722），再起 AstrBot（6185）

Ctrl+C 一次全部退出。
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASTRBOT_DIR = ROOT / "astrbot"
ASTRBOT_REPO = "https://github.com/AstrBotDevs/AstrBot.git"
PLUGIN_SRC = ROOT / "integrations" / "astrbot_plugin_xinge"
PLUGIN_DST = ASTRBOT_DIR / "data" / "plugins" / "xinge"

BACKEND_PORT = 8722
DASHBOARD_PORT = 6185


def log(msg: str) -> None:
    print(f"[信鸽] {msg}", flush=True)


def venv_python() -> Path:
    if os.name == "nt":
        return ASTRBOT_DIR / ".venv" / "Scripts" / "python.exe"
    return ASTRBOT_DIR / ".venv" / "bin" / "python"


def ensure_astrbot() -> None:
    """没有 AstrBot 就 clone 一份。"""
    if (ASTRBOT_DIR / "main.py").exists():
        return

    log("没找到 AstrBot，开始下载（约 40 MB）…")
    ASTRBOT_DIR.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", ASTRBOT_REPO, str(ASTRBOT_DIR)]
    )
    if result.returncode != 0:
        log("下载失败，检查网络后重试；也可以手动 clone 到 qianfu/astrbot")
        sys.exit(1)


def ensure_venv() -> Path:
    """AstrBot 的依赖和我们后端冲突风险大，单独一个虚拟环境。"""
    py = venv_python()
    if not py.exists():
        log("创建 AstrBot 虚拟环境…")
        subprocess.run([sys.executable, "-m", "venv", str(ASTRBOT_DIR / ".venv")], check=True)

    marker = ASTRBOT_DIR / ".venv" / ".deps-ok"
    if not marker.exists():
        log("安装 AstrBot 依赖（第一次会比较久）…")
        subprocess.run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
        result = subprocess.run(
            [str(py), "-m", "pip", "install", "-r", str(ASTRBOT_DIR / "requirements.txt")]
        )
        if result.returncode != 0:
            log("依赖安装失败，看上面的报错")
            sys.exit(1)
        marker.write_text("ok", encoding="utf-8")
    return py


def sync_plugin() -> None:
    """把信鸽插件放进 AstrBot 的插件目录。"""
    if not PLUGIN_SRC.exists():
        return
    PLUGIN_DST.mkdir(parents=True, exist_ok=True)
    for item in PLUGIN_SRC.iterdir():
        target = PLUGIN_DST / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    log(f"插件已同步到 {PLUGIN_DST.relative_to(ROOT)}")


def wait_http(url: str, timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(1.5)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="信鸽 SJTU")
    parser.add_argument("--port", type=int, default=BACKEND_PORT, help="信鸽后端端口")
    parser.add_argument("--dashboard-port", type=int, default=DASHBOARD_PORT, help="AstrBot 面板端口（仅用于提示）")
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

    log(f"启动信鸽后端 :{args.port}")
    backend = subprocess.Popen(
        [sys.executable, "-m", "app.server", "--port", str(args.port)], cwd=ROOT
    )
    children.append(backend)

    if not wait_http(f"http://127.0.0.1:{args.port}/api/v1/health"):
        log("后端没起来，退出")
        shutdown()

    log(f"信鸽就绪  http://127.0.0.1:{args.port}/static/index.html")

    if args.no_astrbot:
        try:
            backend.wait()
        finally:
            shutdown()

    ensure_astrbot()
    py = ensure_venv()
    sync_plugin()

    log("启动 AstrBot…")
    astrbot = subprocess.Popen([str(py), "main.py"], cwd=ASTRBOT_DIR)
    children.append(astrbot)

    # AstrBot 的面板端口写在自己的 data/cmd_config.json 里，默认 6185
    log(f"AstrBot 面板  http://127.0.0.1:{args.dashboard_port}（端口在 AstrBot 配置里改）")
    log("按 Ctrl+C 全部退出")

    try:
        backend.wait()
    finally:
        shutdown()


if __name__ == "__main__":
    main()
