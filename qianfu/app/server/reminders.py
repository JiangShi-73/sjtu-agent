"""提醒队列：把日程 / 作业 / 新邮件转成"到点要推给用户"的消息。

分工：
  * db 负责建表（reminder / mail_seen / bot_contacts）和存储；
  * 本模块只管规则：什么时候该提醒、生成哪些提醒、什么时候算推过；
  * routers/bot.py 只做 HTTP 包装，QQ 插件每 60 秒拉一次 due 再 ack。

规则（2026-11 需求，用户调整过范围）：
  * 日程 events：有 start_time → 开始前 reminder_advance_event 分钟（默认 60）；
    无 start_time（全天）→ 当天 08:30；jwc 课表同步出来的（source=jwc）不提醒
  * 作业 items：board in {course, mooc, zhitu, lab} 且有 deadline
    → 截止前 reminder_advance_hw 小时（默认 24）+ reminder_advance_hw2 小时（默认 2，
    填 0 = 不要第二次）
    其它板块（college / shuiyuan / custom / event）不按条目提醒；
    活动只要用户点了「加入日程」就会变成 events 里的一条，由日程规则覆盖
  * 新邮件：未读且没提醒过的，title=发件人、body=主题；每轮最多 reminder_hourly_cap 条，
    多出来的合并成一条「还有 N 封新邮件」
  * 静默时段 reminder_silence_start ~ reminder_silence_end（默认 01:00–07:30）：
    fire_at 落进去就顺延到结束时刻
  * 同一 target + 同一 fire_at 已经生成过（任何状态）就跳过，重复抓取不会重复提醒
  * 提醒点已经过去超过 24 小时的（补抓回来的历史作业等）直接不生成

上面带 reminder_ 开头的都是 settings 表里的可配置项，App 通过
GET/POST /api/v1/bot/reminders/settings 读写；改提前量/静默时段会自动重算 pending。

所有时间都用本机时区的 naive datetime，存成 "YYYY-MM-DD HH:MM:SS" 字符串，
和 SQLite 的 datetime('now','localtime') 可直接比较。
"""

from __future__ import annotations

import json
import logging
import time as _time
from datetime import date, datetime, time, timedelta

from . import db

log = logging.getLogger("xinge.reminders")

# 只有这四个板块的条目按 deadline 生成作业提醒
ITEM_BOARDS = ("course", "mooc", "zhitu", "lab")
ITEM_BOARD_NAMES = {
    "course": "Canvas",
    "mooc": "慕课",
    "zhitu": "知途好课",
    "lab": "实验",
}

# 全天日程固定当天 08:30 提醒（不在可配置表里）
EVENT_ALL_DAY_AT = time(8, 30)

# 提醒点已经过去超过 24 小时的不再生成，避免一启动就灌一堆历史迟到提醒
STALE_HOURS = 24

# --- 可配置项（存 settings 表，App 可读写） ---
DEFAULT_SETTINGS: dict = {
    "reminder_enabled": 1,          # Bot 主动推送总开关
    "reminder_advance_event": 60,   # 日程提前多少分钟
    "reminder_advance_hw": 24,      # 作业第一次提前多少小时
    "reminder_advance_hw2": 2,      # 作业第二次提前多少小时（0 = 不要）
    "reminder_silence_start": "01:00",
    "reminder_silence_end": "07:30",
    "reminder_hourly_cap": 3,       # 每小时最多几条（0 = 不限）
}
# 整数项的取值范围
SETTINGS_RANGES: dict[str, tuple[int, int]] = {
    "reminder_advance_event": (0, 7 * 24 * 60),
    "reminder_advance_hw": (0, 30 * 24),
    "reminder_advance_hw2": (0, 30 * 24),
    "reminder_hourly_cap": (0, 100),
}
SETTINGS_KEYS = tuple(DEFAULT_SETTINGS)

# 改这些参数会影响 fire_at，改完要把 pending 重算
RECALC_KEYS = frozenset({
    "reminder_advance_event",
    "reminder_advance_hw",
    "reminder_advance_hw2",
    "reminder_silence_start",
    "reminder_silence_end",
})

TARGET_UMO = "reminder_umo"
TARGET_AT = "reminder_umo_at"
TARGET_SOURCE = "reminder_umo_source"


# ---------------- 基础工具 ----------------

def _now() -> datetime:
    return datetime.now()


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse(value) -> datetime | None:
    """把库里各种时间写法统一成 naive datetime。

    兼容：datetime / date / "2026-11-25" / "2026-11-25 15:00" /
    "2026-11-25T15:00:00" / 带时区后缀的 ISO 串（时区后缀直接忽略）。
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    text = str(value).strip().replace("T", " ")
    for size, fmt in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M"), (10, "%Y-%m-%d")):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


def _extra(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return {}


# ---------------- 可配置参数 ----------------

def _to_int(value, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def _to_hhmm(value, default: str) -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) == 2:
        try:
            hour, minute = int(parts[0]), int(parts[1])
            if 0 <= hour < 24 and 0 <= minute < 60:
                return f"{hour:02d}:{minute:02d}"
        except ValueError:
            pass
    return default


def _normalize_value(key: str, value):
    default = DEFAULT_SETTINGS[key]
    if key == "reminder_enabled":
        return 1 if _to_int(value, int(default)) else 0
    if key.startswith("reminder_silence"):
        return _to_hhmm(value, str(default))
    low, high = SETTINGS_RANGES[key]
    return _clamp(_to_int(value, int(default)), low, high)


def _normalize(values: dict) -> dict:
    return {key: _normalize_value(key, values.get(key, DEFAULT_SETTINGS[key]))
            for key in SETTINGS_KEYS}


_cache: dict = {"at": 0.0, "value": None}
_CACHE_TTL = 5.0


def get_settings(force: bool = False) -> dict:
    """当前提醒参数（带 5 秒缓存，set_settings 会立刻失效）。"""
    cached = _cache["value"]
    now = _time.time()
    if cached is not None and not force and now - _cache["at"] < _CACHE_TTL:
        return dict(cached)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'reminder_%'"
        ).fetchall()
        raw = {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()
    value = _normalize(raw)
    _cache["value"] = value
    _cache["at"] = now
    return dict(value)


def set_settings(patch: dict) -> dict:
    """改提醒参数；改了提前量/静默时段的，自动作废 pending 并按新参数重算。"""
    patch = patch or {}
    old = get_settings(force=True)
    changed: dict[str, object] = {}
    for key in SETTINGS_KEYS:
        if key not in patch:
            continue
        value = _normalize_value(key, patch[key])
        if value != old[key]:
            changed[key] = value
            db.set_setting(key, str(value))
    _cache["value"] = None
    _cache["at"] = 0.0
    if changed:
        log.info("提醒参数已更新：%s", changed)
    new = get_settings(force=True)
    if set(changed) & RECALC_KEYS:
        regenerate_all()
    return new


def _to_time(hhmm: str) -> time:
    hour, minute = str(hhmm).split(":")
    return time(int(hour), int(minute))


def _silence_window(settings: dict) -> tuple[time, time]:
    return (
        _to_time(settings["reminder_silence_start"]),
        _to_time(settings["reminder_silence_end"]),
    )


def _quiet(dt: datetime, settings: dict | None = None) -> datetime:
    """静默时段里的时间顺延到静默结束时刻。"""
    settings = settings or get_settings()
    start, end = _silence_window(settings)
    if start == end:                          # 首尾相同视为不静默
        return dt
    moment = dt.time()
    if start < end:                           # 同一天内的窗口
        if start <= moment < end:
            return datetime.combine(dt.date(), end)
        return dt
    if moment >= start:                       # 跨天窗口：23:00 之后 → 次日
        return datetime.combine(dt.date() + timedelta(days=1), end)
    if moment < end:                          # 07:30 之前 → 当天
        return datetime.combine(dt.date(), end)
    return dt


def _in_quiet(dt: datetime, settings: dict | None = None) -> bool:
    settings = settings or get_settings()
    start, end = _silence_window(settings)
    if start == end:
        return False
    moment = dt.time()
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


# ---------------- 写入提醒 ----------------

def _already_planned(conn, target_type: str, target_id, fire_at: str) -> bool:
    """同一 target 同一时刻是否已经生成过提醒（任何状态都算）。

    只查 pending 不够：ack 之后状态变成 sent，启动补扫会再生成一条一模一样的，
    重启几次就重复推几次。所以这里按「生成过」判断。
    """
    if target_id is None:
        return False
    row = conn.execute(
        """SELECT 1 FROM reminder
           WHERE target_type=? AND target_id=? AND fire_at=? LIMIT 1""",
        (target_type, target_id, fire_at),
    ).fetchone()
    return row is not None


def _insert(
    conn,
    title: str,
    body: str,
    fire_at: datetime,
    target_type: str,
    target_id=None,
    settings: dict | None = None,
) -> int:
    """插一条 pending 提醒；过期太久 / 同一 target 同一 fire_at 已存在就跳过。"""
    if fire_at < _now() - timedelta(hours=STALE_HOURS):
        return 0                                  # 提醒点过期太久，不补
    stamp = _fmt(_quiet(fire_at, settings))
    if _already_planned(conn, target_type, target_id, stamp):
        return 0
    conn.execute(
        """INSERT INTO reminder
           (title, body, fire_at, target_type, target_id, state, created_at)
           VALUES (?,?,?,?,?,'pending',?)""",
        (title.strip() or "提醒", (body or "").strip(), stamp,
         target_type, target_id, _fmt(_now())),
    )
    return 1


# ---------------- 规则：日程 ----------------

def _event_rules(event: dict, settings: dict) -> list[tuple[datetime, str]]:
    """日程该在什么时候、推什么。返回 [(fire_at, body)]。"""
    day = _parse(event.get("event_date"))
    if not day:
        return []
    start = (event.get("start_time") or "").strip()
    location = (event.get("location") or "").strip()
    rules: list[tuple[datetime, str]] = []
    if start:
        begins = _parse(f'{day:%Y-%m-%d} {start}')
        if not begins:
            return []
        body = f"{begins:%m-%d %H:%M} 开始"
        if location:
            body += f" · {location}"
        advance = int(settings.get("reminder_advance_event", 60))
        rules.append((begins - timedelta(minutes=advance), body))
    else:
        body = f"{day:%m-%d} 全天"
        if location:
            body += f" · {location}"
        rules.append((datetime.combine(day.date(), EVENT_ALL_DAY_AT), body))
    return rules


def _event_happens_at(event: dict) -> datetime | None:
    """日程"事情本身"发生的时刻：有开始时间就是开始时间，全天就是当天 08:30。"""
    day = _parse(event.get("event_date"))
    if not day:
        return None
    start = (event.get("start_time") or "").strip()
    if start:
        return _parse(f"{day:%Y-%m-%d} {start}")
    return datetime.combine(day.date(), EVENT_ALL_DAY_AT)


def generate_for_event(event: dict, settings: dict | None = None) -> int:
    """给一条 events 记录生成提醒（课表来源的跳过）。"""
    if not event or event.get("id") is None:
        return 0
    if (event.get("source") or "") == "jwc":
        return 0
    settings = settings or get_settings()
    happens = _event_happens_at(event)
    if happens is not None and happens < _now():
        return 0                                  # 已经开始/结束的日程不再提醒
    rules = _event_rules(event, settings)
    if not rules:
        return 0
    title = event.get("title") or "日程提醒"
    conn = db.connect()
    try:
        created = 0
        for fire_at, body in rules:
            created += _insert(conn, title, body, fire_at, "event", event["id"], settings)
        conn.commit()
        return created
    finally:
        conn.close()


# ---------------- 规则：作业 / 信息条目 ----------------

def _item_rules(item: dict, settings: dict) -> list[tuple[datetime, str]]:
    board = item.get("board") or ""
    if board not in ITEM_BOARDS:
        return []
    deadline = _parse(item.get("deadline"))
    if not deadline or deadline < _now():
        return []                                 # 已经截止的不再生成提醒
    name = ITEM_BOARD_NAMES.get(board, board)
    first = int(settings.get("reminder_advance_hw", 24))
    second = int(settings.get("reminder_advance_hw2", 2))
    offsets = [first]
    if second > 0 and second != first:
        offsets.append(second)
    rules = []
    for hours in offsets:
        label = f"提前 {hours} 小时" if hours else "截止时间"
        rules.append((deadline - timedelta(hours=hours),
                      f"{name} · 截止 {deadline:%m-%d %H:%M}（{label}）"))
    return rules


def generate_for_item(item: dict, settings: dict | None = None) -> int:
    """给一条 items 记录生成作业提醒（提前量读配置）。"""
    if not item or item.get("id") is None:
        return 0
    settings = settings or get_settings()
    rules = _item_rules(item, settings)
    if not rules:
        return 0
    title = item.get("title") or "作业提醒"
    conn = db.connect()
    try:
        created = 0
        for fire_at, body in rules:
            created += _insert(conn, title, body, fire_at, "item", item["id"], settings)
        conn.commit()
        return created
    finally:
        conn.close()


def generate_for_items(items: list[dict], settings: dict | None = None) -> int:
    """批量生成作业提醒（入库后 / 启动补扫用）。"""
    settings = settings or get_settings()
    return sum(generate_for_item(item, settings) for item in items or [])


# ---------------- 规则：邮件 ----------------

def _mail_fingerprint(message: dict) -> str:
    """邮件唯一标识：有 mid 用 mid，没有就拼 发件人|主题|时间。"""
    mid = str(message.get("mid") or message.get("uid") or "").strip()
    if mid:
        return mid
    sender = (message.get("from_addr") or message.get("from_name") or "").strip()
    return f'{sender}|{message.get("subject") or ""}|{message.get("date") or ""}'


def _fetch_mail(limit: int = 20) -> list[dict]:
    """读最近 N 封未读邮件，优先 jAccount，退回 IMAP 邮箱密码。"""
    from xinge import mail as mailer

    limit = max(1, int(limit))
    try:
        data = mailer.fetch_via_jaccount(limit=limit, unread_only=True)
        # Zimbra 不认 limit，会把全部未读一次性返回，这里自己截到最近 N 封
        return (data.get("messages") or [])[:limit]
    except Exception as exc:                     # jAccount 没登录 / 会话过期
        from . import config

        cfg = config.load()
        address = cfg.get("mail_address") or ""
        password = cfg.get("mail_password") or ""
        if not (address and password):
            raise RuntimeError(f"读不到邮件：{exc}") from exc
        data = mailer.fetch_messages(address, password, limit=limit)
        return (data.get("messages") or [])[:limit]


def _prune_mail_seen(conn, days: int = 30) -> None:
    """mail_seen 只留最近 N 天，别无限涨。"""
    conn.execute(
        "DELETE FROM mail_seen WHERE datetime(seen_at) < datetime('now','localtime',?)",
        (f"-{int(days)} days",),
    )


def mail_tick(limit: int = 20, max_notify: int | None = None) -> dict:
    """巡检一次新邮件，把没提醒过的生成提醒。

    Args:
        limit: 拉最近多少封（Zimbra 会返回全部未读，这里强截到 N）。
        max_notify: 这一轮最多单独提醒几封；默认跟 reminder_hourly_cap 走。

    Returns:
        {"ok": bool, "new": 新邮件数, "created": 生成提醒数, ...}
    """
    settings = get_settings()
    now = _now()
    if _in_quiet(now, settings):
        # 静默时段先攒着，等出了静默下一次巡检统一生成（每封只提醒一次）
        return {"ok": True, "quiet": True, "new": 0, "created": 0}

    if max_notify is None:
        cap = int(settings.get("reminder_hourly_cap", 3))
        max_notify = cap if cap > 0 else 3
    max_notify = max(1, int(max_notify))

    try:
        messages = _fetch_mail(limit)
    except Exception as exc:                    # 没登录邮箱是正常状态，不刷日志
        log.debug("邮件巡检跳过：%s", exc)
        return {"ok": False, "error": str(exc), "new": 0, "created": 0}

    conn = db.connect()
    try:
        fresh: list[tuple[str, dict]] = []
        for message in messages:
            if message.get("seen"):
                continue
            fingerprint = _mail_fingerprint(message)
            if not fingerprint:
                continue
            seen = conn.execute(
                "SELECT 1 FROM mail_seen WHERE mid=?", (fingerprint,)
            ).fetchone()
            if seen:
                continue
            fresh.append((fingerprint, message))

        if not fresh:
            _prune_mail_seen(conn)
            conn.commit()
            return {"ok": True, "new": 0, "created": 0}

        # 拉回来是最新在前，推送按时间顺序从早到晚更自然
        fresh.reverse()
        created = 0
        for _mid, message in fresh[:max_notify]:
            sender = (message.get("from_name") or message.get("from_addr") or "未知发件人").strip()
            subject = (message.get("subject") or "(无主题)").strip()
            created += _insert(conn, sender, subject, now, "mail", settings=settings)

        extra = fresh[max_notify:]
        if extra:
            brief = []
            for _mid, message in extra[:3]:
                sender = (message.get("from_name") or message.get("from_addr") or "未知发件人").strip()
                subject = (message.get("subject") or "(无主题)").strip()
                brief.append(f"{sender}：{subject}")
            if len(extra) > 3:
                brief.append("…")
            created += _insert(
                conn, f"还有 {len(extra)} 封新邮件", "\n".join(brief), now,
                "mail", settings=settings,
            )

        for fingerprint, _message in fresh:
            conn.execute(
                "INSERT OR IGNORE INTO mail_seen(mid, seen_at) VALUES (?,?)",
                (fingerprint, _fmt(now)),
            )
        _prune_mail_seen(conn)
        conn.commit()
        return {"ok": True, "new": len(fresh), "created": created}
    finally:
        conn.close()


# ---------------- 队列操作 ----------------

def due(limit: int = 10) -> list[dict]:
    """到点要推的提醒，按 fire_at 升序；受总开关和每小时上限约束。"""
    settings = get_settings()
    if not settings["reminder_enabled"]:
        return []                                 # 总开关关掉：一条都不给
    limit = max(1, min(int(limit or 10), 200))
    cap = int(settings["reminder_hourly_cap"])
    conn = db.connect()
    try:
        if cap > 0:
            this_hour = _now().strftime("%Y-%m-%d %H:") + "%"
            sent = conn.execute(
                "SELECT COUNT(*) FROM reminder WHERE state='sent' AND sent_at LIKE ?",
                (this_hour,),
            ).fetchone()[0]
            remaining = cap - sent
            if remaining <= 0:
                return []                         # 这个小时推够了
            limit = min(limit, remaining)
        rows = conn.execute(
            """SELECT id, title, body, fire_at FROM reminder
               WHERE state='pending'
                 AND datetime(fire_at) <= datetime('now','localtime')
               ORDER BY fire_at, id LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def ack(ids: list[int]) -> int:
    """标记已推送。"""
    ids = [int(i) for i in ids or []]
    if not ids:
        return 0
    conn = db.connect()
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"""UPDATE reminder SET state='sent', sent_at=?
                WHERE state='pending' AND id IN ({marks})""",
            (_fmt(_now()), *ids),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_skipped(ids: list[int]) -> int:
    """标记跳过（例如内容过时/用户不要）。"""
    ids = [int(i) for i in ids or []]
    if not ids:
        return 0
    conn = db.connect()
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"""UPDATE reminder SET state='skipped', sent_at=?
                WHERE state='pending' AND id IN ({marks})""",
            (_fmt(_now()), *ids),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_failed(reminder_id) -> int:
    """标记推送失败，retry 次数 +1。"""
    try:
        reminder_id = int(reminder_id)
    except (TypeError, ValueError):
        return 0
    conn = db.connect()
    try:
        cur = conn.execute(
            """UPDATE reminder SET state='failed', sent_at=?, retry=retry+1
               WHERE id=? AND state='pending'""",
            (_fmt(_now()), reminder_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def cancel_for(target_type: str, target_id) -> int:
    """删掉某条日程/条目关联的 pending 提醒（删除或改时间时用）。"""
    if target_id is None:
        return 0
    conn = db.connect()
    try:
        cur = conn.execute(
            """DELETE FROM reminder
               WHERE target_type=? AND target_id=? AND state='pending'""",
            (target_type, int(target_id)),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------- 推送对象（存 settings 表，App 可读写） ----------------

def get_target() -> dict:
    """当前推送对象（umo）。没绑过就是空串。"""
    umo = db.get_setting(TARGET_UMO, "").strip()
    return {
        "umo": umo,
        "bound_at": db.get_setting(TARGET_AT, "") if umo else "",
        "source": db.get_setting(TARGET_SOURCE, "") if umo else "",
    }


def target_bound() -> bool:
    return bool(get_target()["umo"])


def set_target(umo: str) -> dict:
    """App 里手动指定 / 解绑推送对象。手动设过就不再被机器人私聊自动覆盖。"""
    umo = (umo or "").strip()
    if umo:
        db.set_setting(TARGET_UMO, umo)
        db.set_setting(TARGET_AT, _fmt(_now()))
        db.set_setting(TARGET_SOURCE, "manual")
    else:
        db.set_setting(TARGET_UMO, "")
        db.set_setting(TARGET_AT, "")
        db.set_setting(TARGET_SOURCE, "")
    return get_target()


def bind_target(umo: str, sender_id: str = "", nickname: str = "") -> dict:
    """插件收到私聊时上报，自动记下推送对象。

    只有当前不是 manual 才接受覆盖，用户在 App 里手动设过就以 App 为准；
    联系人名单（bot_contacts）由 bot 路由另外维护。
    """
    umo = (umo or "").strip()
    current = get_target()
    result = {"ok": True, "accepted": False, "target": current}
    if not umo:
        result["reason"] = "umo 为空"
        return result
    if current.get("source") == "manual":
        result["reason"] = "已手动设置推送对象，不自动覆盖"
        return result
    db.set_setting(TARGET_UMO, umo)
    db.set_setting(TARGET_AT, _fmt(_now()))
    db.set_setting(TARGET_SOURCE, "auto")
    result["accepted"] = True
    result["target"] = get_target()
    return result


# ---------------- 全量扫描 / 补扫 / 参数变更重算 ----------------

def _purge_stale_pending() -> int:
    """清掉已经没意义的 pending：提醒点过期太久、目标已截止/已删除/已开始。

    启动和参数重算时跑一遍，避免队列里堆一堆推出去也只能 skip 的提醒。
    """
    removed = 0
    conn = db.connect()
    try:
        removed += conn.execute(
            """DELETE FROM reminder
               WHERE state='pending'
                 AND datetime(fire_at) < datetime('now','localtime',?)""",
            (f"-{STALE_HOURS} hours",),
        ).rowcount
        removed += conn.execute(
            """DELETE FROM reminder
               WHERE state='pending' AND target_type='item'
                 AND (target_id IS NULL OR target_id NOT IN (
                        SELECT id FROM items
                        WHERE deadline IS NOT NULL AND deadline <> ''
                          AND datetime(deadline) >= datetime('now','localtime')))"""
        ).rowcount
        removed += conn.execute(
            """DELETE FROM reminder
               WHERE state='pending' AND target_type='event'
                 AND (target_id IS NULL OR target_id NOT IN (SELECT id FROM events))"""
        ).rowcount
        conn.commit()
        # 已经开始/结束的日程提醒（时间要逐条算，只用 Python 判断）
        rows = conn.execute(
            """SELECT r.id, e.id AS eid, e.event_date, e.start_time
               FROM reminder r JOIN events e ON e.id = r.target_id
               WHERE r.state='pending' AND r.target_type='event'"""
        ).fetchall()
        dead = [
            row["id"] for row in rows
            if (_event_happens_at(dict(row)) or _now()) < _now()
        ]
        if dead:
            marks = ",".join("?" * len(dead))
            removed += conn.execute(
                f"DELETE FROM reminder WHERE id IN ({marks})", dead
            ).rowcount
            conn.commit()
    finally:
        conn.close()
    if removed:
        log.info("清掉 %s 条过期 pending 提醒", removed)
    return removed


def _scan_targets(days: int | None = None) -> tuple[list[dict], list[dict]]:
    """捞出要排提醒的日程和条目；days=None 表示不限天数（全量重算用）。"""
    now = _now()
    conn = db.connect()
    try:
        start = now.date().isoformat()
        if days:
            end = (now.date() + timedelta(days=max(1, int(days)))).isoformat()
            event_sql = """SELECT * FROM events
                           WHERE event_date BETWEEN ? AND ?
                             AND COALESCE(source,'') <> 'jwc'"""
            event_args: list = [start, end]
        else:
            event_sql = """SELECT * FROM events
                           WHERE event_date >= ?
                             AND COALESCE(source,'') <> 'jwc'"""
            event_args = [start]
        events = [dict(r) for r in conn.execute(event_sql, event_args)]

        marks = ",".join("?" * len(ITEM_BOARDS))
        item_sql = f"""SELECT * FROM items
                       WHERE board IN ({marks})
                         AND deadline IS NOT NULL AND deadline <> ''
                         AND archived_at IS NULL
                         AND datetime(deadline) >= datetime('now','localtime')"""
        item_args: list = list(ITEM_BOARDS)
        if days:
            horizon = (now + timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d %H:%M:%S")
            item_sql += " AND datetime(deadline) <= ?"
            item_args.append(horizon)
        items = [dict(r) for r in conn.execute(item_sql, item_args)]
    finally:
        conn.close()
    return events, items


def _generate_all(days: int | None, settings: dict) -> dict:
    now = _now()
    events, items = _scan_targets(days)
    stats = {"events": 0, "items": 0, "created": 0}
    for event in events:
        happens = _event_happens_at(event)
        if happens is None or happens < now:        # 已经开始了的不补
            continue
        stats["events"] += 1
        stats["created"] += generate_for_event(event, settings)
    for item in items:
        stats["items"] += 1
        stats["created"] += generate_for_item(item, settings)
    return stats


def mend(days: int = 7) -> dict:
    """启动时把未来 N 天内该提醒的补齐（幂等，重复跑不会重复推）。

    只补「事情本身还没发生」的：日程没开始、作业没过截止；
    提醒点已经过去超过 STALE_HOURS 小时的由 _insert 统一丢掉。
    """
    _purge_stale_pending()
    stats = _generate_all(days, get_settings())
    if stats["created"]:
        log.info("提醒补扫：日程 %s 条、条目 %s 条，新增提醒 %s 条",
                 stats["events"], stats["items"], stats["created"])
    return stats


def regenerate_all() -> dict:
    """参数变了：作废所有 event/item 的 pending，按新参数把未来事项重算一遍。

    邮件提醒不动（它们已经是「现在」该推的，重算没有意义）。
    """
    conn = db.connect()
    try:
        removed = conn.execute(
            "DELETE FROM reminder WHERE state='pending' AND target_type IN ('event','item')"
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    _purge_stale_pending()
    stats = _generate_all(None, get_settings(force=True))
    stats["removed"] = removed
    log.info("提醒参数变更重算：清掉 %s 条 pending，重新生成 %s 条",
             removed, stats["created"])
    return stats
