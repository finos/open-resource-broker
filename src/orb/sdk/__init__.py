"""
Open Resource Broker SDK - Programmatic interface for cloud resource operations.

This SDK provides a clean, async-first API for cloud resource provisioning
while maintaining full compatibility with the existing CQRS architecture.

Key Features:
- Automatic handler discovery from existing CQRS handlers
- CLI-style parameter name support with automatic mapping
- Zero code duplication - reuses all existing DTOs and domain objects
- Clean Architecture compliance with layer separation
- Dependency injection integration
- Async/await support throughout
- Full backward compatibility

Parameter Name Mapping:
The SDK now accepts both CLI-style and CQRS-style parameter names:

CLI-Style (NEW):
    async with orb() as client:
        # Use CLI parameter names that match command-line arguments
        request = await client.create_request(
            template_id="EC2FleetInstant",
            count=5  # Maps to requested_count internally
        )

CQRS-Style (Backward Compatible):
    async with orb() as client:
        # Original CQRS parameter names still work
        request = await client.create_request(
            template_id="EC2FleetInstant",
            requested_count=5  # Original CQRS name
        )

Supported Mappings:
- count → requested_count
- provider → provider_name

Usage:
    from orb import orb

    async with orb(provider="aws") as client:
        templates = await client.list_templates(active_only=True)
        # Both parameter styles work:
        request1 = await client.create_request(template_id="basic", count=5)
        request2 = await client.create_request(template_id="basic", requested_count=5)
        status = await client.get_request_status(request_id=request1.id)
"""

from .client import ORB, OpenResourceBroker, ORBClient
from .config import SDKConfig
from .exceptions import ConfigurationError, ProviderError, SDKError

# Convenient alias
orb = ORBClient

__all__: list[str] = [
    "ORBClient",
    "ORB",
    "orb",
    "OpenResourceBroker",
    "ConfigurationError",
    "ProviderError",
    "SDKConfig",
    "SDKError",
]
