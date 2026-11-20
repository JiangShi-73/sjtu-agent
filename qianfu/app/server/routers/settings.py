"""设置：读写本地配置（API Key 等）。

密钥只落在 ~/.xinge/config.json，不进仓库、不进前端代码。
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import config

router = APIRouter(tags=["settings"])

SENSITIVE = {"sjtu_api_key", "canvas_token"}


def _mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "已设置"


@router.get("/settings")
def read_settings():
    data = config.load()
    return {
        k: (_mask(v) if k in SENSITIVE else v)
        for k, v in data.items()
    }


@router.put("/settings")
def write_settings(body: dict):
    payload = {k: v for k, v in (body or {}).items() if k in config.DEFAULTS}
    # 传空字符串表示"不修改"，避免前端把掩码写回去
    payload = {k: v for k, v in payload.items() if v not in ("", None)}
    return {"ok": True, "settings": config.save(payload)}
