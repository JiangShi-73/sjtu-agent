"""水源社区登录与查询。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from xinge import shuiyuan

router = APIRouter(tags=["shuiyuan"])


@router.get("/shuiyuan/status")
def status():
    return {"logged_in": shuiyuan.logged_in()}


@router.post("/shuiyuan/login")
async def login():
    """开浏览器让用户 jAccount 登录，最多等 5 分钟。"""
    result = await asyncio.to_thread(shuiyuan.login)
    if not result.get("ok"):
        raise HTTPException(status_code=408, detail=result.get("error") or "登录超时")
    return result


@router.get("/shuiyuan/categories")
def categories():
    try:
        return {"categories": shuiyuan.categories()}
    except shuiyuan.ShuiyuanError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/shuiyuan/latest")
def latest(limit: int = 20):
    try:
        return {"topics": shuiyuan.latest(max(1, min(limit, 60)))}
    except shuiyuan.ShuiyuanError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/shuiyuan/search")
def search(q: str, limit: int = 10):
    try:
        return {"topics": shuiyuan.search(q, max(1, min(limit, 30)))}
    except shuiyuan.ShuiyuanError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/shuiyuan/topic/{topic_id}")
def topic(topic_id: int, posts: int = 20):
    try:
        return shuiyuan.topic(topic_id, max(1, min(posts, 50)))
    except shuiyuan.ShuiyuanError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
