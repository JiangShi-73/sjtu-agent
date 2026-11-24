"""信鸽 SJTU —— AstrBot 插件。

设计原则：**少记指令**。

    /信鸽            看总览
    /信鸽 作业       看某个模块（活动/慕课/实验/邮件/学院/水源/日程/存档）
    /信鸽 搜 奖学金   搜
    /作业 3          直接看第 3 条

更好用的是**回数字**：列表出来之后，直接回 1、2、3 就能看详情，
不用再重复指令。

还有**自然语言建日程**：不用任何指令前缀，直接说

    明天下午3点图书馆开会
    周五晚上7点和张三吃饭

机器人会自己解析日期/时间，建好日程后回一句确认。
"""

from __future__ import annotations

import re
import time
from datetime import date

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


# ---------- 自然语言建日程 ----------
#
# 用户直接说「明天下午3点图书馆开会」就能建日程，不用记任何指令。
# 日期/时间不在插件里手算：交给后端 /events/parse（规则解析，日期零误差），
# 插件只负责判断「这句话是不是在说日程」、以及把结果翻成一句确认。

# 日期词：今天/明天/后天/大后天、周X/下周X/星期X、11月30日、2026-11-30
RE_NL_DATE = re.compile(
    r"(?:今天|今日|明天|明日|后天|大后天)"
    r"|(?:(?:下下|下|本|这)?(?:周|星期|礼拜)[一二三四五六日天])"
    r"|(?:\d{1,2}\s*月\s*\d{1,2}\s*[日号])"
    r"|(?:\d{4}-\d{1,2}-\d{1,2})"
)
# 时间词：3点 / 15:30 / 12点半
RE_NL_TIME = re.compile(r"\d{1,2}\s*(?:[:：]\s*\d{1,2}|[点时])")
# 只说了时间没说哪天时，再看看有没有「要干件事」的词
RE_NL_VERB = re.compile(
    r"(开会|会议|上课|上机|实验|吃饭|见面|面试|体检|考试|报名|讲座|组会"
    r"|讨论|复习|作业|提交|截止|活动|安排|提醒|打球|跑步|训练|电影|自习|聚会)"
)
# 问句不建日程（「明天下午3点有课吗」）
RE_NL_QUERY = re.compile(
    r"(什么|啥|有没有|哪些|几点|多少|查|看看|看一下|列出|吗|呢|怎么|为什么|[？?])"
)
# 追问日期/标题时用户反悔
RE_NL_CANCEL = re.compile(r"^(算了|不用了|不建了|取消|没事|没有|没啥|别记了)")

WEEKDAY_CN = "一二三四五六日"

# 缺日期/标题时挂起，等用户补一句再建（会话 -> {text, missing, at}）
PENDING: dict[str, dict] = {}
PENDING_TTL = 300


def _nl_normalize(text: str) -> str:
    """把口语写法换成后端解析器认识的写法（12点半 → 12点30分）。"""
    text = re.sub(r"(\d{1,2})\s*点半", lambda m: f"{m.group(1)}点30分", text)
    text = re.sub(r"(\d{1,2})\s*点一刻", lambda m: f"{m.group(1)}点15分", text)
    text = re.sub(r"(\d{1,2})\s*点三刻", lambda m: f"{m.group(1)}点45分", text)
    return text


def _nl_looks_like_schedule(text: str) -> bool:
    """粗略判断「这句话想不想建日程」，宁缺毋滥。

    有日期但光秃秃一句「今天真热」不算日程；得有钟点或者「要干件事」的词。
    """
    if RE_NL_QUERY.search(text):
        return False
    if RE_NL_DATE.search(text):
        return bool(RE_NL_TIME.search(text) or RE_NL_VERB.search(text))
    return bool(RE_NL_TIME.search(text) and RE_NL_VERB.search(text))


def _nl_when(iso: str) -> str:
    """2026-11-25 → 明天；不是近几天就写 11月25日。"""
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return iso
    delta = (day - date.today()).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    if 0 < delta < 7:
        return "周" + WEEKDAY_CN[day.weekday()]
    return f"{day.month}月{day.day}日"


def _nl_silence(event: AstrMessageEvent) -> None:
    """拦住 AstrBot 默认的 LLM 回复，免得同一条消息回两遍。"""
    if hasattr(event, "call_llm"):
        event.call_llm = True
    event.stop_event()


def _nl_pending(event: AstrMessageEvent) -> dict | None:
    item = PENDING.get(_sid(event))
    if not item:
        return None
    if time.time() - item["at"] > PENDING_TTL:
        PENDING.pop(_sid(event), None)
        return None
    return item


def _nl_remember(event: AstrMessageEvent, text: str, missing: list[str]) -> None:
    PENDING[_sid(event)] = {"text": text, "missing": missing, "at": time.time()}


def _nl_forget(event: AstrMessageEvent) -> None:
    PENDING.pop(_sid(event), None)


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

    # ---------- 自然语言建日程 ----------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-10)
    async def make_schedule(self, event: AstrMessageEvent):
        """兜底：没匹配到 /指令 的自然语言，看着像日程就直接建。

        优先级 -10 = 排在所有指令后面，不跟 /信鸽 /作业 抢消息；
        不像日程的消息原样放行，不打断 AstrBot 默认的 LLM 回复。
        """
        text = (event.message_str or "").strip()
        if not text or text.startswith("/"):
            return

        # 群聊里必须 @ 机器人或者带唤醒词才响应；没叫我们就保持沉默，
        # 别把整个群的聊天都当日程（也保持和原来一样：群消息不触发默认 LLM）
        if not event.is_at_or_wake_command:
            event.stop_event()
            return

        # 上一句还差日期/标题没建完时，这一句可能是在补信息
        pending = _nl_pending(event)
        merged = False
        if pending:
            if RE_NL_CANCEL.search(text):
                _nl_forget(event)
                return
            missing = pending.get("missing") or []
            # 这一句是不是在补上缺的那块：缺日期补日期，缺标题给个短标题
            answer = bool("date" in missing and RE_NL_DATE.search(text)) or bool(
                "title" in missing and not RE_NL_QUERY.search(text) and len(text) <= 16
            )
            if answer:
                text = f"{pending['text']} {text}"
                merged = True
            else:
                _nl_forget(event)                      # 用户换话题了，别一直挂着

        if not merged and not _nl_looks_like_schedule(text):
            return

        try:
            parsed = await _post("/events/parse", {"text": _nl_normalize(text)})
        except Exception as exc:                       # noqa: BLE001
            logger.error(f"日程解析失败: {exc}")
            return

        when_day = str(parsed.get("event_date") or "").strip()
        title = str(parsed.get("title") or "").strip()

        if not when_day or not title:
            # 补的那句没帮上忙就安静放行，别把用户挂在追问里
            if merged:
                improved = bool(when_day and "date" in (pending or {}).get("missing", [])) or bool(
                    title and "title" in (pending or {}).get("missing", [])
                )
                if not improved:
                    _nl_forget(event)
                    return
            missing = []
            if not when_day:
                missing.append("date")
            if not title:
                missing.append("title")
            _nl_remember(event, text, missing)
            if not when_day:
                yield event.plain_result(
                    "这是哪一天？可以说「明天」「下周三」或者「11月30日」。"
                )
            else:
                yield event.plain_result("要做什么？给我一个标题就行。")
            _nl_silence(event)
            return

        try:
            created = await _post(
                "/events",
                {
                    "title": title[:60],
                    "event_date": when_day,
                    "start_time": parsed.get("start_time") or "",
                    "end_time": parsed.get("end_time") or "",
                    "location": parsed.get("location") or "",
                    "note": text,
                    "source": "qq",
                },
            )
        except Exception as exc:                       # noqa: BLE001
            logger.error(f"建日程失败: {exc}")
            yield event.plain_result("后端没响应，日程没建成，稍后再试")
            _nl_silence(event)
            return

        if not (created or {}).get("ok"):
            yield event.plain_result("日程没建成，稍后再试")
            _nl_silence(event)
            return

        _nl_forget(event)
        clock = f" {parsed['start_time']}" if parsed.get("start_time") else ""
        place = f"，地点：{parsed['location']}" if parsed.get("location") else ""
        yield event.plain_result(
            f"已加日程：{_nl_when(when_day)}{clock} {title}（{when_day}）{place}"
        )
        _nl_silence(event)

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
