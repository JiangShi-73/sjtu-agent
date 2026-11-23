"""存档区与定时任务。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from .. import db, scheduler

router = APIRouter(tags=["archive"])


@router.get("/archive")
def archive_list(board: str | None = None, limit: int = 200):
    """存档区：过了 ddl 的条目，按板块分。"""
    items = db.list_items(board, False, max(1, min(limit, 500)), archived=True)
    stats = {row["board"]: row for row in db.archive_stats()}
    return {"count": len(items), "items": items, "boards": stats}


@router.post("/archive/run")
async def archive_run(grace_days: int = 0, keep_days: int = 7):
    """手动跑一次归档 + 清理。"""
    moved = await asyncio.to_thread(db.archive_due, grace_days)
    removed = await asyncio.to_thread(db.purge_archived, keep_days)
    return {"ok": True, "archived": moved, "purged": removed}


@router.delete("/archive/{item_id}")
def archive_restore(item_id: int):
    """从存档区恢复（其实是从存档里彻底删掉）。"""
    return {"ok": db.delete_item(item_id)}


@router.post("/items/read-all")
def read_all(board: str | None = None):
    """把某个板块（不传就是全部）的未读一次标掉。"""
    return {"ok": True, "marked": db.mark_all_read(board)}


@router.post("/items/read-batch")
def read_batch(payload: dict):
    """批量标已读：{"ids": [1,2,3]}"""
    return {"ok": True, "marked": db.mark_read_many(payload.get("ids") or [])}


@router.post("/items/archive-batch")
def archive_batch(payload: dict):
    """批量归档：{"ids": [1,2,3]}"""
    return {"ok": True, "archived": db.archive_items(payload.get("ids") or [])}


@router.get("/scheduler")
def scheduler_status():
    """定时任务状态。"""
    return scheduler.status()
