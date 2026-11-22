"""抓取调度：把 xinge 的信息源接到数据库和事件总线。

这一层是 App 和抓取内核之间的**唯一接缝**：
将来加源、加调度策略、加并发控制，都改这里，不动上面的接口。
"""

from __future__ import annotations

import asyncio
import os
import time

from starlette.concurrency import run_in_threadpool

from xinge.auth import SessionStore
from xinge.sources import build

from . import db
from .events import bus

_session = SessionStore()


def _make(key: str):
    from .config import load as load_config

    cfg = load_config()
    kwargs = {}
    if key == "canvas" and cfg.get("canvas_token"):
        kwargs["token"] = cfg["canvas_token"]
    if key == "second_classroom":
        kwargs["api_key"] = cfg.get("sjtu_api_key", "")
    return build(key, _session, **kwargs)


async def fetch_one(key: str) -> dict:
    started = time.time()
    try:
        src = _make(key)
        report = await run_in_threadpool(src.fetch)
    except Exception as exc:                       # 源自身崩了也不能拖垮服务
        db.record_run(key, False, str(exc), 0)
        bus.publish({"type": "source.error", "key": key, "error": str(exc)})
        return {"key": key, "ok": False, "error": str(exc), "added": 0}

    added = db.upsert_items(report.items) if report.ok else 0

    # 慕课作业过期的直接清掉，避免信息流里堆往期作业
    if key == "cnmooc" and report.ok:
        db.delete_expired("cnmooc", "作业/测验")

    db.record_run(key, report.ok, report.error, len(report.items))

    payload = {
        "key": key,
        "ok": report.ok,
        "error": report.error,
        "fetched": len(report.items),
        "added": added,
        "elapsed_ms": report.elapsed_ms or int((time.time() - started) * 1000),
    }
    bus.publish({"type": "source.done", **payload})
    if added:
        bus.publish({"type": "items.added", "key": key, "count": added})
    return payload


async def fetch_all(keys: list[str] | None = None) -> list[dict]:
    keys = keys or [
        "canvas", "cnmooc", "pe", "second_classroom",
        "aihaoke", "rss", "college",
    ]
    # 各源互不依赖，并发跑：学院公告要开浏览器，串行会拖到一分钟以上
    return list(await asyncio.gather(*(fetch_one(k) for k in keys)))
