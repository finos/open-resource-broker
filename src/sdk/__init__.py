"""
OpenHFPlugin SDK - Programmatic interface for Host Factory operations.

This SDK provides a clean, async-first API for cloud resource provisioning
while maintaining full compatibility with the existing CQRS architecture.

Key Features:
- Automatic handler discovery from existing CQRS handlers
- Zero code duplication - reuses all existing DTOs and domain objects
- Clean Architecture compliance with layer separation
- Dependency injection integration
- Async/await support throughout

Usage:
    from ohfpsdk import OHFPSDK

    async with OHFPSDK(provider="aws") as sdk:
        templates = await sdk.list_templates(active_only=True)
        request = await sdk.create_request(template_id="basic", machine_count=5)
        status = await sdk.get_request_status(request_id=request.id)
"""

from .client import OpenHFPluginSDK
from .config import SDKConfig
from .exceptions import ConfigurationError, ProviderError, SDKError

# Convenient alias
OHFPSDK = OpenHFPluginSDK

__all__: list[str] = [
    "OpenHFPluginSDK",
    "OHFPSDK",
    "SDKConfig",
    "SDKError",
    "ConfigurationError",
    "ProviderError",
]
