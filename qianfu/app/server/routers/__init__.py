from .aihaoke import router as aihaoke_router
from .bot import router as bot_router
from .canvas import router as canvas_router
from .courses import router as courses_router
from .mail import router as mail_router
from .events import router as events_router
from .items import router as items_router
from .settings import router as settings_router
from .sources import router as sources_router
from .system import router as system_router

__all__ = [
    "aihaoke_router",
    "bot_router",
    "canvas_router",
    "courses_router",
    "mail_router",
    "events_router",
    "items_router",
    "settings_router",
    "sources_router",
    "system_router",
]
