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
from datetime import datetime
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
"""

DEFAULT_BOARDS = [
    ("course", "课程", 10),
    ("event", "活动", 20),
    ("lab", "实验", 30),
    ("mooc", "慕课", 40),
    ("custom", "自定义", 90),
]

# 信息源 -> 默认板块
SOURCE_BOARD = {
    "canvas": "course",
    "second_classroom": "event",
    "pe": "lab",
    "cnmooc": "mooc",
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
        for key, name, sort in DEFAULT_BOARDS:
            conn.execute(
                "INSERT OR IGNORE INTO boards(key, name, sort) VALUES (?,?,?)",
                (key, name, sort),
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
            board = SOURCE_BOARD.get(it.source, "custom")
            cur = conn.execute(
                """INSERT OR IGNORE INTO items
                   (source, uid, board, title, summary, url, category,
                    published, deadline, extra, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    it.source,
                    it.uid,
                    board,
                    it.title,
                    it.summary,
                    it.url,
                    it.category,
                    it.published.isoformat(sep=" ", timespec="minutes") if it.published else None,
                    it.deadline.isoformat(sep=" ", timespec="minutes") if it.deadline else None,
                    json.dumps(it.extra, ensure_ascii=False),
                    now,
                ),
            )
            added += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return added


def list_items(board: str | None, unread: bool, limit: int) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT * FROM items"
        where, args = [], []
        if board:
            where.append("board = ?")
            args.append(board)
        if unread:
            where.append("read_at IS NULL")
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
