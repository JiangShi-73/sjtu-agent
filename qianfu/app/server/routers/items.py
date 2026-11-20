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
