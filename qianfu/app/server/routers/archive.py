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


@router.get("/scheduler")
def scheduler_status():
    """定时任务状态。"""
    return scheduler.status()
