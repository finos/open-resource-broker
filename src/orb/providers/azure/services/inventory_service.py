"""Azure read/query utilities and SDK status fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, TypedDict, cast

from orb.domain.base.ports import LoggingPort
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.infrastructure.cyclecloud_session import CycleCloudRequestContext
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureHandlerStatusResult,
)
from orb.providers.base.strategy import ProviderOperation, ProviderResult

AzureProviderApiRef = AzureProviderApi | str


@dataclass
class AzureStatusQueryContext:
    """Parameters for an Azure instance status query."""

    instance_ids: list[str]
    resource_group: str
    provider_api: Optional[AzureProviderApi | str]


@dataclass
class AzureReadOperationContext:
    """Provider-owned runtime context for Azure status and describe operations."""

    operation_name: str
    request_id: str | None
    template_id: str
    request_metadata: dict[str, Any]
    cyclecloud_request_context: CycleCloudRequestContext
    provider_api: Optional[AzureProviderApiRef]
    provider_api_key: str | None
    resource_group: str | None
    instance_ids: list[str] = field(default_factory=list)
    resource_ids: list[str] = field(default_factory=list)
    grouped_resource_mapping: dict[str, list[str]] = field(default_factory=dict)
    direct_resource_id: str | None = None
    fail_on_partial_status_error: bool = False


class AzureStatusProviderData(TypedDict, total=False):
    """Provider-owned identity fields surfaced by Azure handlers."""

    vm_id: str
    vmss_instance_id: str
    node_id: str
    node_name: str
    vm_name: str


class AzureStatusResult(TypedDict, total=False):
    """Normalized Azure status result used for cross-handler identity matching."""

    instance_id: str
    name: str
    provider_data: AzureStatusProviderData


class AzureMachineConversionServiceProtocol(Protocol):
    """Structural subset of AzureMachineConversionService used by SDK fallback."""

    def convert_sdk_vm(self, vm: object, azure_client: AzureClient) -> dict[str, Any]:
        """Convert an SDK VM object into the normalized machine/result shape."""
        ...


def normalize_status_result(result: AzureHandlerStatusResult) -> AzureStatusResult:
    """Build an AzureStatusResult from a generic handler status dict."""
    normalized: AzureStatusResult = {}

    instance_id = result.get("instance_id")
    if isinstance(instance_id, str) and instance_id:
        normalized["instance_id"] = instance_id

    name = result.get("name")
    if isinstance(name, str) and name:
        normalized["name"] = name

    raw_provider_data = result.get("provider_data")
    if isinstance(raw_provider_data, dict):
        provider_data: AzureStatusProviderData = {}
        for key in ("vm_id", "vmss_instance_id", "node_id", "node_name", "vm_name"):
            value = raw_provider_data.get(key)
            if isinstance(value, str) and value:
                provider_data[key] = value
        if provider_data:
            normalized["provider_data"] = provider_data

    return normalized


def normalize_status_results(results: list[AzureHandlerStatusResult]) -> list[AzureStatusResult]:
    """Normalize a generic handler status list for Azure status matching."""
    return [normalize_status_result(result) for result in results]


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


def resolve_read_provider_api(
    operation: ProviderOperation,
    normalize_provider_api: Callable[[Any], Any],
) -> Optional[AzureProviderApiRef]:
    """Resolve and normalize the Azure provider API carried by an operation."""
    provider_api = operation.parameters.get("provider_api")
    if provider_api in (None, ""):
        return None
    normalized_provider_api = normalize_provider_api(provider_api)
    if isinstance(normalized_provider_api, (AzureProviderApi, str)):
        return cast(AzureProviderApiRef, normalized_provider_api)
    return None


def collect_status_resource_ids(
    grouped_resource_mapping: dict[str, list[str]],
    direct_resource_id: object,
) -> list[str]:
    """Collect all Azure resource ids relevant to a status query."""
    resource_ids: list[str] = []
    for resource_id, mapped_ids in grouped_resource_mapping.items():
        if resource_id and mapped_ids and resource_id not in resource_ids:
            resource_ids.append(resource_id)

    if direct_resource_id not in (None, "") and str(direct_resource_id) not in resource_ids:
        resource_ids.append(str(direct_resource_id))
    return resource_ids


def build_read_operation_context(
    *,
    operation: ProviderOperation,
    operation_name: str,
    default_resource_group: Optional[str],
    normalize_provider_api: Callable[[Any], Any],
) -> AzureReadOperationContext:
    """Build the provider-owned runtime context for Azure read operations."""
    metadata = request_metadata(operation)
    cyclecloud_request_context = CycleCloudRequestContext.from_mapping(metadata)
    provider_api = resolve_read_provider_api(operation, normalize_provider_api)
    provider_api_key = None
    if isinstance(provider_api, AzureProviderApi):
        provider_api_key = provider_api.value
    elif isinstance(provider_api, str):
        provider_api_key = provider_api

    resource_group = resolve_operation_resource_group(operation, default_resource_group)
    request_id = operation_request_id(operation)
    template_id = str(operation.parameters.get("template_id", "unknown"))

    if operation_name == "get_instance_status":
        instance_ids = list(operation.parameters.get("instance_ids", []) or [])
        if not instance_ids:
            raise AzureValidationError(
                "Instance IDs are required for status query",
                error_code="MISSING_INSTANCE_IDS",
            )
        if not resource_group:
            raise AzureValidationError(
                "resource_group is required for status query",
                error_code="MISSING_RESOURCE_GROUP",
            )

        grouped_resource_mapping = group_instance_ids_by_resource(
            instance_ids,
            operation.parameters.get("resource_mapping", {}) or {},
        )
        direct_resource_id = operation.parameters.get("resource_id")
        if (
            direct_resource_id in (None, "")
            and provider_api == AzureProviderApi.CYCLECLOUD
            and cyclecloud_request_context.cluster_name not in (None, "")
        ):
            direct_resource_id = cyclecloud_request_context.cluster_name

        return AzureReadOperationContext(
            operation_name=operation_name,
            request_id=str(request_id) if request_id not in (None, "") else None,
            template_id=template_id,
            request_metadata=metadata,
            cyclecloud_request_context=cyclecloud_request_context,
            provider_api=provider_api,
            provider_api_key=provider_api_key,
            resource_group=resource_group,
            instance_ids=instance_ids,
            resource_ids=collect_status_resource_ids(grouped_resource_mapping, direct_resource_id),
            grouped_resource_mapping=grouped_resource_mapping,
            direct_resource_id=str(direct_resource_id)
            if direct_resource_id not in (None, "")
            else None,
        )

    resource_ids = list(operation.parameters.get("resource_ids", []) or [])
    if not resource_ids:
        raise AzureValidationError(
            "Resource IDs are required for instance discovery",
            error_code="MISSING_RESOURCE_IDS",
        )
    if provider_api in (None, ""):
        raise AzureValidationError(
            "provider_api is required for Azure resource discovery",
            error_code="MISSING_PROVIDER_API",
        )

    return AzureReadOperationContext(
        operation_name=operation_name,
        request_id=str(request_id) if request_id not in (None, "") else None,
        template_id=template_id,
        request_metadata=metadata,
        cyclecloud_request_context=cyclecloud_request_context,
        provider_api=provider_api,
        provider_api_key=provider_api_key,
        resource_group=resource_group,
        resource_ids=resource_ids,
        direct_resource_id=resource_ids[0] if len(resource_ids) == 1 else None,
    )


def build_read_handler_request(
    *,
    read_context: AzureReadOperationContext,
    provider_name: str,
    resource_ids: list[str],
    additional_metadata: Optional[dict[str, Any]] = None,
) -> Request:
    """Build the Request object used for Azure read/query handler calls."""
    metadata: dict[str, Any] = {"resource_group": read_context.resource_group}
    metadata.update(read_context.cyclecloud_request_context.to_metadata())
    if additional_metadata:
        metadata.update(additional_metadata)

    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id=read_context.template_id,
        machine_count=1,
        provider_type="azure",
        provider_name=provider_name,
        request_id=read_context.request_id,
        metadata=metadata,
    )
    request.resource_ids = resource_ids
    if read_context.provider_api_key:
        request.provider_api = read_context.provider_api_key
    return request


def status_candidate_ids(result: AzureStatusResult) -> set[str]:
    """Return all plausible instance identifiers from a status result."""
    provider_data = result.get("provider_data") or {}
    candidate_ids = {
        result.get("instance_id", ""),
        result.get("name", ""),
        provider_data.get("vm_id", ""),
        provider_data.get("vmss_instance_id", ""),
        provider_data.get("node_id", ""),
        provider_data.get("node_name", ""),
        provider_data.get("vm_name", ""),
    }
    candidate_ids.discard("")
    return candidate_ids

def observed_status_ids(instance_details: list[AzureStatusResult]) -> set[str]:
    """Collect all candidate identifiers across a list of instance detail dicts."""
    observed_ids: set[str] = set()
    for instance in instance_details:
        observed_ids.update(status_candidate_ids(instance))
    return observed_ids

def filter_status_results(
    results: list[AzureStatusResult],
    requested_ids: list[str],
) -> list[AzureStatusResult]:
    """Return only the results whose candidate IDs overlap with the requested set."""
    requested = {str(item) for item in requested_ids}
    filtered: list[AzureStatusResult] = []
    for result in results:
        if status_candidate_ids(result) & requested:
            filtered.append(result)
    return filtered

def status_resource_ids(
    operation: ProviderOperation,
    instance_ids: list[str],
) -> list[str]:
    """Resolve the Azure resource IDs relevant to the given instance IDs."""
    raw_resource_mapping = operation.parameters.get("resource_mapping", {}) or {}
    grouped_resource_mapping = group_instance_ids_by_resource(
        instance_ids,
        raw_resource_mapping,
    )
    return collect_status_resource_ids(
        grouped_resource_mapping,
        operation.parameters.get("resource_id"),
    )

def sdk_status_result(
    *,
    status_context: AzureStatusQueryContext,
    azure_client: Optional[AzureClient],
    machine_conversion_service: AzureMachineConversionServiceProtocol,
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
