from .agent import router as agent_router
from .api_manage import router as api_manage_router
from .aihaoke import router as aihaoke_router
from .bot import router as bot_router
from .canvas import router as canvas_router
from .colleges import router as colleges_router
from .courses import router as courses_router
from .mail import router as mail_router
from .events import router as events_router
from .items import router as items_router
from .jaccount import router as jaccount_router
from .settings import router as settings_router
from .setup import router as setup_router
from .shuiyuan import router as shuiyuan_router
from .sources import router as sources_router
from .system import router as system_router

__all__ = [
    "agent_router",
    "api_manage_router",
    "aihaoke_router",
    "bot_router",
    "canvas_router",
    "colleges_router",
    "courses_router",
    "mail_router",
    "events_router",
    "items_router",
    "jaccount_router",
    "settings_router",
    "setup_router",
    "shuiyuan_router",
    "sources_router",
    "system_router",
]
