"""Authentication strategy implementations."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bearer_token_strategy import BearerTokenStrategy
    from .no_auth_strategy import NoAuthStrategy

_LAZY_ATTRS = {
    "BearerTokenStrategy": ".bearer_token_strategy",
    "NoAuthStrategy": ".no_auth_strategy",
}


def __getattr__(name: str) -> Any:
    # The bearer-token strategy imports JWT libraries; load strategies on demand.
    module_name = _LAZY_ATTRS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name, __name__), name)


__all__: list[str] = ["BearerTokenStrategy", "NoAuthStrategy"]
