"""Agent 对话接口。"""

from __future__ import annotations

from fastapi import APIRouter

from .. import api_registry, config
from xinge import agent

router = APIRouter(tags=["agent"])


@router.get("/agent/tools")
def list_tools():
    """当前暴露给模型的所有工具。"""
    active = api_registry.agent_enabled()
    tools = [t for t in agent.TOOLS if t["function"]["name"] in active]
    return {
        "count": len(tools),
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "parameters": t["function"]["parameters"],
            }
            for t in tools
        ],
    }


@router.post("/agent/chat")
async def agent_chat(body: dict):
    """和 AI 对话，它会自己决定调哪些接口。"""
    cfg = config.load()
    messages = body.get("messages") or []

    if not messages and body.get("text"):
        messages = [{"role": "user", "content": body["text"]}]

    return await agent.chat(
        messages,
        base=cfg.get("llm_base") or "",
        key=cfg.get("llm_key") or "",
        model=cfg.get("llm_model") or "",
        tool_names=api_registry.agent_enabled(),
    )
