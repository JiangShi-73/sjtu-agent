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

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import db, shots

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


def _activity_rows(days: int, with_labor: bool) -> list[dict]:
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
    return out


def _text_list(rows: list[dict]) -> str:
    """给 QQ 直接发的一段话。带序号，用户回「3」就能选中第 3 个。"""
    lines = [f"第二课堂活动（共 {len(rows)} 个）"]
    for i, it in enumerate(rows, 1):
        when = (it.get("start") or "")[:16].replace("T", " ")
        tail = []
        if it.get("signup_end"):
            tail.append("报名截止 " + it["signup_end"][:16].replace("T", " "))
        if it.get("labor_counted"):
            tail.append(f"劳动学时 {it.get('labor_hours') or 0:g}")
        if it.get("new_era"):
            tail.append("新时代讲座")
        lines.append(f"{i}. {it['title']}")
        lines.append(f"   {when}" + ("  ·  " + " · ".join(tail) if tail else ""))
    return "\n".join(lines)


@router.get("/bot/activities")
def activities(days: int = 7, with_labor: bool = False, format: str = "json"):
    """未来 N 天的第二课堂活动。

    format=text 直接给一段可以发到 QQ 的文本（带序号）。
    """
    rows = _activity_rows(days, with_labor)
    if format == "text":
        return {"days": days, "count": len(rows), "text": _text_list(rows)}
    return {"days": days, "count": len(rows), "items": rows}


def _find_activity(item_id: int) -> dict:
    for row in db.list_items("event", False, 300):
        if row["id"] == item_id:
            item = _plain(row)
            if item["source"] != "second_classroom":
                raise HTTPException(status_code=400, detail="这条不是第二课堂活动")
            return item
    raise HTTPException(status_code=404, detail="活动不存在")


@router.get("/bot/mail")
def bot_mail(limit: int = 10, format: str = "json"):
    """收件箱最新邮件（机器人推送用）。"""
    from .. import config
    from xinge import mail as mailer

    cfg = config.load()
    address = cfg.get("mail_address") or ""
    password = cfg.get("mail_password") or ""
    if not (address and password):
        raise HTTPException(status_code=400, detail="还没有配置邮箱")

    try:
        data = mailer.fetch_messages(address, password, limit=max(1, min(limit, 50)))
    except mailer.MailError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if format == "text":
        if not data["messages"]:
            return {"count": 0, "text": "收件箱是空的"}
        lines = [f"收件箱（未读 {data['unread']} / 共 {data['total']}）"]
        for i, m in enumerate(data["messages"], 1):
            lines.append(f"{i}. {m['subject']}")
            lines.append(f"   {m['from_name'] or m['from_addr']} · {m['date']}")
        return {"count": len(data["messages"]), "text": "\n".join(lines)}

    return data


@router.get("/bot/aihaoke")
def aihaoke(days: int = 30, format: str = "json"):
    """知途好课：进行中的课程 + 作业截止。"""
    from datetime import date, timedelta

    today = date.today()
    end = (today + timedelta(days=max(1, min(days, 120)))).isoformat()

    rows = []
    for row in db.list_items("zhitu", False, 200):
        item = _plain(row)
        if not db.is_scheduled(item):
            continue
        rows.append(
            {
                "id": item["id"],
                "title": item["title"],
                "course": item["extra"].get("course"),
                "teacher": item["extra"].get("teacher"),
                "deadline": item["deadline"],
                "url": item["url"],
            }
        )
    rows.sort(key=lambda x: x["deadline"] or "")

    if format == "text":
        if not rows:
            return {"count": 0, "text": "知途好课暂无待交作业"}
        lines = [f"知途好课待交作业（{len(rows)} 个）"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   截止 {r['deadline'][:16]}")
        return {"count": len(rows), "text": "\n".join(lines)}

    return {"count": len(rows), "items": rows}


@router.get("/bot/activity/{item_id}")
def activity_detail(item_id: int):
    """单个活动的详情（机器人回文字用）。"""
    item = _find_activity(item_id)
    x = item["extra"]
    return {
        "id": item["id"],
        "title": item["title"],
        "url": item["url"],
        "type": item["category"],
        "start": x.get("start"),
        "end": x.get("end"),
        "signup_start": x.get("signup_start"),
        "signup_end": x.get("signup_end"),
        "place": x.get("place"),
        "sponsor": x.get("sponsor"),
        "signup_method": x.get("signup"),
        "signed_up": x.get("signed_up"),
        "capacity": x.get("capacity"),
        "labor_counted": bool(x.get("labor_counted")),
        "labor_hours": x.get("labor_hours"),
        "new_era": bool(x.get("new_era")),
        "introduction": x.get("introduction"),
        "presenter": x.get("presenter"),
        "shot": f"/api/v1/bot/activity/{item_id}/shot.png",
    }


@router.get("/bot/activity/{item_id}/shot.png")
def activity_shot(item_id: int, force: bool = False):
    """把活动详情页截成 PNG，机器人直接发图。"""
    item = _find_activity(item_id)
    if not item["url"]:
        raise HTTPException(status_code=400, detail="这条活动没有详情页地址")

    try:
        png = shots.capture(item["url"], force=force)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:                       # 浏览器超时等
        raise HTTPException(status_code=502, detail=f"截图失败：{exc}") from exc

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": f"max-age={shots.TTL_SECONDS}"},
    )
