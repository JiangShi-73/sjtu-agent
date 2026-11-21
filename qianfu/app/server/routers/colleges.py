"""学院公告的分组与订阅开关。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import config, db
from xinge.sources.college_sites import COLLEGES

router = APIRouter(tags=["colleges"])


def _enabled_map() -> dict:
    return config.load().get("college_enabled") or {}


def enabled_keys() -> set[str]:
    """默认全部开启，只有显式关掉的才跳过。"""
    m = _enabled_map()
    return {c["key"] for c in COLLEGES if m.get(c["key"], True)}


@router.get("/colleges")
def list_colleges():
    m = _enabled_map()
    stats = db.count_by_category("college")

    out = []
    for c in COLLEGES:
        stat = stats.get(c["name"], {"count": 0, "unread": 0})
        out.append(
            {
                "key": c["key"],
                "name": c["name"],
                "url": c["url"],
                "mode": c["mode"],
                "enabled": bool(m.get(c["key"], True)) and c["mode"] != "todo",
                "count": stat["count"],
                "unread": stat["unread"],
            }
        )
    out.sort(key=lambda x: (-x["count"], x["name"]))
    return {"count": len(out), "colleges": out}


@router.patch("/colleges/{key}")
def toggle_college(key: str, body: dict):
    if not any(c["key"] == key for c in COLLEGES):
        raise HTTPException(status_code=404, detail="学院不存在")

    m = dict(_enabled_map())
    if "enabled" in body:
        m[key] = bool(body["enabled"])
    config.save({"college_enabled": m})
    return {"ok": True, "key": key, "enabled": bool(m.get(key, True))}
