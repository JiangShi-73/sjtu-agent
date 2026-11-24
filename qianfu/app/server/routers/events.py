"""日程：手动增删改 + 对话式创建。

GET /events 同时返回两类数据：
  events  —— 用户手动/对话创建的
  derived —— 带截止时间的信息条目（只读，画在日历上）
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from .. import db, nlp

router = APIRouter(tags=["events"])


@router.get("/events")
def list_events(
    start: str = Query(default=""),
    end: str = Query(default=""),
):
    if not start or not end:
        today = date.today()
        start = (today - timedelta(days=7)).isoformat()
        end = (today + timedelta(days=45)).isoformat()
    events = db.list_events(start, end)
    return {
        # done 本来是 0/1，这里统一成布尔给前端划线用
        "events": [{**e, "done": bool(e.get("done"))} for e in events],
        "derived": db.items_with_deadline(start, end),
    }


@router.post("/events")
def create_event(body: dict):
    title = (body.get("title") or "").strip()
    event_date = (body.get("event_date") or "").strip()
    if not title or not event_date:
        raise HTTPException(status_code=400, detail="title 和 event_date 必填")
    event = db.create_event({**body, "title": title, "event_date": event_date})
    return {"ok": True, "event": event}


@router.patch("/events/{event_id}")
def update_event(event_id: int, body: dict):
    if not db.get_event(event_id):
        raise HTTPException(status_code=404, detail="事件不存在")
    return {"ok": True, "event": db.update_event(event_id, body)}


@router.delete("/events/{event_id}")
def delete_event(event_id: int):
    if not db.delete_event(event_id):
        raise HTTPException(status_code=404, detail="事件不存在")
    return {"ok": True}


@router.post("/events/parse")
def preview(body: dict):
    """只解析不落库，给前端做预览。"""
    return nlp.parse(body.get("text", ""))


@router.post("/chat")
def chat(body: dict):
    """一句话建日程。缺信息就反问，不猜。"""
    text = (body.get("text") or "").strip()
    if not text:
        return {"ok": False, "reply": "说一句你想安排的事吧，比如「明天下午3点在图书馆开会」。"}

    parsed = nlp.parse(text)
    missing = parsed["missing"]

    if "date" in missing:
        return {
            "ok": False,
            "parsed": parsed,
            "reply": "这是哪一天？可以说「明天」「下周三」或者「11月25日」。",
        }
    if "title" in missing:
        return {
            "ok": False,
            "parsed": parsed,
            "reply": "要做什么？给我一个标题就行。",
        }

    event = db.create_event(
        {
            "title": parsed["title"],
            "event_date": parsed["event_date"],
            "start_time": parsed["start_time"],
            "end_time": parsed["end_time"],
            "location": parsed["location"],
            "note": parsed["raw"],
            "source": "chat",
        }
    )

    when = parsed["event_date"]
    if parsed["start_time"]:
        when += f" {parsed['start_time']}"
        if parsed["end_time"]:
            when += f"–{parsed['end_time']}"
    place = f"，地点 {parsed['location']}" if parsed["location"] else ""
    return {
        "ok": True,
        "event": event,
        "parsed": parsed,
        "reply": f"已安排：{when} · {parsed['title']}{place}",
    }
