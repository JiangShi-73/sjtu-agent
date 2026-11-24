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
    """对某个站做一次登录，把会话存下来。"""
    # 校园活动要拿 JWT，走它自己的换票流程
    if site == "activity":
        from xinge.sources import activity_portal

        def _login_activity():
            from xinge.auth import SessionStore

            token = activity_portal.get_token(SessionStore(), refresh=True)
            return {"ok": bool(token), "site": "activity"}

        result = await asyncio.to_thread(_login_activity)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail="换不到活动平台令牌，先确认 jAccount 已登录")
        return result

    result = await asyncio.to_thread(jaccount.login, site)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "登录失败")
    return result


@router.post("/jaccount/sso")
async def sso_all():
    """所有站都试一遍，返回每个站的结果。

    单个站失败不影响其它站——每个都单独兜住异常。
    """
    results = {}
    for site in jaccount.SITES:
        try:
            results[site] = await asyncio.to_thread(jaccount.login, site)
        except Exception as exc:                  # noqa: BLE001
            results[site] = {"ok": False, "error": str(exc)[:160]}
    return {"results": results}
