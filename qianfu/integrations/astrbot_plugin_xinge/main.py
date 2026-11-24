"""信鸽 SJTU —— AstrBot 插件。

设计原则：**少记指令**。

    /信鸽            看总览
    /信鸽 作业       看某个模块（活动/慕课/实验/邮件/学院/水源/日程/存档）
    /信鸽 搜 奖学金   搜
    /作业 3          直接看第 3 条

更好用的是**回数字**：列表出来之后，直接回 1、2、3 就能看详情，
不用再重复指令。
"""

from __future__ import annotations

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

BASE = "http://127.0.0.1:8722/api/v1"

# 每个会话最近一次列表，用来支持「回数字看详情」
LAST: dict[str, list] = {}

MODULES = {
    "作业": ("/bot/canvas", "Canvas 作业"),
    "慕课": ("/bot/cnmooc", "慕课作业"),
    "实验": ("/bot/pe", "体育实验"),
    "活动": ("/bot/second-classroom", "第二课堂"),
    "邮件": ("/bot/mail", "邮件"),
}


async def _json(path: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(BASE + path, params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, payload: dict | None = None, **params) -> dict:
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(BASE + path, json=payload or {}, params=params)
        resp.raise_for_status()
        return resp.json()


def _sid(event: AstrMessageEvent) -> str:
    return str(getattr(event, "unified_msg_origin", "") or id(event))


def _num(text: str) -> int:
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    return int(digits) if digits else 0


def _remember(event: AstrMessageEvent, items: list, kind: str) -> None:
    LAST[_sid(event)] = {"items": items, "kind": kind}


def _recall(event: AstrMessageEvent) -> dict | None:
    return LAST.get(_sid(event))


class XingePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # ---------- 一个入口 ----------

    @filter.command("信鸽")
    async def entry(self, event: AstrMessageEvent, arg: str = ""):
        """总览，或 /信鸽 作业 / 慕课 / 活动 / 邮件 / 学院 / 水源 / 日程 / 存档 / 搜 xxx"""
        text = (arg or "").strip()

        if not text:
            async for r in self._overview(event):
                yield r
            return

        if text.startswith("搜"):
            async for r in self.search(event, text[1:].strip()):
                yield r
            return

        if "日历" in text or "日程" in text:
            async for r in self.calendar(event, text):
                yield r
            return

        if "存档" in text:
            async for r in self.archive(event):
                yield r
            return

        if "ddl" in text.lower() or "截止" in text:
            async for r in self.ddl(event, text):
                yield r
            return

        for key in MODULES:
            if key in text:
                async for r in self.module(event, key, _num(text)):
                    yield r
                return

        if "学院" in text:
            async for r in self.colleges(event):
                yield r
            return

        if "水源" in text:
            async for r in self.shuiyuan(event, text.replace("水源", "").strip()):
                yield r
            return

        yield event.plain_result(
            "看不懂这个。试试：\n"
            "/信鸽 作业\n/信鸽 活动\n/信鸽 邮件\n/信鸽 日程\n/信鸽 搜 奖学金"
        )

    async def _overview(self, event: AstrMessageEvent):
        try:
            data = await _json("/bot/boards")
        except Exception as exc:                      # noqa: BLE001
            logger.error(f"信鸽未启动: {exc}")
            yield event.plain_result("信鸽后端没起来")
            return

        lines = ["信鸽 SJTU"]
        for board in data.get("boards") or []:
            if board["unread"]:
                lines.append(f'{board["name"]}  {board["unread"]}')
        lines.append("")
        lines.append("/信鸽 作业｜活动｜邮件｜日程｜搜 <词>")
        yield event.plain_result("\n".join(lines))

    # ---------- 模块列表 ----------

    @filter.command("作业")
    async def canvas(self, event: AstrMessageEvent, arg: str = ""):
        async for r in self.module(event, "作业", _num(arg)):
            yield r

    @filter.command("活动")
    async def activity(self, event: AstrMessageEvent, arg: str = ""):
        async for r in self.module(event, "活动", _num(arg)):
            yield r

    @filter.command("慕课")
    async def cnmooc(self, event: AstrMessageEvent, arg: str = ""):
        async for r in self.module(event, "慕课", _num(arg)):
            yield r

    @filter.command("邮件")
    async def mail(self, event: AstrMessageEvent, arg: str = ""):
        async for r in self.module(event, "邮件", _num(arg)):
            yield r

    async def module(self, event: AstrMessageEvent, key: str, index: int = 0):
        path, title = MODULES[key]
        try:
            data = await _json(path, limit=20)
        except Exception as exc:                      # noqa: BLE001
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or data.get("mails") or []
        if not items:
            yield event.plain_result(f"{title}暂无内容")
            return

        _remember(event, items, key)

        if index:
            yield event.plain_result(self._detail(items, index, key))
            return

        lines = [f"{title}（{len(items)} 条）"]
        for i, it in enumerate(items[:10], 1):
            when = str(it.get("deadline") or it.get("published") or it.get("date") or "")[:10]
            lines.append(f'{i}. {self._name(it, key)[:32]}  {when}')
        if len(items) > 10:
            lines.append(f"…还有 {len(items) - 10} 条")
        lines.append("")
        lines.append("回数字看详情，比如 1")
        yield event.plain_result("\n".join(lines))

    @staticmethod
    def _name(it: dict, key: str) -> str:
        if key == "邮件":
            return it.get("subject") or ""
        return it.get("title") or ""

    @staticmethod
    def _detail(items: list, index: int, key: str) -> str:
        if not (1 <= index <= len(items)):
            return f"只有 {len(items)} 条"
        it = items[index - 1]

        if key == "邮件":
            return (
                f'主题：{it.get("subject")}\n'
                f'发件人：{it.get("from")}\n'
                f'时间：{it.get("date")}\n\n'
                f'{(it.get("preview") or "")[:600]}'
            )

        lines = [it.get("title") or ""]
        if it.get("deadline"):
            lines.append(f'截止 {str(it["deadline"])[:16]}')
        if it.get("summary"):
            lines.extend(["", it["summary"][:400]])
        if it.get("url"):
            lines.extend(["", it["url"]])
        return "\n".join(lines)

    # ---------- 回数字 ----------

    @filter.regex(r"^\s*(\d{1,2})\s*$")
    async def pick(self, event: AstrMessageEvent):
        """列表之后直接回数字看详情"""
        recalled = _recall(event)
        if not recalled:
            return

        index = _num(event.message_str)
        if not index:
            return

        yield event.plain_result(self._detail(recalled["items"], index, recalled["kind"]))

    # ---------- 其它 ----------

    @filter.command("ddl")
    async def ddl(self, event: AstrMessageEvent, arg: str = ""):
        days = _num(arg) or 7
        try:
            data = await _json("/bot/deadlines", days=days)
        except Exception as exc:                      # noqa: BLE001
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or []
        if not items:
            yield event.plain_result(f"未来 {days} 天没有要交的")
            return

        _remember(event, items, "作业")
        lines = [f"未来 {days} 天（{len(items)} 项）"]
        for i, it in enumerate(items[:10], 1):
            lines.append(f'{i}. {it.get("title", "")[:34]}')
            lines.append(f'   {str(it.get("deadline"))[:16]}')
        lines.append("\n回数字看详情")
        yield event.plain_result("\n".join(lines))

    @filter.command("学院")
    async def colleges(self, event: AstrMessageEvent):
        try:
            data = await _json("/bot/colleges", format="text")
        except Exception as exc:                      # noqa: BLE001
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "暂无学院公告")

    @filter.command("搜")
    async def search(self, event: AstrMessageEvent, keyword: str = ""):
        if not keyword:
            yield event.plain_result("用法：/搜 奖学金")
            return
        try:
            data = await _json("/bot/search", q=keyword, limit=10, format="text")
        except Exception as exc:                      # noqa: BLE001
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.plain_result(data.get("text") or "没找到")

    @filter.command("水源")
    async def shuiyuan(self, event: AstrMessageEvent, arg: str = ""):
        keyword = (arg or "").strip()
        try:
            if keyword:
                data = await _json("/shuiyuan/search", q=keyword, limit=10)
                topics = data.get("topics") or []
                if not topics:
                    yield event.plain_result(f'水源没搜到「{keyword}」')
                    return
                lines = [f'水源搜「{keyword}」（{len(topics)} 条）']
                for t in topics:
                    lines.append(f'[{t["id"]}] {t["title"][:36]}')
                lines.append("\n看正文：/水源看 <id>")
                yield event.plain_result("\n".join(lines))
                return

            data = await _json("/shuiyuan/latest", limit=10)
        except Exception as exc:                      # noqa: BLE001
            yield event.plain_result(f"水源查询失败：{exc}")
            return

        topics = data.get("topics") or []
        if not topics:
            yield event.plain_result("水源没有新话题，或者还没登录")
            return

        lines = [f"水源最新（{len(topics)} 条）"]
        for t in topics:
            lines.append(f'[{t["id"]}] {t["title"][:36]}（{t["posts_count"]} 楼）')
        lines.append("\n看正文：/水源看 <id>")
        yield event.plain_result("\n".join(lines))

    @filter.command("水源看")
    async def shuiyuan_read(self, event: AstrMessageEvent, arg: str = ""):
        topic_id = _num(arg)
        if not topic_id:
            yield event.plain_result("用法：/水源看 535269")
            return
        try:
            data = await _json(f"/shuiyuan/topic/{topic_id}", posts=10)
        except Exception as exc:                      # noqa: BLE001
            yield event.plain_result(f"读取失败：{exc}")
            return

        lines = [f'# {data.get("title")}', str(data.get("url") or "")]
        for p in (data.get("posts") or [])[:5]:
            lines.append(f'\n{p["floor"]} 楼 {p["user"]}：')
            lines.append((p.get("text") or "")[:280])
        yield event.plain_result("\n".join(lines)[:1800])

    @filter.command("日程")
    async def schedule(self, event: AstrMessageEvent, arg: str = ""):
        """和 /日历 同一个。"""
        async for r in self.calendar(event, arg):
            yield r

    @filter.command("日历")
    async def calendar(self, event: AstrMessageEvent, arg: str = ""):
        view = "week" if "周" in arg else ("day" if ("日" in arg or "天" in arg) else "month")
        show = "events"

        url = f"{BASE}/bot/calendar.png?view={view}&show={show}"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as exc:                      # noqa: BLE001
            logger.error(f"日历图渲染失败: {exc}")
            yield event.plain_result("日历渲染失败，稍后再试")
            return

        yield event.image_result(url)

    @filter.command("存档")
    async def archive(self, event: AstrMessageEvent, arg: str = ""):
        try:
            data = await _json("/archive")
        except Exception as exc:                      # noqa: BLE001
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or []
        if not items:
            yield event.plain_result("存档区是空的")
            return

        lines = [f"存档区（{len(items)} 条，保留 7 天）"]
        for it in items[:12]:
            lines.append(f'{it.get("category")} · {it["title"][:30]}')
        yield event.plain_result("\n".join(lines))

    @filter.command("刷新")
    async def refresh(self, event: AstrMessageEvent):
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(BASE + "/sources/fetch-all")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:                      # noqa: BLE001
            yield event.plain_result(f"抓取失败：{exc}")
            return

        results = data.get("results") or data or []
        added = sum(r.get("added", 0) for r in results if isinstance(r, dict))
        yield event.plain_result(f"抓取完成，新增 {added} 条")

    @filter.command("清理")
    async def clean(self, event: AstrMessageEvent):
        try:
            data = await _post("/archive/run")
        except Exception as exc:                      # noqa: BLE001
            yield event.plain_result(f"清理失败：{exc}")
            return
        yield event.plain_result(
            f'归档 {data.get("archived", 0)} 条，清掉 {data.get("purged", 0)} 条'
        )
