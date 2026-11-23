#!/usr/bin/env python3
"""打 Linux 便携运行时。

和 Windows 版思路一样：自带解释器 + 依赖，解压就能跑。
解释器用 python-build-standalone（astral-sh 维护的便携 CPython），
不依赖系统装了什么。

    python3 scripts/build_linux.py

产物：build/xinge-linux/
    python/        便携 CPython
    packages/      依赖
    app/ xinge/ astrbot/ main.py
    start.sh
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
OUT = BUILD / "xinge-linux"

PY_VERSION = "3.12.10"
PBS_TAG = "20241016"
PBS_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    + PBS_TAG
    + "/cpython-"
    + PY_VERSION
    + "+"
    + PBS_TAG
    + "-x86_64-unknown-linux-gnu-install_only.tar.gz"
)

COPY = ["app", "xinge", "integrations", "scripts"]
COPY_FILES = ["main.py", "requirements.txt", "README.md"]


def log(msg: str) -> None:
    print("[信鸽] " + msg, flush=True)


def copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        log("跳过（不存在）：" + src.name)
        return
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules", ".venv", "*.log"),
        dirs_exist_ok=True,
    )


def fetch_python(dest: Path) -> Path:
    """下载便携 CPython 并解压。"""
    marker = dest / "python" / "bin" / "python3"
    if marker.exists():
        log("Python 已存在，跳过下载")
        return marker

    dest.mkdir(parents=True, exist_ok=True)
    archive = BUILD / "cpython-linux.tar.gz"

    if not archive.exists():
        log("下载 Python " + PY_VERSION)
        urllib.request.urlretrieve(PBS_URL, archive)

    log("解压")
    with tarfile.open(archive) as tar:
        tar.extractall(dest)

    if not marker.exists():
        raise SystemExit("解压后没找到 " + str(marker))

    return marker


def install_packages(python: Path, dest: Path) -> None:
    """把依赖装进 packages/。"""
    if (dest / "fastapi").exists() and (dest / "uvicorn").exists():
        log("依赖已装，跳过")
        return

    log("装依赖（几分钟）")
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "--target",
            str(dest),
            "-r",
            str(ROOT / "requirements.txt"),
        ],
        check=True,
    )


def write_start(dest: Path) -> None:
    """写一个 start.sh，双击或 ./start.sh 就能跑。"""
    body = "\n".join(
        [
            "#!/usr/bin/env bash",
            "# 信鸽 SJTU —— 双击或在终端执行 ./start.sh",
            "set -e",
            'cd "$(dirname "$0")"',
            "",
            "export XINGE_PORTABLE=1",
            'export PYTHONPATH="$PWD/packages:$PWD:$PWD/astrbot"',
            "export PYTHONIOENCODING=utf-8",
            "",
            'exec ./python/bin/python3 -m app.server "$@"',
            "",
        ]
    )
    path = dest / "start.sh"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def sanitize_config(dest: Path) -> None:
    """清掉本机的凭据，别打进安装包。"""
    cfg = dest / "astrbot" / "data" / "cmd_config.json"
    if not cfg.exists():
        return

    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    for key in ("platform", "provider", "provider_sources"):
        data.pop(key, None)

    cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log("已清掉打包里的模型和 QQ 凭据")


def main() -> None:
    if platform.system() != "Linux":
        log("这个脚本要在 Linux 上跑（Windows 上请用 build_portable.py）")
        raise SystemExit(1)

    log("输出目录：" + str(OUT))
    OUT.mkdir(parents=True, exist_ok=True)

    python = fetch_python(OUT)
    install_packages(python, OUT / "packages")

    for name in COPY:
        copy_tree(ROOT / name, OUT / name)

    for name in COPY_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, OUT / name)

    if (ROOT / "astrbot" / "main.py").exists():
        copy_tree(ROOT / "astrbot", OUT / "astrbot")
        sanitize_config(OUT)
    else:
        log("没有 astrbot/，跳过（首次启动会自动 clone）")

    write_start(OUT)

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1024 / 1024
    log("完成：" + str(OUT) + "（" + str(round(size)) + " MB）")
    log("测试：cd build/xinge-linux && ./start.sh")


if __name__ == "__main__":
    main()
