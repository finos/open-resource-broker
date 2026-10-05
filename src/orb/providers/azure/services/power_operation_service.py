"""Azure start/stop-instance orchestration.

Mirrors AWS's ``START_INSTANCES``/``STOP_INSTANCES`` operations: a batch of
machine IDs is grouped by the Azure resource (VMSS or SingleVM) and handler
that owns each one, then dispatched concurrently, returning a per-machine
success map so a failure on one resource does not fail the whole batch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureReleaseContext
from orb.providers.azure.services.handler_resolver import AzureHandlerResolver
from orb.providers.base.strategy import ProviderOperation, ProviderResult


@dataclass(frozen=True)
class _PowerOperationGroup:
    """One Azure resource's worth of machines to start/stop together."""

    provider_api: AzureProviderApi
    resource_id: str
    resource_group: str
    machine_ids: list[str]


def _build_power_operation_groups(
    operation: ProviderOperation,
    *,
    default_resource_group: Optional[str],
) -> list[_PowerOperationGroup]:
    """Group requested machine IDs by (provider_api, resource_id, resource_group).

    Each machine's own persisted coordinates are used rather than a single
    operation-wide value, because one start/stop batch can span multiple
    VMSS/SingleVM resources (see ``StartMachinesOrchestrator``/
    ``StopMachinesOrchestrator``, which supply ``machine_coordinates`` keyed
    by machine ID).
    """
    instance_ids: list[str] = list(operation.parameters.get("instance_ids", []) or [])
    if not instance_ids:
        raise AzureValidationError(
            "Instance IDs are required for this operation",
            error_code="MISSING_INSTANCE_IDS",
        )

    machine_coordinates: dict[str, dict[str, Any]] = (
        operation.parameters.get("machine_coordinates") or {}
    )

    grouped_ids: dict[tuple[str, str, str], list[str]] = {}
    group_order: list[tuple[str, str, str]] = []
    for machine_id in instance_ids:
        coordinates = machine_coordinates.get(machine_id) or {}
        provider_api_raw = str(coordinates.get("provider_api") or "")
        resource_id = str(coordinates.get("resource_id") or "")
        provider_data = coordinates.get("provider_data") or {}
        resource_group = str(provider_data.get("resource_group") or default_resource_group or "")

        if not provider_api_raw or not resource_id or not resource_group:
            raise AzureValidationError(
                "Missing Azure coordinates (provider_api/resource_id/resource_group) "
                f"for machine '{machine_id}'",
                error_code="MISSING_MACHINE_COORDINATES",
            )

        key = (provider_api_raw, resource_id, resource_group)
        if key not in grouped_ids:
            grouped_ids[key] = []
            group_order.append(key)
        grouped_ids[key].append(machine_id)

    groups: list[_PowerOperationGroup] = []
    for key in group_order:
        provider_api_raw, resource_id, resource_group = key
        try:
            provider_api = AzureProviderApi(provider_api_raw)
        except ValueError as exc:
            raise AzureValidationError(
                f"Invalid Azure provider_api: {provider_api_raw!r}",
                error_code="INVALID_PROVIDER_API",
            ) from exc
        groups.append(
            _PowerOperationGroup(
                provider_api=provider_api,
                resource_id=resource_id,
                resource_group=resource_group,
                machine_ids=grouped_ids[key],
            )
        )
    return groups


class AzurePowerOperationService:
    """Own Azure start/stop-instance dispatch across VMSS/SingleVM resources."""

    def __init__(
        self,
        *,
        logger: LoggingPort,
        handler_provider: AzureHandlerResolver,
        default_resource_group: Optional[str],
    ) -> None:
        self._logger = logger
        self._handler_provider = handler_provider
        self._default_resource_group = default_resource_group

    async def start_instances_async(self, operation: ProviderOperation) -> ProviderResult:
        """Power on stopped/deallocated machines; returns a per-machine success map."""
        return await self._dispatch_async(operation, operation_name="start_instances")

    async def stop_instances_async(self, operation: ProviderOperation) -> ProviderResult:
        """Power off running machines; deallocates (stops billing) by default.

        ``deallocate`` can be explicitly disabled via
        ``operation.parameters["deallocate"] = False`` to request a plain
        power-off that keeps the compute allocation (and its billing) in
        place instead.
        """
        deallocate = bool(operation.parameters.get("deallocate", True))
        return await self._dispatch_async(
            operation,
            operation_name="stop_instances",
            deallocate=deallocate,
        )

    async def _dispatch_async(
        self,
        operation: ProviderOperation,
        *,
        operation_name: str,
        deallocate: bool = True,
    ) -> ProviderResult:
        try:
            groups = _build_power_operation_groups(
                operation,
                default_resource_group=self._default_resource_group,
            )
        except AzureValidationError as exc:
            return ProviderResult.error_result(
                str(exc), exc.error_code, {"operation": operation_name}
            )

        group_results = await asyncio.gather(
            *(
                self._run_group_async(
                    group,
                    operation_name=operation_name,
                    deallocate=deallocate,
                )
                for group in groups
            )
        )
        merged_results: dict[str, bool] = {}
        for result in group_results:
            merged_results.update(result)

        return ProviderResult.success_result(
            {"results": merged_results},
            {"operation": operation_name},
        )

    async def _run_group_async(
        self,
        group: _PowerOperationGroup,
        *,
        operation_name: str,
        deallocate: bool,
    ) -> dict[str, bool]:
        handler = self._handler_provider.resolve_handler(group.provider_api)
        if handler is None:
            self._logger.error(
                "Azure %s: no handler available for provider_api '%s'",
                operation_name,
                group.provider_api.value,
            )
            return {machine_id: False for machine_id in group.machine_ids}

        context = AzureReleaseContext(
            resource_group=group.resource_group,
            resource_id=group.resource_id,
        )
        try:
            if operation_name == "start_instances":
                return await handler.start_hosts_async(
                    machine_ids=group.machine_ids,
                    resource_id=group.resource_id,
                    context=context,
                )
            return await handler.stop_hosts_async(
                machine_ids=group.machine_ids,
                resource_id=group.resource_id,
                context=context,
                deallocate=deallocate,
            )
        except Exception as exc:
            # A handler-level failure (including the base AzureHandler's
            # "unsupported for this provider_api" rejection, e.g. CycleCloud)
            # fails every machine in this group rather than the whole batch.
            self._logger.error(
                "Azure %s dispatch failed for resource '%s' (%s): %s",
                operation_name,
                group.resource_id,
                group.provider_api.value,
                exc,
                exc_info=True,
            )
            return {machine_id: False for machine_id in group.machine_ids}
