"""Shared Azure provider capability metadata."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from orb.providers.azure.domain.template.value_objects import AzureProviderApi

_AZURE_API_CAPABILITIES: dict[str, dict[str, Any]] = {
    AzureProviderApi.VMSS.value: {
        "supported_fleet_types": [],
        "supports_spot": True,
        "supports_on_demand": True,
        "max_instances": 1000,
    },
    AzureProviderApi.VMSS_UNIFORM.value: {
        "supported_fleet_types": [],
        "supports_spot": True,
        "supports_on_demand": True,
        "max_instances": 1000,
    },
    AzureProviderApi.SINGLE_VM.value: {
        "supported_fleet_types": [],
        "supports_spot": True,
        "supports_on_demand": True,
        "max_instances": 1000,
    },
    AzureProviderApi.CYCLECLOUD.value: {
        "supported_fleet_types": [],
        "supports_spot": False,
        "supports_on_demand": True,
        "requires_existing_cluster": True,
        "required_create_fields": ["cluster_name", "node_array"],
        "capacity_limit_source": "cluster_status.maxCount",
        "supports_async_operations": True,
    },
}


def get_supported_api_capabilities() -> dict[str, dict[str, Any]]:
    """Return Azure API capability metadata."""
    return deepcopy(_AZURE_API_CAPABILITIES)


def get_supported_apis() -> list[str]:
    """Return the canonical list of Azure provider APIs."""
    return list(_AZURE_API_CAPABILITIES.keys())
