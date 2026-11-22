"""机器人接入向导。

用户只需要在这里填 QQ 机器人的 AppID / Secret，
模型、平台、插件由后端自动写进 AstrBot。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from .. import astrbot, config
from xinge import astrbot_setup

router = APIRouter(tags=["setup"])


@router.get("/setup/status")
def setup_status():
    cfg = config.load()
    state = astrbot_setup.status(cfg)
    state["running"] = astrbot.alive()
    state["dashboard"] = f"http://127.0.0.1:{astrbot.DASHBOARD_PORT}"
    return state


@router.post("/setup/qq")
async def setup_qq(body: dict):
    """填 QQ 机器人的 AppID / Secret，写完自动重启 AstrBot。"""
    appid = str(body.get("appid") or "").strip()
    secret = str(body.get("secret") or "").strip()

    if not appid.isdigit():
        return {"ok": False, "error": "AppID 应该是一串数字"}
    if len(secret) < 8:
        return {"ok": False, "error": "Secret 看起来不对"}

    config.save({"qq_appid": appid, "qq_secret": secret})
    cfg = config.load()
    result = await asyncio.to_thread(astrbot.apply_config, cfg)
    return result


@router.post("/setup/model")
async def setup_model(body: dict):
    """改模型（一般不用动，默认走 opencode）。"""
    key = str(body.get("key") or "").strip()
    if not key:
        return {"ok": False, "error": "Key 不能为空"}

    config.save(
        {
            "llm_key": key,
            "llm_base": str(body.get("base") or "").strip(),
            "llm_model": str(body.get("model") or "").strip(),
        }
    )
    cfg = config.load()
    result = await asyncio.to_thread(astrbot.apply_config, cfg)
    return result


@router.post("/setup/restart")
async def setup_restart():
    return await asyncio.to_thread(astrbot.restart)


@router.post("/setup/stop")
async def setup_stop():
    return await asyncio.to_thread(astrbot.stop)
