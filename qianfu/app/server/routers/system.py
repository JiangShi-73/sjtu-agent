"""健康、诊断、日志、修复。

这三个接口是给外部 Agent 用的：DSH / Codex 拿到 base_url 就能自己排障。
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter

from .. import db, repair

router = APIRouter(tags=["system"])

STARTED_AT = time.time()
LOG_FILE = Path.home() / ".xinge" / "logs" / "app.log"


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


@router.post("/auth/logout")
def auth_logout():
    """退出 jAccount：清掉本机保存的所有登录态。

    不是关软件——清完回到登录页，下次要重新扫码。
    """
    import os

    home = Path(os.path.expanduser("~")) / ".xinge"
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

    # config 里的 token 也要清
    cfg_path = home / "config.json"
    if cfg_path.exists():
        import json

        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            for key in ("canvas_token", "sjtu_api_key", "aihaoke_token"):
                cfg.pop(key, None)
            cfg_path.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, json.JSONDecodeError):
            pass

    return {"ok": True, "removed": removed}


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
