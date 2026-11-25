"""健康、诊断、日志、修复。

这三个接口是给外部 Agent 用的：DSH / Codex 拿到 base_url 就能自己排障。
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from .. import config, db, repair

router = APIRouter(tags=["system"])

STARTED_AT = time.time()
LOG_FILE = Path.home() / ".xinge" / "logs" / "app.log"

# 导出时必须剔除的敏感值：Key / 密钥 / 登录态，一律不进导出包
SENSITIVE_KEYS = (
    "llm_key",
    "qq_secret",
    "mail_password",
    "canvas_token",
    "sjtu_api_key",
    "aihaoke_token",
)


def _xinge_home() -> Path:
    """本机数据目录（XINGE_HOME 可覆盖，测试时能指到临时目录）。"""
    return Path(os.environ.get("XINGE_HOME") or (Path.home() / ".xinge"))


@router.get("/health")
def health():
    return {
        "status": "ok",
        "uptime_s": int(time.time() - STARTED_AT),
        "db": str(db.DB_PATH),
    }


@router.get("/auth/status")
def auth_status():
    from .. import qrlogin

    return qrlogin.status()


def _scrub_astrbot_secrets() -> bool:
    """把 AstrBot 配置里同步过去的模型 Key / QQ Secret 也清掉。

    只清磁盘上的凭据，不动 AstrBot 进程；下次重启 AstrBot 就不会再带着
    旧 Key / Secret 跑。失败不影响退出登录本身。
    """
    try:
        from xinge import astrbot_setup

        data = astrbot_setup.read_astrbot_config()
        if not data:
            return False
        changed = False
        for source in data.get("provider_sources") or []:
            if source.get("key"):
                source["key"] = []
                changed = True
        for platform in data.get("platform") or []:
            if platform.get("type") == "qq_official" and (
                platform.get("secret") or platform.get("enable")
            ):
                platform["secret"] = ""
                platform["enable"] = False
                changed = True
        if changed:
            astrbot_setup.write_astrbot_config(data)
        return changed
    except Exception:                              # noqa: BLE001
        return False


@router.post("/auth/logout")
def auth_logout():
    """退出 jAccount：清掉本机保存的所有登录态和可用凭据。

    不是关软件——清完回到登录页，下次要重新扫码。清掉的是「凭据」：
    各站会话 / cookie、config.json 里的 token、邮箱密码、QQ Secret、模型 Key，
    以及 AstrBot 配置里同步过去的 Key / Secret。
    保留 llm_base / llm_model / qq_appid 这类配置项（它们本身不是密码），
    重新登录后不用再重填接口地址和模型名。
    """
    home = _xinge_home()
    removed = []

    # 这些文件里存着各站的会话
    for name in ("session.json", "shuiyuan.json", "jwc.json", "aihaoke.json", "impsa.json"):
        path = home / name
        if path.exists():
            try:
                path.unlink()
                removed.append(name)
            except OSError:
                pass

    # 其它站点的 cookie 文件名字不固定，统一扫一遍（config.json 除外）
    if home.exists():
        for path in sorted(home.glob("*.json")):
            if path.name == "config.json" or path.name in removed or not path.is_file():
                continue
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass

    # config 里的 token / 密码 / 密钥也要清
    cfg_path = home / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            for key in (
                "canvas_token",
                "sjtu_api_key",
                "aihaoke_token",
                "mail_password",
                "qq_secret",
                "llm_key",
            ):
                cfg.pop(key, None)
            cfg_path.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, json.JSONDecodeError):
            pass

    return {"ok": True, "removed": removed, "astrbot_scrubbed": _scrub_astrbot_secrets()}


@router.post("/auth/qr/start")
def auth_qr_start():
    from .. import qrlogin

    return qrlogin.start()


@router.get("/auth/qr/status/{sid}")
def auth_qr_status(sid: str):
    from .. import qrlogin

    return qrlogin.poll(sid)


@router.post("/auth/qr/cancel/{sid}")
def auth_qr_cancel(sid: str):
    from .. import qrlogin

    qrlogin.cancel(sid)
    return {"ok": True}


@router.get("/system/browser")
def browser_status():
    """这台机器上能不能找到浏览器（截图和课程检测都靠它）。"""
    from xinge import browser

    return browser.status()


@router.get("/diagnose")
def diagnose():
    """结构化诊断：问题列表 + 每个源的状态 + 登录态。"""
    issues: list[dict] = []
    states = {s["key"]: s for s in db.source_states()}

    for key, name in {
        "canvas": "Canvas",
        "cnmooc": "CNMOOC",
        "pe": "物理实验",
        "second_classroom": "第二课堂",
    }.items():
        st = states.get(key)
        if not st:
            issues.append({"level": "info", "source": key, "msg": f"{name} 尚未抓取过"})
            continue
        if not st.get("last_ok"):
            issues.append(
                {
                    "level": "error",
                    "source": key,
                    "msg": st.get("last_error") or "上次抓取失败",
                }
            )

    session = db.DATA_DIR / "session.json"
    authed = session.exists() and session.stat().st_size > 4
    if not authed:
        issues.append(
            {"level": "warn", "source": "auth", "msg": "未登录，需要扫码（fix: reauth.jaccount）"}
        )

    return {
        "ok": not any(i["level"] == "error" for i in issues),
        "issues": issues,
        "authenticated": authed,
        "fixes": [f["id"] for f in repair.list_fixes()],
    }


@router.get("/logs")
def logs(limit: int = 200):
    if not LOG_FILE.exists():
        return {"lines": []}
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": lines[-limit:]}


@router.get("/fixes")
def fixes():
    return {"fixes": repair.list_fixes()}


@router.post("/repair")
def run_fix(body: dict):
    fix_id = (body or {}).get("fix_id", "")
    return repair.apply(fix_id)


# ---------------- 我的数据：导出 / 删除 ----------------

def _safe_config() -> dict:
    """当前配置，剔除所有敏感值（导出包里不能出现明文 Key）。"""
    cfg = dict(config.load())
    for key in SENSITIVE_KEYS:
        cfg.pop(key, None)
    return cfg


def _dump_database() -> dict:
    """把 app.db 导成 {"表名": [行...]} 的 JSON 结构（只读，不动原库）。"""
    path = Path(db.DB_PATH)
    if not path.exists():
        return {}
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        dump: dict = {}
        for table in tables:
            dump[table] = [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
        return dump
    except sqlite3.Error:
        return {}
    finally:
        if conn is not None:
            conn.close()


@router.get("/data/export")
def data_export():
    """导出我的全部数据：config.json（已脱敏）+ app.db 的 JSON dump，打包成 zip。

    站点登录态（session / cookie）不导出；API Key / 邮箱密码 / QQ Secret 等
    敏感值一律剔除。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "config.json",
            json.dumps(_safe_config(), ensure_ascii=False, indent=2),
        )
        bundle.writestr(
            "app.db.json",
            json.dumps(_dump_database(), ensure_ascii=False, indent=2, default=str),
        )
        bundle.writestr(
            "README.txt",
            "信鸽 SJTU 数据导出\n"
            "=====================\n"
            "config.json   本机配置（已剔除 API Key / 邮箱密码 / QQ Secret 等凭据）\n"
            "app.db.json   本机数据库的 JSON 版本（板块 / 条目 / 日程 / 提醒等）\n"
            "\n"
            "站点登录态（session.json 等 cookie）不会导出。\n",
        )
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="xinge-data-{stamp}.zip"'
        },
    )


@router.post("/data/delete")
def data_delete(body: dict | None = None):
    """删除本机保存的全部数据。

    必须显式确认（body: {"confirm": "DELETE"}）才执行，避免误触。
    删掉：config.json、各站点 cookie（~/.xinge/*.json）、app.db（含 -wal/-shm）。
    返回这次删了哪些文件。
    """
    confirm = str((body or {}).get("confirm") or "").strip()
    if confirm != "DELETE":
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": '需要确认：请求体传 {"confirm": "DELETE"}'},
        )

    home = _xinge_home()
    targets: list[Path] = []
    if home.exists():
        targets.extend(path for path in sorted(home.glob("*.json")) if path.is_file())
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db.DB_PATH) + suffix)
        if path.exists():
            targets.append(path)

    removed: list[str] = []
    failed: list[str] = []
    for path in targets:
        for attempt in range(5):
            try:
                path.unlink()
                removed.append(path.name if path.parent == home else str(path))
                break
            except FileNotFoundError:
                break
            except OSError:
                # Windows 上运行中的 sqlite 连接会短暂占着文件，稍等重试
                if attempt == 4:
                    failed.append(path.name if path.parent == home else str(path))
                else:
                    time.sleep(0.2)

    return {"ok": not failed, "removed": removed, "failed": failed, "home": str(home)}

