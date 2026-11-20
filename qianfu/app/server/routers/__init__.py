from .items import router as items_router
from .settings import router as settings_router
from .sources import router as sources_router
from .system import router as system_router

__all__ = ["items_router", "settings_router", "sources_router", "system_router"]
