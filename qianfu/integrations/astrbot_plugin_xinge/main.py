"""信鸽 SJTU —— AstrBot 插件。

把信鸽的抓取结果接进 QQ。信鸽后端跑在 http://127.0.0.1:8722，
这里只调 /api/v1/bot/* 那几个接口，不重复实现业务逻辑。
"""

from __future__ import annotations

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

BASE = "http://127.0.0.1:8722/api/v1"


async def _get(path: str, **params) -> dict:
    params.setdefault("format", "text")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(BASE + path, params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, payload: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(BASE + path, json=payload or {})
        resp.raise_for_status()
        return resp.json()


class XingePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # ---------- 概览 ----------

    @filter.command("信鸽")
    async def overview(self, event: AstrMessageEvent):
        """信鸽总览：各板块还有多少条"""
        try:
            data = await _get("/bot/boards")
        except Exception as exc:
            logger.error(f"信鸽未启动: {exc}")
            yield event.plain_result("信鸽后端没起来，先运行 python main.py")
            return

        lines = ["信鸽 SJTU"]
        for board in data.get("boards") or []:
            lines.append(f'{board["name"]}  {board["unread"]} 条未读')
        yield event.plain_result("\n".join(lines))

    @filter.command("今天")
    async def today(self, event: AstrMessageEvent):
        """今天/近期要交的东西"""
        try:
            data = await _get("/bot/deadlines", days=7)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or []
        if not items:
            yield event.plain_result("未来 7 天没有要交的")
            return

        lines = [f"未来 7 天（{len(items)} 项）"]
        for i, it in enumerate(items[:10], 1):
            lines.append(f'{i}. {it.get("title", "")[:40]}')
            if it.get("deadline"):
                lines.append(f'   截止 {str(it["deadline"])[:16]}')
        yield event.plain_result("\n".join(lines))

    # ---------- 各板块 ----------

    @filter.command("作业")
    async def canvas(self, event: AstrMessageEvent):
        """Canvas 待交作业"""
        try:
            data = await _get("/bot/canvas", limit=10)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "暂无作业")

    @filter.command("慕课")
    async def cnmooc(self, event: AstrMessageEvent):
        """慕课作业与测验"""
        try:
            data = await _get("/bot/cnmooc", limit=10)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "暂无慕课作业")

    @filter.command("实验")
    async def pe(self, event: AstrMessageEvent):
        """体育实验"""
        try:
            data = await _get("/bot/pe", limit=10)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "暂无实验")

    @filter.command("二课")
    async def second_classroom(self, event: AstrMessageEvent):
        """第二课堂活动列表"""
        try:
            data = await _get("/bot/second-classroom", limit=10)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "暂无活动")

    # ---------- 学院公告 ----------

    @filter.command("学院")
    async def colleges(self, event: AstrMessageEvent):
        """学院公告：列出所有学院和条数"""
        try:
            data = await _get("/bot/colleges")
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "暂无学院公告")

    @filter.command("学院公告")
    async def college_items(self, event: AstrMessageEvent, key: str = ""):
        """某个学院的公告，用法：/学院公告 seiee"""
        if not key:
            yield event.plain_result("用法：/学院公告 <学院代号>\n先发 /学院 看代号")
            return
        try:
            data = await _get(f"/bot/colleges/{key}", limit=8)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "该学院暂无公告")

    # ---------- 搜索 ----------

    @filter.command("搜")
    async def search(self, event: AstrMessageEvent, keyword: str = ""):
        """全库搜索，用法：/搜 奖学金"""
        if not keyword:
            yield event.plain_result("用法：/搜 <关键词>")
            return
        try:
            data = await _get("/bot/search", q=keyword, limit=8)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "没找到")

# ---------- 日历图片 ----------

    @filter.command("日历")
    async def calendar(self, event: AstrMessageEvent, arg: str = ""):
        """发日历图。用法：/日历 月|周|日 [课表|任务]"""
        view = "month"
        show = "all"

        text = (arg or "").strip()
        if "周" in text:
            view = "week"
        elif "日" in text or "天" in text:
            view = "day"
        elif "月" in text:
            view = "month"

        if "课表" in text or "日程" in text:
            show = "events"
        elif "任务" in text or "作业" in text:
            show = "tasks"

        url = f"{BASE}/bot/calendar.png?view={view}&show={show}"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as exc:
            logger.error(f"日历图渲染失败: {exc}")
            yield event.plain_result("日历渲染失败，稍后再试")
            return

        yield event.image_result(url)

    # ---------- 推送控制 ----------

    @filter.command("推送")
    async def digest(self, event: AstrMessageEvent):
        """看看有哪些还没推送过"""
        try:
            data = await _get("/bot/digest", limit=10, only_unpushed="true")
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or []
        if not items:
            yield event.plain_result("没有待推送的内容")
            return

        lines = [f"待推送 {len(items)} 条"]
        for i, it in enumerate(items, 1):
            lines.append(f'{i}. [{it.get("board")}] {it.get("title", "")[:36]}')
        lines.append("\n发 /确认 标记为已推送")
        yield event.plain_result("\n".join(lines))

    @filter.command("确认")
    async def ack(self, event: AstrMessageEvent):
        """把待推送的标记为已推送"""
        try:
            data = await _get("/bot/digest", limit=200, only_unpushed="true")
            ids = [it["id"] for it in (data.get("items") or [])]
            if not ids:
                yield event.plain_result("没有待推送的内容")
                return
            result = await _post("/bot/ack", {"ids": ids})
        except Exception as exc:
            yield event.plain_result(f"操作失败：{exc}")
            return
        yield event.plain_result(f'已标记 {result.get("marked", 0)} 条')

    @filter.command("刷新")
    async def refresh(self, event: AstrMessageEvent):
        """触发一次全量抓取"""
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(BASE + "/sources/fetch-all")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            yield event.plain_result(f"抓取失败：{exc}")
            return

        added = sum(r.get("added", 0) for r in (data.get("results") or data or []))
        yield event.plain_result(f"抓取完成，新增 {added} 条")