"""信鸽 SJTU —— AstrBot 插件。

把信鸽的抓取结果接进 QQ。信鸽后端跑在 http://127.0.0.1:8722，
这里只调 /api/v1/bot/* 那几个接口，不重复实现业务逻辑。

指令分三层：

    /信鸽                     总览
    /作业  /慕课  /实验  /二课  每个模块的列表
    /作业 3                   看第 3 条的详情
"""

from __future__ import annotations

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

BASE = "http://127.0.0.1:8722/api/v1"


async def _get(path: str, **params) -> dict:
    params.setdefault("format", "text")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(BASE + path, params=params)
        resp.raise_for_status()
        return resp.json()


async def _get_json(path: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(BASE + path, params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, payload: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(BASE + path, json=payload or {})
        resp.raise_for_status()
        return resp.json()


def _num(arg: str, default: int) -> int:
    """从参数里抠一个数字出来。"""
    digits = "".join(ch for ch in (arg or "") if ch.isdigit())
    return int(digits) if digits else default


class XingePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # ---------- 总览 ----------

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
        lines.append("")
        lines.append("细化：/作业 /慕课 /实验 /二课 /学院 /水源 /邮件 /ddl /日历")
        yield event.plain_result("\n".join(lines))

    @filter.command("ddl")
    async def deadlines(self, event: AstrMessageEvent, arg: str = ""):
        """未来几天要交的。用法：/ddl [天数]"""
        days = _num(arg, 7)
        try:
            data = await _get_json("/bot/deadlines", days=days)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or []
        if not items:
            yield event.plain_result(f"未来 {days} 天没有要交的")
            return

        lines = [f"未来 {days} 天（{len(items)} 项）"]
        for i, it in enumerate(items[:12], 1):
            lines.append(f'{i}. {it.get("title", "")[:36]}')
            if it.get("deadline"):
                lines.append(f'   {str(it["deadline"])[:16]}')
        yield event.plain_result("\n".join(lines))

    # ---------- 各模块 ----------

    async def _module(self, event: AstrMessageEvent, path: str, title: str, arg: str):
        """模块通用处理：数字看详情，否则列列表。"""
        index = _num(arg, 0)

        try:
            data = await _get_json(path, limit=20)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or []
        if not items:
            yield event.plain_result(f"{title}暂无内容")
            return

        if index:
            if not (1 <= index <= len(items)):
                yield event.plain_result(f"只有 {len(items)} 条，没有第 {index} 条")
                return
            it = items[index - 1]
            lines = [it.get("title", "")]
            if it.get("deadline"):
                lines.append(f'截止 {str(it["deadline"])[:16]}')
            if it.get("summary"):
                lines.append("")
                lines.append(it["summary"][:400])
            if it.get("url"):
                lines.append("")
                lines.append(it["url"])
            yield event.plain_result("\n".join(lines))
            return

        lines = [f"{title}（{len(items)} 条）"]
        for i, it in enumerate(items[:12], 1):
            when = str(it.get("deadline") or it.get("published") or "")[:10]
            lines.append(f'{i}. {it.get("title", "")[:34]}  {when}')
        if len(items) > 12:
            lines.append(f"…还有 {len(items) - 12} 条")
        lines.append("")
        lines.append(f"看详情：/{event.message_str.split()[0].lstrip('/')} <序号>")
        yield event.plain_result("\n".join(lines))

    @filter.command("作业")
    async def canvas(self, event: AstrMessageEvent, arg: str = ""):
        """Canvas 作业。用法：/作业 [序号]"""
        async for r in self._module(event, "/bot/canvas", "Canvas 作业", arg):
            yield r

    @filter.command("慕课")
    async def cnmooc(self, event: AstrMessageEvent, arg: str = ""):
        """慕课作业与测验。用法：/慕课 [序号]"""
        async for r in self._module(event, "/bot/cnmooc", "慕课作业", arg):
            yield r

    @filter.command("实验")
    async def pe(self, event: AstrMessageEvent, arg: str = ""):
        """体育实验。用法：/实验 [序号]"""
        async for r in self._module(event, "/bot/pe", "体育实验", arg):
            yield r

    @filter.command("二课")
    async def second_classroom(self, event: AstrMessageEvent, arg: str = ""):
        """第二课堂活动。用法：/二课 [序号]"""
        async for r in self._module(event, "/bot/second-classroom", "第二课堂", arg):
            yield r

    @filter.command("邮件")
    async def mail(self, event: AstrMessageEvent, arg: str = ""):
        """最近邮件。用法：/邮件 [序号]"""
        index = _num(arg, 0)
        try:
            data = await _get_json("/bot/mail", limit=20)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return

        mails = data.get("mails") or []
        if not mails:
            yield event.plain_result("没有邮件")
            return

        if index:
            if not (1 <= index <= len(mails)):
                yield event.plain_result(f"只有 {len(mails)} 封")
                return
            m = mails[index - 1]
            yield event.plain_result(
                f'发件人：{m.get("from")}\n'
                f'主题：{m.get("subject")}\n'
                f'时间：{m.get("date")}\n\n'
                f'{(m.get("preview") or "")[:600]}'
            )
            return

        lines = [f"最近邮件（{len(mails)} 封）"]
        for i, m in enumerate(mails[:12], 1):
            mark = "" if m.get("read") else "● "
            lines.append(f'{i}. {mark}{m.get("subject", "")[:32]}')
            lines.append(f'   {m.get("from", "")[:24]}  {str(m.get("date"))[:10]}')
        lines.append("")
        lines.append("看正文：/邮件 <序号>")
        yield event.plain_result("\n".join(lines))

    @filter.command("水源")
    async def shuiyuan(self, event: AstrMessageEvent, arg: str = ""):
        """水源社区。用法：/水源 [关键词]"""
        keyword = (arg or "").strip()

        try:
            if keyword:
                data = await _get_json("/shuiyuan/search", q=keyword, limit=10)
                topics = data.get("topics") or []
                if not topics:
                    yield event.plain_result(f'水源没搜到「{keyword}」')
                    return
                lines = [f'水源搜「{keyword}」（{len(topics)} 条）']
                for t in topics:
                    lines.append(f'[{t["id"]}] {t["title"][:36]}')
                lines.append("")
                lines.append("看正文：/水源看 <id>")
                yield event.plain_result("\n".join(lines))
                return

            data = await _get_json("/shuiyuan/latest", limit=12)
        except Exception as exc:
            yield event.plain_result(f"水源查询失败：{exc}")
            return

        topics = data.get("topics") or []
        if not topics:
            yield event.plain_result("水源没有新话题，或者还没登录")
            return

        lines = [f"水源最新（{len(topics)} 条）"]
        for t in topics:
            lines.append(f'[{t["id"]}] {t["title"][:36]}（{t["posts_count"]} 楼）')
        lines.append("")
        lines.append("看正文：/水源看 <id>")
        yield event.plain_result("\n".join(lines))

    @filter.command("水源看")
    async def shuiyuan_read(self, event: AstrMessageEvent, arg: str = ""):
        """读水源某个话题。用法：/水源看 <id>"""
        topic_id = _num(arg, 0)
        if not topic_id:
            yield event.plain_result("用法：/水源看 535269")
            return

        try:
            data = await _get_json(f"/shuiyuan/topic/{topic_id}", posts=10)
        except Exception as exc:
            yield event.plain_result(f"读取失败：{exc}")
            return

        lines = [f'# {data.get("title")}', str(data.get("url") or "")]
        for p in (data.get("posts") or [])[:6]:
            lines.append(f'\n{p["floor"]} 楼 {p["user"]}（{p["at"]}）：')
            lines.append((p.get("text") or "")[:300])
        yield event.plain_result("\n".join(lines)[:1800])

    # ---------- 学院公告 ----------

    @filter.command("学院")
    async def colleges(self, event: AstrMessageEvent):
        """学院列表和条数"""
        try:
            data = await _get("/bot/colleges")
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "暂无学院公告")

    @filter.command("学院公告")
    async def college_items(self, event: AstrMessageEvent, arg: str = ""):
        """某个学院的公告。用法：/学院公告 seiee [序号]"""
        parts = (arg or "").split()
        if not parts:
            yield event.plain_result("用法：/学院公告 seiee\n先发 /学院 看代号")
            return

        key = parts[0]
        index = _num(parts[1], 0) if len(parts) > 1 else 0

        try:
            data = await _get_json(f"/bot/colleges/{key}", limit=20)
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or []
        if not items:
            yield event.plain_result("该学院暂无公告")
            return

        if index:
            if not (1 <= index <= len(items)):
                yield event.plain_result(f"只有 {len(items)} 条")
                return
            it = items[index - 1]
            yield event.plain_result(
                f'{it["title"]}\n{it.get("date") or ""}\n{it.get("url") or ""}'
            )
            return

        lines = [f'{data.get("college")}（{len(items)} 条）']
        for i, it in enumerate(items[:12], 1):
            lines.append(f'{i}. {it["title"][:34]}  {it.get("date") or ""}')
        lines.append("")
        lines.append(f"看详情：/学院公告 {key} <序号>")
        yield event.plain_result("\n".join(lines))

    # ---------- 搜索 ----------

    @filter.command("搜")
    async def search(self, event: AstrMessageEvent, keyword: str = ""):
        """全库搜索，用法：/搜 奖学金"""
        if not keyword:
            yield event.plain_result("用法：/搜 <关键词>")
            return
        try:
            data = await _get("/bot/search", q=keyword, limit=10)
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
            data = await _get_json("/bot/digest", limit=10, only_unpushed="true")
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
            data = await _get_json("/bot/digest", limit=200, only_unpushed="true")
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
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(BASE + "/sources/fetch-all")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            yield event.plain_result(f"抓取失败：{exc}")
            return

        results = data.get("results") or data or []
        added = sum(r.get("added", 0) for r in results if isinstance(r, dict))
        yield event.plain_result(f"抓取完成，新增 {added} 条")

    @filter.command("存档")
    async def archive(self, event: AstrMessageEvent, arg: str = ""):
        """存档区：过了 ddl 的条目，一周后自动删。用法：/存档 [板块]"""
        board = (arg or "").strip() or None
        try:
            data = await _get_json("/archive", **({"board": board} if board else {}))
        except Exception as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or []
        if not items:
            yield event.plain_result("存档区是空的")
            return

        lines = [f"存档区（{len(items)} 条，保留 7 天）"]
        for it in items[:15]:
            lines.append(f'{it.get("category")} · {it["title"][:30]}')
        yield event.plain_result("\n".join(lines))
