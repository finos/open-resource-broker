"""Azure read/query orchestration for status and resource discovery."""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureHandler,
    AzureHandlerStatusResult,
    RAISE_ON_STATUS_ERROR_METADATA_KEY,
)
from orb.providers.azure.infrastructure.vmss_cleanup import VmssCleanupCoordinator
from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager
from orb.providers.azure.services.resource_metadata_service import (
    AzureDeploymentStatusServiceProtocol,
)
from orb.providers.azure.services.inventory_service import (
    AzureReadOperationContext,
    AzureStatusResult,
    build_read_handler_request,
    filter_status_results,
    normalize_status_results,
    observed_status_ids,
)
from orb.providers.infrastructure.error_codes import ProviderErrorEntry
from orb.providers.base.strategy import ProviderResult

class AzureResourceMetadataServiceProtocol(Protocol):
    """Structural subset used to enrich Azure read metadata."""

    async def augment_vmss_capacity_metadata_async(
        self,
        metadata: dict[str, Any],
        resource_ids: list[str],
        *,
        resource_manager: Optional[AzureResourceManager],
        resource_group: Optional[str],
    ) -> None:
        """Attach VMSS capacity/provisioning metadata to the result asynchronously."""
        ...

    async def augment_single_vm_deployment_metadata_async(
        self,
        metadata: dict[str, Any],
        request_metadata: dict[str, Any],
        *,
        resource_group: Optional[str],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
    ) -> None:
        """Attach single-VM ARM deployment metadata to the result asynchronously."""
        ...

    def augment_shortfall_metadata(self, metadata: dict[str, Any]) -> None:
        """Attach shortfall summary metadata when fleet errors indicate capacity gaps."""
        ...


class ResolveAzureHandler(Protocol):
    """Callable protocol for resolving one Azure handler from a provider API."""

    def __call__(
        self,
        provider_api: AzureProviderApi,
        *,
        allow_vmss_uniform_fallback: bool = False,
    ) -> Optional[AzureHandler]:
        """Resolve a handler for the given Azure provider API."""


@runtime_checkable
class VmssResourceErrorReader(Protocol):
    """Capability interface for VMSS-level fleet error inspection."""

    async def get_vmss_resource_errors_async(
        self,
        resource_group: str,
        resource_id: str,
    ) -> list[ProviderErrorEntry]:
        """Return VMSS resource-level errors for one scale set."""


class AzureInventoryQueryService:
    """Own Azure read/query orchestration separate from strategy lifecycle concerns."""

    def __init__(
        self,
        *,
        logger: LoggingPort,
        provider_instance_name: str,
        resource_metadata_service: AzureResourceMetadataServiceProtocol,
    ) -> None:
        self._logger = logger
        self._provider_instance_name = provider_instance_name
        self._resource_metadata_service = resource_metadata_service

    async def get_instance_status_async(
        self,
        *,
        read_context: AzureReadOperationContext,
        resolve_handler: ResolveAzureHandler,
        vmss_cleanup_coordinator: VmssCleanupCoordinator,
    ) -> ProviderResult:
        """Resolve instance status through the async Azure handler contract."""
        vmss_cleanup_coordinator.restore_from_request_metadata(read_context.request_metadata)
        handler_machines = await self._get_instance_status_via_handlers_async(
            read_context=read_context,
            resolve_handler=resolve_handler,
        )
        if handler_machines is not None:
            return await self._build_instance_status_result(
                read_context=read_context,
                handler_machines=handler_machines,
                vmss_cleanup_coordinator=vmss_cleanup_coordinator,
            )

        raise AzureValidationError(
            "Azure get_instance_status requires provider_api-backed handler resolution.",
            error_code="MISSING_PROVIDER_API",
        )

    async def describe_resource_instances_async(
        self,
        *,
        read_context: AzureReadOperationContext,
        resolve_handler: ResolveAzureHandler,
        vmss_cleanup_coordinator: VmssCleanupCoordinator,
        resource_manager: Optional[AzureResourceManager],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
    ) -> ProviderResult:
        """Describe Azure resource-backed instances through the async handler contract."""
        resource_group = read_context.resource_group
        is_vmss = self._prepare_describe_context(
            read_context=read_context,
            vmss_cleanup_coordinator=vmss_cleanup_coordinator,
        )
        result = await self._describe_resource_instances_via_handler_async(
            read_context=read_context,
            resolve_handler=resolve_handler,
            resource_manager=resource_manager,
            deployment_service=deployment_service,
        )

        if result.success and is_vmss:
            instance_details = result.data.get("instances", []) if result.data else []
            if resource_group:
                await vmss_cleanup_coordinator.reconcile(
                    resource_group=resource_group,
                    resource_ids=read_context.resource_ids,
                    observed_ids=observed_status_ids(instance_details),
                )
                if result.metadata is not None:
                    result.metadata.update(
                        vmss_cleanup_coordinator.status_metadata(
                            resource_group=resource_group,
                            resource_ids=read_context.resource_ids,
                        )
                    )

        return result

    async def _get_instance_status_via_handlers_async(
        self,
        *,
        read_context: AzureReadOperationContext,
        resolve_handler: ResolveAzureHandler,
    ) -> Optional[list[AzureStatusResult]]:
        provider_api = read_context.provider_api
        grouped_resource_mapping = read_context.grouped_resource_mapping

        if not provider_api:
            return None

        handler = self._resolve_status_handler(
            provider_api=provider_api,
            resolve_handler=resolve_handler,
        )
        if not handler and not grouped_resource_mapping:
            return None

        if provider_api == AzureProviderApi.SINGLE_VM and handler:
            request = build_read_handler_request(
                read_context=read_context,
                provider_name=self._provider_instance_name,
                resource_ids=read_context.instance_ids,
            )
            return normalize_status_results(
                await handler.check_hosts_status_async(request)
            )

        if grouped_resource_mapping:
            all_results: list[AzureStatusResult] = []
            seen_instance_ids: set[str] = set()
            for resource_id, mapped_ids in grouped_resource_mapping.items():
                group_handler = handler or self._resolve_status_handler(
                    provider_api=provider_api,
                    resolve_handler=resolve_handler,
                )
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
                machines = normalize_status_results(
                    await group_handler.check_hosts_status_async(request)
                )
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
        machines = normalize_status_results(
            await handler.check_hosts_status_async(request)
        )
        if provider_api == AzureProviderApi.SINGLE_VM:
            return machines
        return filter_status_results(machines, read_context.instance_ids)

    async def _describe_resource_instances_via_handler_async(
        self,
        *,
        read_context: AzureReadOperationContext,
        resolve_handler: ResolveAzureHandler,
        resource_manager: Optional[AzureResourceManager],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
    ) -> ProviderResult:
        resolved = self._resolve_describe_handler(
            read_context=read_context,
            resolve_handler=resolve_handler,
        )
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
        handler_machines: list[AzureStatusResult],
        vmss_cleanup_coordinator: VmssCleanupCoordinator,
    ) -> ProviderResult:
        """Build the normalized handler-backed instance-status result."""
        is_vmss = read_context.provider_api in (
            AzureProviderApi.VMSS,
            AzureProviderApi.VMSS_UNIFORM,
        )
        result = ProviderResult.success_result(
            {
                "instances": handler_machines,
                "queried_count": len(read_context.instance_ids),
            },
            {
                "operation": "get_instance_status",
                "instance_ids": read_context.instance_ids,
                "method": "handler",
            },
        )
        if is_vmss and read_context.resource_group and read_context.resource_ids:
            await vmss_cleanup_coordinator.reconcile(
                resource_group=read_context.resource_group,
                resource_ids=read_context.resource_ids,
                observed_ids=observed_status_ids(handler_machines),
            )
            result.metadata.update(
                vmss_cleanup_coordinator.status_metadata(
                    resource_group=read_context.resource_group,
                    resource_ids=read_context.resource_ids,
                )
            )
        return result

    @staticmethod
    def _prepare_describe_context(
        *,
        read_context: AzureReadOperationContext,
        vmss_cleanup_coordinator: VmssCleanupCoordinator,
    ) -> bool:
        """Restore VMSS cleanup state and set the describe status failure policy."""
        is_vmss = read_context.provider_api in (
            AzureProviderApi.VMSS,
            AzureProviderApi.VMSS_UNIFORM,
        )
        vmss_cleanup_coordinator.restore_from_request_metadata(read_context.request_metadata)
        read_context.raise_on_status_error = (
            is_vmss
            and vmss_cleanup_coordinator.has_pending(
                resource_group=read_context.resource_group,
                resource_ids=read_context.resource_ids,
            )
        )
        return is_vmss

    def _build_describe_handler_request(
        self,
        *,
        read_context: AzureReadOperationContext,
    ) -> Any:
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
        destination: list[AzureStatusResult],
        seen_instance_ids: set[str],
        machines: list[AzureStatusResult],
    ) -> None:
        """Append status results while preserving first-seen instance identities."""
        for machine in machines:
            machine_id = str(machine.get("instance_id"))
            if machine_id in seen_instance_ids:
                continue
            destination.append(machine)
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

    @staticmethod
    def _resolve_status_handler(
        *,
        provider_api: AzureProviderApi,
        resolve_handler: ResolveAzureHandler,
    ) -> Optional[AzureHandler]:
        """Resolve a handler for status operations with VMSS uniform fallback enabled."""
        return resolve_handler(
            provider_api,
            allow_vmss_uniform_fallback=True,
        )

    def _resolve_describe_handler(
        self,
        *,
        read_context: AzureReadOperationContext,
        resolve_handler: ResolveAzureHandler,
    ) -> ProviderResult | tuple[AzureProviderApi, AzureHandler]:
        """Resolve the concrete handler for describe operations or return a failure result."""
        provider_api = read_context.provider_api
        provider_api_key = read_context.provider_api_key or ""
        if provider_api is None:
            return ProviderResult.error_result(
                "provider_api is required for Azure resource discovery",
                "MISSING_PROVIDER_API",
            )

        handler = resolve_handler(provider_api)
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
            return ProviderResult.success_result({"instances": []}, metadata)

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
        return ProviderResult.success_result(
            data={"instances": instance_details},
            metadata=metadata,
        )
