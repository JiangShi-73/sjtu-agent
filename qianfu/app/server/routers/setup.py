"""机器人接入向导。

用户只需要在这里填 QQ 机器人的 AppID / Secret，
模型、平台、插件由后端自动写进 AstrBot。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from .. import astrbot, config, providers
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


@router.get("/setup/providers")
def setup_providers():
    """模型供应商目录：前端下拉选供应商，地址和常用模型自动带出。"""
    return providers.list_providers()


def _llm_summary(cfg: dict) -> dict:
    """当前模型配置的回显（不返回完整 Key，只给脱敏后的样子）。"""
    base = str(cfg.get("llm_base") or "").strip()
    model = str(cfg.get("llm_model") or "").strip()
    key = str(cfg.get("llm_key") or "").strip()
    provider = providers.match_base(base)
    return {
        "provider": provider["id"] if provider else "custom",
        "provider_name": provider["name"] if provider else "自定义",
        "base": base,
        "model": model,
        "key_set": bool(key),
        "key_hint": providers.mask_key(key),
        "needs_key": provider["needs_key"] if provider else False,
    }


@router.get("/setup/status")
def setup_status():
    cfg = config.load()
    state = astrbot_setup.status(cfg)
    state["running"] = astrbot.alive()
    state["dashboard"] = f"http://127.0.0.1:{astrbot.DASHBOARD_PORT}"
    # 信鸽自己存的模型配置（AstrBot 侧的状态在上面那些字段里）
    state["llm"] = _llm_summary(cfg)
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
    """改模型：前端选了供应商后把 base/model/key 一起送过来。

    参数还是老的 base / model / key 三个，但语义更宽了：
    - key 留空 = 不改，沿用已存的 Key（换供应商时不能这么干，会报错）；
    - Ollama、自定义这类不强制要 Key 的供应商，key 可以一直空着；
    - base / model 留空时沿用当前值，只填一个也能用。
    """
    current = config.load()
    key = str(body.get("key") or "").strip()
    base = str(body.get("base") or "").strip()
    model = str(body.get("model") or "").strip()

    cur_base = str(current.get("llm_base") or "").strip()
    if not base:
        base = cur_base

    provider = providers.match_base(base)
    if not model:
        # 换供应商时用它的默认模型；没换就用现在的，别写成 null
        if base != cur_base:
            model = providers.default_model(provider)
        if not model and base == cur_base:
            model = str(current.get("llm_model") or "").strip()

    # 目录里的供应商按 needs_key 判断；目录外的自定义地址不拦 Key
    if not key:
        stored = str(current.get("llm_key") or "").strip()
        if providers.needs_key(base) and not (stored and base == cur_base):
            return {
                "ok": False,
                "error": "这个供应商需要填 API Key（Key 留空只对 Ollama/自定义生效）",
            }
        key = stored

    if not base:
        return {"ok": False, "error": "接口地址不能为空"}
    if not model:
        return {"ok": False, "error": "模型不能为空"}

    config.save({"llm_key": key, "llm_base": base, "llm_model": model})
    cfg = config.load()
    result = await asyncio.to_thread(astrbot.apply_config, cfg)
    result.setdefault("provider", provider["id"] if provider else "custom")
    return result


@router.post("/setup/restart")
async def setup_restart():
    return await asyncio.to_thread(astrbot.restart)


@router.post("/setup/stop")
async def setup_stop():
    return await asyncio.to_thread(astrbot.stop)
