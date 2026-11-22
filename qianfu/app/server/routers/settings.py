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


@router.get("/feeds")
def list_feeds():
    cfg = config.load()
    return {"feeds": cfg.get("rss_feeds") or []}


@router.post("/feeds")
def add_feed(body: dict):
    url = (body.get("url") or "").strip()
    if not url.startswith("http"):
        return {"ok": False, "error": "url 必须以 http 开头"}
    cfg = config.load()
    feeds = [f for f in (cfg.get("rss_feeds") or []) if f.get("url") != url]
    feeds.append(
        {
            "name": (body.get("name") or url).strip(),
            "url": url,
            "board": body.get("board") or "custom",
        }
    )
    config.save({"rss_feeds": feeds})
    return {"ok": True, "feeds": feeds}


@router.delete("/feeds")
def remove_feed(url: str):
    cfg = config.load()
    feeds = [f for f in (cfg.get("rss_feeds") or []) if f.get("url") != url]
    config.save({"rss_feeds": feeds})
    return {"ok": True, "feeds": feeds}


@router.put("/settings")
def write_settings(body: dict):
    payload = {k: v for k, v in (body or {}).items() if k in config.DEFAULTS}
    # 传空字符串表示"不修改"，避免前端把掩码写回去
    payload = {k: v for k, v in payload.items() if v not in ("", None)}
    return {"ok": True, "settings": config.save(payload)}
