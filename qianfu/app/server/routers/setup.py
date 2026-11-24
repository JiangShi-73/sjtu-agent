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


@router.get("/setup/quick")
def quick_status():
    """一键自检：还差什么才能用。

    返回一个清单，前端照着提示一步步配就行，不用翻文档。
    """
    from pathlib import Path
    import os

    from .. import db

    home = Path(os.path.expanduser("~")) / ".xinge"
    cfg_path = home / "config.json"

    cfg = {}
    if cfg_path.exists():
        import json

        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}

    checks = []

    # 看真实登录态，不能只看文件在不在——扫码失败也会留下空 session.json
    try:
        from .. import qrlogin

        canvas_ok = bool(qrlogin.status().get("logged_in"))
    except Exception:                             # noqa: BLE001
        canvas_ok = bool(cfg.get("canvas_token"))

    checks.append(
        {
            "key": "canvas",
            "name": "Canvas 登录",
            "ok": canvas_ok,
            "hint": "点「订阅」→「扫码登录」，用 jAccount 扫一次。",
            "required": True,
        }
    )

    checks.append(
        {
            "key": "model",
            "name": "模型 Key",
            "ok": bool(cfg.get("llm_key")),
            "hint": "点「机器人」填 API Key。不填也能用，只是问助手不能用。",
            "required": False,
        }
    )

    checks.append(
        {
            "key": "qq",
            "name": "QQ 机器人",
            "ok": bool(cfg.get("qq_appid") and cfg.get("qq_secret")),
            "hint": "点「机器人」填 appid/secret。不填就用不了 QQ 推送。",
            "required": False,
        }
    )

    checks.append(
        {
            "key": "shuiyuan",
            "name": "水源社区",
            "ok": (home / "shuiyuan.json").exists(),
            "hint": "在「订阅」里点「全部登录一遍」。",
            "required": False,
        }
    )

    checks.append(
        {
            "key": "data",
            "name": "有数据了",
            "ok": len(db.list_items(None, False, 1)) > 0,
            "hint": "点左下角「抓取全部」。",
            "required": True,
        }
    )

    missing = [c for c in checks if not c["ok"] and c["required"]]
    return {
        "ready": not missing,
        "checks": checks,
        "next": missing[0]["hint"] if missing else "都配好了",
    }


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
