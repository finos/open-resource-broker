"""Azure read/query orchestration for status and resource discovery.

Owns the read-side vocabulary (``AzureReadOperationContext``,
``AzureStatusResult``, the normalization helpers) and the
``AzureInventoryService`` orchestrator. Generic ``ProviderOperation``
parsing lives in ``operation_parsing`` because it is also used by the
write paths (``termination_service``, ``provisioning_service``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Protocol, TypedDict, runtime_checkable

from orb.domain.base.ports import LoggingPort
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.azure.infrastructure.cyclecloud_session import CycleCloudRequestContext
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureHandler,
    AzureHandlerStatusResult,
    RAISE_ON_STATUS_ERROR_METADATA_KEY,
)
from orb.providers.azure.infrastructure.vmss_cleanup import VmssCleanupCoordinator
from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager
from orb.providers.azure.services.operation_parsing import (
    group_instance_ids_by_resource,
    operation_request_id,
    resolve_operation_provider_api,
    resolve_operation_resource_group,
)
from orb.providers.azure.services.resource_metadata_service import (
    AzureDeploymentStatusServiceProtocol,
    AzureResourceMetadataService,
)
from orb.providers.base.strategy import ProviderOperation, ProviderResult
from orb.providers.azure.infrastructure.error_codes import ProviderErrorEntry

if TYPE_CHECKING:
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy


# ---------------------------------------------------------------------------
# Read-side vocabulary
# ---------------------------------------------------------------------------


@dataclass
class AzureReadOperationContext:
    """Provider-owned runtime context for Azure status and describe operations."""

    operation_name: str
    request_id: str | None
    template_id: str
    request_metadata: dict[str, Any]
    cyclecloud_request_context: CycleCloudRequestContext
    provider_api: Optional[AzureProviderApi]
    provider_api_key: str | None
    resource_group: str | None
    instance_ids: list[str] = field(default_factory=list)
    resource_ids: list[str] = field(default_factory=list)
    grouped_resource_mapping: dict[str, list[str]] = field(default_factory=dict)
    direct_resource_id: str | None = None
    raise_on_status_error: bool = False


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
    if raw_provider_data:
        provider_data: AzureStatusProviderData = {}
        for key in ("vm_id", "vmss_instance_id", "node_id", "node_name", "vm_name"):
            value = raw_provider_data.get(key)
            if isinstance(value, str) and value:
                provider_data[key] = value
        if provider_data:
            normalized["provider_data"] = provider_data

    return normalized


def status_candidate_ids(result: AzureHandlerStatusResult) -> set[str]:
    """Return all plausible instance identifiers from a status result."""
    identity = normalize_status_result(result)
    provider_data = identity.get("provider_data") or {}
    candidate_ids = {
        identity.get("instance_id", ""),
        identity.get("name", ""),
        provider_data.get("vm_id", ""),
        provider_data.get("vmss_instance_id", ""),
        provider_data.get("node_id", ""),
        provider_data.get("node_name", ""),
        provider_data.get("vm_name", ""),
    }
    candidate_ids.discard("")
    return candidate_ids


def observed_status_ids(instance_details: list[AzureHandlerStatusResult]) -> set[str]:
    """Collect all candidate identifiers across a list of instance detail dicts."""
    observed_ids: set[str] = set()
    for instance in instance_details:
        observed_ids.update(status_candidate_ids(instance))
    return observed_ids


def filter_status_results(
    results: list[AzureHandlerStatusResult],
    requested_ids: list[str],
) -> list[AzureHandlerStatusResult]:
    """Return only the results whose candidate IDs overlap with the requested set."""
    requested = {str(item) for item in requested_ids}
    filtered: list[AzureHandlerStatusResult] = []
    for result in results:
        if status_candidate_ids(result) & requested:
            filtered.append(result)
    return filtered


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


def build_cyclecloud_request_metadata(
    *,
    operation: ProviderOperation,
    resource_group: Optional[str],
) -> dict[str, Any]:
    """Build the metadata dict required for CycleCloud handler calls."""
    metadata: dict[str, Any] = {"resource_group": resource_group}
    metadata_from_request = operation.parameters.get("request_metadata") or {}
    for key in CYCLECLOUD_METADATA_KEYS:
        value = metadata_from_request.get(key)
        if value not in (None, ""):
            metadata[key] = value
    return metadata


def build_read_operation_context(
    *,
    operation: ProviderOperation,
    operation_name: str,
    default_resource_group: Optional[str],
) -> AzureReadOperationContext:
    """Build the provider-owned runtime context for Azure read operations."""
    metadata: dict[str, Any] = dict(operation.parameters.get("request_metadata") or {})
    cyclecloud_request_context = CycleCloudRequestContext.from_mapping(metadata)
    provider_api = resolve_operation_provider_api(operation)
    provider_api_key = provider_api.value if provider_api is not None else None

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


# ---------------------------------------------------------------------------
# Read-side orchestrator
# ---------------------------------------------------------------------------

@runtime_checkable
class VmssResourceErrorReader(Protocol):
    """Capability interface for VMSS-level fleet error inspection."""

    async def get_vmss_resource_errors_async(
        self,
        resource_group: str,
        resource_id: str,
    ) -> list[ProviderErrorEntry]:
        """Return VMSS resource-level errors for one scale set."""
        ...


class AzureInventoryService:
    """Own Azure read/query orchestration separate from strategy lifecycle concerns."""

    def __init__(
        self,
        *,
        logger: LoggingPort,
        provider_instance_name: str,
        resource_metadata_service: AzureResourceMetadataService,
        handler_provider: AzureProviderStrategy,
        vmss_cleanup_coordinator: VmssCleanupCoordinator,
    ) -> None:
        self._logger = logger
        self._provider_instance_name = provider_instance_name
        self._resource_metadata_service = resource_metadata_service
        self._handler_provider = handler_provider
        self._vmss_cleanup_coordinator = vmss_cleanup_coordinator

    async def get_instance_status_async(
        self,
        read_context: AzureReadOperationContext,
    ) -> ProviderResult:
        """Resolve instance status through the async Azure handler contract."""
        self._vmss_cleanup_coordinator.restore_from_request_metadata(
            read_context.request_metadata
        )
        handler_machines = await self._get_instance_status_via_handlers_async(read_context)
        if handler_machines is not None:
            return await self._build_instance_status_result(
                read_context=read_context,
                handler_machines=handler_machines,
            )

        raise AzureValidationError(
            "Azure get_instance_status requires provider_api-backed handler resolution.",
            error_code="MISSING_PROVIDER_API",
        )

    async def describe_resource_instances_async(
        self,
        *,
        read_context: AzureReadOperationContext,
        resource_manager: Optional[AzureResourceManager],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
    ) -> ProviderResult:
        """Describe Azure resource-backed instances through the async handler contract."""
        resource_group = read_context.resource_group
        is_vmss = self._prepare_describe_context(read_context)
        result = await self._describe_resource_instances_via_handler_async(
            read_context=read_context,
            resource_manager=resource_manager,
            deployment_service=deployment_service,
        )

        if result.success and is_vmss and resource_group:
            instance_details = result.data.get("instances", []) if result.data else []
            await self._vmss_cleanup_coordinator.reconcile(
                resource_group=resource_group,
                resource_ids=read_context.resource_ids,
                observed_ids=observed_status_ids(instance_details),
            )
            return result.model_copy(
                update={
                    "metadata": {
                        **(result.metadata or {}),
                        **self._vmss_cleanup_coordinator.status_metadata(
                            resource_group=resource_group,
                            resource_ids=read_context.resource_ids,
                        ),
                    }
                }
            )

        return result

    async def _get_instance_status_via_handlers_async(
        self,
        read_context: AzureReadOperationContext,
    ) -> Optional[list[AzureHandlerStatusResult]]:
        provider_api = read_context.provider_api
        grouped_resource_mapping = read_context.grouped_resource_mapping

        if not provider_api:
            return None

        handler = self._resolve_status_handler(provider_api)
        if not handler and not grouped_resource_mapping:
            return None

        if provider_api == AzureProviderApi.SINGLE_VM and handler:
            request = build_read_handler_request(
                read_context=read_context,
                provider_name=self._provider_instance_name,
                resource_ids=read_context.instance_ids,
            )
            return await handler.check_hosts_status_async(request)

        if grouped_resource_mapping:
            all_results: list[AzureHandlerStatusResult] = []
            seen_instance_ids: set[str] = set()
            for resource_id, mapped_ids in grouped_resource_mapping.items():
                group_handler = handler or self._resolve_status_handler(provider_api)
                if not group_handler:
                    continue

                request = build_read_handler_request(
                    read_context=read_context,
                    provider_name=self._provider_instance_name,
                    resource_ids=[resource_id],
                    additional_metadata=self._handler_status_metadata(
                        provider_api=provider_api,
                        instance_ids=mapped_ids,
                    ),
                )
                machines = await group_handler.check_hosts_status_async(request)
                self._append_unique_status_results(
                    destination=all_results,
                    seen_instance_ids=seen_instance_ids,
                    machines=filter_status_results(machines, mapped_ids),
                )

            if all_results:
                return all_results

        resource_id = read_context.direct_resource_id
        if not handler or not resource_id:
            return None

        request = build_read_handler_request(
            read_context=read_context,
            provider_name=self._provider_instance_name,
            resource_ids=(
                read_context.instance_ids
                if provider_api == AzureProviderApi.SINGLE_VM
                else [resource_id]
            ),
            additional_metadata=self._handler_status_metadata(
                provider_api=provider_api,
                instance_ids=read_context.instance_ids,
            ),
        )
        machines = await handler.check_hosts_status_async(request)
        if provider_api == AzureProviderApi.SINGLE_VM:
            return machines
        return filter_status_results(machines, read_context.instance_ids)

    async def _describe_resource_instances_via_handler_async(
        self,
        *,
        read_context: AzureReadOperationContext,
        resource_manager: Optional[AzureResourceManager],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
    ) -> ProviderResult:
        resolved = self._resolve_describe_handler(read_context)
        if isinstance(resolved, ProviderResult):
            return resolved
        provider_api, handler = resolved

        request = self._build_describe_handler_request(read_context=read_context)
        instance_details = await handler.check_hosts_status_async(request)
        return await self._build_describe_instances_result(
            read_context=read_context,
            handler=handler,
            instance_details=instance_details,
            resource_manager=resource_manager,
            deployment_service=deployment_service,
            include_shortfall_metadata=provider_api != AzureProviderApi.CYCLECLOUD,
        )

    async def _build_instance_status_result(
        self,
        *,
        read_context: AzureReadOperationContext,
        handler_machines: list[AzureHandlerStatusResult],
    ) -> ProviderResult:
        """Build the normalized handler-backed instance-status result."""
        is_vmss = read_context.provider_api in (
            AzureProviderApi.VMSS,
            AzureProviderApi.VMSS_UNIFORM,
        )
        metadata: dict[str, Any] = {
            "operation": "get_instance_status",
            "instance_ids": read_context.instance_ids,
            "method": "handler",
        }
        status_result = self._resource_metadata_service.attach_provider_fulfilment(
            metadata,
            instances=handler_machines,
            target_units=len(read_context.instance_ids),
        )
        result = ProviderResult.success_result(
            {
                "instances": status_result.instances,
                "queried_count": len(read_context.instance_ids),
            },
            metadata,
        )
        if is_vmss and read_context.resource_group and read_context.resource_ids:
            await self._vmss_cleanup_coordinator.reconcile(
                resource_group=read_context.resource_group,
                resource_ids=read_context.resource_ids,
                observed_ids=observed_status_ids(handler_machines),
            )
            return result.model_copy(
                update={
                    "metadata": {
                        **(result.metadata or {}),
                        **self._vmss_cleanup_coordinator.status_metadata(
                            resource_group=read_context.resource_group,
                            resource_ids=read_context.resource_ids,
                        ),
                    }
                }
            )
        return result

    def _prepare_describe_context(
        self,
        read_context: AzureReadOperationContext,
    ) -> bool:
        """Restore VMSS cleanup state and set the describe status failure policy."""
        is_vmss = read_context.provider_api in (
            AzureProviderApi.VMSS,
            AzureProviderApi.VMSS_UNIFORM,
        )
        self._vmss_cleanup_coordinator.restore_from_request_metadata(
            read_context.request_metadata
        )
        read_context.raise_on_status_error = (
            is_vmss
            and self._vmss_cleanup_coordinator.has_pending(
                resource_group=read_context.resource_group,
                resource_ids=read_context.resource_ids,
            )
        )
        return is_vmss

    def _build_describe_handler_request(
        self,
        *,
        read_context: AzureReadOperationContext,
    ) -> Request:
        """Build the handler request used for Azure resource-instance discovery."""
        extra_metadata: dict[str, Any] = {}
        if read_context.provider_api == AzureProviderApi.SINGLE_VM:
            deployment_name = read_context.request_metadata.get("deployment_name")
            if deployment_name not in (None, ""):
                extra_metadata["deployment_name"] = str(deployment_name)
        extra_metadata[RAISE_ON_STATUS_ERROR_METADATA_KEY] = (
            read_context.raise_on_status_error
        )
        return build_read_handler_request(
            read_context=read_context,
            provider_name=self._provider_instance_name,
            resource_ids=read_context.resource_ids,
            additional_metadata=extra_metadata or None,
        )

    @staticmethod
    def _append_unique_status_results(
        *,
        destination: list[AzureHandlerStatusResult],
        seen_instance_ids: set[str],
        machines: list[AzureHandlerStatusResult],
    ) -> None:
        """Append status results while preserving first-seen instance identities."""
        for machine in machines:
            identity = normalize_status_result(machine)
            provider_data = identity.get("provider_data") or {}
            machine_id = next(
                (
                    value
                    for value in (
                        identity.get("instance_id"),
                        identity.get("name"),
                        provider_data.get("vm_id"),
                        provider_data.get("vmss_instance_id"),
                        provider_data.get("node_id"),
                        provider_data.get("node_name"),
                        provider_data.get("vm_name"),
                    )
                    if value
                ),
                "",
            )
            if machine_id and machine_id in seen_instance_ids:
                continue
            destination.append(machine)
            if machine_id:
                seen_instance_ids.add(machine_id)

    @staticmethod
    def _handler_status_metadata(
        *,
        provider_api: AzureProviderApi,
        instance_ids: list[str],
    ) -> dict[str, Any] | None:
        """Return provider-specific handler metadata for status requests."""
        if provider_api == AzureProviderApi.CYCLECLOUD:
            return {"node_ids": instance_ids}
        return None

    def _resolve_status_handler(
        self,
        provider_api: AzureProviderApi,
    ) -> Optional[AzureHandler]:
        """Resolve a handler for status operations with VMSS uniform fallback enabled."""
        return self._handler_provider.resolve_handler(
            provider_api,
            allow_vmss_uniform_fallback=True,
        )

    def _resolve_describe_handler(
        self,
        read_context: AzureReadOperationContext,
    ) -> ProviderResult | tuple[AzureProviderApi, AzureHandler]:
        """Resolve the concrete handler for describe operations or return a failure result."""
        provider_api = read_context.provider_api
        provider_api_key = read_context.provider_api_key or ""
        if provider_api is None:
            return ProviderResult.error_result(
                "provider_api is required for Azure resource discovery",
                "MISSING_PROVIDER_API",
            )

        handler = self._handler_provider.resolve_handler(provider_api)
        if not handler:
            return ProviderResult.error_result(
                f"No handler available for provider_api: {provider_api_key}",
                "HANDLER_NOT_FOUND",
            )
        return provider_api, handler

    @staticmethod
    def _collect_instance_fleet_errors(instance_details: list[AzureHandlerStatusResult]) -> list[dict[str, Any]]:
        """Collect distinct fleet errors embedded in handler provider data."""
        fleet_errors: list[dict[str, Any]] = []
        for inst in instance_details:
            provider_data = inst.get("provider_data") or {}
            for error in provider_data.get("fleet_errors") or []:
                if error not in fleet_errors:
                    fleet_errors.append(error)
        return fleet_errors

    @staticmethod
    async def _get_optional_vmss_resource_errors(
        handler: AzureHandler,
        logger: LoggingPort,
        *,
        resource_group: str | None,
        resource_ids: list[str],
    ) -> list[ProviderErrorEntry]:
        """Read VMSS resource-level errors when the concrete handler exposes them."""
        vmss_errors: list[ProviderErrorEntry] = []
        if not resource_group:
            return vmss_errors
        if not isinstance(handler, VmssResourceErrorReader):
            logger.warning(
                "VMSS resource error lookup requested from handler '%s' without VMSS error support",
                type(handler).__name__,
            )
            return vmss_errors
        for resource_id in resource_ids:
            raw_errors = await handler.get_vmss_resource_errors_async(
                resource_group,
                resource_id,
            )
            for error in raw_errors:
                if error not in vmss_errors:
                    vmss_errors.append(error)
        return vmss_errors

    async def _build_describe_instances_result(
        self,
        *,
        read_context: AzureReadOperationContext,
        handler: AzureHandler,
        instance_details: list[AzureHandlerStatusResult],
        resource_manager: Optional[AzureResourceManager],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
        include_shortfall_metadata: bool,
    ) -> ProviderResult:
        """Build the normalized describe-resource-instances result and metadata."""
        provider_api = read_context.provider_api
        provider_api_key = read_context.provider_api_key or ""
        resource_ids = read_context.resource_ids
        resource_group = read_context.resource_group
        metadata: dict[str, Any] = {
            "operation": "describe_resource_instances",
            "resource_ids": resource_ids,
            "provider_api": provider_api_key,
            "handler_used": provider_api_key,
            "instance_count": len(instance_details),
        }

        if not instance_details:
            if provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM):
                vmss_errors = await self._get_optional_vmss_resource_errors(
                    handler,
                    self._logger,
                    resource_group=resource_group,
                    resource_ids=resource_ids,
                )
                if vmss_errors:
                    metadata["fleet_errors"] = vmss_errors
                await self._resource_metadata_service.augment_vmss_capacity_metadata_async(
                    metadata,
                    resource_ids,
                    resource_manager=resource_manager,
                    resource_group=resource_group,
                )
            elif provider_api == AzureProviderApi.SINGLE_VM:
                await self._resource_metadata_service.augment_single_vm_deployment_metadata_async(
                    metadata,
                    read_context.request_metadata,
                    resource_group=resource_group,
                    deployment_service=deployment_service,
                )
            status_result = self._resource_metadata_service.attach_provider_fulfilment(
                metadata,
                instances=[],
                target_units=(
                    len(resource_ids)
                    if provider_api == AzureProviderApi.SINGLE_VM
                    else None
                ),
            )
            return ProviderResult.success_result(
                {"instances": status_result.instances},
                metadata,
            )

        fleet_errors = self._collect_instance_fleet_errors(instance_details)
        if fleet_errors:
            metadata["fleet_errors"] = fleet_errors
        if provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM):
            await self._resource_metadata_service.augment_vmss_capacity_metadata_async(
                metadata,
                resource_ids,
                resource_manager=resource_manager,
                resource_group=resource_group,
            )
        if include_shortfall_metadata:
            self._resource_metadata_service.augment_shortfall_metadata(metadata)
        status_result = self._resource_metadata_service.attach_provider_fulfilment(
            metadata,
            instances=instance_details,
            target_units=(
                len(resource_ids)
                if provider_api == AzureProviderApi.SINGLE_VM
                else None
            ),
        )
        return ProviderResult.success_result(
            data={"instances": status_result.instances},
            metadata=metadata,
        )
