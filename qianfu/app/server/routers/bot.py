"""给 QQ 机器人用的接口。

设计原则：机器人只需要"拉增量 → 推送 → 确认"，不需要懂业务。
所有接口都在 /api/v1/bot 下，只监听本机，没有额外鉴权。

典型循环（每分钟一次）：

    GET  /api/v1/bot/digest?limit=20        # 还没推送过的条目
    推送 QQ 消息
    POST /api/v1/bot/ack  {"ids": [...]}    # 标记已推送，下次不再给

另外几个按需接口：

    GET /api/v1/bot/deadlines?days=7        # 未来 N 天的截止事项（Canvas/实验/慕课）
    GET /api/v1/bot/activities?days=7       # 未来 N 天的第二课堂活动（含报名窗口、劳动学时）
    GET  /api/v1/bot/activities?filter=labor # 按属性筛活动（labor / newera / all）
    GET  /api/v1/bot/activities/new.png      # 最近 N 小时的新活动列表图（QQ 推送用）
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import config, db, reminders, shots

router = APIRouter(tags=["bot"])


def date_today():
    """今天（单独抽出来方便测试替换）。"""
    return date.today()


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


# ---------------- 提醒队列（QQ 插件每 60 秒拉一次） ----------------

@router.get("/bot/reminders/due")
def reminders_due(limit: int = 10):
    """到点要推的提醒。推完调 ack，插件靠 target_bound 判断有没有配置推送对象。"""
    settings = reminders.get_settings()
    rows = reminders.due(max(1, min(limit, 100)))
    return {
        "count": len(rows),
        "target_bound": reminders.target_bound(),
        "enabled": bool(settings["reminder_enabled"]),
        "reminders": rows,
    }


@router.post("/bot/reminders/ack")
def reminders_ack(body: dict):
    """确认提醒：默认标成已推送；mode=skip / failed 标对应状态。"""
    ids = [int(i) for i in (body.get("ids") or []) if str(i).isdigit()]
    mode = str(body.get("mode") or "sent").strip().lower()
    if mode == "skip":
        marked = reminders.mark_skipped(ids)
    elif mode in ("failed", "fail"):
        marked = sum(reminders.mark_failed(i) for i in ids)
    else:
        marked = reminders.ack(ids)
    return {"ok": True, "marked": marked}


@router.get("/bot/reminders/target")
def reminder_target():
    """当前推送对象（QQ 私聊 umo）——App 的「推送对象」就读写这里。"""
    return reminders.get_target()


@router.post("/bot/reminders/target")
def set_reminder_target(body: dict):
    """手动指定推送对象；umo 传空串表示解绑。"""
    return {"ok": True, "target": reminders.set_target(body.get("umo") or "")}


@router.post("/bot/reminders/bind")
def bind_reminder_target(body: dict):
    """插件收到私聊时上报：只在「还没有推送对象」时自动绑定并记下这个联系人。

    隐私最小化：之后别人的私聊一律不落库（openid / 昵称不再永久保存），
    也不会改掉已经设好的推送对象。QQ 官方机器人拿不到 QQ 号，只有 openid。
    """
    umo = str(body.get("umo") or "").strip()
    sender_id = str(body.get("sender_id") or "").strip()
    nickname = str(body.get("nickname") or "").strip()
    openid = str(body.get("openid") or "").strip() or sender_id
    if not openid and ":" in umo:
        openid = umo.rsplit(":", 1)[-1]

    # 已经有推送对象：这次私聊不记录任何人，直接返回现状
    if reminders.target_bound():
        return {
            "ok": True,
            "accepted": False,
            "reason": "已有推送对象，不再记录新的联系人",
            "target": reminders.get_target(),
        }

    result = reminders.bind_target(umo, sender_id=sender_id, nickname=nickname)
    if openid and result.get("accepted"):
        contact = db.upsert_bot_contact(openid, nickname)
        if contact:
            result["contact"] = contact
    return result


@router.get("/bot/contacts")
def bot_contacts(limit: int = 50):
    """最近和机器人私聊过的人：App 的「推送对象」下拉列表读这里（只留 30 天）。"""
    return db.list_bot_contacts(max(1, min(limit, 200)))


@router.delete("/bot/contacts/{openid}")
def remove_bot_contact(openid: str):
    """删掉某个人被记下的 openid / 昵称（隐私最小化：可单条删除）。"""
    return {"ok": True, "deleted": db.delete_bot_contact(openid)}


# ---------------- 提醒参数（App 可改） ----------------

@router.get("/bot/reminders/settings")
def reminder_settings():
    """提醒参数：总开关 / 提前量 / 静默时段 / 每小时上限。"""
    return reminders.get_settings()


@router.post("/bot/reminders/settings")
def set_reminder_settings(body: dict):
    """改提醒参数，返回改后的全量。改提前量/静默时段会自动重算 pending。"""
    return reminders.set_settings(body or {})


@router.get("/bot/deadlines")
def deadlines(days: int = 7):
    """未来 N 天要交的东西（只有日历白名单里的三类）。"""
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=max(1, min(days, 90)))).isoformat()
    return {"days": days, "items": db.items_with_deadline(start, end)}


def _activity_rows(days: int, kind: str = "all", limit: int = 20) -> list[dict]:
    """未来 N 天的第二课堂活动（带劳动学时 / 新时代讲座属性）。

    属性从 activity_meta 表读（列表接口拿不到，后台逐条补的）。
    """
    from .. import activity_meta

    return activity_meta.future_activities(days=days, kind=kind, limit=limit)


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
def activities(
    days: int = 14,
    filter: str = "all",
    limit: int = 20,
    format: str = "json",
    with_labor: bool = False,
):
    """未来 N 天的第二课堂活动，可以按属性筛。

    Args:
        days: 往后看几天（1-90）。
        filter: labor 只看有劳动学时的 / newera 只看新时代讲座课 / all 全部。
        limit: 最多返回几条。
        format: text 直接给一段能发到 QQ 的文本（带序号）。
        with_labor: 老参数，等价于 filter=labor。
    """
    if filter in ("labor", "newera"):
        kind = filter
    else:
        kind = "labor" if with_labor else "all"
    rows = _activity_rows(days, kind, limit)
    if format == "text":
        return {"days": days, "filter": kind, "count": len(rows),
                "text": _text_list(rows)}
    return {"days": days, "filter": kind, "count": len(rows),
            "activities": rows, "items": rows}


@router.get("/bot/activities/new.png")
def activities_new_png(hours: int = 24):
    """把最近 N 小时的新活动画成一张图（QQ 推送用，失败不抛业务异常）。"""
    from .. import actrender

    try:
        data = actrender.render(hours)
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"渲染失败：{exc}") from exc

    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": f"max-age={actrender.TTL_SECONDS}"},
    )


def _find_activity(item_id: int) -> dict:
    for row in db.list_items("event", False, 300):
        if row["id"] == item_id:
            item = _plain(row)
            if item["source"] != "second_classroom":
                raise HTTPException(status_code=400, detail="这条不是第二课堂活动")
            return item
    raise HTTPException(status_code=404, detail="活动不存在")


def _item_when(row: dict) -> str:
    """列表里该显示的时间。

    第二课堂活动的 deadline 存的是「报名截止」，不是活动时间——活动的开始
    时间在 extra.start（抓取时同时写进 published）。其它板块的 deadline 就是
    真正的截止时间，照旧优先用它。
    """
    if row.get("source") == "second_classroom":
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        raw = extra.get("start") or row.get("published") or ""
    else:
        raw = row.get("deadline") or row.get("published") or row.get("date") or ""
    return str(raw).replace("T", " ")[:16]


def _activity_row(row: dict) -> dict:
    """一条第二课堂活动的机器人视图。

    活动没有「截止」概念：把 deadline（=报名截止）清掉，报名截止单独放
    signup_end，活动时间放 start / published。否则机器人列表会把报名截止
    当成活动时间显示（/活动 里日期会整体提前一天）。
    """
    item = _plain(row)
    extra = item.get("extra") or {}
    start = _item_when(item)
    end = str(extra.get("end") or "").replace("T", " ")[:16]
    signup_end = str(extra.get("signup_end") or "").replace("T", " ")[:16]
    item.update(
        {
            "start": start,
            "end": end,
            "place": str(extra.get("place") or ""),
            "signup_end": signup_end,
            "published": item.get("published") or start,
            "deadline": None,          # 活动没有截止时间，别再拿报名截止冒充
        }
    )
    return item


def _activity_items(limit: int) -> list[dict]:
    """第二课堂活动的机器人视图列表（按活动开始时间倒序，和活动板块一致）。"""
    rows = db.list_items("event", False, max(1, min(limit, 50)))
    return [_activity_row(r) for r in rows]


def _activity_text(rows: list[dict]) -> str:
    """第二课堂活动 → 一段能直接发 QQ 的话（时间用活动开始时间）。"""
    if not rows:
        return "第二课堂暂无内容"
    lines = [f"第二课堂（{len(rows)} 条）"]
    for i, it in enumerate(rows, 1):
        lines.append(f"{i}. {it['title']}")
        tail = [x for x in (it.get("start") or "", it.get("place") or "") if x]
        if tail:
            lines.append("   " + " · ".join(tail))
        if it.get("signup_end"):
            lines.append(f"   报名截止 {it['signup_end']}")
    return "\n".join(lines)


def _board_items(board: str, limit: int, format: str, title: str) -> dict:
    """按板块取条目，text 模式直接给机器人念。"""
    rows = [_plain(r) for r in db.list_items(board, False, max(1, min(limit, 50)))]
    if format == "text":
        if not rows:
            return {"count": 0, "text": f"{title}暂无内容"}
        lines = [f"{title}（{len(rows)} 条）"]
        for i, r in enumerate(rows, 1):
            lines.append(f'{i}. {r["title"]}')
            when = _item_when(r)
            if when:
                lines.append(f"   {when}")
        return {"count": len(rows), "text": "\n".join(lines)}
    return {"count": len(rows), "items": rows}


@router.get("/bot/boards")
def bot_boards():
    """有哪些板块、各多少条。"""
    return {"boards": db.boards()}


@router.get("/bot/canvas")
def bot_canvas(limit: int = 10, format: str = "json"):
    """Canvas 待交作业。"""
    return _board_items("course", limit, format, "Canvas 作业")


@router.get("/bot/cnmooc")
def bot_cnmooc(limit: int = 10, format: str = "json"):
    """慕课作业与测验。"""
    return _board_items("mooc", limit, format, "慕课作业")


@router.get("/bot/pe")
def bot_pe(limit: int = 10, format: str = "json"):
    """体育实验。"""
    return _board_items("lab", limit, format, "体育实验")


@router.get("/bot/second-classroom")
def bot_second_classroom(limit: int = 10, format: str = "json"):
    """第二课堂活动（列表，挑一个再用 /bot/activity/{id} 看详情）。

    时间一律是**活动开始时间**（start，等于 extra.start）；报名截止单独放在
    signup_end。这里以前直接返回原始 items 行，deadline 字段是报名截止，
    机器人列表（/活动）拿 deadline 当活动时间显示，日期就整体错了。
    """
    rows = _activity_items(limit)
    if format == "text":
        return {"count": len(rows), "text": _activity_text(rows)}
    return {"count": len(rows), "items": rows}


@router.get("/bot/zhitu")
def bot_zhitu(limit: int = 10, format: str = "json"):
    """知途好课的课程与任务。"""
    return _board_items("zhitu", limit, format, "知途好课")


@router.get("/bot/search")
def bot_search(q: str, limit: int = 10, format: str = "json"):
    """全库搜标题。"""
    needle = q.strip().lower()
    if not needle:
        raise HTTPException(status_code=400, detail="搜索词不能为空")

    rows = []
    for row in db.list_items(None, False, 2000):
        if needle in (row["title"] or "").lower():
            rows.append(_plain(row))
            if len(rows) >= max(1, min(limit, 50)):
                break

    if format == "text":
        if not rows:
            return {"count": 0, "text": f"没有找到「{q}」"}
        lines = [f"「{q}」找到 {len(rows)} 条"]
        for i, r in enumerate(rows, 1):
            lines.append(f'{i}. [{r["board"]}] {r["title"]}')
        return {"count": len(rows), "text": "\n".join(lines)}

    return {"count": len(rows), "query": q, "items": rows}


@router.post("/bot/colleges/{key}")
def bot_toggle_college(key: str, body: dict):
    """机器人也能开关某个学院的订阅。"""
    from xinge.sources.college_sites import COLLEGES

    if not any(c["key"] == key for c in COLLEGES):
        raise HTTPException(status_code=404, detail="学院不存在")

    m = dict(config.load().get("college_enabled") or {})
    if "enabled" in body:
        m[key] = bool(body["enabled"])
    config.save({"college_enabled": m})
    return {"ok": True, "key": key, "enabled": bool(m.get(key, True))}


@router.get("/bot/colleges")
def bot_colleges(format: str = "json"):
    """学院公告的学院列表（带订阅状态和条数）。"""
    from xinge.sources.college_sites import COLLEGES

    cfg = config.load()
    enabled_map = cfg.get("college_enabled") or {}
    stats = db.count_by_category("college")

    rows = []
    for c in COLLEGES:
        stat = stats.get(c["name"], {"count": 0, "unread": 0})
        rows.append(
            {
                "key": c["key"],
                "name": c["name"],
                "enabled": bool(enabled_map.get(c["key"], True)) and c["mode"] != "todo",
                "count": stat["count"],
                "unread": stat["unread"],
            }
        )
    rows.sort(key=lambda x: (-x["count"], x["name"]))

    if format == "text":
        lines = [f"学院公告（{len(rows)} 个学院）"]
        for i, r in enumerate(rows, 1):
            mark = "" if r["enabled"] else "（未订阅）"
            lines.append(f'{i}. {r["name"]}  {r["count"]} 条{mark}')
        return {"count": len(rows), "text": "\n".join(lines)}

    return {"count": len(rows), "colleges": rows}


@router.get("/bot/colleges/{key}")
def bot_college_items(key: str, limit: int = 10, format: str = "json"):
    """某个学院的公告。"""
    from xinge.sources.college_sites import COLLEGES

    site = next((c for c in COLLEGES if c["key"] == key), None)
    if not site:
        raise HTTPException(status_code=404, detail="学院不存在")

    rows = []
    for row in db.list_items("college", False, 500):
        if row["category"] != site["name"]:
            continue
        item = _plain(row)
        rows.append(
            {
                "id": item["id"],
                "title": item["title"],
                "url": item["url"],
                "date": item["published"],
                "college": site["name"],
            }
        )
        if len(rows) >= max(1, min(limit, 50)):
            break

    if format == "text":
        if not rows:
            return {"count": 0, "text": f'{site["name"]}暂无公告'}
        lines = [f'{site["name"]}（{len(rows)} 条）']
        for i, r in enumerate(rows, 1):
            lines.append(f'{i}. {r["title"]}')
            if r["date"]:
                lines.append(f'   {r["date"][:10]}')
        return {"count": len(rows), "text": "\n".join(lines)}

    return {"count": len(rows), "college": site["name"], "items": rows}


@router.get("/bot/calendar.png")
def bot_calendar_png(
    view: str = "month",
    show: str = "all",
    date: str = "",
):
    """把日历渲染成图片发给机器人。

    Args:
        view: month 月 / week 周 / day 日。
        show: all 课表+任务 / events 只有课表 / tasks 只有任务。
        date: 基准日期 YYYY-MM-DD，空则今天。
    """
    from .. import calrender

    try:
        data = calrender.render(view, show, date)
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"渲染失败：{exc}") from exc

    return Response(content=data, media_type="image/png")


@router.get("/bot/calendar")
def bot_calendar_text(
    view: str = "month",
    show: str = "all",
    date: str = "",
    format: str = "text",
):
    """同一份数据的文字版（不方便看图时用）。"""
    from .. import calrender

    days = calrender.collect(show if show in ("all", "events", "tasks") else "all")
    try:
        anchor = datetime.strptime(date, "%Y-%m-%d").date() if date else date_today()
    except ValueError:
        anchor = date_today()

    if view == "day":
        keys = [anchor.isoformat()]
        label = anchor.strftime("%m 月 %d 日")
    elif view == "week":
        monday = anchor - timedelta(days=anchor.weekday())
        keys = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
        label = f'{monday.strftime("%m-%d")} 那一周'
    else:
        keys = sorted(days)
        label = f'{anchor.year} 年 {anchor.month} 月'
        keys = [k for k in keys if k.startswith(f'{anchor.year}-{anchor.month:02d}')]

    lines = [f"{label}"]
    for key in keys:
        items = days.get(key) or []
        if not items:
            continue
        lines.append(f"\n{key}")
        for it in items:
            mark = "" if it["kind"] == "event" else "（任务）"
            lines.append(f'  {it["time"] or "全天"} {it["title"]}{mark}')
    if len(lines) == 1:
        lines.append("这段时间没有安排")

    return {"count": len(lines) - 1, "text": "\n".join(lines)}


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
    """单个活动的详情（机器人回文字用）。属性优先读缓存表。"""
    from .. import activity_meta

    item = _find_activity(item_id)
    x = item["extra"]
    meta = activity_meta.meta_map([item_id]).get(item_id) or {}
    labor_counted = bool(meta.get("labor_counted", False)) if meta else bool(x.get("labor_counted"))
    labor_hours = meta.get("labor_hours") if meta else x.get("labor_hours")
    new_era = bool(meta.get("new_era", False)) if meta else bool(x.get("new_era"))
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
        "labor_counted": labor_counted,
        "labor_hours": labor_hours,
        "new_era": new_era,
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
