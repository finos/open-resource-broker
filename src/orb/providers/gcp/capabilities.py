"""Shared GCP provider capability metadata."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from orb.providers.gcp.domain.template.value_objects import GCPProviderApi

_GCP_API_CAPABILITIES: dict[str, dict[str, Any]] = {
    GCPProviderApi.MIG.value: {
        "supported_fleet_types": [],
        "supports_spot": True,
        "supports_on_demand": True,
        "supports_async_operations": True,
        "supports_start_stop": False,
        "max_instances": 1000,
        "native_cleanup": "managed_instance_group_resize_or_delete",
    },
    GCPProviderApi.SINGLE_VM.value: {
        "supported_fleet_types": [],
        "supports_spot": True,
        "supports_on_demand": True,
        "supports_async_operations": False,
        "supports_start_stop": True,
        "max_instances": 1,
        "native_cleanup": "instance_delete",
    },
}


def get_supported_api_capabilities() -> dict[str, dict[str, Any]]:
    """Return GCP API capability metadata."""
    return deepcopy(_GCP_API_CAPABILITIES)


def get_supported_apis() -> list[str]:
    """Return canonical GCP provider API names."""
    return list(_GCP_API_CAPABILITIES.keys())
