"""信息源：列表、手动抓取。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..collector import fetch_all, fetch_one

router = APIRouter(tags=["sources"])

KNOWN = {
    "canvas": "Canvas 课程平台",
    "cnmooc": "好大学在线 · 站内消息",
    "pe": "物理实验通知",
    "second_classroom": "第二课堂活动",
    "rss": "RSS 订阅",
    "aihaoke": "知途好课",
    "college": "学院公告",
    "shuiyuan": "水源社区",
}


@router.get("/sources")
def list_sources():
    states = {s["key"]: s for s in db.source_states()}
    out = []
    for key, name in KNOWN.items():
        st = states.get(key, {})
        out.append(
            {
                "key": key,
                "name": name,
                "board": db.SOURCE_BOARD.get(key, "custom"),
                "last_run": st.get("last_run"),
                "last_ok": bool(st.get("last_ok")) if st else None,
                "last_error": st.get("last_error", ""),
                "last_count": st.get("last_count", 0),
            }
        )
    return {"sources": out}


@router.post("/sources/{key}/fetch")
async def fetch_source(key: str):
    if key not in KNOWN:
        raise HTTPException(status_code=404, detail=f"未知信息源: {key}")
    return await fetch_one(key)


@router.post("/sources/fetch-all")
async def fetch_everything():
    return {"results": await fetch_all()}
