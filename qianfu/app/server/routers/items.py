"""信息条目与板块。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import db

router = APIRouter(tags=["items"])


@router.get("/boards")
def list_boards():
    return db.boards()


@router.get("/items")
def list_items(
    board: str | None = Query(None),
    unread: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    college: str | None = Query(None, description="只看某个学院（学院公告板块用）"),
):
    items = db.list_items(board, unread, limit, category=college)
    return {"items": items, "college": college}


@router.post("/items/{item_id}/read")
def mark_read(item_id: int):
    if not db.mark_read(item_id):
        raise HTTPException(status_code=404, detail="条目不存在或已读")
    return {"ok": True}


@router.delete("/items/read")
def clear_read():
    """一键清掉所有已读条目。"""
    return {"ok": True, "deleted": db.delete_read_items()}


@router.delete("/items/{item_id}")
def delete_item(item_id: int):
    if not db.delete_item(item_id):
        raise HTTPException(status_code=404, detail="条目不存在")
    return {"ok": True}


@router.post("/items/{item_id}/schedule")
def schedule_item(item_id: int, kind: str = "start"):
    """把一条信息加成日程。

    kind=start  —— 活动开始时间（默认）
    kind=signup —— 报名截止时间
    """
    items = [i for i in db.list_items(None, False, 1000) if i["id"] == item_id]
    if not items:
        raise HTTPException(status_code=404, detail="条目不存在")
    item = items[0]

    extra = item.get("extra") or {}
    if isinstance(extra, str):
        import json as _json

        try:
            extra = _json.loads(extra)
        except ValueError:
            extra = {}

    if kind == "signup":
        raw = extra.get("signup_end") or ""
        when = raw.replace("T", " ")[:16]
        title = "报名截止 · " + item["title"]
    else:
        raw = extra.get("start") or item.get("published") or ""
        when = raw.replace("T", " ")[:16]
        title = item["title"]

    if not when or len(when) < 10:
        raise HTTPException(status_code=400, detail=f"这条信息没有{'报名截止' if kind == 'signup' else '开始'}时间")

    end_time = ""
    if kind == "start":
        end_raw = (extra.get("end") or "").replace("T", " ")
        end_time = end_raw[11:16] if len(end_raw) >= 16 else ""

    event = db.create_event(
        {
            "title": title,
            "event_date": when[:10],
            "start_time": when[11:16],
            "end_time": end_time,
            "location": extra.get("place") or extra.get("location") or "",
            "note": item.get("summary") or "",
            "source": "item",
        }
    )
    return {"ok": True, "kind": kind, "event": event}
