"""API routers package."""

from .machines import router as machines_router
from .requests import router as requests_router
from .templates import router as templates_router

__all__: list[str] = ["machines_router", "requests_router", "templates_router"]
