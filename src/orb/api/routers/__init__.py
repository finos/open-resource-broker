"""API routers package."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .machines import router as machines_router
    from .requests import router as requests_router
    from .templates import router as templates_router

_LAZY_ROUTERS = {
    "machines_router": ".machines",
    "requests_router": ".requests",
    "templates_router": ".templates",
}


def __getattr__(name: str) -> Any:
    # Importing a single router module (e.g. ``events``) must not load them all.
    module_name = _LAZY_ROUTERS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return importlib.import_module(module_name, __name__).router


__all__: list[str] = ["machines_router", "requests_router", "templates_router"]
