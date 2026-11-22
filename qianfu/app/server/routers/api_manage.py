"""接口管理：开关问助手工具和机器人接口。"""

from __future__ import annotations

from fastapi import APIRouter

from .. import api_registry

router = APIRouter(tags=["api"])


@router.get("/api/manage")
def manage_list():
    """两套消费者各自能看到哪些能力。"""
    return api_registry.overview()


@router.patch("/api/manage")
def manage_toggle(body: dict):
    """开关一个工具或接口。

    body: {"kind": "agent" | "bot", "key": "...", "enabled": true}
    """
    return api_registry.toggle(
        str(body.get("kind") or ""),
        str(body.get("key") or ""),
        bool(body.get("enabled", True)),
    )
