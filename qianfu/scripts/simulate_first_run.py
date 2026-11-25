"""模拟「第一次打开信鸽」：备份现有数据后清空。

用法：

    python scripts/simulate_first_run.py backup      # 备份并清空，然后手动启动 App
    python scripts/simulate_first_run.py restore     # 恢复最近一次备份
    python scripts/simulate_first_run.py list        # 看有哪些备份

注意：清空的是 ~/.xinge（登录态、配置、抓取数据），不动 AstrBot 的配置和插件。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
DATA = HOME / ".xinge"
BACKUP_ROOT = HOME / ".xinge-backups"


def _running() -> bool:
    """看应用是不是还开着（还开着就别动数据）。"""
    try:
        out = subprocess.run(
            ["tasklist"], capture_output=True, text=True, timeout=15, errors="ignore"
        ).stdout
    except Exception:
        return False
    return "信鸽" in out


def do_backup() -> int:
    if _running():
        print("应用还开着。先完全关掉「信鸽 SJTU」，再跑这个命令。")
        return 1
    if not DATA.exists():
        print("没有 ~/.xinge，已经是首次运行状态了。直接启动 App 即可。")
        return 0

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_ROOT / ("xinge-" + stamp)
    shutil.copytree(DATA, dest)
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print("已备份到 " + str(dest) + " (" + str(size // 1024) + " KB)")

    shutil.rmtree(DATA)
    print("已清空 ~/.xinge —— 现在是「第一次打开」的状态")
    print("")
    print("下一步：启动「信鸽 SJTU」，应该看到登录页、需要重新扫码。")
    print("测完想恢复：python scripts/simulate_first_run.py restore")
    return 0


def do_restore() -> int:
    if _running():
        print("应用还开着。先完全关掉「信鸽 SJTU」，再跑这个命令。")
        return 1
    if not BACKUP_ROOT.exists():
        print("没有找到任何备份。")
        return 1
    candidates = sorted(p for p in BACKUP_ROOT.iterdir() if p.is_dir())
    if not candidates:
        print("没有找到任何备份。")
        return 1

    latest = candidates[-1]
    if DATA.exists():
        shutil.rmtree(DATA)
    shutil.copytree(latest, DATA)
    print("已从 " + latest.name + " 恢复 ~/.xinge")
    print("现在启动 App，应该回到原来的登录态和数据。")
    return 0


def do_list() -> int:
    if not BACKUP_ROOT.exists() or not any(BACKUP_ROOT.iterdir()):
        print("没有任何备份。")
        return 0
    for p in sorted(BACKUP_ROOT.iterdir()):
        if p.is_dir():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            print("  " + p.name + "  " + str(size // 1024) + " KB")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "backup":
        return do_backup()
    if cmd == "restore":
        return do_restore()
    if cmd == "list":
        return do_list()
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
