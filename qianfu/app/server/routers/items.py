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
):
    return {"items": db.list_items(board, unread, limit)}


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
def schedule_item(item_id: int):
    """把一条信息一键加成日程（第二课堂等活动用）。"""
    items = [i for i in db.list_items(None, False, 1000) if i["id"] == item_id]
    if not items:
        raise HTTPException(status_code=404, detail="条目不存在")
    item = items[0]
    extra = item.get("extra") or {}
    event_date = (item.get("published") or "")[:10] or ""
    if not event_date:
        raise HTTPException(status_code=400, detail="这条信息没有日期，无法排期")

    event = db.create_event(
        {
            "title": item["title"],
            "event_date": event_date,
            "start_time": (item.get("published") or "")[11:16],
            "end_time": "",
            "location": extra.get("place") or extra.get("location") or "",
            "note": item.get("summary") or "",
            "source": "item",
        }
    )
    return {"ok": True, "event": event}
