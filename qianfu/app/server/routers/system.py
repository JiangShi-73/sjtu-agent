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
