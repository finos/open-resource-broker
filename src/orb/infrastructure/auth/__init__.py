"""Authentication infrastructure components."""

from typing import TYPE_CHECKING, Any

from .registry import AuthRegistry, get_auth_registry

if TYPE_CHECKING:
    from .strategy import BearerTokenStrategy, NoAuthStrategy


def __getattr__(name: str) -> Any:
    # Strategy classes pull in JWT libraries; load them on demand.
    if name in ("BearerTokenStrategy", "NoAuthStrategy"):
        from . import strategy

        return getattr(strategy, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__: list[str] = [
    "AuthRegistry",
    "BearerTokenStrategy",
    "NoAuthStrategy",
    "get_auth_registry",
]
