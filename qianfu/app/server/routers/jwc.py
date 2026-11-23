"""教务网：课表查询与同步进日历。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import db
from xinge import jwc

router = APIRouter(tags=["jwc"])


@router.get("/jwc/status")
def status():
    return {"logged_in": jwc.logged_in()}


@router.post("/jwc/login")
async def login():
    result = await asyncio.to_thread(jwc.login)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "登录失败")
    return result


@router.get("/jwc/timetable")
async def timetable(year: int | None = None, term: int | None = None):
    """拉课表。"""
    try:
        return await asyncio.to_thread(jwc.timetable, year, term)
    except jwc.JwcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jwc/calendar")
async def calendar():
    """本学期起止日期。"""
    try:
        return await asyncio.to_thread(jwc.term_calendar)
    except jwc.JwcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jwc/sync")
async def sync(weeks: int = 0, all_weeks: bool = True):
    """把课表写进日历。

    Args:
        weeks: 只排前 N 周，0 表示排满整个学期。
        all_weeks: 是否排满学期，默认 True。
    """
    limit = weeks if weeks and weeks > 0 else None
    try:
        events = await asyncio.to_thread(jwc.to_events, limit)
    except jwc.JwcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created = 0
    skipped = 0
    for event in events:
        if db.event_exists(event["title"], event["event_date"], event.get("start_time")):
            skipped += 1
            continue
        db.create_event(event)
        created += 1

    return {
        "ok": True,
        "total": len(events),
        "created": created,
        "skipped": skipped,
        "weeks": limit or "全部",
    }
