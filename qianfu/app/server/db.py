"""SQLite 存储层。

只用标准库 sqlite3，不做 ORM——单机场景够用，也方便将来换。

设计要点：
  * items 表用 (source, uid) 唯一约束做去重，重复抓取不会产生脏数据
  * boards 是"视图"概念，item 上冗余存一份 board，换板块不用改历史数据
  * sources_state 记录每个源的健康状况，供诊断接口使用
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_DIR = Path.home() / ".xinge"
DB_PATH = DATA_DIR / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    uid        TEXT NOT NULL,
    board      TEXT NOT NULL DEFAULT 'inbox',
    title      TEXT NOT NULL,
    summary    TEXT DEFAULT '',
    url        TEXT DEFAULT '',
    category   TEXT DEFAULT '',
    published  TEXT,
    deadline   TEXT,
    extra      TEXT DEFAULT '{}',
    read_at    TEXT,
    starred    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(source, uid)
);
CREATE INDEX IF NOT EXISTS idx_items_board ON items(board, published DESC);
CREATE INDEX IF NOT EXISTS idx_items_unread ON items(read_at);

CREATE TABLE IF NOT EXISTS boards (
    key   TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    sort  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sources_state (
    key        TEXT PRIMARY KEY,
    last_run   TEXT,
    last_ok    INTEGER,
    last_error TEXT DEFAULT '',
    last_count INTEGER DEFAULT 0,
    total_runs INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    event_date TEXT NOT NULL,
    start_time TEXT DEFAULT '',
    end_time   TEXT DEFAULT '',
    location   TEXT DEFAULT '',
    note       TEXT DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'manual',
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);

CREATE TABLE IF NOT EXISTS reminder (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    body        TEXT DEFAULT '',
    fire_at     TEXT NOT NULL,
    target_type TEXT DEFAULT 'custom',
    target_id   INTEGER,
    state       TEXT NOT NULL DEFAULT 'pending',
    sent_at     TEXT,
    retry       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminder_due ON reminder(state, fire_at);

CREATE TABLE IF NOT EXISTS mail_seen (
    mid     TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_contacts (
    openid    TEXT PRIMARY KEY,
    nickname  TEXT DEFAULT '',
    last_seen TEXT
);
"""

DEFAULT_BOARDS = [
    ("course", "Canvas", 10),
    ("event", "活动", 20),
    ("lab", "实验", 30),
    ("mooc", "慕课", 40),
    ("zhitu", "知途好课", 60),
    ("college", "学院公告", 70),
    ("shuiyuan", "水源社区", 75),
    ("custom", "自定义", 90),
]

# 信息源 -> 默认板块
SOURCE_BOARD = {
    "canvas": "course",
    "second_classroom": "event",
    "pe": "lab",
    "cnmooc": "mooc",
    "rss": "custom",          # 单条订阅可以在 extra.board 里覆盖
    "aihaoke": "zhitu",
    "college": "college",
    "shuiyuan": "shuiyuan",
}

# 只有这四个板块的条目会按 deadline 生成作业提醒（活动靠「加入日程」走 events）
_REMINDER_BOARDS = ("course", "mooc", "zhitu", "lab")


def _log() -> logging.Logger:
    return logging.getLogger("xinge.db")


def _after_event_created(event: dict) -> None:
    """新建日程后按规则生成提醒（课表来源在 reminders 里跳过）。"""
    if not event:
        return
    try:
        from . import reminders

        reminders.generate_for_event(event)
    except Exception as exc:                      # noqa: BLE001
        _log().warning("日程提醒生成失败：%s", exc)


def _after_event_updated(event: dict, fields: dict) -> None:
    """改了时间/标题/地点，把这条日程的旧提醒作废重排。"""
    if not event or not ({"event_date", "start_time", "title", "location"} & set(fields)):
        return
    try:
        from . import reminders

        reminders.cancel_for("event", event.get("id"))
        reminders.generate_for_event(event)
    except Exception as exc:                      # noqa: BLE001
        _log().warning("日程提醒更新失败：%s", exc)


def _after_event_deleted(event_id: int) -> None:
    try:
        from . import reminders

        reminders.cancel_for("event", event_id)
    except Exception as exc:                      # noqa: BLE001
        _log().warning("日程提醒清理失败：%s", exc)


def _after_items_upserted(rows: list) -> None:
    """条目入库后给作业板块生成提醒（幂等：同一 fire_at 不重复插）。"""
    if not rows:
        return
    try:
        from . import reminders

        reminders.generate_for_items(rows)
    except Exception as exc:                      # noqa: BLE001
        _log().warning("作业提醒生成失败：%s", exc)


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # 给 QQ 机器人用：记下已经推送过的条目，避免重复发
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
        if "pushed_at" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN pushed_at TEXT")
        # 过期归档：到期的先移进存档区，放一周再删
        if "archived_at" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN archived_at TEXT")

        for key, name, sort in DEFAULT_BOARDS:
            # 名称跟着代码走：改了 DEFAULT_BOARDS 下次启动就生效
            conn.execute(
                """INSERT INTO boards(key, name, sort) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE SET name=excluded.name, sort=excluded.sort""",
                (key, name, sort),
            )

        # 代码里删掉的板块，库里也清掉（比如放弃公众号之后的 wechat）
        keep = [b[0] for b in DEFAULT_BOARDS]
        placeholders = ",".join("?" * len(keep))
        conn.execute(f"DELETE FROM boards WHERE key NOT IN ({placeholders})", keep)
        conn.execute(
            f"DELETE FROM items WHERE board NOT IN ({placeholders})", keep
        )
        conn.commit()
    finally:
        conn.close()


def upsert_items(items: list) -> int:
    """写入条目，返回真正新增的条数。"""
    if not items:
        return 0
    conn = connect()
    now = datetime.now().isoformat(timespec="seconds")
    added = 0
    remind_rows: list[dict] = []
    try:
        for it in items:
            board = (it.extra or {}).get("board") or SOURCE_BOARD.get(it.source, "custom")
            published = it.published.isoformat(sep=" ", timespec="minutes") if it.published else None
            deadline = it.deadline.isoformat(sep=" ", timespec="minutes") if it.deadline else None
            extra = json.dumps(it.extra, ensure_ascii=False)

            cur = conn.execute(
                """INSERT OR IGNORE INTO items
                   (source, uid, board, title, summary, url, category,
                    published, deadline, extra, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (it.source, it.uid, board, it.title, it.summary, it.url,
                 it.category, published, deadline, extra, now),
            )

            if cur.rowcount:
                added += 1
            else:
                # 已存在：更新可变字段，但保留 read_at / starred / board
                conn.execute(
                    """UPDATE items SET
                         title=?, summary=?, url=?, category=?,
                         published=?, deadline=?, extra=?
                       WHERE source=? AND uid=?""",
                    (it.title, it.summary, it.url, it.category,
                     published, deadline, extra, it.source, it.uid),
                )

            # 作业提醒：只有这四个板块、且有截止时间的才排提醒
            if board in _REMINDER_BOARDS and deadline:
                row = conn.execute(
                    """SELECT id, board, title, deadline
                       FROM items WHERE source=? AND uid=?""",
                    (it.source, it.uid),
                ).fetchone()
                if row:
                    remind_rows.append(dict(row))
        conn.commit()
    finally:
        conn.close()
    _after_items_upserted(remind_rows)
    return added


def list_items(
    board: str | None,
    unread: bool,
    limit: int,
    category: str | None = None,
    archived: bool = False,
) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT * FROM items"
        where, args = [], []
        where.append("archived_at IS NOT NULL" if archived else "archived_at IS NULL")
        if board:
            where.append("board = ?")
            args.append(board)
        if unread:
            where.append("read_at IS NULL")
        if category:
            where.append("category = ?")
            args.append(category)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(published, created_at) DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()
    for r in rows:
        r["extra"] = json.loads(r["extra"] or "{}")
        r["read"] = bool(r["read_at"])
    return rows


def mark_read(item_id: int) -> bool:
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE items SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (datetime.now().isoformat(timespec="seconds"), item_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def bot_digest(since: str | None, limit: int, only_unpushed: bool) -> list[dict]:
    """给机器人用的增量列表。"""
    conn = connect()
    try:
        sql = "SELECT * FROM items WHERE 1=1"
        args: list = []
        if since:
            sql += " AND created_at > ?"
            args.append(since)
        if only_unpushed:
            sql += " AND pushed_at IS NULL"
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def mark_pushed(ids: list[int]) -> int:
    if not ids:
        return 0
    conn = connect()
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"UPDATE items SET pushed_at=? WHERE id IN ({marks})",
            [datetime.now().isoformat(timespec="seconds"), *ids],
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def event_exists(title: str, event_date: str, start_time: str | None) -> bool:
    """同一天同一时间同名的日程已经存在？"""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM events WHERE title=? AND event_date=? AND COALESCE(start_time,'')=?",
            (title, event_date, start_time or ""),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def count_by_category(board: str) -> dict:
    """按 category 统计条数和未读。"""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT category,
                      COUNT(*) AS c,
                      SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS u
               FROM items WHERE board=?
               GROUP BY category""",
            (board,),
        ).fetchall()
        return {r["category"]: {"count": r["c"], "unread": r["u"] or 0} for r in rows}
    finally:
        conn.close()


def delete_item(item_id: int) -> bool:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_all_read(board: str | None = None) -> int:
    """把某个板块（或全部）的未读标成已读。

    Args:
        board: 板块 key，None 表示全部。

    Returns:
        标了多少条。
    """
    conn = connect()
    try:
        if board:
            cur = conn.execute(
                """UPDATE items SET read_at = datetime('now','localtime')
                   WHERE read_at IS NULL AND archived_at IS NULL AND board = ?""",
                (board,),
            )
        else:
            cur = conn.execute(
                """UPDATE items SET read_at = datetime('now','localtime')
                   WHERE read_at IS NULL AND archived_at IS NULL"""
            )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_read_many(ids: list[int]) -> int:
    """批量标已读。"""
    if not ids:
        return 0
    conn = connect()
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"""UPDATE items SET read_at = datetime('now','localtime')
                WHERE read_at IS NULL AND id IN ({marks})""",
            ids,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def archive_items(ids: list[int]) -> int:
    """手动把几条移进存档区。"""
    if not ids:
        return 0
    conn = connect()
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(
            f"""UPDATE items SET archived_at = datetime('now','localtime')
                WHERE archived_at IS NULL AND id IN ({marks})""",
            ids,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def archive_due(grace_days: int = 0) -> int:
    """把过了 ddl 的条目移进存档区。

    Args:
        grace_days: 截止后宽限几天再归档，默认 0。

    Returns:
        这次归档了多少条。
    """
    conn = connect()
    try:
        cur = conn.execute(
            """UPDATE items
               SET archived_at = datetime('now','localtime')
               WHERE archived_at IS NULL
                 AND deadline IS NOT NULL AND deadline <> ''
                 AND datetime(deadline) < datetime('now','localtime',?)""",
            (f"-{int(grace_days)} days",),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def purge_archived(days: int = 7) -> int:
    """删掉在存档区待满 N 天的条目。

    Args:
        days: 保留天数，默认 7。

    Returns:
        删掉了多少条。
    """
    conn = connect()
    try:
        cur = conn.execute(
            """DELETE FROM items
               WHERE archived_at IS NOT NULL
                 AND datetime(archived_at) < datetime('now','localtime',?)""",
            (f"-{int(days)} days",),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def archive_stats() -> list[dict]:
    """存档区按板块统计。"""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT board, COUNT(*) AS c,
                      MIN(archived_at) AS oldest,
                      MIN(deadline) AS deadline
               FROM items WHERE archived_at IS NOT NULL
               GROUP BY board"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_expired(source: str, category: str) -> int:
    """删掉某类里已经过期的条目（例如往期作业）。"""
    conn = connect()
    try:
        cur = conn.execute(
            """DELETE FROM items
               WHERE source=? AND category=?
                 AND deadline IS NOT NULL AND deadline <> ''
                 AND date(deadline) < date('now','localtime')""",
            (source, category),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def delete_read_items() -> int:
    """清掉所有已读条目。"""
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM items WHERE read_at IS NOT NULL")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def boards() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM boards ORDER BY sort").fetchall()
        out = []
        for r in rows:
            n = conn.execute(
                "SELECT COUNT(*) c FROM items WHERE board=? AND read_at IS NULL",
                (r["key"],),
            ).fetchone()["c"]
            out.append({"key": r["key"], "name": r["name"], "unread": n})
        return out
    finally:
        conn.close()


def record_run(key: str, ok: bool, error: str, count: int) -> None:
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO sources_state(key, last_run, last_ok, last_error, last_count, total_runs)
               VALUES (?,?,?,?,?,1)
               ON CONFLICT(key) DO UPDATE SET
                 last_run=excluded.last_run,
                 last_ok=excluded.last_ok,
                 last_error=excluded.last_error,
                 last_count=excluded.last_count,
                 total_runs=sources_state.total_runs+1""",
            (key, datetime.now().isoformat(timespec="seconds"), int(ok), error, count),
        )
        conn.commit()
    finally:
        conn.close()


def source_states() -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM sources_state")]
    finally:
        conn.close()


def upsert_bot_contact(openid: str, nickname: str = "") -> dict:
    """记下和机器人私聊过的人（openid + 昵称），App 里下拉选推送对象。"""
    openid = (openid or "").strip()
    if not openid:
        return {}
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO bot_contacts(openid, nickname, last_seen)
               VALUES (?,?,?)
               ON CONFLICT(openid) DO UPDATE SET
                 nickname=excluded.nickname,
                 last_seen=excluded.last_seen""",
            (openid, (nickname or "").strip(), datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM bot_contacts WHERE openid=?", (openid,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def list_bot_contacts(limit: int = 50) -> list[dict]:
    """最近和机器人私聊过的联系人，最近说话的在前面。"""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT openid, nickname, last_seen FROM bot_contacts
               ORDER BY COALESCE(last_seen, '') DESC, openid DESC LIMIT ?""",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()

# ---------------- 日程 ----------------

def create_event(data: dict) -> dict:
    conn = connect()
    now = datetime.now().isoformat(timespec="seconds")
    try:
        cur = conn.execute(
            """INSERT INTO events(title, event_date, start_time, end_time,
                                  location, note, source, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                data["title"].strip(),
                data["event_date"],
                data.get("start_time", ""),
                data.get("end_time", ""),
                data.get("location", ""),
                data.get("note", ""),
                data.get("source", "manual"),
                now,
            ),
        )
        conn.commit()
        event = get_event(cur.lastrowid)
    finally:
        conn.close()
    _after_event_created(event)
    return event


def get_event(event_id: int) -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def list_events(start: str, end: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE event_date BETWEEN ? AND ? ORDER BY event_date, start_time",
            (start, end),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_event(event_id: int, data: dict) -> dict:
    fields = {k: v for k, v in data.items() if k in {
        "title", "event_date", "start_time", "end_time", "location", "note", "done",
    }}
    if not fields:
        return get_event(event_id)
    conn = connect()
    try:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE events SET {sets} WHERE id=?", (*fields.values(), event_id))
        conn.commit()
        event = get_event(event_id)
    finally:
        conn.close()
    _after_event_updated(event, fields)
    return event


def delete_event(event_id: int) -> bool:
    _after_event_deleted(event_id)
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# 只有这几类会铺到日历上。其它带 deadline 的条目（比如第二课堂报名截止）
# 只留在信息流里，避免日历被塞满。
CALENDAR_RULES: tuple[tuple[str, str], ...] = (
    ("canvas", "作业"),
    ("pe", "实验"),
    ("cnmooc", "作业/测验"),
    ("aihaoke", "作业"),
)


def _row_extra(row: dict) -> dict:
    """row 里的 extra 可能是 JSON 串也可能是 dict。"""
    extra = row.get("extra")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError:
            extra = {}
    return extra if isinstance(extra, dict) else {}


def is_scheduled(row: dict) -> bool:
    """这条信息该不该出现在日历 / 右栏待办里。

    日历和右栏共用这一个判断，避免两边规则跑偏。
    已提交的仍然留在日历上（由调用方标 done=true 划掉）；
    过期的保留到「过期后 1 天」，超过 1 天才从日历移除。
    """
    if (row.get("source"), row.get("category")) not in CALENDAR_RULES:
        return False

    deadline = row.get("deadline") or ""
    if deadline:
        cutoff = (date.today() - timedelta(days=1)).isoformat()
        if deadline[:10] < cutoff:
            return False                    # 过期超过 1 天才不再出现
    return True


def items_with_deadline(start: str, end: str) -> list[dict]:
    """把「必须进日历」的条目铺到日历上（只读，不写回 items）。

    每条带 done：已提交（extra.submitted）算办完，前端据此划线，但条目本身
    不消失；过期的只是还没办完，保留 1 天后由 is_scheduled 移出。
    """
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT id, source, board, title, deadline, url, category, extra
               FROM items
               WHERE deadline IS NOT NULL AND deadline <> ''
                 AND date(deadline) BETWEEN ? AND ?
               ORDER BY deadline""",
            (start, end),
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            extra = _row_extra(item)
            if not is_scheduled(item):
                continue
            item.pop("extra", None)
            item["done"] = bool(extra.get("submitted"))
            out.append(item)
        return out
    finally:
        conn.close()


def mark_expired_read(grace_days: int = 1) -> int:
    """过期/发布超过宽限天数的条目自动标已读（邮件不在 items 表，天然不受影响）。

    Args:
        grace_days: 过期/发布后宽限几天才标已读，默认 1 天。

    Returns:
        标了多少条。
    """
    conn = connect()
    offset = f"-{int(grace_days)} days"
    try:
        cur = conn.execute(
            """UPDATE items SET read_at = datetime('now','localtime')
               WHERE read_at IS NULL
                 AND (
                   (deadline IS NOT NULL AND deadline <> ''
                    AND datetime(deadline) < datetime('now','localtime',?))
                   OR
                   ((deadline IS NULL OR deadline = '')
                    AND published IS NOT NULL
                    AND datetime(published) < datetime('now','localtime',?))
                 )""",
            (offset, offset),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
