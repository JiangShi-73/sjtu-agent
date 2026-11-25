"""把源码同步到已安装版（开发调试用）。

用法： python scripts/deploy_installed.py [--restart]
"""
from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL = Path(os.environ.get("LOCALAPPDATA", ""))
INSTALL = LOCAL / "Programs" / "xinge-desktop"
RES = INSTALL / "resources"
DST = RES / "xinge"

PAIRS = [
    ("app/server", "app/server", "*.py"),
    ("xinge", "xinge", "*.py"),
    ("app/web", "app/web", "*"),
]


def sync_tree(src_name: str, dst_name: str, pattern: str) -> list[str]:
    src_root = ROOT / src_name
    dst_root = DST / dst_name
    changed: list[str] = []
    if not src_root.is_dir():
        return changed
    for src in src_root.rglob(pattern):
        if not src.is_file():
            continue
        rel = src.relative_to(src_root)
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            continue
        shutil.copy2(src, dst)
        changed.append(dst_name + "/" + str(rel))
    return changed


def sync_plugin() -> list[str]:
    src = ROOT / "integrations" / "astrbot_plugin_xinge" / "main.py"
    if not src.is_file():
        return []
    changed = []
    targets = [
        DST / "integrations" / "astrbot_plugin_xinge" / "main.py",
        DST / "astrbot" / "data" / "plugins" / "xinge" / "main.py",
    ]
    for dst in targets:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            continue
        shutil.copy2(src, dst)
        changed.append(str(dst.relative_to(RES)))
    return changed


def pack_asar() -> str:
    """重新打包 resources/app.asar —— Electron 真正加载的入口。"""
    desktop = ROOT / "app" / "desktop"
    asar = desktop / "node_modules" / ".bin" / "asar.cmd"
    if not asar.is_file():
        asar = desktop / "node_modules" / ".bin" / "asar"
    if not asar.is_file():
        return "跳过 app.asar：找不到 asar 工具"

    tmp = Path(os.environ.get("TEMP", "/tmp")) / "xinge-asar-work"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for name in ("main.js", "preload.js", "package.json", "xinge.ico"):
        f = desktop / name
        if f.is_file():
            shutil.copy2(f, tmp / name)

    out = tmp.parent / "xinge-app.asar"
    if out.exists():
        out.unlink()
    subprocess.run([str(asar), "pack", str(tmp), str(out)], check=True, shell=True)
    shutil.copy2(out, RES / "app.asar")
    return "app.asar 已更新（" + str((RES / "app.asar").stat().st_size) + " 字节）"


def main() -> int:
    if not DST.is_dir():
        print("没找到安装版：" + str(DST))
        return 1

    changed: list[str] = []
    for src_name, dst_name, pattern in PAIRS:
        changed += sync_tree(src_name, dst_name, pattern)
    changed += sync_plugin()

    if changed:
        print("同步了 " + str(len(changed)) + " 个文件")
        for c in changed:
            print("  " + c)
    else:
        print("源码已是最新，无需同步")

    print(pack_asar())

    if "--restart" in sys.argv:
        print("重启应用…")
        subprocess.run(["taskkill", "/F", "/IM", "信鸽 SJTU.exe"], capture_output=True)
        exe = INSTALL / "信鸽 SJTU.exe"
        subprocess.Popen([str(exe)])
        print("已启动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
