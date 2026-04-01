"""Azure status query utilities and SDK status fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.base.strategy import ProviderOperation, ProviderResult


@dataclass
class AzureStatusQueryContext:
    """Parameters for an Azure instance status query."""

    instance_ids: list[str]
    resource_group: str
    provider_api: Optional[AzureProviderApi | str]


CYCLECLOUD_METADATA_KEYS = (
    "cluster_name",
    "node_array",
    "node_ids",
    "operation_id",
    "operation_location",
    "cyclecloud_url",
    "cyclecloud_credential_path",
    "cyclecloud_verify_ssl",
    "cyclecloud_auth_mode",
    "cyclecloud_aad_scope",
)


def request_metadata(operation: ProviderOperation) -> dict[str, Any]:
    """Extract the request_metadata dict from a provider operation."""
    return dict(operation.parameters.get("request_metadata") or {})

def resolve_operation_resource_group(
    operation: ProviderOperation,
    default_resource_group: Optional[str],
) -> Optional[str]:
    """Return the resource group from request metadata, falling back to the default."""
    metadata = request_metadata(operation)
    request_resource_group = metadata.get("resource_group")
    if request_resource_group not in (None, ""):
        return str(request_resource_group)
    return default_resource_group

def group_instance_ids_by_resource(
    instance_ids: list[str],
    resource_mapping: dict[str, Any],
) -> dict[str, list[str]]:
    """Group the requested instance IDs by their owning Azure resource ID."""
    grouped: dict[str, list[str]] = {}
    if not resource_mapping:
        return grouped

    for key, value in resource_mapping.items():
        resource_id: Optional[str] = None
        mapped_ids: list[str] = []

        if isinstance(value, tuple):
            if value:
                resource_id = value[0] if isinstance(value[0], str) else None
            mapped_ids = [key]
        elif isinstance(value, str):
            resource_id = value
            mapped_ids = [key]
        elif isinstance(value, list):
            if isinstance(key, str):
                resource_id = key
            mapped_ids = [str(v) for v in value if v]

        if not resource_id:
            continue

        bucket = grouped.setdefault(resource_id, [])
        for mapped_id in mapped_ids:
            if mapped_id in instance_ids and mapped_id not in bucket:
                bucket.append(mapped_id)

    return grouped

def build_cyclecloud_request_metadata(
    *,
    operation: ProviderOperation,
    resource_group: Optional[str],
) -> dict[str, Any]:
    """Build the metadata dict required for CycleCloud handler calls."""
    metadata: dict[str, Any] = {"resource_group": resource_group}
    metadata_from_request = request_metadata(operation)
    for key in CYCLECLOUD_METADATA_KEYS:
        value = metadata_from_request.get(key)
        if value not in (None, ""):
            metadata[key] = value
    return metadata

def status_candidate_ids(result: dict[str, Any]) -> set[str]:
    """Return all plausible instance identifiers from a single status result."""
    provider_data = result.get("provider_data") or {}
    candidate_ids = {
        str(result.get("instance_id")),
        str(provider_data.get("vm_id")),
        str(provider_data.get("vmss_instance_id")),
        str(provider_data.get("node_id")),
        str(provider_data.get("vm_name")),
    }
    candidate_ids.discard("None")
    candidate_ids.discard("")
    return candidate_ids

def observed_status_ids(instance_details: list[dict[str, Any]]) -> set[str]:
    """Collect all candidate identifiers across a list of instance detail dicts."""
    observed_ids: set[str] = set()
    for instance in instance_details:
        observed_ids.update(status_candidate_ids(instance))
    return observed_ids

def filter_status_results(
    results: list[dict[str, Any]],
    requested_ids: list[str],
) -> list[dict[str, Any]]:
    """Return only the results whose candidate IDs overlap with the requested set."""
    requested = {str(item) for item in requested_ids}
    filtered: list[dict[str, Any]] = []
    for result in results:
        provider_data = result.get("provider_data") or {}
        candidate_ids = {
            str(result.get("instance_id")),
            str(provider_data.get("vm_id")),
            str(provider_data.get("vmss_instance_id")),
            str(provider_data.get("node_id")),
            str(provider_data.get("vm_name")),
        }
        candidate_ids.discard("None")
        if candidate_ids & requested:
            filtered.append(result)
    return filtered

def status_resource_ids(
    operation: ProviderOperation,
    instance_ids: list[str],
) -> list[str]:
    """Resolve the Azure resource IDs relevant to the given instance IDs."""
    resource_ids: list[str] = []
    raw_resource_mapping = operation.parameters.get("resource_mapping", {}) or {}
    for resource_id, mapped_ids in group_instance_ids_by_resource(
        instance_ids,
        raw_resource_mapping,
    ).items():
        if resource_id and mapped_ids and resource_id not in resource_ids:
            resource_ids.append(resource_id)

    direct_resource_id = operation.parameters.get("resource_id")
    if direct_resource_id not in (None, "") and str(direct_resource_id) not in resource_ids:
        resource_ids.append(str(direct_resource_id))
    return resource_ids

def sdk_status_result(
    *,
    status_context: AzureStatusQueryContext,
    azure_client: Optional[AzureClient],
    machine_conversion_service: Any,
    logger: LoggingPort,
) -> ProviderResult:
    """Query VM status directly via the Azure SDK as a fallback."""
    if not azure_client:
        return ProviderResult.error_result(
            "Azure client not available", "AZURE_CLIENT_NOT_AVAILABLE"
        )

    machines: list[dict[str, Any]] = []
    compute = azure_client.compute_client

    for vm_id in status_context.instance_ids:
        try:
            vm = compute.virtual_machines.get(
                resource_group_name=status_context.resource_group,
                vm_name=vm_id,
                expand="instanceView",
            )
            machines.append(machine_conversion_service.convert_sdk_vm(vm, azure_client))
        except Exception as exc:
            logger.error("Failed to get status for VM '%s': %s", vm_id, exc)
            machines.append(
                {
                    "instance_id": vm_id,
                    "status": "unknown",
                    "provider_type": "azure",
                    "error": str(exc),
                }
            )

    return ProviderResult.success_result(
        {"instances": machines, "queried_count": len(status_context.instance_ids)},
        {"operation": "get_instance_status", "instance_ids": status_context.instance_ids},
    )
