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

import asyncio
import re
import time
from datetime import date, datetime, timedelta

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

BASE = "http://127.0.0.1:8722/api/v1"

# ---------- 主动提醒推送 ----------
#
# 后端每分钟把到点的提醒放进 due 队列，插件每 60 秒拉一次，主动私聊推给
# 「推送对象」。推送对象不写死在插件里：用户在信鸽 App 里设置，插件只负责
# 私聊时把 umo 上报给后端（/reminders/bind）、每轮读一次（/reminders/target）。
REMINDER_INTERVAL = 60          # 轮询间隔（秒）
REMINDER_BATCH = 10             # 每轮最多拉几条
REMINDER_HOURLY_CAP = 3         # 兜底：同一小时最多推几条（实际读后端 settings）
REMINDER_STALE_HOURS = 24       # fire_at 比现在早超过这个时间 → 丢弃，不补推
REMINDER_RETRY_SECONDS = 600    # 推过但 ack 失败：这段时间只补 ack，不重复推
QUIET_START_MIN = 23 * 60       # 兜底静默开始 23:00（实际读后端 settings）
QUIET_END_MIN = 7 * 60 + 30     # 兜底静默结束 07:30（实际读后端 settings）

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


# 提醒轮询用的短超时版本：后端没起来时别让一轮卡上几分钟
async def _rjson(path: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(BASE + path, params=params)
        resp.raise_for_status()
        return resp.json()


async def _rpost(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(BASE + path, json=payload)
        resp.raise_for_status()
        return resp.json()


def _parse_dt(value) -> datetime | None:
    """后端给的是本地时间串（2026-11-25T15:00:00），转成本地 naive datetime。"""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is not None:                       # 带时区就换成机器本地时间
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _parse_hhmm(value, default: int) -> int:
    """把 "23:00" / "7:30" / "23:00:00" 解析成分钟数；解析不了用兜底值。"""
    if value is None:
        return default
    parts = str(value).strip().split(":")
    if len(parts) < 2:
        return default
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return hour * 60 + minute


def _in_quiet(now: datetime, start_min: int = QUIET_START_MIN,
              end_min: int = QUIET_END_MIN) -> bool:
    """静默时段判断，支持跨零点（默认 23:00–07:30）。start==end 视为不静默。"""
    minutes = now.hour * 60 + now.minute
    if start_min <= end_min:
        return start_min <= minutes < end_min
    return minutes >= start_min or minutes < end_min


def _mask_umo(umo: str) -> str:
    """default_1906503071:FriendMessage:abc123 → default_1906503071:FriendMessage:***c123"""
    parts = (umo or "").split(":")
    if len(parts) < 3:
        return umo or "（空）"
    return ":".join(parts[:-1]) + ":***" + parts[-1][-4:]


def _reminder_text(item: dict, fire: datetime, now: datetime) -> str:
    """单条提醒的推送文案。邮件和日程分开写。"""
    title = str(item.get("title") or "").strip() or "提醒"
    body = str(item.get("body") or "").strip()

    if str(item.get("target_type") or "") == "mail":
        lines = [f"📧 新邮件 · {title}"]
        if body:
            lines.append(body[:200])
        return "\n".join(lines)

    when = fire.strftime("%H:%M")
    if fire.date() != now.date():
        when = f"{fire.month}月{fire.day}日 {when}"
    return f"⏰ {title}\n{when} {body}".rstrip()


def _backlog_text(items: list) -> str:
    """24 小时内、已经过期的多条提醒合并成一条补推。"""
    lines = [f"补推：{len(items)} 条提醒"]
    for item, fire in items:
        title = str(item.get("title") or "").strip() or "提醒"
        body = str(item.get("body") or "").strip()
        if str(item.get("target_type") or "") == "mail":
            lines.append(f"· 📧 {title}" + (f"：{body[:40]}" if body else ""))
        else:
            lines.append(f"· {fire.month}月{fire.day}日 {fire.strftime('%H:%M')} {title}")
    return "\n".join(lines)


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
        self._push_task: asyncio.Task | None = None
        self._hour_bucket = ""
        self._hour_count = 0
        # 已推但还没 ack 的提醒 id -> 推送时间，防 ack 失败时每分钟重复推
        self._recent_pushed: dict[int, float] = {}
        self._push_fail_streak = 0
        self._loop_fail_streak = 0
        self._target_error_logged = False
        self._settings_error_logged = False

    # ---------- 生命周期：起/停提醒推送循环 ----------
    #
    # 注意：绝对不能在本类里定义 __del__。AstrBot 的 star_manager 靠
    # 「没有 __del__」来决定要不要调 terminate()，定义了就永远不会 terminate，
    # 循环会泄漏并在插件重载后多开一份。

    async def initialize(self) -> None:
        """插件激活：起后台循环。"""
        if self._push_task and not self._push_task.done():
            return
        self._push_task = asyncio.create_task(self._reminder_loop())
        logger.info(
            f"信鸽提醒推送已启动：每 {REMINDER_INTERVAL}s 拉一次 due，"
            f"静默/每小时上限/开关读后端 settings"
        )

    async def terminate(self) -> None:
        """插件禁用/重载：停循环。"""
        task, self._push_task = self._push_task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("信鸽提醒推送循环已停止")

    # ---------- 提醒推送循环 ----------

    async def _reminder_loop(self) -> None:
        await asyncio.sleep(5)  # 等平台适配器和后端就绪，别启动瞬间刷一堆错
        while True:
            try:
                await self._reminder_tick()
                self._loop_fail_streak = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:                # noqa: BLE001
                self._loop_fail_streak += 1
                # 连续失败别刷日志：第 1 次 + 之后每 30 次记一条
                if self._loop_fail_streak == 1 or self._loop_fail_streak % 30 == 0:
                    logger.warning(
                        f"提醒轮询异常（连续 {self._loop_fail_streak} 次）：{exc}"
                    )
            await asyncio.sleep(REMINDER_INTERVAL)

    async def _reminder_tick(self) -> None:
        now = datetime.now()
        settings = await self._reminder_settings()
        if not settings["enabled"]:
            return                                  # Bot 主动推送被用户关了（App 刷新不受影响）
        if _in_quiet(now, settings["silence_start"], settings["silence_end"]):
            return                                  # 静默时段：不推也不 ack

        umo = await self._reminder_target()
        if not umo:
            return                                  # 还没绑定推送对象

        data = await _rjson("/bot/reminders/due", limit=REMINDER_BATCH)
        reminders = data.get("reminders") or []
        if not reminders:
            return

        stale: list[int] = []                       # 过期 > 24h 或时间解析不了 → 丢弃
        resend: list[int] = []                      # 推过但 ack 失败 → 只补 ack
        fresh: list[tuple[dict, datetime]] = []     # 正常该推的

        for item in reminders:
            rid = item.get("id")
            if rid is None:
                continue
            fire = _parse_dt(item.get("fire_at"))
            if fire is None:
                stale.append(rid)
            elif now - fire > timedelta(hours=REMINDER_STALE_HOURS):
                stale.append(rid)                   # 过期太久，不补推
            elif self._was_pushed(rid):
                resend.append(rid)
            else:
                fresh.append((item, fire))

        if stale:
            logger.info(f"丢弃过期提醒 {stale}（超过 {REMINDER_STALE_HOURS}h）")
            await self._ack(stale, mode="skip")
        if resend:
            await self._ack(resend)
        if not fresh:
            return

        self._hour_bucket_reset(now)
        if self._hour_count >= settings["hourly_cap"]:
            return                                  # 这个小时推够了，不 ack，下轮再来

        if len(fresh) == 1:
            item, fire = fresh[0]
            text = _reminder_text(item, fire, now)
        else:
            text = _backlog_text(fresh)             # 24h 内过期的多条合并补推
        ids = [item["id"] for item, _ in fresh]

        if not await self._push(umo, text, ids):
            return                                  # 发不出去：不 ack，下轮重试
        self._hour_count += 1
        for rid in ids:
            self._recent_pushed[rid] = time.time()
        if await self._ack(ids):
            for rid in ids:
                self._recent_pushed.pop(rid, None)

    def _hour_bucket_reset(self, now: datetime) -> None:
        bucket = now.strftime("%Y%m%d%H")
        if bucket != self._hour_bucket:
            self._hour_bucket = bucket
            self._hour_count = 0

    def _was_pushed(self, rid) -> bool:
        ts = self._recent_pushed.get(rid)
        if ts is None:
            return False
        if time.time() - ts > REMINDER_RETRY_SECONDS:
            self._recent_pushed.pop(rid, None)
            return False
        return True

    async def _reminder_settings(self) -> dict:
        """每轮读后端配置：Bot 推送开关、静默时段、每小时上限都以 App 设置为准。"""
        try:
            data = await _rjson("/bot/reminders/settings")
            self._settings_error_logged = False
        except Exception as exc:                    # noqa: BLE001
            if not self._settings_error_logged:     # 只记第一次，别每轮刷
                logger.warning(f"读取推送设置失败（用兜底值，静默重试）：{exc}")
                self._settings_error_logged = True
            data = {}

        data = data if isinstance(data, dict) else {}
        enabled = data.get("reminder_enabled", 1)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("", "0", "false", "off", "no")
        try:
            cap = int(data.get("reminder_hourly_cap", REMINDER_HOURLY_CAP))
        except (TypeError, ValueError):
            cap = REMINDER_HOURLY_CAP
        return {
            "enabled": bool(enabled),
            "silence_start": _parse_hhmm(
                data.get("reminder_silence_start"), QUIET_START_MIN
            ),
            "silence_end": _parse_hhmm(
                data.get("reminder_silence_end"), QUIET_END_MIN
            ),
            "hourly_cap": max(0, min(cap, 100)),
        }

    async def _reminder_target(self) -> str:
        """每轮都从后端读推送对象：App 里改了立刻生效，插件不自己记。"""
        try:
            data = await _rjson("/bot/reminders/target")
            self._target_error_logged = False
        except Exception as exc:                    # noqa: BLE001
            if not self._target_error_logged:       # 只记第一次，别每轮刷
                logger.warning(f"读取推送对象失败（静默重试）：{exc}")
                self._target_error_logged = True
            return ""
        return str((data or {}).get("umo") or "").strip()

    async def _push(self, umo: str, text: str, ids: list[int]) -> bool:
        """主动私聊推一条。失败不抛，交给下一轮重试。"""
        try:
            sent = await self.context.send_message(umo, MessageChain().message(text))
        except Exception as exc:                    # noqa: BLE001
            self._push_fail_streak += 1
            if self._push_fail_streak == 1 or self._push_fail_streak % 30 == 0:
                logger.warning(
                    f"提醒推送失败（连续 {self._push_fail_streak} 次）：{exc}"
                )
            return False
        if not sent:
            self._push_fail_streak += 1
            if self._push_fail_streak == 1 or self._push_fail_streak % 30 == 0:
                logger.warning(
                    f"提醒推送失败：没找到匹配平台（连续 {self._push_fail_streak} 次）"
                )
            return False
        self._push_fail_streak = 0
        logger.info(f"已推送提醒 {ids}：{text.splitlines()[0]}")
        return True

    async def _ack(self, ids: list[int], mode: str | None = None) -> bool:
        ids = [int(rid) for rid in ids if rid is not None]
        if not ids:
            return True
        payload: dict = {"ids": ids}
        if mode:
            payload["mode"] = mode              # mode=skip：过期太久，后端别再发
        try:
            await _rpost("/bot/reminders/ack", payload)
            return True
        except Exception as exc:                    # noqa: BLE001
            logger.warning(f"提醒 ack 失败 {ids}：{exc}")
            return False

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

    # ---------- 推送对象绑定 ----------

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=-100)
    async def remember_umo(self, event: AstrMessageEvent):
        """每条私聊都把 umo/昵称报给后端（群聊不报）。

        后端用这些信息维护「最近联系人」列表，App 里就是从这个列表选推送对象；
        是否覆盖已绑定的目标由后端决定，插件只管上报。
        不 stop_event：这条消息照常走后面的指令/自然语言建日程。
        """
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not umo:
            return

        try:
            sender_id = str(event.get_sender_id() or "")
            nickname = str(event.get_sender_name() or "")
        except Exception:                           # noqa: BLE001
            sender_id, nickname = "", ""

        try:
            await _rpost(
                "/bot/reminders/bind",
                {"umo": umo, "sender_id": sender_id, "nickname": nickname},
            )
            logger.info(f"推送对象已上报：{_mask_umo(umo)}（{nickname or '无昵称'}）")
        except Exception as exc:                    # noqa: BLE001
            logger.warning(f"上报推送对象失败（不影响聊天）：{_mask_umo(umo)} {exc}")

    @filter.command("提醒")
    async def reminder_status(self, event: AstrMessageEvent, arg: str = ""):
        """/提醒：看推送对象绑定和推送状态。"""
        now = datetime.now()
        lines = ["信鸽提醒"]

        bound = source = bound_at = ""
        try:
            data = await _rjson("/bot/reminders/target")
            data = data or {}
            bound = str(data.get("umo") or "").strip()
            source = str(data.get("source") or "").strip()
            bound_at = str(data.get("bound_at") or "").strip()
        except Exception as exc:                    # noqa: BLE001
            yield event.plain_result(f"后端没响应：{exc}")
            return

        if bound:
            lines.append(f"推送对象：已绑定 {_mask_umo(bound)}")
            extra = []
            if source:
                extra.append("手动设置" if source == "manual" else "自动绑定")
            if bound_at:
                extra.append(bound_at[:16])
            if extra:
                lines.append("（" + "，".join(extra) + "）")
        else:
            lines.append("推送对象：未绑定")
            lines.append("在信鸽 App 的「机器人」设置里选一个最近联系人。")

        settings = await self._reminder_settings()

        try:
            due = await _rjson("/bot/reminders/due", limit=REMINDER_BATCH)
            count = due.get("count")
            if count is None:
                count = len(due.get("reminders") or [])
            lines.append(f"现在到点待推：{int(count)} 条")
        except Exception:                           # noqa: BLE001
            pass

        start, end = settings["silence_start"], settings["silence_end"]
        quiet = _in_quiet(now, start, end)
        self._hour_bucket_reset(now)
        lines.append(f"Bot 主动推送：{'开' if settings['enabled'] else '关'}")
        lines.append(f"本小时已推：{self._hour_count}/{settings['hourly_cap']}")
        lines.append(
            f"静默时段：{start // 60:02d}:{start % 60:02d}"
            f"–{end // 60:02d}:{end % 60:02d}"
            + ("（现在静默，不推）" if quiet else "")
        )
        lines.append(f"轮询：每 {REMINDER_INTERVAL} 秒")
        yield event.plain_result("\n".join(lines))

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


    @filter.command("活动图")
    async def activity_shot(self, event: AstrMessageEvent, arg: str = ""):
        """发第二课堂活动详情页截图。用法：/活动图 <序号> 或 /活动图 <活动id>"""
        index = _num(arg)
        if not index:
            yield event.plain_result("用法：先 /活动 看列表，再 /活动图 2")
            return

        try:
            data = await _json("/bot/second-classroom", limit=60)
        except Exception as exc:                  # noqa: BLE001
            yield event.plain_result(f"查询失败：{exc}")
            return

        items = data.get("items") or []
        if not items:
            yield event.plain_result("第二课堂暂无活动")
            return

        target = None
        for it in items:
            if it.get("id") == index:
                target = it
                break
        if target is None:
            if 1 <= index <= len(items):
                target = items[index - 1]
            else:
                yield event.plain_result(f"只有 {len(items)} 条，没有第 {index} 条")
                return

        url = f"{BASE}/bot/activity/{target['id']}/shot.png"
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as exc:                  # noqa: BLE001
            logger.error(f"活动截图失败: {exc}")
            yield event.plain_result("截图失败，稍后再试")
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