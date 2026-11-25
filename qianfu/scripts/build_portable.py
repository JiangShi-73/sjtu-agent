"""打包成免安装的 Windows 便携版。

    python scripts/build_portable.py

产出 build/信鸽SJTU-完整版/ 和同名 zip：

    python/          嵌入式 Python 3.12（不用装 Python）
    packages/        所有第三方依赖（含 AstrBot 的 322 个包）
    app/ xinge/     信鸽源码
    astrbot/        AstrBot 源码
    启动.bat        双击即用

用户拿到解压就能跑，不用联网、不用装依赖。
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
OUT = BUILD / "信鸽SJTU-完整版"

PY_VERSION = "3.12.10"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"

SKIP_DIRS = {".git", "__pycache__", ".venv", "build", "node_modules", "data", ".pytest_cache"}


def log(msg: str) -> None:
    print(f"[打包] {msg}", flush=True)


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc", "*.db", "*.log"),
        dirs_exist_ok=True,
    )


def fetch_python(dest: Path) -> Path:
    """下载嵌入式 Python 并解开。"""
    if (dest / "python.exe").exists():
        log("嵌入式 Python 已存在，跳过下载")
        for pth in dest.glob("python*._pth"):
            pth.write_text(
                "\r\n".join(
                    ["python312.zip", ".", "Lib\\site-packages", "..\\packages", "..\\packages\\win32", "..\\packages\\win32\\lib", "..\\packages\\pywin32_system32", "..", "..\\astrbot", ""]
                ),
                encoding="utf-8",
            )
        return dest / "python.exe"

    dest.mkdir(parents=True, exist_ok=True)
    archive = BUILD / "python-embed.zip"
    if not archive.exists():
        log(f"下载嵌入式 Python {PY_VERSION} …")
        urllib.request.urlretrieve(PY_URL, archive)

    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)

    # 嵌入式版默认不加载 site-packages，要手动开
    for pth in dest.glob("python*._pth"):
        pth.write_text(
            "\r\n".join(
                ["python312.zip", ".", "Lib\\site-packages", "..\\packages", "..\\packages\\win32", "..\\packages\\win32\\lib", "..\\packages\\pywin32_system32", "..", "..\\astrbot", ""]
            ),
            encoding="utf-8",
        )

    # 嵌入式版没带 pip，引导一个
    if not (dest / "Lib" / "site-packages" / "pip").exists():
        log("引导 pip …")
        getpip = BUILD / "get-pip.py"
        if not getpip.exists():
            urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", getpip)
        subprocess.run(
            [str(dest / "python.exe"), str(getpip), "--no-warn-script-location"], check=True
        )
    return dest / "python.exe"


def install_packages(python: Path, dest: Path) -> None:
    if (dest / "fastapi").exists() and (dest / "astrbot").exists():
        log("依赖已装好，跳过")
        return

    dest.mkdir(parents=True, exist_ok=True)
    reqs = [ROOT / "requirements.txt", ROOT / "astrbot" / "requirements.txt"]
    args = [str(python), "-m", "pip", "install", "--target", str(dest), "--upgrade"]
    for req in reqs:
        if req.exists():
            args += ["-r", str(req)]

    log("安装依赖（约 650 MB，第一次 5~10 分钟）…")
    result = subprocess.run(args)
    if result.returncode != 0:
        raise SystemExit("依赖安装失败")


def sanitize_config(dest: Path) -> None:
    """清掉打包机上的 QQ 凭据和模型 key，别人拿到不会连到你的机器人。

    Args:
        dest: 打包输出目录。
    """
    cfg_path = dest / "astrbot" / "data" / "cmd_config.json"
    if not cfg_path.exists():
        return

    data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    data["platform"] = []
    data["provider"] = []
    data["provider_sources"] = []

    # 面板：只监听本机 + 首次登录强制改口令 + 随机 jwt_secret
    # 不这么做的话，打包机会把固定的面板口令哈希和 jwt_secret 带给每一个拿到安装包的人
    dash = data.setdefault("dashboard", {})
    dash["host"] = "127.0.0.1"
    dash["password_change_required"] = True
    dash["jwt_secret"] = secrets.token_hex(32)

    cfg_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8-sig"
    )
    log("已清空打包内的 QQ 配置和模型 key；面板改绑本机并要求首次改口令")


def write_launcher(dest: Path) -> None:
    bat = "\r\n".join(
        [
            "@echo off",
            "chcp 65001 >nul",
            "cd /d \"%~dp0\"",
            "set PYTHONPATH=%~dp0packages;%~dp0;%~dp0astrbot",
            "set XINGE_PORTABLE=1",
            "set PYTHONIOENCODING=utf-8",
            "echo.",
            "echo   信鸽 SJTU",
            "echo   信鸽   http://127.0.0.1:8722/static/index.html",
            "echo   AstrBot http://127.0.0.1:6185",
            "echo.",
            "python\\python.exe main.py",
            "pause",
        ]
    )
    launcher = dest / "启动.bat"
    launcher.write_text(bat, encoding="utf-8")

    # 回归保护：便携版必须告诉 astrbot_setup 直接用 packages/ 里的依赖，
    # 不能去建 venv（嵌入式 Python 没有 venv 模块，建了必炸）。
    # 曾经漏过这一行：发出去的 zip 里 启动.bat 没有它，用户启动就报
    # "No module named venv"。
    if "set XINGE_PORTABLE=1" not in launcher.read_text(encoding="utf-8"):
        raise SystemExit("启动.bat 少了 set XINGE_PORTABLE=1，打包中止")


def main() -> None:
    log(f"输出目录 {OUT}")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    log("复制源码…")
    for name in ("app", "xinge", "integrations", "scripts"):
        src = ROOT / name
        if src.exists():
            copy_tree(src, OUT / name)
    for name in ("main.py", "requirements.txt", "README.md"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, OUT / name)

    if not (ROOT / "astrbot" / "main.py").exists():
        raise SystemExit("先跑一次 python main.py，让 AstrBot 下载下来")

    log("复制 AstrBot…")
    copy_tree(ROOT / "astrbot", OUT / "astrbot")

    py = fetch_python(OUT / "python")
    install_packages(py, OUT / "packages")

    # pywin32 的 DLL 必须放在 python.exe 旁边
    sys32 = OUT / "packages" / "pywin32_system32"
    if sys32.exists():
        for dll in sys32.glob("*.dll"):
            shutil.copy2(dll, py.parent / dll.name)

    sanitize_config(OUT)
    write_launcher(OUT)

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1024 / 1024
    log(f"完成，未压缩 {size:.0f} MB")

    archive = BUILD / "信鸽SJTU-完整版.zip"
    log(f"压缩到 {archive.name} …")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in OUT.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(OUT))
    log(f"压缩包 {archive.stat().st_size / 1024 / 1024:.0f} MB")


if __name__ == "__main__":
    main()
