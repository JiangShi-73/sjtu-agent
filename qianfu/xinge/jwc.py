"""教务网（i.sjtu.edu.cn）课表。

登录走 jAccount OAuth：/jaccountlogin 会 302 到 jaccount，
拿到 code 再换回教务自己的 JSESSIONID。

课表接口是正方那套：

    POST /kbcx/xskbcx_cxXsKb.html?gnmkdm=N2151
         xnm=2026  xqm=3  kzlx=ck

返回的 kbList 每条是「一门课 × 一个星期 × 一个节次」，
带 dateDigitSeparator（下一次上课的日期）、xqj（星期几）、
jcs（第几节）、zcd（周次范围）、cdmc（教室）。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from . import jaccount

ORIGIN = "https://i.sjtu.edu.cn"
COOKIE_PATH = Path(os.path.expanduser("~")) / ".xinge" / "jwc.json"

# 每节课的开始时间
START_AT = {
    1: "08:00",
    2: "08:55",
    3: "10:00",
    4: "10:55",
    5: "14:00",
    6: "14:55",
    7: "16:00",
    8: "16:55",
    9: "18:30",
    10: "19:25",
    11: "20:20",
    12: "21:15",
    13: "22:10",
    14: "23:05",
}
# 每节课的结束时间
END_AT = {
    1: "08:45",
    2: "09:40",
    3: "10:45",
    4: "11:40",
    5: "14:45",
    6: "15:40",
    7: "16:45",
    8: "17:40",
    9: "19:15",
    10: "20:10",
    11: "21:05",
    12: "22:00",
    13: "22:55",
    14: "23:50",
}


def period_time(periods: str) -> tuple[str, str]:
    """把「3-4」这样的节次换成起止时间。

    Args:
        periods: 形如 "3-4"、"11-13"、"1-2节"。

    Returns:
        (开始时间, 结束时间)，认不出来就返回空串。
    """
    import re

    nums = [int(n) for n in re.findall(r"[0-9]+", periods or "")]
    if not nums:
        return "", ""
    first, last = nums[0], nums[-1]
    return START_AT.get(first, ""), END_AT.get(last, "")


class JwcError(RuntimeError):
    pass


def _load() -> dict:
    if not COOKIE_PATH.exists():
        return {}
    try:
        return json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(jar: dict) -> None:
    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_PATH.write_text(json.dumps(jar, ensure_ascii=False), encoding="utf-8")


def logged_in() -> bool:
    return bool(_load().get("JSESSIONID"))


def login() -> dict:
    """用 jAccount 会话换教务的 JSESSIONID。"""
    jar = jaccount.cookies()
    if not jar.get("JAAuthCookie"):
        return {"ok": False, "error": "还没有 jAccount 登录态"}

    with httpx.Client(headers={"User-Agent": jaccount.UA}, timeout=30, follow_redirects=True) as client:
        for name, value in jar.items():
            client.cookies.set(name, value, domain="jaccount.sjtu.edu.cn")
        resp = client.get(ORIGIN + "/jaccountlogin")
        cookies = {
            c.name: c.value
            for c in client.cookies.jar
            if c.domain and "jaccount" not in c.domain
        }

    if not cookies.get("JSESSIONID") or "login_slogin" in str(resp.url):
        return {"ok": False, "error": "SSO 没进教务，可能要重新扫码登录"}

    _save(cookies)
    return {"ok": True, "cookies": len(cookies)}


def _client() -> httpx.Client:
    jar = _load()
    if not jar:
        result = login()
        if not result.get("ok"):
            raise JwcError(result.get("error") or "登录失败")
        jar = _load()
    return httpx.Client(
        headers={"User-Agent": jaccount.UA, "X-Requested-With": "XMLHttpRequest"},
        cookies=jar,
        timeout=40,
        follow_redirects=True,
    )


def term_calendar() -> dict:
    """从校历页解析本学期起止日期。

    页面里有一行：2026-2027学年1学期(2026-09-07至2027-02-07)

    Returns:
        {"name": 学期名, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
    """
    import re

    with _client() as client:
        resp = client.get(ORIGIN + "/xtgl/index_cxAreaFive.html")

    match = re.search(
        r"(\d{4}-\d{4}学年\d学期)\s*[(（]\s*(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})",
        resp.text,
    )
    if not match:
        raise JwcError("校历页里没找到学期起止日期")

    return {"name": match.group(1), "start": match.group(2), "end": match.group(3)}


def parse_weeks(zcd: str) -> list[int]:
    """解析周次范围。

    Args:
        zcd: 形如 "1-12周"、"3-15周(单)"、"1-16周(双)"、"1-8周,10-16周"。

    Returns:
        周次列表，从 1 开始。
    """
    import re

    text = (zcd or "").strip()
    if not text:
        return []

    parity = ""
    if "单" in text:
        parity = "odd"
    elif "双" in text:
        parity = "even"

    weeks: list[int] = []
    for part in re.split(r"[,，]", text):
        nums = [int(n) for n in re.findall(r"[0-9]+", part)]
        if not nums:
            continue
        if len(nums) >= 2:
            weeks.extend(range(nums[0], nums[1] + 1))
        else:
            weeks.append(nums[0])

    if parity == "odd":
        weeks = [w for w in weeks if w % 2 == 1]
    elif parity == "even":
        weeks = [w for w in weeks if w % 2 == 0]

    return sorted(set(weeks))


def timetable(year: int | None = None, term: int | None = None) -> dict:
    """拉课表。

    Args:
        year: 学年起始年，如 2026。
        term: 学期，3 = 秋，12 = 春。

    Returns:
        {"year": ..., "term": ..., "term_name": ..., "student": {...}, "courses": [...]}
    """
    today = date.today()
    year = year or (today.year if today.month >= 8 else today.year - 1)
    term = term or (3 if today.month >= 8 or today.month <= 1 else 12)

    with _client() as client:
        resp = client.post(
            ORIGIN + "/kbcx/xskbcx_cxXsKb.html?gnmkdm=N2151",
            data={"xnm": str(year), "xqm": str(term), "kzlx": "ck"},
        )
        if resp.status_code != 200 or len(resp.text) < 20:
            raise JwcError(f"课表接口返回 {resp.status_code}")
        data = resp.json() or {}

    courses = []
    for row in data.get("kbList") or []:
        courses.append(
            {
                "name": row.get("kcmc") or "",
                "teacher": row.get("xm") or "",
                "room": row.get("cdmc") or "",
                "weekday": int(row.get("xqj") or 0),
                "weekday_name": row.get("xqjmc") or "",
                "periods": row.get("jcs") or "",
                "weeks": row.get("zcd") or "",
                "next_date": row.get("dateDigitSeparator") or "",
            }
        )

    info = data.get("xsxx") or {}
    return {
        "year": year,
        "term": term,
        "term_name": f'{info.get("XNMC", "")} 第 {info.get("XQMMC", "")} 学期',
        "student": {
            "name": info.get("XM"),
            "class": info.get("BJMC"),
            "major": info.get("ZYMC"),
        },
        "courses": courses,
    }


def to_events(weeks: int | None = None, upcoming_only: bool = True) -> list[dict]:
    """把课表转成日历事件。

    用校历里的学期开始日期算第 1 周的周一，再按每门课的周次范围铺满。

    Args:
        weeks: 只排前 N 周；None 表示排满整个学期。
        upcoming_only: 只排今天及以后的课，默认 True。

    Returns:
        事件字典列表，可直接写进日历。
    """
    data = timetable()
    calendar = term_calendar()

    term_start = datetime.strptime(calendar["start"], "%Y-%m-%d").date()
    # 学期开始那天可能不是周一，往前推到周一
    term_start -= timedelta(days=term_start.weekday())

    today = date.today()
    events: list[dict] = []

    for course in data["courses"]:
        start_time, end_time = period_time(course["periods"])
        weekday = int(course.get("weekday") or 1)

        week_numbers = parse_weeks(course.get("weeks") or "")
        if not week_numbers:
            continue
        if weeks:
            week_numbers = week_numbers[:weeks]

        for week in week_numbers:
            day = term_start + timedelta(days=7 * (week - 1) + (weekday - 1))
            if upcoming_only and day < today:
                continue
            events.append(
                {
                    "title": course["name"],
                    "event_date": day.isoformat(),
                    "start_time": start_time,
                    "end_time": end_time,
                    "location": course["room"],
                    "note": f'{course["teacher"]} · {course["weeks"]}',
                    "source": "jwc",
                }
            )

    return events
