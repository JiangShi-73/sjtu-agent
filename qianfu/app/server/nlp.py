"""中文日程解析。

不依赖大模型——用规则解析常见的口语表达：
    "明天下午3点在图书馆开会"
    "下周三上午10点到11点 组会"
    "11月25日晚上7点 讨论作业"

解析失败时返回 missing 字段，由调用方追问。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

RE_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
RE_CN_DATE = re.compile(r"(\d{1,2})月(\d{1,2})[日号]")
RE_WEEK = re.compile(r"(下下|下|本|这)?(?:周|星期|礼拜)([一二三四五六日天])")
RE_DAYWORD = re.compile(r"(大后天|后天|明天|明日|今天|今日)")
RE_PERIOD = re.compile(r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|晚)")
# 分钟部分除了阿拉伯数字，还要认「半 / 一刻 / 三刻 / 整」
# 否则 "12点半" 会解析成 12:00，而且「半」会残留到标题里
_M = r"(半|一刻|三刻|整|\d{1,2})?"
RE_TIME = re.compile(r"(\d{1,2})(?:[:：点时])\s*" + _M + r"分?")
RE_RANGE = re.compile(
    r"(\d{1,2})(?:[:：点时])\s*" + _M + r"\s*(?:到|至|-|~|—)\s*(\d{1,2})(?:[:：点时])\s*" + _M
)
RE_LOCATION = re.compile(r"(?:在|地点[:：]?)([^\s，,。；;]{1,20})")

# 不带「在」的地点：东上院308 / 东中院2-106 / 3号楼 / 五餐 / 图书馆 / 308教室
RE_ROOM = re.compile(
    r"([东南西北中][上下中]?院\s*\d[\w\-]*"
    r"|[一二三四五六七八九十\d]+号楼"
    r"|[一二三四五六七八九十\d]+餐"
    r"|(?:包玉刚|主)?图书馆"
    r"|[A-Za-z]?\d{2,4}\s*(?:教室|室))"
)
MINUTE_WORD = {"半": 30, "一刻": 15, "三刻": 45, "整": 0}

PERIOD_OFFSET = {"凌晨": 0, "早上": 0, "早晨": 0, "上午": 0, "中午": 12,
                 "下午": 12, "傍晚": 12, "晚上": 12, "夜里": 12, "晚": 12}


def _norm(text: str) -> str:
    table = str.maketrans("０１２３４５６７８９：", "0123456789:")
    return text.translate(table).strip()


def _resolve_date(text: str, today: date) -> tuple[date | None, str]:
    """返回 (日期, 被消费的原文片段)。"""
    m = RE_ISO.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(0)
        except ValueError:
            pass

    m = RE_CN_DATE.search(text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = today.year if (month, day) >= (today.month, today.day) else today.year + 1
        try:
            return date(year, month, day), m.group(0)
        except ValueError:
            pass

    m = RE_DAYWORD.search(text)
    if m:
        word = m.group(1)
        delta = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3}[word]
        return today + timedelta(days=delta), word

    m = RE_WEEK.search(text)
    if m:
        prefix, wd = m.group(1) or "", WEEKDAY[m.group(2)]
        base = today - timedelta(days=today.weekday())      # 本周一
        if prefix == "下":
            base += timedelta(days=7)
        elif prefix == "下下":
            base += timedelta(days=14)
        elif prefix in ("本", "这"):
            pass
        else:
            # 光说"周三"：取最近的将来那天
            delta = (wd - today.weekday()) % 7
            return today + timedelta(days=delta), m.group(0)
        return base + timedelta(days=wd), m.group(0)

    return None, ""


def _minutes(value: str | None) -> int:
    """把「半 / 一刻 / 三刻 / 25」换成分钟数。"""
    if not value:
        return 0
    if value in MINUTE_WORD:
        return MINUTE_WORD[value]
    try:
        return int(value)
    except ValueError:
        return 0


def _resolve_time(text: str) -> tuple[str, str, list[str]]:
    """返回 (开始, 结束, 被消费的片段列表)。

    时段词（上午/下午/晚上）必须取时间**前面最近**的那个，
    否则 "明天下午3点" 会解析成凌晨三点。
    """
    used: list[str] = []
    start = end = ""
    head = 0                      # 时间片段在原串里的起始位置

    m = RE_RANGE.search(text)
    if m:
        h1, m1, h2, m2 = m.groups()
        start = f"{int(h1):02d}:{_minutes(m1):02d}"
        end = f"{int(h2):02d}:{_minutes(m2):02d}"
        used.append(m.group(0))
        head = m.start()
    else:
        m = RE_TIME.search(text)
        if m:
            start = f"{int(m.group(1)):02d}:{_minutes(m.group(2)):02d}"
            used.append(m.group(0))
            head = m.start()

    if not start:
        return "", "", used

    # 只看时间前面那段，取最后一个时段词
    period = ""
    for pm in RE_PERIOD.finditer(text[:head]):
        period = pm.group(1)
    if period:
        used.append(period)

    offset = PERIOD_OFFSET.get(period, 0)
    h, mm = start.split(":")
    hh = int(h)
    if offset and hh < 12:
        hh += offset
    start = f"{hh:02d}:{mm}"

    if end:
        eh, em = end.split(":")
        ehh = int(eh)
        if offset and ehh < 12:
            ehh += offset
        end = f"{ehh:02d}:{em}"
    else:
        end_dt = datetime(2000, 1, 1, hh, int(mm)) + timedelta(hours=1)
        end = end_dt.strftime("%H:%M")

    return start, end, used


def parse(text: str, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    today = now.date()
    raw = text
    text = _norm(text)

    day, date_frag = _resolve_date(text, today)
    start, end, time_frags = _resolve_time(text)

    location = ""
    loc_drop = ""
    m = RE_LOCATION.search(text)
    if m:
        raw_loc = m.group(1)
        location = raw_loc
        # "在图书馆开会" 里的"开会"是标题的一部分，只截到动词前
        for stop in ("开会", "集合", "讨论", "上课", "面试", "吃饭", "见"):
            idx = location.find(stop)
            if idx > 0:
                location = location[:idx]
        location = location.strip("的 ")
        # 只从标题里删掉「在 + 地点」，不要把后面的动词一起删了
        keep = len(m.group(0)) - len(raw_loc) + len(location)
        loc_drop = m.group(0)[:keep]
    else:
        # 没说「在」，也试试认楼/教室号：东上院308、3号楼、五餐、图书馆
        rm = RE_ROOM.search(text)
        if rm:
            location = rm.group(1).strip()
            loc_drop = rm.group(0)

    title = text
    for frag in filter(None, [date_frag, *time_frags, loc_drop]):
        title = title.replace(frag, " ")
    title = re.sub(r"[，,。；;：:\s]+", " ", title).strip()

    missing = []
    if not day:
        missing.append("date")
    if not start:
        missing.append("time")
    if not title:
        missing.append("title")

    return {
        "raw": raw,
        "title": title,
        "event_date": day.isoformat() if day else "",
        "start_time": start,
        "end_time": end,
        "location": location,
        "missing": missing,
    }
