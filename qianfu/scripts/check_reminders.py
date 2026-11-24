#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""信鸽提醒功能端到端验收脚本（只写测试数据，不改产品代码）。

用法（在 qianfu/ 下）::

    python scripts/check_reminders.py

验收项（对应任务清单）::

    1. 作业提醒规则   course 板块 deadline=明天12:00 -> 24h/2h 两条；college 不生成
    2. 日程提醒规则   有 start_time -> 提前 60 分钟；无 start_time -> 当天 08:30
    3. 静默时段       fire_at 落在 01:00-07:30 -> 顺延到 07:30
    4. 去重 / 幂等    同一 target 重复生成不重复插入；重启补扫（mend）不重复
    5. due / ack      fire_at 改到过去能被 due 捞到；ack 后 state 正确
    6. 修改日程时间   旧 pending 提醒作废、按新时间重排
    7. 删除日程       关联的 pending 提醒被清掉
    8. 推送对象       /target 手动绑定；/bind 不覆盖 manual；空串解绑

环境::

    XINGE_API   后端地址，默认 http://127.0.0.1:8722/api/v1
    XINGE_DB    SQLite 路径，默认 ~/.xinge/app.db

数据清理：测试数据统一带 source='check_reminders' 和本次运行的 TAG，
每个用例内 try/finally 清理，结尾再兜底扫一遍并打印剩余条数。
只会删自己造的数据，绝不删用户真实条目/日程。

说明：第 4 项的“重启补扫”用 reminders.mend(days=7) 模拟（与
app/server/main.py lifespan 启动时调用的函数完全一致，且该函数幂等），
因此不需要真的重启后端、不会影响用户。若仓库源码正在编辑导致无法导入，
本项会明确 FAIL 提示重试。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from types import SimpleNamespace

# Windows 控制台不是 UTF-8 时也别崩
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# ---------------------------------------------------------------- 常量

ROOT = Path(__file__).resolve().parents[1]          # qianfu/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API_BASE = (os.environ.get("XINGE_API") or "http://127.0.0.1:8722/api/v1").rstrip("/")
DB_PATH = Path(os.environ.get("XINGE_DB") or (Path.home() / ".xinge" / "app.db"))

STAMP = datetime.now()
TAG = "check-reminders-%s-%s" % (STAMP.strftime("%m%d-%H%M%S"), os.getpid())
SRC = "check_reminders"                              # 测试条目的 source，清理时按它兜底

# 需求文档里的默认提醒参数（验收按默认值核对）
DOC = {
    "hw_first": 24,          # 作业：截止前 24 小时
    "hw_second": 2,          # 作业：截止前 2 小时
    "event_advance": 60,     # 日程：开始前 60 分钟
    "all_day_at": (8, 30),   # 全天日程：当天 08:30
    "silence": ("01:00", "07:30"),
}

DEFAULT_SETTINGS = {
    "reminder_enabled": 1,
    "reminder_advance_event": 60,
    "reminder_advance_hw": 24,
    "reminder_advance_hw2": 2,
    "reminder_silence_start": "01:00",
    "reminder_silence_end": "07:30",
    "reminder_hourly_cap": 3,
}

# ---------------------------------------------------------------- 基础工具


def now() -> datetime:
    return datetime.now()


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_fire(text) -> datetime | None:
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(text), pattern)
        except ValueError:
            continue
    return None


def hhmm(text) -> dt_time:
    hour, minute = str(text).split(":")[:2]
    return dt_time(int(hour), int(minute))


def quiet_adjust(dt: datetime, silence: tuple[str, str]) -> datetime:
    """独立实现一遍静默顺延规则，用来算期望值（不调用被测代码）。"""
    start, end = hhmm(silence[0]), hhmm(silence[1])
    if start == end:
        return dt
    moment = dt.time()
    if start < end:                                  # 同一天内的窗口
        if start <= moment < end:
            return datetime.combine(dt.date(), end)
        return dt
    if moment >= start:                              # 跨天：静默开始之后 -> 次日
        return datetime.combine(dt.date() + timedelta(days=1), end)
    if moment < end:                                 # 跨天：07:30 之前 -> 当天
        return datetime.combine(dt.date(), end)
    return dt


def in_quiet(dt: datetime, silence: tuple[str, str]) -> bool:
    start, end = hhmm(silence[0]), hhmm(silence[1])
    if start == end:
        return False
    moment = dt.time()
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=20000")
    except sqlite3.Error:
        pass
    return conn


def query(sql: str, args=()) -> list[dict]:
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def execute(sql: str, args=()) -> int:
    conn = connect()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def table_counts() -> dict:
    out = {}
    for table in ("events", "items", "reminder"):
        try:
            out[table] = query("SELECT COUNT(*) AS c FROM %s" % table)[0]["c"]
        except sqlite3.Error:
            out[table] = -1
    return out


def reminder_counts_by_state() -> dict:
    rows = query("SELECT state, COUNT(*) AS c FROM reminder GROUP BY state")
    return {row["state"]: row["c"] for row in rows}


def api(method: str, path: str, body=None, params: dict | None = None):
    url = API_BASE + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("%s %s -> HTTP %s: %s" % (method, path, exc.code, detail))
    except urllib.error.URLError as exc:
        raise RuntimeError("%s %s -> 连不上后端 %s: %s" % (method, path, API_BASE, exc))


def sha256_of(path: Path) -> str:
    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "n/a"


def backend_runtime_info() -> dict:
    """Windows 下读监听端口的后端进程启动时间，用来判断它有没有加载最新代码。"""
    info = {"pid": None, "started": None, "error": None}
    if os.name != "nt":
        info["error"] = "非 Windows，跳过进程启动时间检查"
        return info
    port = urllib.parse.urlsplit(API_BASE).port or 8722
    script = (
        "$c = Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -First 1; if ($c) { $p = Get-Process -Id $c.OwningProcess; "
        "\"$($p.Id)|$($p.StartTime.ToString('yyyy-MM-dd HH:mm:ss'))\" }" % port
    )
    try:
        import subprocess

        proc = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                              capture_output=True, text=True, timeout=20)
        lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
        if lines:
            pid_text, started_text = lines[-1].split("|", 1)
            info["pid"] = int(pid_text)
            info["started"] = datetime.strptime(started_text.strip(), "%Y-%m-%d %H:%M:%S")
        else:
            info["error"] = "没有找到监听 %d 的进程" % port
    except Exception as exc:  # noqa: BLE001
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


# ---------------------------------------------------------------- 规则引擎（进程内）


class Engine:
    """复用后端源码里的 db / reminders 模块（只用来触发作业提醒生成和补扫）。"""

    def __init__(self):
        self.ok = False
        self.error = None
        self.db = None
        self.reminders = None
        try:
            from app.server import db as server_db          # noqa: WPS433
            from app.server import reminders as server_rem  # noqa: WPS433
            self.db = server_db
            self.reminders = server_rem
            self.ok = True
        except Exception as exc:                            # noqa: BLE001
            self.error = "%s: %s" % (type(exc).__name__, exc)

    def settings(self) -> dict:
        if self.ok:
            try:
                return dict(self.reminders.get_settings(force=True))
            except Exception:                               # noqa: BLE001
                pass
        return read_settings_from_db()

    def require(self) -> None:
        if not self.ok:
            raise RuntimeError(
                "无法导入 qianfu/app/server 规则引擎（%s）。"
                "如果源码正在编辑，等保存完再跑一次。" % self.error
            )


def read_settings_from_db() -> dict:
    """兜底：直接读 settings 表并按默认值归一化。"""
    settings = dict(DEFAULT_SETTINGS)
    try:
        rows = query("SELECT key, value FROM settings WHERE key LIKE 'reminder_%'")
    except sqlite3.Error:
        return settings
    raw = {row["key"]: str(row["value"]).strip() for row in rows}
    for key, default in DEFAULT_SETTINGS.items():
        if key not in raw:
            continue
        value = raw[key]
        if key == "reminder_enabled":
            settings[key] = 0 if value in ("", "0", "false", "False") else 1
        elif key.startswith("reminder_silence"):
            settings[key] = value if value.count(":") == 1 else default
        else:
            try:
                settings[key] = int(float(value))
            except ValueError:
                pass
    return settings


def fake_item(board: str, uid_suffix: str, deadline: datetime):
    """collector 入库时用的条目对象（db.upsert_items 的入参形状）。"""
    return SimpleNamespace(
        source=SRC,
        uid="%s-%s" % (TAG, uid_suffix),
        title="%s 作业 %s" % (TAG, uid_suffix),
        summary="验收脚本造的数据",
        url="",
        category="验收",
        published=datetime.now(),
        deadline=deadline,
        extra={"board": board},
    )


# ---------------------------------------------------------------- 用例记录


class Case:
    def __init__(self, name: str):
        self.name = name
        self.evidence: list[str] = []
        self.failures: list[str] = []

    def note(self, message: str) -> None:
        self.evidence.append(message)

    def expect(self, condition, message: str) -> bool:
        if not condition:
            self.failures.append(message)
            return False
        return True

    def require(self, condition, message: str) -> None:
        if not condition:
            raise AssertionError(message)


class Context:
    def __init__(self):
        self.created_item_ids: set[int] = set()
        self.created_event_ids: set[int] = set()
        self.openids: set[str] = set()
        self.target_backup: dict | None = None
        self.settings: dict = {}
        self.engine = Engine()
        self.baseline: dict = {}

    # --- 清单个用例的数据（只删自己造的行，带安全校验） ---

    def purge_item(self, item_id: int) -> None:
        try:
            rows = query("SELECT id, source FROM items WHERE id=?", (int(item_id),))
            if rows and rows[0]["source"] not in (SRC,):
                raise RuntimeError("拒绝删除非验收数据: items.id=%s" % item_id)
            execute("DELETE FROM reminder WHERE target_type='item' AND target_id=?", (int(item_id),))
            execute("DELETE FROM items WHERE id=?", (int(item_id),))
        finally:
            self.created_item_ids.discard(int(item_id))

    def purge_event(self, event_id: int) -> None:
        try:
            rows = query("SELECT id, source FROM events WHERE id=?", (int(event_id),))
            if rows and rows[0]["source"] not in (SRC,):
                raise RuntimeError("拒绝删除非验收数据: events.id=%s" % event_id)
            execute("DELETE FROM reminder WHERE target_type='event' AND target_id=?", (int(event_id),))
            execute("DELETE FROM events WHERE id=?", (int(event_id),))
        finally:
            self.created_event_ids.discard(int(event_id))

    def restore_target(self) -> None:
        """把推送对象设置恢复成脚本运行前的原值。"""
        if self.target_backup is None:
            return
        keys = ("reminder_umo", "reminder_umo_at", "reminder_umo_source")
        execute("DELETE FROM settings WHERE key IN (?,?,?)", keys)
        for key in keys:
            value = str(self.target_backup.get(key) or "")
            if value:
                execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (key, value))

    # --- 造数据 ---

    def upsert_item(self, board: str, uid_suffix: str, deadline: datetime) -> dict:
        self.engine.require()
        item = fake_item(board, uid_suffix, deadline)
        self.engine.db.upsert_items([item])
        rows = query("SELECT * FROM items WHERE source=? AND uid=?", (SRC, item.uid))
        if not rows:
            raise RuntimeError("测试条目没写进数据库: %s" % item.uid)
        self.created_item_ids.add(int(rows[0]["id"]))
        return rows[0]

    def create_event(self, **body) -> dict:
        body.setdefault("source", SRC)
        body.setdefault("title", "%s 日程" % TAG)
        response = api("POST", "/events", body)
        event = response.get("event") or {}
        if not event.get("id"):
            raise RuntimeError("POST /events 没有返回 event.id: %r" % response)
        self.created_event_ids.add(int(event["id"]))
        return event


def reminders_for(target_type: str, target_id) -> list[dict]:
    return query(
        """SELECT id, title, body, fire_at, state, retry, target_type, target_id
           FROM reminder WHERE target_type=? AND target_id=? ORDER BY id""",
        (target_type, int(target_id)),
    )


def reminder_row(reminder_id: int) -> dict | None:
    rows = query("SELECT * FROM reminder WHERE id=?", (int(reminder_id),))
    return rows[0] if rows else None


def sent_this_hour() -> int:
    """本小时已 sent 的提醒数（due 的每小时配额按它算）。"""
    return query(
        "SELECT COUNT(*) AS c FROM reminder WHERE state='sent' AND sent_at LIKE ?",
        (now().strftime("%Y-%m-%d %H:") + "%",),
    )[0]["c"]


def create_case_event(
    ctx: Context,
    when: datetime | None = None,
    event_date: str | None = None,
    start_time: str | None = None,
    name: str = "event",
    **extra,
) -> dict:
    body = {"title": "%s %s" % (TAG, name), **extra}
    if when is not None:
        body["event_date"] = when.strftime("%Y-%m-%d")
        body["start_time"] = when.strftime("%H:%M")
    if event_date is not None:
        body["event_date"] = event_date
    if start_time is not None:
        body["start_time"] = start_time
    return ctx.create_event(**body)


# ---------------------------------------------------------------- 8 个验收项


def case_1_item_rules(case: Case, ctx: Context) -> None:
    """作业提醒：course/mooc/zhitu/lab 24h+2h；其它板块不生成。"""
    tomorrow = (now() + timedelta(days=1)).date()
    deadline = datetime.combine(tomorrow, dt_time(12, 0))
    offsets = [DOC["hw_first"], DOC["hw_second"]]
    want = {fmt(quiet_adjust(deadline - timedelta(hours=h), DOC["silence"])) for h in offsets}

    row = ctx.upsert_item("course", "hw-course", deadline)
    try:
        rows = reminders_for("item", row["id"])
        got = [reminder["fire_at"] for reminder in rows]
        case.note(
            "course 条目 id=%s deadline=%s -> %d 条提醒 fire_at=%s"
            % (row["id"], deadline.strftime("%m-%d %H:%M"), len(rows), got)
        )
        case.expect(len(rows) == 2, "course 应生成 2 条提醒（24h/2h），实际 %d 条" % len(rows))
        case.expect(
            set(got) == want,
            "fire_at 期望 %s，实际 %s" % (sorted(want), sorted(got)),
        )
        case.expect(
            all(reminder["state"] == "pending" for reminder in rows),
            "新生成的提醒 state 都应是 pending，实际 %s" % [reminder["state"] for reminder in rows],
        )
    finally:
        ctx.purge_item(row["id"])

    college = ctx.upsert_item("college", "hw-college", deadline)
    try:
        rows = reminders_for("item", college["id"])
        case.note("college 条目 id=%s deadline=%s -> %d 条提醒（期望 0）" % (college["id"], deadline, len(rows)))
        case.expect(len(rows) == 0, "college 板块不应生成提醒，实际 %d 条: %s"
                    % (len(rows), [r["fire_at"] for r in rows]))
    finally:
        ctx.purge_item(college["id"])


def case_2_event_rules(case: Case, ctx: Context) -> None:
    """日程提醒：有 start_time 提前 60 分钟；无 start_time 当天 08:30。"""
    advance = timedelta(minutes=DOC["event_advance"])
    base = now().replace(second=0, microsecond=0)
    start = base + timedelta(hours=2)

    event = create_case_event(ctx, when=start, name="2小时后", location="验收")
    try:
        rows = reminders_for("event", event["id"])
        raw = start - advance
        want = quiet_adjust(raw, DOC["silence"])
        got = [reminder["fire_at"] for reminder in rows]
        shifted = "（静默时段顺延）" if want != raw else ""
        case.note(
            "有 start_time 的日程: start=%s 规则值 fire=%s%s 实际=%s"
            % (fmt(start), fmt(raw), shifted, got)
        )
        case.expect(len(rows) == 1, "有 start_time 的日程应生成 1 条提醒，实际 %d 条" % len(rows))
        case.expect(got == [fmt(want)], "fire_at 期望 %s，实际 %s" % (fmt(want), got))
    finally:
        ctx.purge_event(event["id"])

    # 锚点：明天 12:00 开始 -> 提前 60 分钟（不受静默影响，证明提前量本身）
    tomorrow = (now() + timedelta(days=1)).date()
    anchor_start = datetime.combine(tomorrow, dt_time(12, 0))
    anchor = create_case_event(ctx, when=anchor_start, name="锚点12点")
    try:
        rows = reminders_for("event", anchor["id"])
        want = anchor_start - advance
        case.note(
            "锚点: start=%s -> fire_at=%s（期望 start-60min = %s）"
            % (fmt(anchor_start), [r["fire_at"] for r in rows], fmt(want))
        )
        case.expect(len(rows) == 1, "锚点日程应生成 1 条提醒，实际 %d 条" % len(rows))
        case.expect([r["fire_at"] for r in rows] == [fmt(want)],
                    "锚点 fire_at 期望 %s，实际 %s" % (fmt(want), [r["fire_at"] for r in rows]))
    finally:
        ctx.purge_event(anchor["id"])

    # 全天日程：无 start_time -> 当天 08:30
    all_day = create_case_event(ctx, event_date=tomorrow.isoformat(), name="全天")
    try:
        rows = reminders_for("event", all_day["id"])
        want = datetime.combine(tomorrow, dt_time(*DOC["all_day_at"]))
        case.note(
            "无 start_time 的全天日程 %s -> fire_at=%s（期望当天 08:30 = %s）"
            % (tomorrow.isoformat(), [r["fire_at"] for r in rows], fmt(want))
        )
        case.expect(len(rows) == 1, "全天日程应生成 1 条提醒，实际 %d 条" % len(rows))
        case.expect([r["fire_at"] for r in rows] == [fmt(want)],
                    "全天 fire_at 期望 %s，实际 %s" % (fmt(want), [r["fire_at"] for r in rows]))
    finally:
        ctx.purge_event(all_day["id"])


def case_3_quiet(case: Case, ctx: Context) -> None:
    """静默时段：fire_at 落在 01:00-07:30 顺延到 07:30（用户已把静默开始改成 01:00）。"""
    tomorrow = (now() + timedelta(days=1)).date()
    advance = timedelta(minutes=DOC["event_advance"])

    sub_cases = [
        ("fire 03:00 -> 07:30", tomorrow, "04:00"),  # 原始 fire 次日 03:00（落在静默里）-> 次日 07:30
        ("fire 06:00 -> 07:30", tomorrow, "07:00"),  # 原始 fire 次日 06:00（<07:30）-> 次日 07:30
        ("边界 07:30 不顺延", tomorrow, "08:30"),     # 原始 fire 次日 07:30 -> 保持 07:30
    ]
    for label, day, start_time in sub_cases:
        start = datetime.combine(day, hhmm(start_time))
        event = create_case_event(ctx, when=start, name="静默-" + label)
        try:
            rows = reminders_for("event", event["id"])
            raw = start - advance
            want = quiet_adjust(raw, DOC["silence"])
            got = [reminder["fire_at"] for reminder in rows]
            case.note(
                "%s: start=%s 原始 fire=%s in_quiet=%s -> 实际=%s 期望=%s"
                % (label, start.strftime("%m-%d %H:%M"), fmt(raw), in_quiet(raw, DOC["silence"]), got, fmt(want))
            )
            case.expect(len(rows) == 1, "%s 应生成 1 条提醒，实际 %d 条" % (label, len(rows)))
            case.expect(got == [fmt(want)], "%s fire_at 期望 %s，实际 %s" % (label, fmt(want), got))
        finally:
            ctx.purge_event(event["id"])


def case_4_dedup(case: Case, ctx: Context) -> None:
    """去重 / 幂等：重复生成不重复插入；补扫（重启）不重复插入。"""
    tomorrow = (now() + timedelta(days=1)).date()
    deadline = datetime.combine(tomorrow, dt_time(12, 0))

    # --- 4a 同一 target 重复生成 ---
    row = ctx.upsert_item("course", "dedup", deadline)
    try:
        first = reminders_for("item", row["id"])
        case.expect(len(first) == 2, "首次生成应 2 条，实际 %d 条" % len(first))
        # 模拟同一份抓取结果重复入库（生产路径：db.upsert_items -> 提醒生成）
        ctx.engine.db.upsert_items([fake_item("course", "dedup", deadline)])
        second = reminders_for("item", row["id"])
        # 再直接调一次规则引擎
        ctx.engine.reminders.generate_for_item(row)
        third = reminders_for("item", row["id"])
        case.note(
            "同一 target 重复生成: 首次 %d 条 -> 重复 upsert 后 %d 条 -> 再调 generate %d 条; fire_at=%s"
            % (len(first), len(second), len(third), [r["fire_at"] for r in third])
        )
        case.expect(len(second) == 2, "重复 upsert 不应新增提醒，实际 %d 条" % len(second))
        case.expect(len(third) == 2, "重复调用 generate 不应新增提醒，实际 %d 条" % len(third))
        pairs = [(r["fire_at"], r["target_id"]) for r in third]
        case.expect(len(pairs) == len(set(pairs)), "出现重复 (fire_at, target) 行: %s" % pairs)
    finally:
        ctx.purge_item(row["id"])

    # --- 4b 重启补扫幂等（等价 main.py lifespan 调 reminders.mend(days=7)） ---
    row2 = ctx.upsert_item("course", "mend", deadline)
    try:
        before = table_counts()
        stats1 = ctx.engine.reminders.mend(days=7)
        mid = table_counts()
        rows_mid = reminders_for("item", row2["id"])
        stats2 = ctx.engine.reminders.mend(days=7)
        after = table_counts()
        rows_after = reminders_for("item", row2["id"])
        case.note(
            "补扫#1 created=%s reminder 总数 %s->%s；补扫#2 created=%s 总数 %s->%s"
            % (stats1.get("created"), before["reminder"], mid["reminder"],
               stats2.get("created"), mid["reminder"], after["reminder"])
        )
        case.note(
            "测试条目提醒数: 补扫后 %d 条 -> 再补扫后 %d 条，fire_at=%s"
            % (len(rows_mid), len(rows_after), [r["fire_at"] for r in rows_after])
        )
        case.expect(stats2.get("created") == 0,
                    "第二次补扫不应新增提醒，实际 created=%s" % stats2.get("created"))
        case.expect(after["reminder"] == mid["reminder"],
                    "第二次补扫后 reminder 总数变化 %s -> %s（不幂等）" % (mid["reminder"], after["reminder"]))
        case.expect(len(rows_after) == 2, "补扫后测试条目应仍是 2 条提醒，实际 %d 条" % len(rows_after))
    finally:
        ctx.purge_item(row2["id"])


def case_5_due_ack(case: Case, ctx: Context) -> None:
    """due / ack：时间穿越能被 due 返回；ack 后 state 正确。"""
    tomorrow = (now() + timedelta(days=1)).date()
    events = [create_case_event(
        ctx, when=datetime.combine(tomorrow, dt_time(hour, 0)),
        name="due-ack-%d" % (index + 1),
    ) for index, hour in enumerate((10, 11, 12))]
    # 夹具：本小时可能已被插件的真实推送占满配额，此时 due 按设计返回 0 条。
    # 既不打扰用户、又能测到 due：测试期间临时解绑推送对象（插件这一轮不再拉
    # due、不会推给真实用户）+ 临时把每小时上限抬到「已发 + 2」，测完立刻恢复。
    original_target: dict = {}
    original_cap = 0
    temp_cap = None
    try:
        ids = []
        for event in events:
            rows = reminders_for("event", event["id"])
            case.require(len(rows) == 1, "日程 %s 未生成唯一提醒（实际 %d 条）" % (event["id"], len(rows)))
            ids.append(int(rows[0]["id"]))

        original_target = api("GET", "/bot/reminders/target")
        original_cap = int((api("GET", "/bot/reminders/settings") or {}).get("reminder_hourly_cap") or 0)
        sent_count = sent_this_hour()
        if original_target.get("umo"):
            api("POST", "/bot/reminders/target", {"umo": ""})
        if original_cap > 0:
            # 用户队列里可能有比测试更早的 pending；把上限临时抬到最大值，
            # 保证 due 的 limit 不把测试提醒截掉（生效窗口 <1 秒，且推送对象已解绑）
            temp_cap = 100
            api("POST", "/bot/reminders/settings", {"reminder_hourly_cap": temp_cap})
        case.note(
            "本小时真实推送已 sent=%d（每小时上限 %s）；夹具：临时解绑推送对象=%s，临时上限=%s"
            % (sent_count, original_cap or "不限", bool(original_target.get("umo")), temp_cap)
        )

        # 只把第一条 fire_at 改到过去（时间穿越），其余两条留给 skip/failed
        past = fmt(now() - timedelta(hours=20))
        execute("UPDATE reminder SET fire_at=? WHERE id=?", (past, ids[0]))
        due = api("GET", "/bot/reminders/due", params={"limit": 200})
        due_ids = [int(item["id"]) for item in (due.get("reminders") or [])]
        case.note(
            "fire_at 改为过去 %s；due count=%s（含用户历史提醒），测试提醒 id=%s 命中=%s"
            % (past, due.get("count"), ids, sorted(set(ids) & set(due_ids)))
        )
        case.expect(ids[0] in due_ids,
                    "fire_at 改到过去后 due 没有返回它: count=%s due_ids=%s"
                    % (due.get("count"), due_ids[:20]))

        # ack 默认 -> sent
        marked = api("POST", "/bot/reminders/ack", {"ids": [ids[0]]})
        row = reminder_row(ids[0]) or {}
        case.note("ack 默认: marked=%s state=%s sent_at=%s" % (marked.get("marked"), row.get("state"), row.get("sent_at")))
        case.expect(marked.get("marked") == 1, "ack 应标记 1 条，实际 %s" % marked.get("marked"))
        case.expect(row.get("state") == "sent", "ack 后 state 期望 sent，实际 %s" % row.get("state"))
        due_after = [int(item["id"]) for item in (api("GET", "/bot/reminders/due", params={"limit": 200}).get("reminders") or [])]
        case.expect(ids[0] not in due_after, "ack 后 due 不应再返回 id=%s" % ids[0])

        # ack mode=skip -> skipped
        marked = api("POST", "/bot/reminders/ack", {"ids": [ids[1]], "mode": "skip"})
        row = reminder_row(ids[1]) or {}
        case.note("ack mode=skip: marked=%s state=%s" % (marked.get("marked"), row.get("state")))
        case.expect(marked.get("marked") == 1, "mode=skip 应标记 1 条，实际 %s" % marked.get("marked"))
        case.expect(row.get("state") == "skipped", "mode=skip 后 state 期望 skipped，实际 %s" % row.get("state"))

        # ack mode=failed -> failed + retry+1
        marked = api("POST", "/bot/reminders/ack", {"ids": [ids[2]], "mode": "failed"})
        row = reminder_row(ids[2]) or {}
        case.note("ack mode=failed: marked=%s state=%s retry=%s" % (marked.get("marked"), row.get("state"), row.get("retry")))
        case.expect(marked.get("marked") == 1, "mode=failed 应标记 1 条，实际 %s" % marked.get("marked"))
        case.expect(row.get("state") == "failed", "mode=failed 后 state 期望 failed，实际 %s" % row.get("state"))
        case.expect(row.get("retry") == 1, "mode=failed 后 retry 期望 1，实际 %s" % row.get("retry"))
    finally:
        for event in events:
            ctx.purge_event(event["id"])        # 先删测试提醒，释放 sent 配额
        if original_cap > 0 and temp_cap is not None:
            try:
                api("POST", "/bot/reminders/settings", {"reminder_hourly_cap": original_cap})
            except Exception as exc:  # noqa: BLE001
                case.note("恢复每小时上限失败: %s" % exc)
        if original_target.get("umo"):
            try:
                ctx.restore_target()
            except Exception as exc:  # noqa: BLE001
                case.note("恢复推送对象失败: %s" % exc)


def case_6_update_event(case: Case, ctx: Context) -> None:
    """修改日程时间：旧 pending 提醒作废、按新时间重排。"""
    tomorrow = (now() + timedelta(days=1)).date()
    old_start = datetime.combine(tomorrow, dt_time(15, 0))
    new_start = datetime.combine(tomorrow, dt_time(16, 30))
    advance = timedelta(minutes=DOC["event_advance"])

    event = create_case_event(ctx, when=old_start, name="改时间")
    try:
        before = reminders_for("event", event["id"])
        case.expect(len(before) == 1, "改时间前应 1 条提醒，实际 %d 条" % len(before))
        old_id = before[0]["id"] if before else None
        old_fire = fmt(old_start - advance)

        api("PATCH", "/events/%s" % event["id"], {"start_time": new_start.strftime("%H:%M")})
        after = reminders_for("event", event["id"])
        pending = [row for row in after if row["state"] == "pending"]
        new_fire = fmt(new_start - advance)
        case.note(
            "PATCH start_time %s -> %s；提醒行 %d->%d 条（pending %d 条），旧 fire_at=%s，新 fire_at=%s，旧行 id=%s 是否还在=%s"
            % (old_start.strftime("%H:%M"), new_start.strftime("%H:%M"),
               len(before), len(after), len(pending), old_fire,
               [row["fire_at"] for row in after], old_id,
               old_id in [row["id"] for row in after])
        )
        case.expect(len(pending) == 1, "改时间后应只有 1 条 pending 提醒，实际 %d 条" % len(pending))
        case.expect(
            [row["fire_at"] for row in pending] == [new_fire],
            "改时间后 fire_at 期望 %s，实际 %s" % (new_fire, [row["fire_at"] for row in pending]),
        )
        case.expect(all(row["fire_at"] != old_fire for row in after),
                    "旧 fire_at %s 的提醒没有被作废" % old_fire)
        old_row = reminder_row(old_id) if old_id else None
        case.expect(old_row is None or old_row.get("state") != "pending",
                    "旧提醒 id=%s 仍是 pending（既没删除也没作废）: %r" % (old_id, old_row))
    finally:
        ctx.purge_event(event["id"])


def case_7_delete_event(case: Case, ctx: Context) -> None:
    """删除日程：关联的 pending 提醒被清掉。"""
    tomorrow = (now() + timedelta(days=1)).date()
    start = datetime.combine(tomorrow, dt_time(18, 0))
    event = create_case_event(ctx, when=start, name="待删除")
    try:
        before = reminders_for("event", event["id"])
        case.expect(len(before) == 1, "删除前应有 1 条提醒，实际 %d 条" % len(before))
        api("DELETE", "/events/%s" % event["id"])
        ctx.created_event_ids.discard(int(event["id"]))
        after = reminders_for("event", event["id"])
        event_left = query("SELECT COUNT(*) AS c FROM events WHERE id=?", (event["id"],))[0]["c"]
        case.note(
            "删除日程 id=%s（start=%s）后：event 行数=%d，关联 reminder 行数 %d->%d"
            % (event["id"], fmt(start), event_left, len(before), len(after))
        )
        case.expect(event_left == 0, "日程没有被删除，events 仍有 %d 行" % event_left)
        case.expect(len(after) == 0, "删除日程后关联提醒应清空，实际还剩 %d 条" % len(after))
    finally:
        ctx.purge_event(event["id"])


def case_8_target(case: Case, ctx: Context) -> None:
    """推送对象：/target 手动设置、/bind 不覆盖 manual、空串解绑。"""
    original = api("GET", "/bot/reminders/target")
    ctx.target_backup = {
        "reminder_umo": original.get("umo") or "",
        "reminder_umo_at": original.get("bound_at") or "",
        "reminder_umo_source": original.get("source") or "",
    }
    umo_a = "%s:FriendMessage:aaaaaaaaaaaa" % TAG
    umo_b = "%s:FriendMessage:bbbbbbbbbbbb" % TAG
    openid = "check-reminders-openid-%s" % STAMP.strftime("%m%d%H%M%S")
    ctx.openids.add(openid)
    try:
        # 1) /target 设一个 umo -> GET 能读回且 source=manual
        response = api("POST", "/bot/reminders/target", {"umo": umo_a})
        target = response.get("target") or {}
        read_back = api("GET", "/bot/reminders/target")
        case.note(
            "POST /target umo=%s -> 返回 umo=%r source=%r bound_at=%r"
            % (umo_a, target.get("umo"), target.get("source"), target.get("bound_at"))
        )
        case.expect(target.get("umo") == umo_a, "POST /target 返回的 umo 不对: %r" % target.get("umo"))
        case.expect(target.get("source") == "manual", "POST /target 后 source 期望 manual，实际 %r" % target.get("source"))
        case.expect(read_back.get("umo") == umo_a, "GET /target 没有读回刚设的 umo: %r" % read_back.get("umo"))
        case.expect(read_back.get("source") == "manual", "GET /target source 期望 manual，实际 %r" % read_back.get("source"))
        case.expect(bool(read_back.get("bound_at")), "GET /target bound_at 应为空而不是 %r" % read_back.get("bound_at"))

        # 2) /bind 一个不同 umo -> 不应覆盖 manual
        bound = api("POST", "/bot/reminders/bind",
                    {"umo": umo_b, "sender_id": openid, "nickname": "验收"})
        after_bind = api("GET", "/bot/reminders/target")
        case.note(
            "POST /bind umo=%s（不同）-> accepted=%r reason=%r；GET 仍是 umo=%r source=%r"
            % (umo_b, bound.get("accepted"), bound.get("reason"), after_bind.get("umo"), after_bind.get("source"))
        )
        case.expect(bound.get("accepted") is False, "/bind 不应接受覆盖 manual，实际 accepted=%r" % bound.get("accepted"))
        case.expect(after_bind.get("umo") == umo_a, "/bind 之后 umo 被覆盖了: %r" % after_bind.get("umo"))
        case.expect(after_bind.get("source") == "manual", "/bind 之后 source 不是 manual: %r" % after_bind.get("source"))

        # 3) /target 空串 -> 解绑
        response = api("POST", "/bot/reminders/target", {"umo": ""})
        target = response.get("target") or {}
        read_back = api("GET", "/bot/reminders/target")
        case.note(
            "POST /target 空串 -> 返回 umo=%r source=%r；GET 回读 umo=%r source=%r"
            % (target.get("umo"), target.get("source"), read_back.get("umo"), read_back.get("source"))
        )
        case.expect(not target.get("umo"), "空串解绑后 umo 期望空，实际 %r" % target.get("umo"))
        case.expect(not read_back.get("umo"), "空串解绑后 GET umo 期望空，实际 %r" % read_back.get("umo"))
        case.expect(not read_back.get("source"), "空串解绑后 source 期望空，实际 %r" % read_back.get("source"))
    finally:
        ctx.restore_target()
        try:
            execute("DELETE FROM bot_contacts WHERE openid=?", (openid,))
        except sqlite3.Error:
            pass


CASES = [
    ("1 作业提醒规则（course 2 条 / college 0 条）", case_1_item_rules),
    ("2 日程提醒规则（提前 60 分钟 / 全天 08:30）", case_2_event_rules),
    ("3 静默时段（01:00-07:30 顺延 07:30）", case_3_quiet),
    ("4 去重 / 幂等（重复生成、重启补扫）", case_4_dedup),
    ("5 due / ack 流程（时间穿越、skip/failed）", case_5_due_ack),
    ("6 修改日程时间（旧提醒作废、重排）", case_6_update_event),
    ("7 删除日程（关联 pending 提醒清掉）", case_7_delete_event),
    ("8 推送对象（manual 不被 bind 覆盖、空串解绑）", case_8_target),
]


# ---------------------------------------------------------------- 清理 & 报告


def sweep_test_data(ctx: Context) -> tuple[int, int, int]:
    """兜底清理：按 source 和本次记录过的 id 删测试数据。"""
    item_ids = {int(row["id"]) for row in query("SELECT id FROM items WHERE source=?", (SRC,))}
    item_ids |= set(ctx.created_item_ids)
    event_ids = {int(row["id"]) for row in query("SELECT id FROM events WHERE source=?", (SRC,))}
    event_ids |= set(ctx.created_event_ids)

    removed_reminders = 0
    for item_id in item_ids:
        removed_reminders += execute("DELETE FROM reminder WHERE target_type='item' AND target_id=?", (item_id,))
    for event_id in event_ids:
        removed_reminders += execute("DELETE FROM reminder WHERE target_type='event' AND target_id=?", (event_id,))
    removed_items = execute("DELETE FROM items WHERE source=?", (SRC,))
    removed_events = execute("DELETE FROM events WHERE source=?", (SRC,))
    for openid in ctx.openids:
        try:
            execute("DELETE FROM bot_contacts WHERE openid=?", (openid,))
        except sqlite3.Error:
            pass
    ctx.created_item_ids.clear()
    ctx.created_event_ids.clear()
    ctx.restore_target()
    return removed_items, removed_events, removed_reminders


def check_residue(ctx: Context, case: Case) -> None:
    """清理后校验：没有残留测试数据，推送对象恢复原样。"""
    leftover_items = query("SELECT COUNT(*) AS c FROM items WHERE source=?", (SRC,))[0]["c"]
    leftover_events = query("SELECT COUNT(*) AS c FROM events WHERE source=?", (SRC,))[0]["c"]
    case.expect(leftover_items == 0, "残留测试条目 %d 条（source=%s）" % (leftover_items, SRC))
    case.expect(leftover_events == 0, "残留测试日程 %d 条（source=%s）" % (leftover_events, SRC))

    target = api("GET", "/bot/reminders/target")
    backup = ctx.target_backup or {}
    case.expect((target.get("umo") or "") == (backup.get("reminder_umo") or ""),
                "推送对象 umo 未恢复: 当前 %r 期望 %r" % (target.get("umo"), backup.get("reminder_umo")))
    case.expect((target.get("source") or "") == (backup.get("reminder_umo_source") or ""),
                "推送对象 source 未恢复: 当前 %r 期望 %r" % (target.get("source"), backup.get("reminder_umo_source")))

    try:
        remote = api("GET", "/bot/reminders/settings") or {}
    except Exception as exc:  # noqa: BLE001
        remote = {}
        case.failures.append("读取提醒参数失败: %s" % exc)
    expected_settings = {key: ctx.settings.get(key) for key in DEFAULT_SETTINGS}
    actual_settings = {key: remote.get(key) for key in DEFAULT_SETTINGS}
    case.expect(actual_settings == expected_settings,
                "提醒参数未恢复: 当前 %s 期望 %s" % (actual_settings, expected_settings))
    case.note("提醒参数复原校验: %s" % actual_settings)

    final_counts = table_counts()
    baseline = ctx.baseline
    case.note(
        "清理后 events=%s items=%s reminder=%s（基线 events=%s items=%s reminder=%s）"
        % (final_counts["events"], final_counts["items"], final_counts["reminder"],
           baseline.get("events"), baseline.get("items"), baseline.get("reminder"))
    )
    case.note("清理后 reminder 状态分布: %s" % reminder_counts_by_state())
    if final_counts != baseline:
        case.note(
            "注意：总数与基线不同，可能是验收期间后端定时抓取/重启补扫造成的用户数据变化（非测试残留）"
        )


def main() -> int:
    print("=" * 78)
    print("信鸽提醒功能端到端验收")
    print("=" * 78)
    print("时间       : %s" % STAMP.strftime("%Y-%m-%d %H:%M:%S"))
    print("后端       : %s" % API_BASE)
    print("数据库     : %s" % DB_PATH)
    print("测试标记   : %s" % TAG)

    # 后端探活
    try:
        ping = api("GET", "/bot/ping")
        print("后端探活   : OK %s" % ping)
    except Exception as exc:  # noqa: BLE001
        print("后端探活   : 失败 %s" % exc)
        print("汇总: PASS 0 / FAIL 0（后端不可用，未执行任何用例）")
        return 2

    reminders_py = ROOT / "app" / "server" / "reminders.py"
    print("规则源码   : %s sha256=%s" % (reminders_py, sha256_of(reminders_py)))
    deployed = Path.home() / "AppData" / "Local" / "Programs" / "xinge-desktop" / "resources" / "xinge" / "app" / "server" / "reminders.py"
    if deployed.exists():
        print("部署副本   : sha256=%s %s" % (
            sha256_of(deployed),
            "（与源码一致）" if sha256_of(deployed) == sha256_of(reminders_py) else "（与源码不一致，后端可能跑的是旧代码）",
        ))

    runtime = backend_runtime_info()
    if runtime.get("started"):
        print("后端进程   : pid=%s 启动于 %s"
              % (runtime["pid"], runtime["started"].strftime("%Y-%m-%d %H:%M:%S")))
        if deployed.exists():
            deployed_mtime = datetime.fromtimestamp(deployed.stat().st_mtime)
            if runtime["started"] < deployed_mtime:
                print("警告       : 后端进程启动(%s)早于部署的 reminders.py 更新时间(%s)，"
                      "HTTP 侧可能仍是旧提醒代码，建议重启后端后重跑"
                      % (runtime["started"].strftime("%H:%M:%S"),
                         deployed_mtime.strftime("%H:%M:%S")))
            else:
                print("代码版本   : 后端进程启动晚于部署的 reminders.py，运行的是最新提醒代码")
    else:
        print("后端进程   : 读取失败（%s）" % runtime.get("error"))

    try:
        remote_settings = api("GET", "/bot/reminders/settings")
        print("参数接口   : GET /bot/reminders/settings OK -> %s"
              % json.dumps(remote_settings, ensure_ascii=False, sort_keys=True))
    except Exception as exc:  # noqa: BLE001
        print("参数接口   : GET /bot/reminders/settings 不可用（%s），后端可能还没重启到最新代码" % exc)

    ctx = Context()
    ctx.settings = ctx.engine.settings()
    print("提醒参数   : %s" % json.dumps(ctx.settings, ensure_ascii=False, sort_keys=True))
    if not ctx.engine.ok:
        print("规则引擎   : 导入失败 %s" % ctx.engine.error)
    try:
        cap = int(ctx.settings.get("reminder_hourly_cap") or 0)
        sent = sent_this_hour()
        print("due 配额   : 每小时上限=%s，本小时已 sent=%s" % (cap or "不限", sent))
        if cap > 0 and sent >= cap:
            print("环境说明   : 本小时真实推送已占满配额，第 5 项会临时解绑推送对象并把上限抬到"
                  "「已发+2」，测完立即恢复（避免插件把测试提醒推给用户）")
    except sqlite3.Error as exc:
        print("due 配额   : 读取失败 %s" % exc)

    # 前置：参数是否默认值（验收按文档默认值核对）
    expected_flags = [
        ("作业提前 24h", ctx.settings.get("reminder_advance_hw") == DOC["hw_first"]),
        ("作业提前 2h", ctx.settings.get("reminder_advance_hw2") == DOC["hw_second"]),
        ("日程提前 60min", ctx.settings.get("reminder_advance_event") == DOC["event_advance"]),
        ("静默 01:00-07:30",
         (ctx.settings.get("reminder_silence_start"), ctx.settings.get("reminder_silence_end")) == DOC["silence"]),
        ("提醒总开关开", int(ctx.settings.get("reminder_enabled") or 0) == 1),
    ]
    print("参数核对   : %s" % ", ".join("%s=%s" % (name, "OK" if ok else "偏差") for name, ok in expected_flags))
    if not all(flag for _name, flag in expected_flags):
        print("警告       : 提醒参数不是文档默认值，1-3 项可能按用户自定义值判定失败")

    target = api("GET", "/bot/reminders/target")
    print("推送对象   : umo=%r source=%r" % (target.get("umo"), target.get("source")))
    ctx.target_backup = {
        "reminder_umo": target.get("umo") or "",
        "reminder_umo_at": target.get("bound_at") or "",
        "reminder_umo_source": target.get("source") or "",
    }

    # 开始前兜底清理上次异常退出的残留
    cleaned = sweep_test_data(ctx)
    if any(cleaned):
        print("启动清理   : 上次残留 items=%s events=%s reminders=%s" % cleaned)
    ctx.baseline = table_counts()
    print("基线计数   : events=%s items=%s reminder=%s %s"
          % (ctx.baseline["events"], ctx.baseline["items"], ctx.baseline["reminder"],
             reminder_counts_by_state()))
    print("-" * 78)

    results: list[tuple[str, bool]] = []
    started = time.time()
    for name, func in CASES:
        case = Case(name)
        case_started = time.time()
        try:
            func(case, ctx)
        except AssertionError as exc:
            case.failures.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            case.failures.append("%s: %s" % (type(exc).__name__, exc))
            traceback.print_exc()
        finally:
            # 单个用例崩了也别把脏数据留到下一项
            try:
                sweep_test_data(ctx)
            except Exception as exc:  # noqa: BLE001
                case.failures.append("用例后清理失败: %s" % exc)
        passed = not case.failures
        results.append((name, passed))
        print("[%s] %s  (%.2fs)" % ("PASS" if passed else "FAIL", name, time.time() - case_started))
        for line in case.evidence:
            print("       %s" % line)
        for line in case.failures:
            print("       失败: %s" % line)

    # 清理校验单独一项
    cleanup_case = Case("清理校验（测试数据清零、推送对象复原）")
    try:
        sweep_test_data(ctx)
        check_residue(ctx, cleanup_case)
    except Exception as exc:  # noqa: BLE001
        cleanup_case.failures.append("%s: %s" % (type(exc).__name__, exc))
    passed = not cleanup_case.failures
    results.append(("清理校验", passed))
    print("[%s] 清理校验（测试数据清零、推送对象复原）" % ("PASS" if passed else "FAIL"))
    for line in cleanup_case.evidence:
        print("       %s" % line)
    for line in cleanup_case.failures:
        print("       失败: %s" % line)

    passed_count = sum(1 for _name, ok in results if ok)
    failed_count = len(results) - passed_count
    elapsed = time.time() - started
    print("-" * 78)
    if failed_count:
        print("失败项     : %s" % ", ".join(name for name, ok in results if not ok))
    print("汇总: PASS %d / FAIL %d（8 项验收 + 清理校验，用时 %.1fs）"
          % (passed_count, failed_count, elapsed))
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
