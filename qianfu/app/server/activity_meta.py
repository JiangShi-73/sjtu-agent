"""第二课堂活动属性：补全、筛选、新活动检测。

平台的活动列表接口只返回标题/时间/地点，**不含**「是否计入劳动学时」
「是否计入新时代社会认知实践讲座课」这些属性——它们只在单条详情接口的
custom_properties 里。所以这里把属性逐条拉下来存进 activity_meta 表，
之后筛选、画图、推送都读缓存，不重复打平台。

三块职责：

    backfill()            后台每 30 分钟补一批未来活动的属性（最多 30 条）
    future_activities()   给 /bot/activities 用的筛选（labor / newera / all）
    new_activities()      最近 N 小时新入库的活动（画图 + 推送用）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from . import db

log = logging.getLogger("xinge.activity_meta")

BACKFILL_LIMIT = 30        # 每轮最多补多少条，别把平台打爆
BACKFILL_DAYS = 60         # 只补未来多少天内的活动
NEW_ACTIVITY_HOURS = 24    # 多久内入库算「新活动」


# ---------------- 小工具 ----------------

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


def _dt(value) -> datetime | None:
    """把库里各种时间写法统一成 naive datetime。"""
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    for size, fmt in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M"), (10, "%Y-%m-%d")):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


def _activity_id(item: dict) -> int | None:
    """平台活动 id：优先 extra.detail_id，其次 uid（形如 portal-19830）。"""
    extra = _extra(item.get("extra"))
    try:
        if extra.get("detail_id") not in (None, ""):
            return int(extra["detail_id"])
    except (TypeError, ValueError):
        pass
    uid = str(item.get("uid") or "")
    if uid.startswith("portal-"):
        try:
            return int(uid[len("portal-"):])
        except ValueError:
            return None
    return None


def _start_of(item: dict) -> datetime | None:
    """活动什么时候开始：extra.start，退回 published。"""
    extra = _extra(item.get("extra"))
    return _dt(extra.get("start")) or _dt(item.get("published"))


def _signup_end_of(item: dict) -> datetime | None:
    return _dt(_extra(item.get("extra")).get("signup_end"))


def meta_flags(item: dict) -> tuple[bool, float, bool]:
    """从一行（可能带 activity_meta 的联表结果）里取 (劳动学时, 学时数, 新时代)。"""
    labor = bool(item.get("labor_counted"))
    try:
        hours = float(item.get("labor_hours") or 0)
    except (TypeError, ValueError):
        hours = 0.0
    return labor, hours, bool(item.get("new_era"))


def upcoming(items: list[dict]) -> list[dict]:
    """挑出还没开始的活动，按开始时间从近到远。"""
    now = datetime.now()
    out: list[dict] = []
    for item in items or []:
        start = _start_of(item)
        if start is None or start <= now:
            continue
        item = dict(item)
        item["start_dt"] = start
        out.append(item)
    out.sort(key=lambda x: x["start_dt"])
    return out


# ---------------- 属性补全 ----------------

def _pending_items(limit: int) -> list[dict]:
    """没抓过属性的未来活动（按开始时间从近到远），只挑带平台 id 的。"""
    now = datetime.now()
    horizon = now + timedelta(days=BACKFILL_DAYS)
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT i.id, i.uid, i.title, i.published, i.extra
               FROM items i LEFT JOIN activity_meta m ON m.item_id = i.id
               WHERE i.source='second_classroom' AND i.board='event'
                 AND i.archived_at IS NULL
                 AND m.item_id IS NULL
                 AND i.uid LIKE 'portal-%'
               ORDER BY COALESCE(i.published, '')"""
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for row in rows:
        item = dict(row)
        aid = _activity_id(item)
        start = _start_of(item)
        if aid is None or start is None or start <= now or start > horizon:
            continue
        item["activity_id"] = aid
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _fetch_and_store(items: list[dict], limit: int) -> int:
    """给一批条目拉属性并写库，返回写入条数。"""
    if not items:
        return 0
    from xinge.auth import SessionStore
    from xinge.sources import activity_portal

    props = activity_portal.fetch_properties(
        SessionStore(), [it["activity_id"] for it in items]
    )
    if not props:
        return 0

    rows = []
    for it in items:
        meta = props.get(it["activity_id"])
        if not meta:
            continue
        rows.append(
            {
                "item_id": it["id"],
                "activity_id": it["activity_id"],
                "labor_counted": bool(meta.get("labor_counted")),
                "labor_hours": float(meta.get("labor_hours") or 0),
                "new_era": bool(meta.get("new_era")),
            }
        )
    return db.upsert_activity_meta(rows)


def backfill(limit: int = BACKFILL_LIMIT) -> dict:
    """补一批未来活动的属性，写进 activity_meta。

    后台每 30 分钟跑一次，一轮最多 30 条；拿不到 token / 平台挂了就
    安静跳过，下一轮再试。

    Args:
        limit: 本轮最多补几条。

    Returns:
        {"ok": bool, "pending": 本轮候选数, "updated": 真正写入的条数, ...}
    """
    limit = max(1, min(int(limit or BACKFILL_LIMIT), 200))
    try:
        pending = _pending_items(limit)
    except Exception as exc:                      # noqa: BLE001
        log.warning("查活动属性待补列表失败：%s", exc)
        return {"ok": False, "pending": 0, "updated": 0, "error": str(exc)}
    if not pending:
        return {"ok": True, "pending": 0, "updated": 0}

    try:
        updated = _fetch_and_store(pending, limit)
    except Exception as exc:                      # noqa: BLE001
        log.warning("活动属性补全失败：%s", exc)
        return {"ok": False, "pending": len(pending), "updated": 0, "error": str(exc)}

    if not updated:
        return {"ok": False, "pending": len(pending), "updated": 0,
                "error": "一条属性都没拿到（登录可能失效）"}
    log.info("活动属性补全 %s 条（本轮候选 %s 条）", updated, len(pending))
    return {"ok": True, "pending": len(pending), "updated": updated}


def ensure_meta(item_ids, limit: int = 12) -> int:
    """给指定条目补属性（画图前调用；最多补 limit 条，失败不影响画图）。"""
    ids: list[int] = []
    for value in item_ids or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return 0

    conn = db.connect()
    try:
        marks = ",".join("?" * len(ids))
        have = {
            r["item_id"]
            for r in conn.execute(
                f"SELECT item_id FROM activity_meta WHERE item_id IN ({marks})", ids
            )
        }
        rows = conn.execute(
            f"""SELECT id, uid, title, published, extra FROM items
                WHERE id IN ({marks}) AND source='second_classroom'""",
            ids,
        ).fetchall()
    finally:
        conn.close()

    missing: list[dict] = []
    for row in rows:
        if row["id"] in have:
            continue
        item = dict(row)
        aid = _activity_id(item)
        if aid is None:
            continue
        item["activity_id"] = aid
        missing.append(item)
        if len(missing) >= max(1, int(limit)):
            break
    if not missing:
        return 0
    try:
        return _fetch_and_store(missing, limit)
    except Exception as exc:                      # noqa: BLE001
        log.warning("画图前补活动属性失败：%s", exc)
        return 0


# ---------------- 查询 ----------------

def meta_map(item_ids) -> dict[int, dict]:
    """item_id -> activity_meta 行（接口里给单条活动补属性用）。"""
    ids: list[int] = []
    for value in item_ids or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}
    conn = db.connect()
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM activity_meta WHERE item_id IN ({marks})", ids
        ).fetchall()
        return {row["item_id"]: dict(row) for row in rows}
    finally:
        conn.close()


def future_activities(days: int = 14, kind: str = "all", limit: int = 20) -> list[dict]:
    """未来 N 天、还没过期也没截止的活动，带属性。

    Args:
        days: 往后看几天（1-90）。
        kind: labor 只看有劳动学时的 / newera 只看新时代讲座 / all 不过滤。
        limit: 最多返回几条。

    Returns:
        给接口直接用的字典列表（时间都是 "YYYY-MM-DD HH:MM"）。
    """
    now = datetime.now()
    end = now + timedelta(days=max(1, min(int(days or 14), 90)))
    limit = max(1, min(int(limit or 20), 200))
    kind = kind if kind in ("labor", "newera") else "all"

    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT i.id, i.title, i.url, i.category, i.published, i.extra,
                      m.labor_counted, m.labor_hours, m.new_era
               FROM items i LEFT JOIN activity_meta m ON m.item_id = i.id
               WHERE i.source='second_classroom' AND i.board='event'
                 AND i.archived_at IS NULL
               ORDER BY COALESCE(i.published, '')"""
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for row in rows:
        item = dict(row)
        extra = _extra(item.get("extra"))
        start = _start_of(item)
        if start is None or start < now or start > end:
            continue
        signup_end = _signup_end_of(item)
        if signup_end is not None and signup_end < now:
            continue                                  # 报名已截止
        labor, hours, era = meta_flags(item)
        if kind == "labor" and not labor:
            continue
        if kind == "newera" and not era:
            continue
        end_dt = _dt(extra.get("end"))
        out.append(
            {
                "id": item["id"],
                "title": item["title"],
                "start": start.strftime("%Y-%m-%d %H:%M"),
                "end": end_dt.strftime("%Y-%m-%d %H:%M") if end_dt else "",
                "place": extra.get("place") or "",
                "signup_end": signup_end.strftime("%Y-%m-%d %H:%M") if signup_end else "",
                "labor_counted": labor,
                "labor_hours": hours,
                "new_era": era,
                "signed_up": extra.get("signed_up"),
                "capacity": extra.get("capacity"),
                "url": item.get("url") or "",
            }
        )
        if len(out) >= limit:
            break
    return out


def display_row(item: dict) -> dict:
    """把一条活动整理成画图/推送要用的样子（时间都转成好读的字符串）。"""
    extra = _extra(item.get("extra"))
    start = _start_of(item)
    end = _dt(extra.get("end"))
    labor, hours, era = meta_flags(item)
    return {
        "id": item.get("id"),
        "title": str(item.get("title") or "").strip(),
        "start_text": f"{start:%m-%d %H:%M}" if start else "",
        "end_text": f"{end:%H:%M}" if end else "",
        "place": str(extra.get("place") or "").strip(),
        "labor_counted": labor,
        "labor_hours": hours,
        "new_era": era,
    }


def new_activities(hours: int = NEW_ACTIVITY_HOURS) -> list[dict]:
    """最近 N 小时新入库、还没开始的活动（按开始时间排，标题去重）。

    Args:
        hours: 看最近多少小时入库的（1-336）。
    """
    hours = max(1, min(int(hours or NEW_ACTIVITY_HOURS), 24 * 14))
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT i.*, m.labor_counted, m.labor_hours, m.new_era
               FROM items i LEFT JOIN activity_meta m ON m.item_id = i.id
               WHERE i.source='second_classroom' AND i.board='event'
                 AND i.archived_at IS NULL
                 AND datetime(i.created_at) >= datetime('now','localtime',?)
               ORDER BY COALESCE(i.published, '')""",
            (f"-{hours} hours",),
        ).fetchall()
    finally:
        conn.close()

    fresh: list[dict] = []
    seen: set[str] = set()
    for row in upcoming([dict(r) for r in rows]):
        key = str(row.get("title") or row["id"])
        if key in seen:
            continue
        seen.add(key)
        fresh.append(row)
    return fresh
