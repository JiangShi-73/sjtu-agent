"""把日历渲染成图片，给 QQ 机器人发。

三种视图，信息密度不同：

    月  —— 格子里只有时间 + 截断的标题，能塞多少塞多少
    周  —— 一天一列，每条显示时间、标题、地点
    日  —— 逐条列出，带完整标题、时间、地点、备注

课表和任务可以分开，也可以一起：

    show=all     课表 + 任务
    show=events  只有课表/日程
    show=tasks   只有任务

渲染方式是拼一张 HTML 再用真浏览器截图，不依赖前端。
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import date, datetime, timedelta

SHOT_DIR = os.path.join(os.path.expanduser("~"), ".xinge", "calendar")
TTL_SECONDS = 300

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _cache_path(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(SHOT_DIR, f"{digest}.png")


def cached(key: str) -> bytes | None:
    path = _cache_path(key)
    try:
        if time.time() - os.path.getmtime(path) < TTL_SECONDS:
            with open(path, "rb") as fh:
                return fh.read()
    except OSError:
        pass
    return None


def _store(key: str, data: bytes) -> None:
    os.makedirs(SHOT_DIR, exist_ok=True)
    with open(_cache_path(key), "wb") as fh:
        fh.write(data)


def collect(show: str = "all") -> dict[str, list[dict]]:
    """把日程和任务按日期归好。

    Args:
        show: all / events / tasks。

    Returns:
        {"YYYY-MM-DD": [{"time": "10:00", "title": ..., "place": ..., "kind": "event"|"task"}]}
    """
    from . import db

    days: dict[str, list[dict]] = {}

    if show in ("all", "events"):
        for row in db.list_events("2020-01-01", "2030-12-31"):
            day = str(row.get("event_date") or "")[:10]
            if not day:
                continue
            days.setdefault(day, []).append(
                {
                    "time": row.get("start_time") or "",
                    "end": row.get("end_time") or "",
                    "title": row.get("title") or "",
                    "place": row.get("location") or "",
                    "note": row.get("note") or "",
                    "kind": "event",
                }
            )

    if show in ("all", "tasks"):
        for row in db.list_items(None, False, 2000):
            deadline = str(row.get("deadline") or "")
            if not deadline:
                continue
            day = deadline[:10]
            days.setdefault(day, []).append(
                {
                    "time": deadline[11:16],
                    "end": "",
                    "title": row.get("title") or "",
                    "place": row.get("category") or "",
                    "note": "",
                    "kind": "task",
                }
            )

    for items in days.values():
        items.sort(key=lambda x: (x["time"] or "99:99"))
    return days


def _cell_items(items: list[dict], limit: int, width: int) -> str:
    """格子里的条目：月视图只留时间 + 截断标题。"""
    out = []
    for it in items[:limit]:
        cls = "ev" if it["kind"] == "event" else "tk"
        title = it["title"][:width]
        if len(it["title"]) > width:
            title += "…"
        prefix = it["time"] + " " if it["time"] else ""
        out.append(f'<div class="chip {cls}">{prefix}{title}</div>')
    rest = len(items) - limit
    if rest > 0:
        out.append(f'<div class="more">还有 {rest} 项</div>')
    return "".join(out)


def month_grid(year: int, month: int) -> list[list[date]]:
    """算出月视图的 6×7 网格（周一起始）。

    自己算而不是用 calendar 模块——这个 Python 的 calendar 缺
    monthdatescalendar，靠不住。

    Args:
        year: 年。
        month: 月（1-12）。

    Returns:
        6 行，每行 7 个 date。
    """
    first = date(year, month, 1)
    start = first - timedelta(days=first.weekday())

    weeks = []
    for row in range(6):
        weeks.append([start + timedelta(days=row * 7 + col) for col in range(7)])
    return weeks


def _month_html(days: dict, anchor: date, show: str) -> tuple[str, str]:
    first = anchor.replace(day=1)
    weeks = month_grid(first.year, first.month)

    head = "".join(f"<th>{w}</th>" for w in WEEKDAYS)
    rows = []
    for week in weeks:
        cells = []
        for day in week:
            items = days.get(day.isoformat(), [])
            dim = "" if day.month == first.month else " dim"
            today = " today" if day == date.today() else ""
            cells.append(
                f'<td class="cell{dim}{today}">'
                f'<div class="dnum">{day.day}</div>'
                f"{_cell_items(items, 4, 12)}"
                f"</td>"
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")

    html = (
        f'<table class="month"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )
    return html, f"{first.year} 年 {first.month} 月"


def _week_html(days: dict, anchor: date, show: str) -> tuple[str, str]:
    monday = anchor - timedelta(days=anchor.weekday())
    head = "".join(
        f'<th>{WEEKDAYS[i]}<span>{ (monday + timedelta(days=i)).strftime("%m-%d") }</span></th>'
        for i in range(7)
    )

    cols = []
    for i in range(7):
        day = monday + timedelta(days=i)
        items = days.get(day.isoformat(), [])
        today = " today" if day == date.today() else ""
        body = []
        for it in items:
            cls = "ev" if it["kind"] == "event" else "tk"
            body.append(
                f'<div class="item {cls}">'
                f'<div class="t">{it["time"]}</div>'
                f'<div class="n">{it["title"]}</div>'
                + (f'<div class="p">{it["place"]}</div>' if it["place"] else "")
                + "</div>"
            )
        if not body:
            body.append('<div class="none">—</div>')
        cols.append(f'<td class="cell{today}">{"".join(body)}</td>')

    html = f'<table class="week"><thead><tr>{head}</tr></thead><tbody><tr>{"".join(cols)}</tr></tbody></table>'
    end = monday + timedelta(days=6)
    label = f'{monday.strftime("%m月%d日")} – {end.strftime("%m月%d日")}'
    return html, label


def _day_html(days: dict, anchor: date, show: str) -> tuple[str, str]:
    items = days.get(anchor.isoformat(), [])
    if not items:
        body = '<div class="empty">这天没有安排</div>'
    else:
        rows = []
        for it in items:
            cls = "ev" if it["kind"] == "event" else "tk"
            span = it["time"] + ("–" + it["end"] if it["end"] else "")
            rows.append(
                f'<div class="row {cls}">'
                f'<div class="time">{span or "全天"}</div>'
                f'<div class="main"><div class="n">{it["title"]}</div>'
                + (f'<div class="p">{it["place"]}</div>' if it["place"] else "")
                + (f'<div class="note">{it["note"]}</div>' if it["note"] else "")
                + "</div></div>"
            )
        body = "".join(rows)

    label = f'{anchor.strftime("%Y 年 %m 月 %d 日")} 周{WEEKDAYS[anchor.weekday()][-1]}'
    return f'<div class="day">{body}</div>', label


STYLE = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 26px 28px;
  font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
  background: #f7f8fa; color: #1f2430;
}
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { font-size: 12px; color: #8a90a0; margin-bottom: 16px; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; background: #fff; border-radius: 12px; overflow: hidden; }
th {
  background: #eef1f6; font-size: 12px; font-weight: 600; color: #5a6274;
  padding: 8px 6px; border: 1px solid #e3e7ef;
}
th span { display: block; font-weight: 400; color: #9aa1b2; font-size: 11px; }
td.cell { vertical-align: top; border: 1px solid #e3e7ef; padding: 6px; }
.month td.cell { height: 108px; width: 14.28%; }
.week td.cell { height: 420px; width: 14.28%; }
.dim { background: #fafbfc; }
.dim .dnum { color: #c3c8d4; }
.today { background: #eef5ff; }
.dnum { font-size: 12px; font-weight: 600; color: #5a6274; margin-bottom: 4px; }
.chip {
  font-size: 11px; line-height: 1.35; padding: 2px 5px; margin-bottom: 3px;
  border-radius: 5px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}
.chip.ev, .item.ev { background: #e6f0ff; color: #1a4fa0; }
.chip.tk, .item.tk { background: #f0f1f4; color: #4a5162; }
.more { font-size: 10px; color: #9aa1b2; }
.item { border-radius: 6px; padding: 6px 7px; margin-bottom: 6px; font-size: 12px; }
.item .t { font-weight: 700; font-size: 11px; opacity: .8; }
.item .n { margin-top: 2px; line-height: 1.4; }
.item .p { font-size: 11px; opacity: .7; margin-top: 2px; }
.none { color: #ccd1dc; text-align: center; padding-top: 20px; }
.day { background: #fff; border-radius: 12px; padding: 8px 0; }
.row { display: flex; gap: 14px; padding: 12px 18px; border-bottom: 1px solid #f0f2f6; }
.row:last-child { border-bottom: none; }
.row .time { width: 96px; font-size: 13px; font-weight: 700; color: #5a6274; flex: none; }
.row.ev .time { color: #1a4fa0; }
.row .main { flex: 1; }
.row .n { font-size: 14px; line-height: 1.45; }
.row .p { font-size: 12px; color: #7a8194; margin-top: 3px; }
.row .note { font-size: 11px; color: #9aa1b2; margin-top: 3px; }
.empty { padding: 40px; text-align: center; color: #9aa1b2; font-size: 14px; }
"""


def render(view: str = "month", show: str = "all", anchor: str = "") -> bytes:
    """渲染一张日历图。

    Args:
        view: month / week / day。
        show: all / events / tasks。
        anchor: 基准日期 YYYY-MM-DD，空则今天。

    Returns:
        PNG 字节。
    """
    view = view if view in ("month", "week", "day") else "month"
    show = show if show in ("all", "events", "tasks") else "all"

    try:
        day = datetime.strptime(anchor, "%Y-%m-%d").date() if anchor else date.today()
    except ValueError:
        day = date.today()

    key = f"{view}|{show}|{day.isoformat()}"
    hit = cached(key)
    if hit:
        return hit

    days = collect(show)
    if view == "month":
        body, label = _month_html(days, day, show)
    elif view == "week":
        body, label = _week_html(days, day, show)
    else:
        body, label = _day_html(days, day, show)

    scope = {"all": "课表 + 任务", "events": "只有课表/日程", "tasks": "只有任务"}[show]
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{STYLE}</style></head><body>"
        f"<h1>{label}</h1><div class='sub'>{scope}</div>"
        f"{body}</body></html>"
    )

    from playwright.sync_api import sync_playwright

    from xinge.browser import launch

    with sync_playwright() as p:
        browser = launch(p, headless=True)
        try:
            page = browser.new_page(viewport={"width": 1180, "height": 900}, device_scale_factor=2)
            page.set_content(html, wait_until="load")
            page.wait_for_timeout(300)
            data = page.screenshot(full_page=True)
        finally:
            browser.close()

    _store(key, data)
    return data
