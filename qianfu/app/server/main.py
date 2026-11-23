"""FastAPI 入口。

启动：cd qianfu && python -m app.server

对外暴露三样东西，都是给"别的程序/Agent"用的：
  1. /api/v1/*      REST + 自动生成的 /openapi.json
  2. /ws            WebSocket 事件流
  3. /              静态前端
"""

from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .events import bus
from .routers import (
    agent_router,
    api_manage_router,
    aihaoke_router,
    bot_router,
    canvas_router,
    colleges_router,
    courses_router,
    mail_router,
    events_router,
    items_router,
    jaccount_router,
    settings_router,
    setup_router,
    shuiyuan_router,
    sources_router,
    system_router,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
LOG_DIR = Path.home() / ".xinge" / "logs"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(logging.StreamHandler())


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    db.init()
    logging.getLogger("xinge.app").info("服务启动，数据库 %s", db.DB_PATH)

    # AstrBot 装了就在后台拉起来，失败不影响信鸽本身
    try:
        from . import astrbot

        astrbot.bootstrap()
    except Exception as exc:                      # noqa: BLE001
        logging.getLogger("xinge.app").warning("AstrBot 没起来：%s", exc)

    yield
    logging.getLogger("xinge.app").info("服务关闭")


app = FastAPI(
    title="信鸽 SJTU",
    version="0.1.0",
    description="机器人管理与信息汇总工作台（本地服务）",
    lifespan=lifespan,
)

@app.middleware("http")
async def bot_guard(request, call_next):
    """机器人接口的开关：关掉的直接 403。"""
    path = request.url.path
    if path.startswith("/api/v1/bot"):
        from . import api_registry

        if not api_registry.bot_allows(path):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"detail": "这个接口在「接口管理」里被关掉了"}, status_code=403
            )
    return await call_next(request)


app.include_router(system_router, prefix="/api/v1")
app.include_router(sources_router, prefix="/api/v1")
app.include_router(setup_router, prefix="/api/v1")
app.include_router(shuiyuan_router, prefix="/api/v1")
app.include_router(items_router, prefix="/api/v1")
app.include_router(jaccount_router, prefix="/api/v1")
app.include_router(settings_router, prefix="/api/v1")
app.include_router(events_router, prefix="/api/v1")
app.include_router(courses_router, prefix="/api/v1")
app.include_router(colleges_router, prefix="/api/v1")
app.include_router(mail_router, prefix="/api/v1")
app.include_router(canvas_router, prefix="/api/v1")
app.include_router(bot_router, prefix="/api/v1")
app.include_router(aihaoke_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(api_manage_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/static/index.html")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    queue = bus.subscribe()
    try:
        while True:
            await ws.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        bus.unsubscribe(queue)


class NoCacheStatic(StaticFiles):
    """界面文件不缓存。

    Electron 会把 app.js / style.css 缓存住，改了代码用户看不到新功能，
    每次都让人手动刷新太蠢，直接不让缓存。
    """

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp


app.mount("/static", NoCacheStatic(directory=WEB_DIR, html=True), name="static")
