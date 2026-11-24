"""后台定时任务：自动抓取 + 自动归档 + 自动清理。

四件事：

    每 30 分钟   抓一遍所有信息源
    每小时       把过了 ddl 的条目移进存档区
    每小时       删掉在存档区待满 7 天的条目
    每小时       过期/发布超过 1 天的条目自动标已读（邮件不在 items 表，不受影响）

用 asyncio 起一个常驻任务，后端启动时自动跑。
"""

from __future__ import annotations

import asyncio
import logging

from . import collector, db, reminders

log = logging.getLogger("xinge.sched")

FETCH_EVERY = 30 * 60          # 抓取间隔（秒）
ARCHIVE_EVERY = 60 * 60        # 归档间隔（秒）
MAIL_EVERY = 30 * 60           # 邮件巡检间隔（秒）
MAIL_LIMIT = 20                # 每次看最近多少封（上限条数跟 settings 走）
ARCHIVE_GRACE_DAYS = 0         # 过期后宽限几天再归档
ARCHIVE_KEEP_DAYS = 7          # 存档区保留几天
READ_GRACE_DAYS = 1            # 过期/发布后宽限几天自动标已读

_state = {"started": False, "last_fetch": None, "last_archive": None,
          "last_mail": None, "last_read": None}


async def _fetch_loop() -> None:
    while True:
        try:
            log.info("定时抓取开始")
            results = await collector.fetch_all()
            added = sum(r.get("added", 0) for r in results)
            log.info("定时抓取完成，新增 %s 条", added)
            _state["last_fetch"] = _now()
        except Exception as exc:                  # noqa: BLE001
            log.warning("定时抓取出错：%s", exc)
        await asyncio.sleep(FETCH_EVERY)


async def _archive_loop() -> None:
    while True:
        try:
            moved = await asyncio.to_thread(db.archive_due, ARCHIVE_GRACE_DAYS)
            removed = await asyncio.to_thread(db.purge_archived, ARCHIVE_KEEP_DAYS)
            if moved or removed:
                log.info("归档 %s 条，清理 %s 条", moved, removed)
            # 过期/发布超过 1 天的自动标已读；只标已读，不动归档
            read = await asyncio.to_thread(db.mark_expired_read, READ_GRACE_DAYS)
            if read:
                log.info("过期 %s 天自动已读 %s 条", READ_GRACE_DAYS, read)
            _state["last_archive"] = _now()
            _state["last_read"] = _now()
        except Exception as exc:                  # noqa: BLE001
            log.warning("归档出错：%s", exc)
        await asyncio.sleep(ARCHIVE_EVERY)


async def _mail_loop() -> None:
    """新邮件巡检：30 分钟看一次未读邮件，生成提醒塞进同一队列。"""
    while True:
        try:
            result = await asyncio.to_thread(reminders.mail_tick, MAIL_LIMIT)
            if result.get("created"):
                log.info(
                    "邮件提醒 +%s（新邮件 %s 封）",
                    result["created"], result.get("new", 0),
                )
            _state["last_mail"] = _now()
        except Exception as exc:                  # noqa: BLE001
            log.warning("邮件巡检出错：%s", exc)
        await asyncio.sleep(MAIL_EVERY)


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def start() -> None:
    """起后台任务（只起一次）。"""
    if _state["started"]:
        return
    _state["started"] = True

    loop = asyncio.get_event_loop()
    loop.create_task(_fetch_loop())
    loop.create_task(_archive_loop())
    loop.create_task(_mail_loop())
    log.info("定时任务已启动：抓取每 %s 分钟，归档每 %s 分钟，邮件每 %s 分钟",
             FETCH_EVERY // 60, ARCHIVE_EVERY // 60, MAIL_EVERY // 60)


def status() -> dict:
    return {
        "running": _state["started"],
        "fetch_every_min": FETCH_EVERY // 60,
        "archive_every_min": ARCHIVE_EVERY // 60,
        "mail_every_min": MAIL_EVERY // 60,
        "keep_days": ARCHIVE_KEEP_DAYS,
        "read_grace_days": READ_GRACE_DAYS,
        "last_fetch": _state["last_fetch"],
        "last_archive": _state["last_archive"],
        "last_mail": _state["last_mail"],
        "last_read": _state["last_read"],
    }
