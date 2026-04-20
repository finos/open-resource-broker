"""Azure read/query orchestration for status and resource discovery."""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler
from orb.providers.azure.infrastructure.vmss_cleanup import VmssCleanupCoordinator
from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager
from orb.providers.azure.services.resource_metadata_service import (
    AzureDeploymentStatusServiceProtocol,
)
from orb.providers.azure.services.inventory_service import (
    AzureReadOperationContext,
    AzureStatusQueryContext,
    AzureStatusResult,
    build_read_handler_request,
    filter_status_results,
    normalize_status_results,
    observed_status_ids,
    sdk_status_result,
)
from orb.providers.base.strategy import ProviderResult


class AzureMachineConversionServiceProtocol(Protocol):
    """Structural subset used by SDK status fallback."""

    def convert_sdk_vm(self, vm: object, azure_client: AzureClient) -> dict[str, Any]:
        """Convert an SDK VM object into the normalized machine/result shape."""
        ...


class AzureResourceMetadataServiceProtocol(Protocol):
    """Structural subset used to enrich Azure read metadata."""

    def augment_vmss_capacity_metadata(
        self,
        metadata: dict[str, Any],
        resource_ids: list[str],
        *,
        resource_manager: Optional[AzureResourceManager],
        resource_group: Optional[str],
    ) -> None:
        """Attach VMSS capacity/provisioning metadata to the result."""
        ...

    def augment_single_vm_deployment_metadata(
        self,
        metadata: dict[str, Any],
        request_metadata: dict[str, Any],
        *,
        resource_group: Optional[str],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
    ) -> None:
        """Attach single-VM ARM deployment metadata to the result."""
        ...

    def augment_shortfall_metadata(self, metadata: dict[str, Any]) -> None:
        """Attach shortfall summary metadata when fleet errors indicate capacity gaps."""
        ...


ResolveAzureHandler = Callable[..., Optional[AzureHandler]]


class AzureInventoryQueryService:
    """Own Azure read/query orchestration separate from strategy lifecycle concerns."""

    def __init__(
        self,
        *,
        logger: LoggingPort,
        provider_instance_name: str,
        machine_conversion_service: AzureMachineConversionServiceProtocol,
        resource_metadata_service: AzureResourceMetadataServiceProtocol,
    ) -> None:
        self._logger = logger
        self._provider_instance_name = provider_instance_name
        self._machine_conversion_service = machine_conversion_service
        self._resource_metadata_service = resource_metadata_service

    def get_instance_status(
        self,
        *,
        read_context: AzureReadOperationContext,
        azure_client: Optional[AzureClient],
        resolve_handler: ResolveAzureHandler,
        vmss_cleanup_coordinator: VmssCleanupCoordinator,
    ) -> ProviderResult:
        """Resolve instance status through handlers first, then SDK fallback."""
        vmss_cleanup_coordinator.restore_from_request_metadata(read_context.request_metadata)

        handler_machines = self._get_instance_status_via_handlers(
            read_context=read_context,
            resolve_handler=resolve_handler,
        )
        if handler_machines is not None:
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
                vmss_cleanup_coordinator.reconcile(
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

        status_context = AzureStatusQueryContext(
            instance_ids=read_context.instance_ids,
            resource_group=read_context.resource_group or "",
            provider_api=read_context.provider_api,
        )
        return sdk_status_result(
            status_context=status_context,
            azure_client=azure_client,
            machine_conversion_service=self._machine_conversion_service,
            logger=self._logger,
        )

    def describe_resource_instances(
        self,
        *,
        read_context: AzureReadOperationContext,
        resolve_handler: ResolveAzureHandler,
        vmss_cleanup_coordinator: VmssCleanupCoordinator,
        resource_manager: Optional[AzureResourceManager],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
    ) -> ProviderResult:
        """Describe Azure resource-backed instances and enrich the metadata."""
        provider_api = read_context.provider_api
        resource_group = read_context.resource_group
        is_vmss = provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM)

        vmss_cleanup_coordinator.restore_from_request_metadata(read_context.request_metadata)
        read_context.fail_on_partial_status_error = bool(
            is_vmss
            and vmss_cleanup_coordinator.has_pending(
                resource_group=resource_group,
                resource_ids=read_context.resource_ids,
            )
        )

        result = self._describe_resource_instances_via_handler(
            read_context=read_context,
            resolve_handler=resolve_handler,
            resource_manager=resource_manager,
            deployment_service=deployment_service,
        )

        if result.success and is_vmss and resource_group:
            instance_details = result.data.get("instances", []) if result.data else []
            vmss_cleanup_coordinator.reconcile(
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

    def _get_instance_status_via_handlers(
        self,
        *,
        read_context: AzureReadOperationContext,
        resolve_handler: ResolveAzureHandler,
    ) -> Optional[list[AzureStatusResult]]:
        provider_api = read_context.provider_api
        grouped_resource_mapping = read_context.grouped_resource_mapping

        if not provider_api:
            return None

        handler = resolve_handler(
            provider_api,
            allow_vmss_uniform_fallback=True,
        )
        if not handler and not grouped_resource_mapping:
            return None

        if provider_api == AzureProviderApi.SINGLE_VM and handler:
            return normalize_status_results(
                handler.check_hosts_status(
                    build_read_handler_request(
                        read_context=read_context,
                        provider_name=self._provider_instance_name,
                        resource_ids=read_context.instance_ids,
                    )
                ),
            )

        if grouped_resource_mapping:
            all_results: list[AzureStatusResult] = []
            seen_instance_ids: set[str] = set()
            for resource_id, mapped_ids in grouped_resource_mapping.items():
                group_handler = handler
                if not group_handler and provider_api:
                    group_handler = resolve_handler(
                        provider_api,
                        allow_vmss_uniform_fallback=True,
                    )
                if not group_handler:
                    continue

                extra_metadata: dict[str, Any] = {}
                if provider_api == AzureProviderApi.CYCLECLOUD:
                    extra_metadata["node_ids"] = mapped_ids
                request = build_read_handler_request(
                    read_context=read_context,
                    provider_name=self._provider_instance_name,
                    resource_ids=[resource_id],
                    additional_metadata=extra_metadata,
                )
                for machine in filter_status_results(
                    normalize_status_results(group_handler.check_hosts_status(request)),
                    mapped_ids,
                ):
                    machine_id = str(machine.get("instance_id"))
                    if machine_id not in seen_instance_ids:
                        all_results.append(machine)
                        seen_instance_ids.add(machine_id)

            if all_results:
                return all_results

        resource_id = read_context.direct_resource_id
        if not handler or not resource_id:
            return None

        extra_metadata: dict[str, Any] = {}
        if provider_api == AzureProviderApi.CYCLECLOUD:
            extra_metadata["node_ids"] = read_context.instance_ids
        request = build_read_handler_request(
            read_context=read_context,
            provider_name=self._provider_instance_name,
            resource_ids=(
                read_context.instance_ids
                if provider_api == AzureProviderApi.SINGLE_VM
                else [resource_id]
            ),
            additional_metadata=extra_metadata,
        )
        if provider_api == AzureProviderApi.SINGLE_VM:
            return normalize_status_results(handler.check_hosts_status(request))
        return filter_status_results(
            normalize_status_results(handler.check_hosts_status(request)),
            read_context.instance_ids,
        )

    def _describe_resource_instances_via_handler(
        self,
        *,
        read_context: AzureReadOperationContext,
        resolve_handler: ResolveAzureHandler,
        resource_manager: Optional[AzureResourceManager],
        deployment_service: AzureDeploymentStatusServiceProtocol | None,
    ) -> ProviderResult:
        resource_ids = read_context.resource_ids
        provider_api = read_context.provider_api
        provider_api_key = read_context.provider_api_key or ""
        resource_group = read_context.resource_group

        handler = resolve_handler(provider_api_key)
        if not handler:
            return ProviderResult.error_result(
                f"No handler available for provider_api: {provider_api_key}",
                "HANDLER_NOT_FOUND",
            )

        extra_metadata: dict[str, Any] = {}
        if provider_api == AzureProviderApi.SINGLE_VM:
            deployment_name = read_context.request_metadata.get("deployment_name")
            if deployment_name not in (None, ""):
                extra_metadata["deployment_name"] = str(deployment_name)
        if read_context.fail_on_partial_status_error:
            extra_metadata["fail_on_partial_status_error"] = True

        request = build_read_handler_request(
            read_context=read_context,
            provider_name=self._provider_instance_name,
            resource_ids=resource_ids,
            additional_metadata=extra_metadata or None,
        )

        instance_details = handler.check_hosts_status(request)

        if not instance_details:
            metadata: dict[str, Any] = {
                "operation": "describe_resource_instances",
                "resource_ids": resource_ids,
                "provider_api": provider_api_key,
                "handler_used": provider_api_key,
                "instance_count": 0,
            }
            if provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM):
                vmss_errors: list[dict[str, Any]] = []
                # getattr: this VMSS-only helper is optional on the handler surface and
                # may be provided by test doubles rather than the concrete VMSS class.
                get_vmss_resource_errors = getattr(handler, "get_vmss_resource_errors", None)
                if resource_group and callable(get_vmss_resource_errors):
                    for resource_id in resource_ids:
                        raw_errors = get_vmss_resource_errors(resource_group, resource_id)
                        if isinstance(raw_errors, list):
                            for error in raw_errors:
                                if isinstance(error, dict) and error not in vmss_errors:
                                    vmss_errors.append(error)
                if vmss_errors:
                    metadata["fleet_errors"] = vmss_errors
                self._resource_metadata_service.augment_vmss_capacity_metadata(
                    metadata,
                    resource_ids,
                    resource_manager=resource_manager,
                    resource_group=resource_group,
                )
            elif provider_api == AzureProviderApi.SINGLE_VM:
                self._resource_metadata_service.augment_single_vm_deployment_metadata(
                    metadata,
                    read_context.request_metadata,
                    resource_group=resource_group,
                    deployment_service=deployment_service,
                )
            return ProviderResult.success_result({"instances": []}, metadata)

        fleet_errors: list[dict[str, Any]] = []
        for inst in instance_details:
            provider_data = inst.get("provider_data") or {}
            if isinstance(provider_data, dict):
                for error in provider_data.get("fleet_errors") or []:
                    if error not in fleet_errors:
                        fleet_errors.append(error)

        metadata: dict[str, Any] = {
            "operation": "describe_resource_instances",
            "resource_ids": resource_ids,
            "provider_api": provider_api_key,
            "handler_used": provider_api_key,
            "instance_count": len(instance_details),
        }
        if fleet_errors:
            metadata["fleet_errors"] = fleet_errors

        if provider_api in (AzureProviderApi.VMSS, AzureProviderApi.VMSS_UNIFORM):
            self._resource_metadata_service.augment_vmss_capacity_metadata(
                metadata,
                resource_ids,
                resource_manager=resource_manager,
                resource_group=resource_group,
            )

        self._resource_metadata_service.augment_shortfall_metadata(metadata)

        return ProviderResult.success_result(
            data={"instances": instance_details},
            metadata=metadata,
        )
