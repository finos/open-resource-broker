"""
Open Resource Broker (ORB) - Cloud resource orchestration platform.

Usage:
    from orb import ORBClient
    from orb.sdk import ORBClient, SDKConfig

    async with ORBClient(provider_type="aws") as client:
        templates = await client.list_templates()
"""

from typing import TYPE_CHECKING, Any

from orb._package import __version__

if TYPE_CHECKING:
    from orb.sdk.client import OpenResourceBroker, ORBClient
    from orb.sdk.config import SDKConfig
    from orb.sdk.exceptions import ConfigurationError, ProviderError, SDKError

    ORB = ORBClient
    orb = ORBClient

# SDK names are resolved on first access so that importing the package (as the
# CLI does) does not load the SDK and every provider it references.
_LAZY_ATTRS = {
    "ORBClient": ("orb.sdk.client", "ORBClient"),
    "OpenResourceBroker": ("orb.sdk.client", "OpenResourceBroker"),
    "ORB": ("orb.sdk.client", "ORBClient"),
    "orb": ("orb.sdk.client", "ORBClient"),
    "SDKConfig": ("orb.sdk.config", "SDKConfig"),
    "ConfigurationError": ("orb.sdk.exceptions", "ConfigurationError"),
    "ProviderError": ("orb.sdk.exceptions", "ProviderError"),
    "SDKError": ("orb.sdk.exceptions", "SDKError"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(target[0]), target[1])
    globals()[name] = value
    return value


__all__ = [
    # Primary public API
    "ORBClient",
    "ORB",
    "orb",
    # Backward-compatible alias
    "OpenResourceBroker",
    # Config and exceptions
    "SDKConfig",
    "SDKError",
    "ConfigurationError",
    "ProviderError",
    # Version
    "__version__",
]
