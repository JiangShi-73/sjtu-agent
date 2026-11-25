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
    image_url   TEXT DEFAULT '',
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

-- 第二课堂活动属性：列表接口不返回，只能逐条拉详情再摊平，所以单独缓存
CREATE TABLE IF NOT EXISTS activity_meta (
    item_id       INTEGER PRIMARY KEY,
    activity_id   INTEGER,
    labor_counted INTEGER NOT NULL DEFAULT 0,
    labor_hours   REAL NOT NULL DEFAULT 0,
    new_era       INTEGER NOT NULL DEFAULT 0,
    fetched_at    TEXT
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


def _after_digest_changed() -> None:
    """日程 / 作业 / 公告一有变动就补一下当天早报（幂等，排过就只更新 pending 内容）。"""
    try:
        from . import reminders

        reminders.ensure_daily_digest()
    except Exception as exc:                      # noqa: BLE001
        _log().warning("早报生成失败：%s", exc)


def _after_event_created(event: dict) -> None:
    """新建日程后按规则生成提醒（课表来源在 reminders 里跳过）。"""
    if not event:
        return
    try:
        from . import reminders

        reminders.generate_for_event(event)
    except Exception as exc:                      # noqa: BLE001
        _log().warning("日程提醒生成失败：%s", exc)
    _after_digest_changed()


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
    _after_digest_changed()


def _after_event_deleted(event_id: int) -> None:
    try:
        from . import reminders

        reminders.cancel_for("event", event_id)
    except Exception as exc:                      # noqa: BLE001
        _log().warning("日程提醒清理失败：%s", exc)
    _after_digest_changed()


def _after_items_upserted(rows: list, new_activities: list | None = None) -> None:
    """条目入库后生成提醒：作业按 deadline 排，新上线的活动合并成一条带图的。

    Args:
        rows: 需要按截止时间排提醒的作业条目。
        new_activities: 本轮新增的第二课堂活动（items 行）。
    """
    if rows:
        try:
            from . import reminders

            reminders.generate_for_items(rows)
        except Exception as exc:                  # noqa: BLE001
            _log().warning("作业提醒生成失败：%s", exc)
    if new_activities:
        try:
            from . import reminders

            reminders.generate_new_activity_reminder(new_activities)
        except Exception as exc:                  # noqa: BLE001
            _log().warning("新活动提醒生成失败：%s", exc)
    _after_digest_changed()


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
        # 新活动提醒要带图片：老库补一列图片地址
        reminder_cols = {r["name"] for r in conn.execute("PRAGMA table_info(reminder)")}
        if "image_url" not in reminder_cols:
            conn.execute("ALTER TABLE reminder ADD COLUMN image_url TEXT DEFAULT ''")

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
        _after_digest_changed()                   # 抓取为空也算一次心跳：跨天后把早报排上
        return 0
    conn = connect()
    now = datetime.now().isoformat(timespec="seconds")
    added = 0
    remind_rows: list[dict] = []
    new_activities: list[dict] = []
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
                if it.source == "second_classroom":
                    fresh = conn.execute(
                        """SELECT id, title, url, published, extra
                           FROM items WHERE source=? AND uid=?""",
                        (it.source, it.uid),
                    ).fetchone()
                    if fresh:
                        new_activities.append(dict(fresh))
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
    _after_items_upserted(remind_rows, new_activities)
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

    作业类看 deadline；课程日历事件没有 deadline，看 extra.cal_end/cal_start，
    结束（或开始）超过宽限天数就归档，免得历史课程事件一直堆在信息流里。

    Args:
        grace_days: 截止后宽限几天再归档，默认 0。

    Returns:
        这次归档了多少条。
    """
    conn = connect()
    moved = 0
    try:
        cur = conn.execute(
            """UPDATE items
               SET archived_at = datetime('now','localtime')
               WHERE archived_at IS NULL
                 AND deadline IS NOT NULL AND deadline <> ''
                 AND datetime(deadline) < datetime('now','localtime',?)""",
            (f"-{int(grace_days)} days",),
        )
        moved += cur.rowcount

        # 课程日历事件：按结束时间归档（没结束时退回开始时间）
        cutoff = (datetime.now() - timedelta(days=int(grace_days))).strftime(
            "%Y-%m-%d %H:%M"
        )
        rows = conn.execute(
            """SELECT id, extra FROM items
               WHERE archived_at IS NULL
                 AND (deadline IS NULL OR deadline = '')
                 AND extra LIKE '%"cal_start"%'"""
        ).fetchall()
        expired = []
        for row in rows:
            extra = _row_extra(dict(row))
            when = extra.get("cal_end") or extra.get("cal_start") or ""
            if isinstance(when, str) and when and when[:16] < cutoff:
                expired.append(row["id"])
        if expired:
            marks = ",".join("?" * len(expired))
            cur = conn.execute(
                f"UPDATE items SET archived_at = datetime('now','localtime') "
                f"WHERE id IN ({marks})",
                expired,
            )
            moved += cur.rowcount

        conn.commit()
        return moved
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


# 联系人只保留最近 30 天：私聊机器人的人不该被永久记下 openid / 昵称。
BOT_CONTACT_TTL_DAYS = 30


def _prune_bot_contacts(conn) -> None:
    """清掉超过 TTL 的联系人（惰性清理，写入/读取时顺手跑）。"""
    conn.execute(
        "DELETE FROM bot_contacts WHERE last_seen IS NOT NULL "
        "AND datetime(last_seen) < datetime('now','localtime',?)",
        (f"-{BOT_CONTACT_TTL_DAYS} days",),
    )


def upsert_bot_contact(openid: str, nickname: str = "") -> dict:
    """记下和机器人私聊过的人（openid + 昵称），App 里下拉选推送对象。

    只应该在「用户主动把自己设成推送对象」那一次调用（见 bot 路由），
    平时别人的私聊不落库；这里另外负责顺手清理过期的记录。
    """
    openid = (openid or "").strip()
    if not openid:
        return {}
    conn = connect()
    try:
        _prune_bot_contacts(conn)
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
    """最近和机器人私聊过的联系人，最近说话的在前面（过期记录先清掉）。"""
    conn = connect()
    try:
        _prune_bot_contacts(conn)
        conn.commit()
        rows = conn.execute(
            """SELECT openid, nickname, last_seen FROM bot_contacts
               ORDER BY COALESCE(last_seen, '') DESC, openid DESC LIMIT ?""",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_bot_contact(openid: str) -> bool:
    """删掉某个人被记下的 openid / 昵称，返回是否真的删了一行。"""
    openid = (openid or "").strip()
    if not openid:
        return False
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM bot_contacts WHERE openid=?", (openid,))
        conn.commit()
        return cur.rowcount > 0
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
# 作业类按 deadline 的日期铺；课程事件没有 deadline，按 extra.cal_start 铺。
CALENDAR_RULES: tuple[tuple[str, str], ...] = (
    ("canvas", "作业"),
    ("canvas", "课程事件"),
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

    两类铺法：
      * 作业类：按 deadline 的日期铺，start 就是 deadline；
      * 课程事件类：没有 deadline，按 extra.cal_start 的日期铺，
        start/end/location/all_day 直接给前端显示上课时间。

    每条带 done：已提交（extra.submitted）算办完，前端据此划线，但条目本身
    不消失；过期的只是还没办完，保留 1 天后由 is_scheduled 移出。
    """
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT id, source, board, title, deadline, url, category, extra
               FROM items
               WHERE (deadline IS NOT NULL AND deadline <> ''
                      AND date(deadline) BETWEEN ? AND ?)
                  OR ((deadline IS NULL OR deadline = '')
                      AND extra LIKE '%"cal_start"%')
               ORDER BY COALESCE(deadline, '')""",
            (start, end),
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            extra = _row_extra(item)
            item.pop("extra", None)
            if not is_scheduled(item):
                continue

            deadline = (item.get("deadline") or "").strip()
            cal_start = extra.get("cal_start")
            if deadline:
                item["start"] = deadline
                item["end"] = ""
            elif isinstance(cal_start, str) and cal_start:
                day = cal_start[:10]
                if not (start <= day <= end):
                    continue                 # 不在这次要铺的日期范围里
                item["start"] = cal_start
                cal_end = extra.get("cal_end")
                item["end"] = cal_end if isinstance(cal_end, str) else ""
            else:
                continue                     # 没截止时间也没开始时间，日历上没处放

            location = extra.get("location")
            item["location"] = location if isinstance(location, str) else ""
            item["all_day"] = bool(extra.get("all_day"))
            item["done"] = bool(extra.get("submitted"))
            out.append(item)

        # 作业按 deadline、课程事件按开始时间，混排后统一按「什么时候」排
        out.sort(key=lambda x: x.get("start") or "")
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


# ---------------- 第二课堂活动属性 ----------------

def upsert_activity_meta(rows: list[dict]) -> int:
    """写入活动属性（同一 item 覆盖），返回写入条数。

    属性只能逐条拉详情，所以存下来复用；后台补全和接口筛选都读这张表。
    """
    rows = [r for r in (rows or []) if r.get("item_id") is not None]
    if not rows:
        return 0

    conn = connect()
    now = datetime.now().isoformat(timespec="seconds")
    try:
        for row in rows:
            conn.execute(
                """INSERT INTO activity_meta
                   (item_id, activity_id, labor_counted, labor_hours, new_era, fetched_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(item_id) DO UPDATE SET
                     activity_id=excluded.activity_id,
                     labor_counted=excluded.labor_counted,
                     labor_hours=excluded.labor_hours,
                     new_era=excluded.new_era,
                     fetched_at=excluded.fetched_at""",
                (
                    int(row["item_id"]),
                    row.get("activity_id"),
                    1 if row.get("labor_counted") else 0,
                    float(row.get("labor_hours") or 0),
                    1 if row.get("new_era") else 0,
                    row.get("fetched_at") or now,
                ),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()
