"""
Field mapping utilities for CLI display.

This module provides field mapping functions that help the CLI display
data consistently regardless of the underlying data format (snake_case,
camelCase, HF format, etc.).
"""

from typing import Any


def get_field_value(
    data_dict: dict[str, Any],
    field_mapping: dict[str, list[str]],
    field_key: str,
    default: str = "N/A",
) -> str:
    """
    Get field value from data dictionary using field mapping.

    Args:
        data_dict: Dictionary containing the data
        field_mapping: Mapping of logical field names to possible actual field names
        field_key: Logical field name to look up
        default: Default value if field not found

    Returns:
        Field value as string, or default if not found
    """
    possible_names = field_mapping.get(field_key, [field_key])

    for name in possible_names:
        if name in data_dict:
            value = data_dict[name]
            return str(value) if value is not None else default

    return default


def get_template_field_mapping() -> dict[str, list[str]]:
    """
    Get mapping of logical template field names to possible actual field names.
    Uses Template model as source of truth for field names.

    Returns:
        Dictionary mapping logical names to [snake_case, camelCase] variants
    """
    return {
        "id": ["template_id", "templateId"],
        "name": ["name"],
        "description": ["description"],
        "provider_api": ["provider_api", "providerApi"],
        "instance_type": ["instance_type", "vmType"],
        "image_id": ["image_id", "imageId"],
        "max_instances": ["max_instances", "maxNumber"],
        "subnet_ids": ["subnet_ids", "subnetIds"],
        "security_group_ids": ["security_group_ids", "securityGroupIds"],
        "key_name": ["key_name", "keyName"],
        "user_data": ["user_data", "userData"],
        "instance_tags": ["instance_tags", "instanceTags"],
        "price_type": ["price_type", "priceType"],
        "max_spot_price": ["max_spot_price", "maxSpotPrice"],
        "allocation_strategy": ["allocation_strategy", "allocationStrategy"],
        "fleet_type": ["fleet_type", "fleetType"],
        # AWS SpotFleet-specific field
        "fleet_role": ["fleet_role", "fleetRole"],
        "created_at": ["created_at", "createdAt"],
        "updated_at": ["updated_at", "updatedAt"],
    }


def get_request_field_mapping() -> dict[str, list[str]]:
    """Get mapping of logical request field names to possible actual field names."""
    return {
        "id": ["request_id", "requestId"],
        "status": ["status"],
        "template_id": ["template_id", "templateId"],
        "num_requested": ["num_requested", "numRequested"],
        "num_allocated": ["num_allocated", "numAllocated"],
        "created_at": ["created_at", "createdAt"],
        "updated_at": ["updated_at", "updatedAt"],
    }


def get_machine_field_mapping() -> dict[str, list[str]]:
    """Get mapping of logical machine field names to possible actual field names."""
    return {
        "id": ["machine_id", "machineId", "instance_id", "instanceId"],
        "name": ["name", "machine_name", "machineName"],
        "status": ["status", "state"],
        "instance_type": ["instance_type", "instanceType", "vm_type", "vmType"],
        "private_ip": ["private_ip", "privateIp", "private_ip_address"],
        "public_ip": ["public_ip", "publicIp", "public_ip_address"],
        "created_at": ["created_at", "createdAt", "launch_time"],
        "template_id": ["template_id", "templateId"],
    }
