"""jAccount 单点登录：一次登录，所有走 jAccount 的站都能进。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from xinge import jaccount

router = APIRouter(tags=["jaccount"])


@router.get("/jaccount/status")
def status():
    """jAccount 会话状态 + 各站是否已登录。"""
    return jaccount.status()


@router.post("/jaccount/sso/{site}")
async def sso(site: str):
    """对某个站做一次 SSO，把会话存下来。"""
    result = await asyncio.to_thread(jaccount.login, site)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "登录失败")
    return result


@router.post("/jaccount/sso")
async def sso_all():
    """所有站都试一遍，返回每个站的结果。"""
    results = {}
    for site in jaccount.SITES:
        results[site] = await asyncio.to_thread(jaccount.login, site)
    return {"results": results}
