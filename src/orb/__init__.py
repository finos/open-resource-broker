"""
Open Resource Broker (ORB) - Cloud resource orchestration platform.

Usage:
    from orb import ORBClient
    from orb.sdk import ORBClient, SDKConfig

    async with ORBClient(provider_type="aws") as client:
        templates = await client.list_templates()
"""

from orb._package import __version__
from orb.sdk.client import OpenResourceBroker, ORBClient
from orb.sdk.config import SDKConfig
from orb.sdk.exceptions import ConfigurationError, ProviderError, SDKError

# Convenient aliases
ORB = ORBClient
orb = ORBClient

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
