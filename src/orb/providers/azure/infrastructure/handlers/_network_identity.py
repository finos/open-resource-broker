"""Shared handler helpers for optional Azure network identity enrichment."""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.infrastructure.services.azure_network_identity_resolver import (
    AzureNetworkIdentity,
)


def empty_network_identity() -> AzureNetworkIdentity:
    """Return the empty network-identity shape used when enrichment fails."""
    return {
        "private_ip": None,
        "public_ip": None,
        "subnet_id": None,
        "vnet_id": None,
        "nic_id": None,
        "nic_name": None,
    }


def network_identity_soft_failure_types() -> tuple[type[BaseException], ...]:
    """Return enrichment failures that should not hide visible Azure machines."""
    azure_error_type: Optional[type[BaseException]]
    error_types: list[type[BaseException]] = [AttributeError, TypeError]
    try:
        from azure.core.exceptions import AzureError

        azure_error_type = AzureError
    except ImportError:
        azure_error_type = None
    if azure_error_type is not None:
        error_types.append(azure_error_type)
    return tuple(dict.fromkeys(error_types))


async def resolve_network_identity_or_empty_async(
    *,
    logger: LoggingPort,
    target_label: str,
    resolver: Callable[[], Awaitable[AzureNetworkIdentity]],
) -> AzureNetworkIdentity:
    """Resolve optional network identity without hiding otherwise visible machines."""
    try:
        return await resolver()
    except network_identity_soft_failure_types() as exc:
        logger.warning(
            "Failed to resolve network identity for %s: %s",
            target_label,
            exc,
        )
        return empty_network_identity()
