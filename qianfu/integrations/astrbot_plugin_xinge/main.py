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

还有**自然语言改提醒设置**：同样是说一句话，不用打开 App

    提醒提前一小时
    作业改成提前两天提醒
    晚上十点以后别推
    静默时间改成 23 点到 7 点
    每小时最多推 5 条
    关掉提醒 / 打开提醒
    现在提醒设置是什么

这些全部用规则解析，不调大模型。

还有**自然语言查询**：不记指令也能问。先按关键词走规则查后端（秒回），
规则认不出来、但确实是在提问的，才交给 agent 兜底

    最近有什么作业
    这周有什么活动
    有新的邮件吗
    明天有什么安排
    今天有课吗

闲聊不调模型，回一句用法提示；像「明天下午3点开会」这种要建日程的，
还是交给建日程的规则，不会被查询抢走。
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


# ---------- 自然语言改提醒设置 ----------
#
# 用户不用打开 App：在 QQ 里直接说「提醒提前一小时」「晚上十点以后别推」
# 就能改后端的提醒参数（/bot/reminders/settings）。
# 全部规则解析，不调大模型：快、零成本、结果确定。
#
# 入口是 _nl_reminder_parse(text)，返回下面四种之一：
#   {"action": "query"}                  —— 回显全部设置
#   {"action": "update", "patch": {...}} —— 改设置（键名同后端 settings）
#   {"action": "usage"}                  —— 像在说设置但没解析出来，给用法提示
#   None                                 —— 不归这里管，原样放行（建日程 / LLM）

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100,
}
_CN_NUM = r"[零〇一二两三四五六七八九十百]+"

# 时长：一小时 / 30分钟 / 两天 / 半小时 / 一个半小时
_RE_DURATION = re.compile(
    r"(?P<num>\d+(?:\.\d+)?|" + _CN_NUM + r"|半)?\s*个?\s*(?P<half>半)?\s*"
    r"(?P<unit>个小时|小时|钟头|分钟|分|天|日|周|星期|礼拜)"
)
_DURATION_SCALE = {                       # 换算成分钟
    "小时": 60, "钟头": 60, "分钟": 1, "分": 1,
    "天": 60 * 24, "日": 60 * 24,
    "周": 60 * 24 * 7, "星期": 60 * 24 * 7, "礼拜": 60 * 24 * 7,
}

# 钟点：23点 / 23:00 / 十点半 / 晚上十点
_RE_CLOCK = re.compile(
    r"(?:(?P<period>凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|夜间|晚)\s*)?"
    r"(?P<hour>\d{1,2}|" + _CN_NUM + r")\s*点"
    r"(?:\s*(?P<half>半)|(?:\s*(?P<minute>\d{1,2})\s*分?))?"
)
_RE_CLOCK_COLON = re.compile(
    r"(?:(?P<period>凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|夜间|晚)\s*)?"
    r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{1,2})(?!\d)"
)

# 静默
_RE_SILENCE_HINT = re.compile(
    r"静默|免打扰|勿扰|别打扰|不要打扰|别推|不要推|别发|不要发"
    r"|别通知|不要通知|不推送|不打扰|别吵"
)
_RE_SILENCE_CANCEL = re.compile(
    r"(?:取消|关掉|关闭|解除|不用|不要|停掉)\s*(?:掉)?\s*(?:那个|我的)?"
    r"\s*(?:静默|免打扰|勿扰)"
    r"|(?:静默|免打扰|勿扰)\s*(?:取消|关掉|关闭|解除|不用了|不要了|停了)"
)
_RE_SILENCE_START = re.compile(r"以后|之后|点后|过后|开始|起")
_RE_SILENCE_END = re.compile(r"以前|之前|点前|为止|截止")
_RE_SILENCE_SET = re.compile(r"改成|改到|改为|设为|设成|调成|调整到")

# 总开关
_RE_SWITCH_ON = re.compile(r"打开|开启|恢复|启用|启动|开起来|开一下|重开")
_RE_SWITCH_OFF = re.compile(
    r"关掉|关闭|关掉吧|关了|关一下|停止|停掉|停用|禁用|关停"
    r"|(?:别|不要|不用|别再|不要再|不用再)(?:再)?(?:给我)?\s*"
    r"(?:推送|推了|推消息|提醒|发消息|发了|通知|打扰)"
)
_RE_SWITCH_TOPIC_ON = re.compile(r"提醒|推送|通知")
_RE_SWITCH_TOPIC_OFF = re.compile(r"提醒|推送|推|通知|打扰")

# 每小时上限
_RE_CAP_ANY = re.compile(r"每小时|每个小时|一小时|一个钟头|每钟头")
_RE_CAP_LIMIT = re.compile(
    r"(?:最多|上限|不超过|限制|只推|只发|限量)\s*(?:推送|推|发)?\s*"
    r"(?P<num>\d+|" + _CN_NUM + r")\s*(?:条|个|次)?"
)

# 查询 / 像查询的开关
_RE_REM_SHOW = re.compile(
    r"(?:打开|查看|看看|看一下|显示|发我|给我看|查一下|瞧一瞧)\s*(?:一下)?\s*"
    r"(?:提醒|推送|通知)?\s*(?:的)?\s*(?:设置|配置|参数)"
)
_RE_REM_QUERY = re.compile(
    r"(?:提醒|推送|通知|静默|免打扰)\s*(?:的)?\s*(?:设置|配置|参数)"
    r"|(?:设置|配置|参数)\s*(?:是什么|是啥|咋样|怎么样|多少|如何|看看|显示|发我|查一下)"
    r"|(?:现在|当前|目前|我的)\s*(?:的)?\s*(?:提醒|推送)?\s*(?:设置|配置|参数|怎么设置)"
    r"|(?:提醒|推送|静默|提前).{0,6}(?:多少|是什么|是啥|多久|几分钟|几小时|几天)"
    r"|怎么设置的?"
)

# 一看就是想「改」但没解析出来的（别拿查询结果糊弄）
_RE_REM_UPDATE_HINT = re.compile(r"改成|改到|改为|设为|设成|调成|调到|调整到|改一下|改下|设置成")

# 「像在说提醒设置」的粗筛：主题词 + 动作词
_RE_REM_TOPIC = re.compile(r"提醒|推送|通知|静默|免打扰|勿扰|打扰|每小时|提前")
_RE_REM_ACTION = re.compile(
    r"提前|改|设|调|关|停|恢复|取消|最多|上限|以后|之后|以前|之前|多少|是什么|怎么"
)

_REMINDER_USAGE = (
    "没认出要改哪个提醒设置。可以这样说：\n"
    "· 提醒提前一小时 / 作业提前两天\n"
    "· 晚上十点以后别推 / 静默改成 23:00 到 7:00\n"
    "· 每小时最多推 5 条\n"
    "· 关掉提醒 / 打开提醒\n"
    "· 现在提醒设置是什么"
)


def _cn_int(token: str) -> int | None:
    """中文/阿拉伯数字 → int：「十二」「二十三」「两」「5」都认。"""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    total = 0
    last = 0
    for ch in token:
        if ch == "十":
            total += (last or 1) * 10
            last = 0
        elif ch == "百":
            total += (last or 1) * 100
            last = 0
        elif ch in _CN_DIGITS:
            last = _CN_DIGITS[ch]
        else:
            return None
    return total + last


def _fmt_hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _fmt_advance(minutes: int) -> str:
    """提前量（分钟）→ 人话：1 天 / 2 小时 / 30 分钟 / 1 小时 30 分钟。"""
    if minutes <= 0:
        return "不提前"
    if minutes % (24 * 60) == 0:
        return f"{minutes // (24 * 60)} 天"
    if minutes % 60 == 0:
        return f"{minutes // 60} 小时"
    if minutes > 60:
        return f"{minutes // 60} 小时 {minutes % 60} 分钟"
    return f"{minutes} 分钟"


def _fmt_hours(hours: int) -> str:
    """提前量（小时）→ 人话：2 天 / 24 小时。"""
    if hours <= 0:
        return "不提前"
    if hours % 24 == 0:
        return f"{hours // 24} 天"
    return f"{hours} 小时"


def _as_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _nl_clock_minutes(hour: int, minute: int, period: str | None) -> int | None:
    """钟点 → 一天中的分钟数；带「晚上/下午/凌晨」的按口语换算成 24 小时制。"""
    if not (0 <= hour <= 24 and 0 <= minute <= 59):
        return None
    if hour == 24 and minute == 0:
        hour = 0
    if period in ("凌晨", "早上", "早晨", "上午"):
        if hour == 12:
            hour = 0
    elif period == "中午":
        if 0 < hour < 11:
            hour += 12
    elif period == "下午":
        if 0 < hour < 12:
            hour += 12
    elif period in ("晚上", "夜里", "夜间", "晚", "傍晚"):
        if hour == 12:                       # 晚上十二点 = 零点
            hour = 0
        elif 0 < hour < 12:
            hour += 12
    if not (0 <= hour <= 23):
        return None
    return hour * 60 + minute


def _nl_clock_times(text: str) -> list[int]:
    """句子里的所有钟点（0–1439 分钟），按出现顺序。"""
    found: list[tuple[int, int]] = []
    for m in _RE_CLOCK_COLON.finditer(text):
        value = _nl_clock_minutes(
            int(m.group(2)), int(m.group(3)), m.group("period")
        )
        if value is not None:
            found.append((m.start(), value))
    for m in _RE_CLOCK.finditer(text):
        hour = _cn_int(m.group("hour"))
        if hour is None:
            continue
        minute = 30 if m.group("half") else int(m.group("minute") or 0)
        value = _nl_clock_minutes(hour, minute, m.group("period"))
        if value is not None:
            found.append((m.start(), value))
    found.sort()
    return [value for _, value in found]


def _nl_duration_minutes(text: str) -> int | None:
    """「一小时」「30分钟」「两天」「半小时」「一个半小时」→ 分钟数。"""
    for m in _RE_DURATION.finditer(text):
        unit = (m.group("unit") or "").replace("个", "")
        scale = _DURATION_SCALE.get(unit)
        if scale is None:
            continue
        amount = 0.0
        num_text = m.group("num")
        if num_text == "半":
            amount = 0.5
        elif num_text:
            value = _cn_int(num_text)
            if value is None:
                continue
            amount = float(value)
        if m.group("half"):
            amount += 0.5
        if amount <= 0:
            continue
        return int(round(amount * scale))
    return None


def _nl_reminder_silence(text: str):
    """静默时段意图：返回 patch / False（像静默但没说清）/ None（不是这个意思）。"""
    if _RE_SILENCE_CANCEL.search(text):
        return {"reminder_silence_start": "00:00", "reminder_silence_end": "00:00"}
    if not _RE_SILENCE_HINT.search(text):
        return None
    times = _nl_clock_times(text)
    if len(times) >= 2:
        start, end = times[0], times[1]
        if start == end:
            return False
        return {
            "reminder_silence_start": _fmt_hm(start),
            "reminder_silence_end": _fmt_hm(end),
        }
    if len(times) == 1:
        moment = _fmt_hm(times[0])
        if _RE_SILENCE_END.search(text):
            return {"reminder_silence_end": moment}
        if _RE_SILENCE_START.search(text) or _RE_SILENCE_SET.search(text):
            return {"reminder_silence_start": moment}
        return False
    return None


def _nl_reminder_advance(text: str):
    """提前量：默认改日程（分钟）；说了「作业」就改作业（小时）。"""
    if "提前" not in text:
        return None
    if not re.search(r"提醒|日程|作业|通知|推送", text):
        return None
    minutes = _nl_duration_minutes(text)
    if minutes is None:
        return None
    if "作业" in text:
        hours = max(1, int(round(minutes / 60)))
        if re.search(r"第二次|第二回", text):
            return {"reminder_advance_hw2": max(0, min(hours, 30 * 24))}
        return {"reminder_advance_hw": max(0, min(hours, 30 * 24))}
    return {"reminder_advance_event": max(0, min(minutes, 7 * 24 * 60))}


def _nl_reminder_cap(text: str):
    """每小时最多推几条。"""
    if not _RE_CAP_ANY.search(text):
        return None
    m = _RE_CAP_LIMIT.search(text)
    if not m:
        return None
    value = _cn_int(m.group("num"))
    if value is None or not (1 <= value <= 100):
        return None
    return {"reminder_hourly_cap": value}


def _nl_reminder_switch(text: str):
    """总开关：打开/关掉提醒。"""
    on = _RE_SWITCH_ON.search(text)
    off = _RE_SWITCH_OFF.search(text)
    if on and off:
        return None
    if on:
        if not _RE_SWITCH_TOPIC_ON.search(text):
            return None
        return {"reminder_enabled": 1}
    if off:
        if not _RE_SWITCH_TOPIC_OFF.search(text):
            return None
        return {"reminder_enabled": 0}
    return None


def _nl_reminder_parse(text: str) -> dict | None:
    """把一句话解析成提醒设置意图；认不出返回 None（原样放行）。

    返回：
      {"action": "query"}                  —— 回显全部设置
      {"action": "update", "patch": {...}} —— 改设置（键名同 /bot/reminders/settings）
      {"action": "usage"}                  —— 像在说设置但没说清，给用法提示
    """
    text = (text or "").strip()
    if not text or text.startswith("/"):
        return None

    # 「打开提醒设置」「看看配置」是查询，不是总开关
    if _RE_REM_SHOW.search(text):
        return {"action": "query"}

    patch: dict = {}
    unclear = False

    silence = _nl_reminder_silence(text)
    if silence is False:
        unclear = True
    elif silence:
        patch.update(silence)

    # 静默句里常带「别推」，命中静默后就别再当总开关
    if not patch and not unclear:
        switch = _nl_reminder_switch(text)
        if switch:
            patch.update(switch)

    if not patch and not unclear:
        advance = _nl_reminder_advance(text)
        if advance:
            patch.update(advance)

    if not patch and not unclear:
        cap = _nl_reminder_cap(text)
        if cap:
            patch.update(cap)

    if patch:
        return {"action": "update", "patch": patch}
    if unclear:
        return {"action": "usage"}
    if _RE_REM_UPDATE_HINT.search(text) and _RE_REM_TOPIC.search(text):
        return {"action": "usage"}          # 「提醒设置改成每天」这种没解析出来
    if _RE_REM_QUERY.search(text):
        return {"action": "query"}
    if _RE_REM_TOPIC.search(text) and _RE_REM_ACTION.search(text):
        return {"action": "usage"}
    return None


def _reminder_settings_text(data: dict) -> str:
    """查询回显：后端 settings 全量 → 一段话。"""
    data = data if isinstance(data, dict) else {}
    enabled = _as_int(data.get("reminder_enabled"), 1) != 0
    advance_event = _as_int(data.get("reminder_advance_event"), 60)
    advance_hw = _as_int(data.get("reminder_advance_hw"), 24)
    advance_hw2 = _as_int(data.get("reminder_advance_hw2"), 2)
    cap = _as_int(data.get("reminder_hourly_cap"), REMINDER_HOURLY_CAP)
    start = str(data.get("reminder_silence_start") or "01:00").strip()
    end = str(data.get("reminder_silence_end") or "07:30").strip()

    lines = ["信鸽提醒设置"]
    lines.append(f"Bot 主动推送：{'开' if enabled else '关'}")
    lines.append(
        f"日程提醒：提前 {_fmt_advance(advance_event)}"
        if advance_event > 0 else "日程提醒：开始时提醒"
    )
    hw = f"作业提醒：提前 {_fmt_hours(advance_hw)}" if advance_hw > 0 else "作业提醒：截止时提醒"
    hw += "（第二次" + (f"提前 {_fmt_hours(advance_hw2)}" if advance_hw2 > 0 else "关") + "）"
    lines.append(hw)
    lines.append("静默时段：已取消" if start == end else f"静默时段：{start}–{end}")
    lines.append(f"每小时最多：{cap} 条" if cap > 0 else "每小时最多：不限")
    lines.append("")
    lines.append("改设置直接说：提醒提前一小时 / 晚上十点以后别推 / 关掉提醒")
    return "\n".join(lines)


def _reminder_confirm(patch: dict, data: dict) -> str:
    """改完回一句确认，带上改后的值（以后端返回为准）。"""
    data = data if isinstance(data, dict) else {}
    parts: list[str] = []

    if "reminder_enabled" in patch:
        enabled = _as_int(data.get("reminder_enabled"), patch.get("reminder_enabled") or 0)
        parts.append("提醒已打开" if enabled else "提醒已关掉，不再主动推送")

    if "reminder_advance_event" in patch:
        value = _as_int(data.get("reminder_advance_event"), patch["reminder_advance_event"])
        if value > 0:
            parts.append(f"日程提醒改为提前 {_fmt_advance(value)}")
        else:
            parts.append("日程提醒改为不提前（开始时提醒）")

    if "reminder_advance_hw" in patch:
        value = _as_int(data.get("reminder_advance_hw"), patch["reminder_advance_hw"])
        if value > 0:
            parts.append(f"作业提醒改为提前 {_fmt_hours(value)}")
        else:
            parts.append("作业提醒改为不提前（截止时提醒）")

    if "reminder_advance_hw2" in patch:
        value = _as_int(data.get("reminder_advance_hw2"), patch["reminder_advance_hw2"])
        if value > 0:
            parts.append(f"作业第二次提醒改为提前 {_fmt_hours(value)}")
        else:
            parts.append("作业第二次提醒已关掉")

    if "reminder_silence_start" in patch or "reminder_silence_end" in patch:
        start = str(data.get("reminder_silence_start") or "").strip()
        end = str(data.get("reminder_silence_end") or "").strip()
        if start and end and start != end:
            parts.append(f"静默时段改为 {start}–{end}")
        elif start and end:
            parts.append("静默时段已取消")

    if "reminder_hourly_cap" in patch:
        value = _as_int(data.get("reminder_hourly_cap"), patch["reminder_hourly_cap"])
        if value > 0:
            parts.append(f"每小时最多推 {value} 条")
        else:
            parts.append("每小时推送不再限量")

    if not parts:
        return "设置没变"
    return "好，" + "，".join(parts)


# ---------- 自然语言查询路由 ----------
#
# 没匹配 /指令 的文字不一律丢给大模型（25–60 秒，还花钱）：
# 先按关键词走规则快速路径，直接查后端拼一段话，秒回；
# 只有规则认不出来、但确实是在提问的，才交给 /agent/chat 兜底。
#
# 优先级 -8：排在「改提醒设置」(-5) 之后、「建日程」(-10) 之前。
# 认领不了的原样放行，不影响原有逻辑。

NL_AGENT_TIMEOUT = 45           # agent 一轮通常 25–60 秒，超时就不干等了
NL_AGENT_SLOW = "这个问题想得有点久，先试试 /作业 /活动 这种指令"
NL_QUERY_USAGE = (
    "我是信鸽，可以直接问我：\n"
    "· 最近有什么作业（DDL）\n"
    "· 这周有什么活动\n"
    "· 有新的邮件吗\n"
    "· 明天有什么安排\n"
    "· 今天有课吗\n"
    "急的话用指令更快：/作业 /活动 /邮件 /日程 /日历"
)

# 关键词 → 意图，顺序就是优先级：越具体的越靠前。
RE_NLQ_DEADLINE = re.compile(r"作业|ddl|deadline|截止|要交|该交|快交|due", re.I)
RE_NLQ_ACTIVITY = re.compile(r"活动|报名|二课|第二课堂|讲座|劳动学时")
RE_NLQ_MAIL = re.compile(r"邮件|邮箱|收件箱|信箱|来信")
RE_NLQ_COURSE = re.compile(
    r"课表|课程表|课程|上课|有课|几节课|几门课|什么课|哪些课|哪门课|第.{1,3}节|教室"
)
RE_NLQ_SCHEDULE_WORD = re.compile(r"日程|安排|日历|行程|待办")
RE_NLQ_WEEKDAY = re.compile(r"(?P<pre>下下|下|这|本)?(?:周|星期|礼拜)(?P<wd>[一二三四五六日天])")
RE_NLQ_TIME = re.compile(
    r"今天|今日|今晚|明天|明日|后天|大后天|这周|本周|下周|下下周|最近|这几天|周末"
    r"|(?:(?:下下|下|这|本)?(?:周|星期|礼拜)[一二三四五六日天])"
)
RE_NLQ_AGENDA = re.compile(
    r"安排|日程|事|课|作业|活动|邮件|考试|开会|会议|忙|空|干|做"
    r"|有什么|有啥|有没有|哪些|多少"
)
# 强疑问词：有就当作问题，交给 agent
RE_NLQ_QUESTION_STRONG = re.compile(
    r"[？?]|什么|啥|有没有|有哪些|哪些|多少|几点|怎么样|怎么|为什么|如何|哪"
)
# 弱疑问词：短句里的「吗/呢」多半是闲聊，不值得为它调 agent
RE_NLQ_QUESTION_TAIL = re.compile(r"吗|呢|嘛")
# 「要建日程」的动作/时间线索：有这些就不抢，交给 -10
RE_NLQ_CREATE = re.compile(
    r"\d{1,2}\s*(?:[:：]\s*\d{1,2}|[点时])|点半|一刻|三刻"
    r"|凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|今晚"
    r"|开会|会议|上课|上机|实验|吃饭|见面|面试|体检|考试|报名|讲座|组会"
    r"|讨论|复习|提交|截止|打球|跑步|训练|电影|自习|聚会|帮我|给我|记一下|提醒我"
)
RE_NLQ_CHAT = re.compile(
    r"^(?:你好|您好|你好呀|你好啊|你好吗|哈喽|嗨|hello|hi|在吗|在不在|在么|在嘛"
    r"|早上好|中午好|下午好|晚上好|晚安|早安|谢谢|多谢|谢了|辛苦了"
    r"|测试|test|你是谁|你是什么|你能干嘛|能干嘛|怎么用|怎么使用|帮助|help|菜单|使用说明)"
    r"[\s!！。.~～?？,，…]*$",
    re.I,
)
RE_NLQ_PICK = re.compile(r"^\s*\d{1,2}\s*$")     # 回数字看详情，别抢
RE_NLQ_WORD = re.compile(r"\w")                  # 纯表情 / 纯问号不算消息


def _nlq_short(text, limit: int = 34) -> str:
    """标题压成一行，太长就截断。

    Args:
        text: 原文，可能是 None。
        limit: 最多留几个字。

    Returns:
        单行短文本。
    """
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[: max(1, limit - 1)] + "…"


def _nlq_intent(text: str) -> str:
    """关键词 → 查询意图。

    Args:
        text: 用户原话（已去首尾空白）。

    Returns:
        deadline / activity / mail / course / event 之一；认不出来返回空串。
    """
    if RE_NLQ_DEADLINE.search(text):
        return "deadline"
    if RE_NLQ_ACTIVITY.search(text):
        return "activity"
    if RE_NLQ_MAIL.search(text):
        return "mail"
    if RE_NLQ_COURSE.search(text):
        return "course"
    if RE_NLQ_SCHEDULE_WORD.search(text):
        return "event"
    # 只说时间不算（「今天真热」），得有安排/事/课这类词，或者问的是周几
    if RE_NLQ_TIME.search(text) and (RE_NLQ_AGENDA.search(text) or RE_NLQ_WEEKDAY.search(text)):
        return "event"
    return ""


def _nlq_in_range(stamp, start: date, end: date) -> bool:
    """时间串的日期是否落在闭区间 [start, end] 里；解析不了就当在。

    Args:
        stamp: "2026-11-24 23:59" / "2026-11-24" 这类时间串。
        start: 区间开始日期。
        end: 区间结束日期。

    Returns:
        在区间里（或解析不了）返回 True。
    """
    text = str(stamp or "")[:10]
    if not text:
        return True
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return True
    return start <= day <= end


def _nlq_range(text: str) -> tuple[date, date, str]:
    """把口语里的日期词换成具体区间。

    Args:
        text: 用户原话。

    Returns:
        (start, end, 人话标签)，闭区间；没提日期默认从今天起 7 天。
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    if "大后天" in text:
        day = today + timedelta(days=3)
        return day, day, "大后天"
    if "后天" in text:
        day = today + timedelta(days=2)
        return day, day, "后天"
    if "明天" in text or "明日" in text:
        day = today + timedelta(days=1)
        return day, day, "明天"
    if "今天" in text or "今日" in text or "今晚" in text:
        return today, today, "今天"

    # 「周三」「下周三」「这周五」：具体某一天
    match = RE_NLQ_WEEKDAY.search(text)
    if match:
        word = match.group("wd")
        target = 7 if word == "天" else WEEKDAY_CN.find(word) + 1
        if target:
            pre = match.group("pre") or ""
            delta = target - today.isoweekday()
            if pre == "下":
                delta += 7
            elif pre == "下下":
                delta += 14
            elif not pre and delta < 0:
                delta += 7                      # 只说「周三」= 最近的周三
            day = today + timedelta(days=delta)
            return day, day, f"周{WEEKDAY_CN[day.weekday()]}"

    if "下周末" in text:
        saturday = monday + timedelta(days=12)
        return saturday, saturday + timedelta(days=1), "下周末"
    if "周末" in text:
        saturday = monday + timedelta(days=5)
        return saturday, saturday + timedelta(days=1), "周末"
    if "下下周" in text:
        start = monday + timedelta(days=14)
        return start, start + timedelta(days=6), "下下周"
    if "下周" in text:
        start = monday + timedelta(days=7)
        return start, start + timedelta(days=6), "下周"
    if "这周" in text or "本周" in text:
        return monday, monday + timedelta(days=6), "这周"
    if "最近" in text or "这几天" in text:
        return today, today + timedelta(days=6), "最近 7 天"
    return today, today + timedelta(days=6), "未来 7 天"


def _nlq_deadline_text(items: list, label: str) -> str:
    """带截止时间的条目 → 一段话（格式对齐 /ddl）。

    Args:
        items: /bot/deadlines 返回的条目。
        label: 时间范围的人话标签。

    Returns:
        可以直接发给用户的文本。
    """
    if not items:
        return f"{label}没有要交的东西"
    lines = [f"{label}要交（{len(items)} 项）"]
    for i, item in enumerate(items[:10], 1):
        lines.append(f'{i}. {_nlq_short(item.get("title"))}')
        lines.append(f'   {str(item.get("deadline") or item.get("start") or "")[:16]}')
    if len(items) > 10:
        lines.append(f"…还有 {len(items) - 10} 项")
    lines.append("")
    lines.append("回数字看详情")
    return "\n".join(lines)


def _nlq_event_text(events: list, derived: list, label: str) -> str:
    """日程 + 带截止的待办按天拼成一段话。

    Args:
        events: /events 里的 events（手动/对话建的日程）。
        derived: /events 里的 derived（带截止时间的作业等，只读）。
        label: 时间范围的人话标签。

    Returns:
        可以直接发给用户的文本。
    """
    days: dict[str, list[str]] = {}

    for event in events:
        day = str(event.get("event_date") or "")[:10]
        if not day:
            continue
        when = str(event.get("start_time") or "")
        end_time = str(event.get("end_time") or "")
        if when and end_time:
            when = f"{when}-{end_time}"
        place = f' @{_nlq_short(event.get("location"), 12)}' if event.get("location") else ""
        mark = "（已完成）" if event.get("done") else ""
        days.setdefault(day, []).append(
            f'{when or "全天"} {_nlq_short(event.get("title"), 30)}{place}{mark}'
        )

    for item in derived:
        stamp = str(item.get("start") or item.get("deadline") or "")
        day = stamp[:10]
        if not day:
            continue
        mark = "（已完成）" if item.get("done") else "（作业）"
        days.setdefault(day, []).append(
            f'{stamp[11:16] or "全天"} {_nlq_short(item.get("title"), 30)}{mark}'
        )

    if not days:
        return f"{label}没有安排"

    total = sum(len(rows) for rows in days.values())
    lines = [f"{label}的安排（{total} 条）"]
    for day in sorted(days):
        lines.append(f"\n{_nl_when(day)}")
        for row in sorted(days[day]):
            lines.append(f"· {row}")
    return "\n".join(lines)


def _nlq_activity_text(items: list) -> str:
    """第二课堂活动 → 一段话。

    Args:
        items: /bot/second-classroom 返回的条目。

    Returns:
        可以直接发给用户的文本。
    """
    if not items:
        return "最近没有第二课堂活动"
    lines = [f"第二课堂活动（{len(items)} 个）"]
    for i, item in enumerate(items[:8], 1):
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        when = str(
            extra.get("start") or item.get("published") or item.get("deadline") or ""
        )[:16].replace("T", " ")
        place = extra.get("place") or ""
        lines.append(f'{i}. {_nlq_short(item.get("title"))}')
        tail = " · ".join(x for x in (when, _nlq_short(place, 14)) if x)
        if tail:
            lines.append(f"   {tail}")
    lines.append("")
    lines.append("回数字看详情")
    return "\n".join(lines)


def _nlq_mail_text(data: dict) -> str:
    """收件箱 → 一段话。

    Args:
        data: /bot/mail 返回的 {"messages": [...], "unread": n}。

    Returns:
        可以直接发给用户的文本。
    """
    data = data if isinstance(data, dict) else {}
    messages = data.get("messages") or []
    if not messages:
        return "收件箱是空的"
    unread = _as_int(data.get("unread"), 0)
    lines = [f"收件箱（未读 {unread}）" if unread else "收件箱"]
    for i, message in enumerate(messages[:8], 1):
        sender = message.get("from_name") or message.get("from_addr") or "未知发件人"
        lines.append(f'{i}. {_nlq_short(message.get("subject"), 36)}')
        lines.append(f'   {_nlq_short(sender, 16)} · {str(message.get("date") or "")[:16]}')
    return "\n".join(lines)


def _nlq_weeks(zcd: str) -> list[int]:
    """解析教务的周次串（同 xinge.jwc.parse_weeks，插件这边不能 import 它）。

    Args:
        zcd: 形如 "1-12周"、"3-15周(单)"、"1-8周,10-16周"。

    Returns:
        周次列表，从 1 开始。
    """
    text = str(zcd or "").strip()
    if not text:
        return []
    parity = "odd" if "单" in text else ("even" if "双" in text else "")
    weeks: list[int] = []
    for part in re.split(r"[,，]", text):
        nums = [int(n) for n in re.findall(r"\d+", part)]
        if len(nums) >= 2:
            weeks.extend(range(nums[0], nums[1] + 1))
        elif nums:
            weeks.append(nums[0])
    if parity == "odd":
        weeks = [w for w in weeks if w % 2 == 1]
    elif parity == "even":
        weeks = [w for w in weeks if w % 2 == 0]
    return sorted(set(weeks))


def _nlq_week_no(term_start: date, day: date) -> int:
    """校历周次：学期开始那周算第 1 周。

    Args:
        term_start: 学期开始日期。
        day: 要算的日期。

    Returns:
        周次（可能 <= 0，说明还没开学）。
    """
    return (day - term_start).days // 7 + 1


def _nlq_course_text(courses: list, start: date, end: date, label: str,
                     term_start: date | None) -> str:
    """课表 → 一段话，只留区间内、当周有课的课。

    Args:
        courses: /jwc/timetable 返回的 courses。
        start: 区间开始日期。
        end: 区间结束日期。
        label: 时间范围的人话标签。
        term_start: 校历学期开始日期；None 表示不按周次过滤。

    Returns:
        可以直接发给用户的文本。
    """
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    grouped: list[tuple[date, list[str]]] = []

    for day in days:
        rows: list[str] = []
        for course in courses:
            if _as_int(course.get("weekday"), 0) != day.isoweekday():
                continue
            weeks = _nlq_weeks(course.get("weeks"))
            if term_start is not None and weeks and _nlq_week_no(term_start, day) not in weeks:
                continue
            period = str(course.get("periods") or "").strip()
            name = _nlq_short(course.get("name"), 24)
            room = str(course.get("room") or "").strip()
            line = f"{period} {name}".strip()
            if room:
                line += f' @{_nlq_short(room, 12)}'
            rows.append(line)
        if rows:
            grouped.append((day, rows))

    if not grouped:
        return f"{label}没有课"

    if len(days) == 1:
        rows = grouped[0][1]
        return f"{label}的课（{len(rows)} 条）\n" + "\n".join(f"· {row}" for row in rows)

    total = sum(len(rows) for _, rows in grouped)
    lines = [f"{label}的课（{total} 条）"]
    for day, rows in grouped:
        lines.append(f"\n{_nl_when(day.isoformat())}")
        for row in rows:
            lines.append(f"· {row}")
    return "\n".join(lines)


async def _nlq_agent(text: str) -> str:
    """规则搞不定的问题才走 agent；超时给一句提示，不让用户干等。

    Args:
        text: 用户原话。

    Returns:
        agent 的回答，或超时/失败时的兜底提示。
    """
    try:
        async with httpx.AsyncClient(timeout=NL_AGENT_TIMEOUT) as client:
            resp = await client.post(
                BASE + "/agent/chat",
                json={"messages": [{"role": "user", "content": text}]},
            )
            resp.raise_for_status()
            data = resp.json() or {}
    except httpx.TimeoutException:
        logger.info("agent 查询超时，回兜底提示")
        return NL_AGENT_SLOW
    except Exception as exc:                        # noqa: BLE001
        logger.warning(f"agent 查询失败：{exc}")
        return "AI 暂时没响应，先试试 /作业 /活动 /邮件 /日程"

    reply = str(data.get("reply") or "").strip()
    return reply[:1500] or NL_AGENT_SLOW


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

    # ---------- 自然语言改提醒设置 ----------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-5)
    async def reminder_nl(self, event: AstrMessageEvent):
        """不用指令、不用开 App：说一句话就改提醒设置。

        优先级 -5：排在所有 /指令（0）后面，排在自然语言建日程（-10）前面。
        认出来的句子自己答并拦住后续兜底；认不出来的原样放行，不打扰原有逻辑。
        """
        text = (event.message_str or "").strip()
        if not text or text.startswith("/"):
            return

        # 群聊没 @ 机器人 / 没唤醒词就不插嘴（交给后面的兜底按原逻辑 stop）
        if not event.is_at_or_wake_command:
            return

        parsed = _nl_reminder_parse(text)
        if not parsed:
            return

        # 「明天下午三点提醒我开会」这种是建日程，让给后面的兜底，别抢
        if RE_NL_DATE.search(text) and _nl_looks_like_schedule(text):
            return

        action = parsed.get("action")

        if action == "query":
            try:
                data = await _rjson("/bot/reminders/settings")
            except Exception as exc:                  # noqa: BLE001
                logger.warning(f"读取提醒设置失败：{exc}")
                yield event.plain_result("后端没响应，暂时读不到提醒设置")
                _nl_silence(event)
                return
            _nl_forget(event)
            yield event.plain_result(_reminder_settings_text(data or {}))
            _nl_silence(event)
            return

        if action == "usage":
            yield event.plain_result(_REMINDER_USAGE)
            _nl_silence(event)
            return

        patch = parsed.get("patch") or {}
        try:
            data = await _rpost("/bot/reminders/settings", patch)
        except Exception as exc:                      # noqa: BLE001
            logger.error(f"改提醒设置失败：{exc}")
            yield event.plain_result("改设置失败：后端没响应，稍后再试")
            _nl_silence(event)
            return

        _nl_forget(event)
        yield event.plain_result(_reminder_confirm(patch, data or {}))
        _nl_silence(event)

    # ---------- 自然语言查询 ----------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-8)
    async def nl_query(self, event: AstrMessageEvent):
        """没匹配指令的自然语言：关键词走规则快速查询，问题才交给 agent。

        优先级 -8：排在「改提醒设置」(-5) 后面、「建日程」(-10) 前面。
        规则能认的直接查后端并回答（毫秒级）；规则认不出来、但确实是在
        提问的才调 /agent/chat（25–60 秒，超时给一句提示）；闲聊给用法提
        示，不浪费模型调用；像建日程的原样放行给 -10。
        """
        text = (event.message_str or "").strip()
        if not text or text.startswith(("/", "／")):
            return
        if RE_NLQ_PICK.match(text) or not RE_NLQ_WORD.search(text):
            return                                  # 回数字 / 纯表情，别抢

        # 群聊没 @ 机器人 / 没唤醒词就不插嘴（和另外两个兜底 handler 一致）
        if not event.is_at_or_wake_command:
            return

        # 正在补建日程（上一条缺日期/标题）：让给 -10，别打断用户的补充
        if _nl_pending(event):
            return

        intent = _nlq_intent(text)

        # 看着像在描述一件要建日程的事：让给 -10。
        # 例外：命中查询关键词、又没有钟点/开会这类线索的（「明天的安排」
        # 「今天要交的作业」）明显是查询，别让 -10 建成一条奇怪的日程。
        if _nl_looks_like_schedule(text) and not (intent and not RE_NLQ_CREATE.search(text)):
            return

        if not intent:
            # 只有真像个问题才交给 agent（25–60 秒，慢）；闲聊/短句给一句用法提示
            question = bool(RE_NLQ_QUESTION_STRONG.search(text)) or (
                len(text) > 6 and bool(RE_NLQ_QUESTION_TAIL.search(text))
            )
            if not question or RE_NLQ_CHAT.match(text):
                yield event.plain_result(NL_QUERY_USAGE)
                _nl_silence(event)
                return
            reply = await _nlq_agent(text)
            yield event.plain_result(reply)
            _nl_silence(event)
            return

        try:
            reply = await self._nlq_answer(event, intent, text)
        except Exception as exc:                    # noqa: BLE001
            logger.warning(f"自然语言查询失败（{intent}）：{exc}")
            reply = "查询失败，后端没响应，先试试 /作业 /活动 /邮件 /日程"
        yield event.plain_result(reply)
        _nl_silence(event)

    async def _nlq_answer(self, event: AstrMessageEvent, intent: str, text: str) -> str:
        """规则快速路径：按意图查后端，拼一段可以直接发出去的话。

        Args:
            event: 当前消息事件（查完列表要记住，支持回数字看详情）。
            intent: _nlq_intent 认出来的意图。
            text: 用户原话。

        Returns:
            给用户看的整段回答。
        """
        today = date.today()
        start, end, label = _nlq_range(text)
        days = max(1, min((end - today).days + 1, 30))

        if intent == "deadline":
            data = await _json("/bot/deadlines", days=days)
            items = [
                item
                for item in (data.get("items") or [])
                if _nlq_in_range(item.get("deadline") or item.get("start"), start, end)
            ]
            if items:
                _remember(event, items, "作业")
            return _nlq_deadline_text(items, label)

        if intent == "activity":
            data = await _json("/bot/second-classroom", limit=8)
            items = data.get("items") or []
            if items:
                _remember(event, items, "活动")
            return _nlq_activity_text(items)

        if intent == "mail":
            try:
                data = await _json("/bot/mail", limit=8)
            except Exception as exc:                # noqa: BLE001
                logger.info(f"自然语言查邮件失败：{exc}")
                return "邮件查不了：可能还没配置邮箱。去 App 的「机器人」里填一下邮箱，再试。"
            return _nlq_mail_text(data)

        if intent == "event":
            data = await _json("/events", start=start.isoformat(), end=end.isoformat())
            return _nlq_event_text(
                data.get("events") or [], data.get("derived") or [], label
            )

        # 课表：没提时间就看本周
        if not (RE_NLQ_TIME.search(text) or RE_NLQ_WEEKDAY.search(text)):
            monday = today - timedelta(days=today.weekday())
            start, end, label = monday, monday + timedelta(days=6), "本周"

        term_start = None
        try:
            calendar = await _json("/jwc/calendar")
            term_start = date.fromisoformat(str(calendar.get("start") or "")[:10])
        except Exception as exc:                    # noqa: BLE001
            logger.info(f"读校历失败，课表不按周次过滤：{exc}")

        try:
            data = await _json("/jwc/timetable")
        except Exception as exc:                    # noqa: BLE001
            logger.info(f"读课表失败：{exc}")
            return "课表查不到：教务还没登录。去 App 里重新登录 jAccount 再试，或者用 /日历 看。"

        return _nlq_course_text(data.get("courses") or [], start, end, label, term_start)

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