"""SQLite 存储层。

只用标准库 sqlite3，不做 ORM——单机场景够用，也方便将来换。

设计要点：
  * items 表用 (source, uid) 唯一约束做去重，重复抓取不会产生脏数据
  * boards 是"视图"概念，item 上冗余存一份 board，换板块不用改历史数据
  * sources_state 记录每个源的健康状况，供诊断接口使用
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime
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
"""

DEFAULT_BOARDS = [
    ("course", "Canvas", 10),
    ("event", "活动", 20),
    ("lab", "实验", 30),
    ("mooc", "慕课", 40),
    ("zhitu", "知途好课", 60),
    ("college", "学院公告", 70),
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
}


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
        conn.commit()
    finally:
        conn.close()
    return added


def list_items(
    board: str | None, unread: bool, limit: int, category: str | None = None
) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT * FROM items"
        where, args = [], []
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
        return get_event(cur.lastrowid)
    finally:
        conn.close()


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
        return get_event(event_id)
    finally:
        conn.close()


def delete_event(event_id: int) -> bool:
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


def is_scheduled(row: dict) -> bool:
    """这条信息该不该出现在日历 / 右栏待办里。

    日历和右栏共用这一个判断，避免两边规则跑偏。
    """
    if (row.get("source"), row.get("category")) not in CALENDAR_RULES:
        return False

    extra = row.get("extra")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError:
            extra = {}
    if (extra or {}).get("submitted"):
        return False                        # 已提交的，不再出现

    deadline = row.get("deadline") or ""
    if deadline and deadline[:10] < date.today().isoformat():
        return False                        # 过期的，也不再出现
    return True


def items_with_deadline(start: str, end: str) -> list[dict]:
    """把「必须进日历」的条目铺到日历上（只读，不写回 items）。"""
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
            if is_scheduled(item):
                item.pop("extra", None)
                out.append(item)
        return out
    finally:
        conn.close()
