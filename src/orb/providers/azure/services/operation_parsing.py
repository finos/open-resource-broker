"""Generic helpers for parsing Azure ``ProviderOperation`` parameters.

Used by every Azure service that consumes a ``ProviderOperation`` — read
paths (``inventory_service``) and write paths (``termination_service``,
``provisioning_service``) alike. No read- or write-specific logic.
"""

from __future__ import annotations

from typing import Optional

from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.base.strategy import ProviderOperation


def operation_request_id(operation: ProviderOperation) -> str | None:
    """Extract the request id from operation parameters or context."""
    return operation.parameters.get("request_id") or (
        operation.context.get("request_id") if operation.context else None
    )


def resolve_operation_resource_group(
    operation: ProviderOperation,
    default_resource_group: Optional[str],
) -> Optional[str]:
    """Return the resource group from request metadata, falling back to the default."""
    metadata = operation.parameters.get("request_metadata") or {}
    request_resource_group = metadata.get("resource_group")
    if request_resource_group not in (None, ""):
        return str(request_resource_group)
    return default_resource_group


def resolve_operation_provider_api(
    operation: ProviderOperation,
) -> Optional[AzureProviderApi]:
    """Resolve the Azure provider API carried by an operation.

    The ``provider_api`` parameter is a string (the enum's ``.value``); enum
    constructors are idempotent so an enum instance would also coerce, but
    every construction site in the repo passes a string.
    """
    raw = operation.parameters.get("provider_api")
    if not raw:
        return None
    try:
        return AzureProviderApi(raw)
    except ValueError as exc:
        raise AzureValidationError(
            f"Invalid Azure provider_api: {raw!r}",
            error_code="INVALID_PROVIDER_API",
        ) from exc


def group_instance_ids_by_resource(
    instance_ids: list[str],
    resource_mapping: dict[str, tuple[Optional[str], int]],
) -> dict[str, list[str]]:
    """Group requested instance IDs by their owning Azure resource ID.

    The mapping shape comes from the deprovisioning orchestrator —
    ``{instance_id: (resource_id, desired_capacity)}``. Entries without a
    ``resource_id`` and instances not in ``instance_ids`` are skipped.
    """
    grouped: dict[str, list[str]] = {}
    for instance_id, (resource_id, _capacity) in resource_mapping.items():
        if not resource_id or instance_id not in instance_ids:
            continue
        bucket = grouped.setdefault(resource_id, [])
        if instance_id not in bucket:
            bucket.append(instance_id)
    return grouped
