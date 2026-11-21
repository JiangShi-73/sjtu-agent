"""给 QQ 机器人用的接口。

设计原则：机器人只需要"拉增量 → 推送 → 确认"，不需要懂业务。
所有接口都在 /api/v1/bot 下，只监听本机，没有额外鉴权。

典型循环（每分钟一次）：

    GET  /api/v1/bot/digest?limit=20        # 还没推送过的条目
    推送 QQ 消息
    POST /api/v1/bot/ack  {"ids": [...]}    # 标记已推送，下次不再给

另外两个按需接口：

    GET /api/v1/bot/deadlines?days=7        # 未来 N 天的截止事项（Canvas/实验/慕课）
    GET /api/v1/bot/activities?days=7       # 未来 N 天的第二课堂活动（含报名窗口、劳动学时）
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter

from .. import db

router = APIRouter(tags=["bot"])


def _plain(row: dict) -> dict:
    """把数据库行整理成机器人好用的结构。"""
    extra = row.get("extra")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError:
            extra = {}
    return {
        "id": row["id"],
        "source": row["source"],
        "board": row["board"],
        "category": row["category"],
        "title": row["title"],
        "summary": row.get("summary") or "",
        "url": row.get("url") or "",
        "published": row.get("published"),
        "deadline": row.get("deadline"),
        "created_at": row.get("created_at"),
        "extra": extra or {},
    }


@router.get("/bot/ping")
def ping():
    return {"ok": True, "time": datetime.now().isoformat(timespec="seconds")}


@router.get("/bot/digest")
def digest(since: str | None = None, limit: int = 30, only_unpushed: bool = True):
    rows = db.bot_digest(since, max(1, min(limit, 200)), only_unpushed)
    return {"count": len(rows), "items": [_plain(r) for r in rows]}


@router.post("/bot/ack")
def ack(body: dict):
    ids = [int(i) for i in (body.get("ids") or []) if str(i).isdigit()]
    return {"ok": True, "marked": db.mark_pushed(ids)}


@router.get("/bot/deadlines")
def deadlines(days: int = 7):
    """未来 N 天要交的东西（只有日历白名单里的三类）。"""
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=max(1, min(days, 90)))).isoformat()
    return {"days": days, "items": db.items_with_deadline(start, end)}


@router.get("/bot/activities")
def activities(days: int = 7, with_labor: bool = False):
    """未来 N 天的第二课堂活动。"""
    today = date.today()
    end = (today + timedelta(days=max(1, min(days, 90)))).isoformat()
    out = []

    for row in db.list_items("event", False, 300):
        item = _plain(row)
        start = (item["extra"].get("start") or item.get("published") or "")[:10]
        if not start or start < today.isoformat() or start > end:
            continue
        if with_labor and not item["extra"].get("labor_counted"):
            continue
        out.append(
            {
                "id": item["id"],
                "title": item["title"],
                "url": item["url"],
                "type": item["category"],
                "start": item["extra"].get("start"),
                "end": item["extra"].get("end"),
                "signup_start": item["extra"].get("signup_start"),
                "signup_end": item["extra"].get("signup_end"),
                "place": item["extra"].get("place"),
                "sponsor": item["extra"].get("sponsor"),
                "signup_method": item["extra"].get("signup"),
                "signed_up": item["extra"].get("signed_up"),
                "capacity": item["extra"].get("capacity"),
                "labor_counted": bool(item["extra"].get("labor_counted")),
                "labor_hours": item["extra"].get("labor_hours"),
                "new_era": bool(item["extra"].get("new_era")),
            }
        )

    out.sort(key=lambda x: x["start"] or "")
    return {"days": days, "count": len(out), "items": out}
