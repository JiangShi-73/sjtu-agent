"""白名单修复动作。

Agent 不能执行任意命令，只能调用这里登记过的动作。
每个动作必须是幂等的，并且返回结构化的结果。
"""

from __future__ import annotations

from . import db

FIXES: dict[str, dict] = {
    "clear.cache": {
        "desc": "清空条目缓存（保留设置与登录态）",
        "args": [],
    },
    "reset.sources": {
        "desc": "重置所有信息源的健康状态记录",
        "args": [],
    },
    "reauth.jaccount": {
        "desc": "提示重新扫码（需要用户在前端完成）",
        "args": [],
        "needs_user": True,
    },
}


def list_fixes() -> list[dict]:
    return [{"id": k, **v} for k, v in FIXES.items()]


def apply(fix_id: str) -> dict:
    if fix_id not in FIXES:
        return {"ok": False, "error": f"未知修复动作: {fix_id}"}

    if fix_id == "clear.cache":
        conn = db.connect()
        try:
            n = conn.execute("DELETE FROM items").rowcount
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "fix": fix_id, "detail": f"删除 {n} 条条目"}

    if fix_id == "reset.sources":
        conn = db.connect()
        try:
            conn.execute("DELETE FROM sources_state")
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "fix": fix_id, "detail": "已重置源状态"}

    if fix_id == "reauth.jaccount":
        return {
            "ok": True,
            "fix": fix_id,
            "detail": "需要用户在界面完成扫码",
            "action": "open_qr",
        }

    return {"ok": False, "error": "未实现"}
