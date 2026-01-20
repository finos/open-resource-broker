"""
Open Resource Broker SDK - Programmatic interface for cloud resource operations.

This SDK provides a clean, async-first API for cloud resource provisioning
while maintaining full compatibility with the existing CQRS architecture.

Key Features:
- Automatic handler discovery from existing CQRS handlers
- Zero code duplication - reuses all existing DTOs and domain objects
- Clean Architecture compliance with layer separation
- Dependency injection integration
- Async/await support throughout

Usage:
    from orb_py import orb

    async with orb(provider="aws") as client:
        templates = await client.list_templates(active_only=True)
        request = await client.create_request(template_id="basic", machine_count=5)
        status = await client.get_request_status(request_id=request.id)
"""

from .client import OpenResourceBroker
from .config import SDKConfig
from .exceptions import ConfigurationError, ProviderError, SDKError

# Convenient aliases
ORB = OpenResourceBroker
orb = OpenResourceBroker

__all__: list[str] = [
    "OpenResourceBroker",
    "ORB",
    "orb",
    "ConfigurationError",
    "ProviderError",
    "SDKConfig",
    "SDKError",
]
