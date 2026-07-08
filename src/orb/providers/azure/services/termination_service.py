"""Azure terminate-instance orchestration."""

from __future__ import annotations

import asyncio
import builtins
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions import AzureValidationError
from orb.providers.azure.infrastructure.cyclecloud_session import CycleCloudRequestContext
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureHandler,
    AzureReleaseContext,
    AzureReleaseHostsResult,
    AzureReleaseProviderData,
)
from orb.providers.azure.services.operation_parsing import (
    group_instance_ids_by_resource,
    resolve_operation_provider_api,
    resolve_operation_resource_group,
)
from orb.providers.base.strategy import ProviderOperation, ProviderResult

if TYPE_CHECKING:
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy


@dataclass
class _TerminationOperationContext:
    """Resolved parameters needed to execute a termination dispatch."""

    instance_ids: list[str]
    grouped_resource_mapping: dict[str, list[str]]
    release_context: AzureReleaseContext
    handler: AzureHandler
    default_resource_id: str


@dataclass(frozen=True)
class _TerminationDispatchFailure:
    """One failed resource-group termination dispatch."""

    resource_id: str
    instance_ids: list[str]
    error_message: str
    error_type: str


@dataclass(frozen=True)
class _TerminationDispatchResult:
    """Outcome of dispatching termination across one or more Azure resources."""

    provider_data: list[AzureReleaseProviderData]
    successful_instance_ids: list[str]
    failures: list[_TerminationDispatchFailure]


async def _dispatch_release_groups_async(
    *,
    handler: AzureHandler,
    instance_ids: list[str],
    grouped_resource_mapping: dict[str, list[str]],
    default_resource_id: str,
    context: AzureReleaseContext,
    logger: LoggingPort,
    record_pending_cleanup: Callable[[AzureReleaseHostsResult | None], None],
) -> _TerminationDispatchResult:
    """Fan out async release_hosts calls per resource and collect provider data.

    Module-level so the existing fan-out tests can exercise this behavior in
    isolation without constructing the full ``AzureTerminationService``.
    """
    termination_provider_data: list[AzureReleaseProviderData] = []
    successful_instance_ids: list[str] = []
    dispatch_failures: list[Exception] = []
    failure_details: list[_TerminationDispatchFailure] = []

    dispatch_groups = grouped_resource_mapping or {default_resource_id: instance_ids}
    dispatch_group_items = list(dispatch_groups.items())
    handler_results = await asyncio.gather(
        *[
            handler.release_hosts_async(
                machine_ids=mapped_instance_ids,
                resource_id=resource_id,
                context=context,
            )
            for resource_id, mapped_instance_ids in dispatch_group_items
        ],
        return_exceptions=True,
    )
    for (resource_id, mapped_instance_ids), handler_result in zip(
        dispatch_group_items,
        handler_results,
        strict=True,
    ):
        if isinstance(handler_result, BaseException):
            # Cancellation and system-level exceptions (CancelledError,
            # KeyboardInterrupt, SystemExit) must propagate, not be swallowed
            # as a dispatch failure.
            if not isinstance(handler_result, Exception):
                raise handler_result
            dispatch_failures.append(handler_result)
            failure_details.append(
                _TerminationDispatchFailure(
                    resource_id=resource_id,
                    instance_ids=list(mapped_instance_ids),
                    error_message=str(handler_result),
                    error_type=type(handler_result).__name__,
                )
            )
            logger.warning(
                "Azure termination dispatch group failed: %s",
                handler_result,
                exc_info=True,
            )
            continue
        record_pending_cleanup(handler_result)
        if handler_result is None:
            continue

        provider_data = handler_result.get("provider_data")
        if provider_data is not None:
            termination_provider_data.append(provider_data)
        successful_instance_ids.extend(mapped_instance_ids)

    if dispatch_failures and not successful_instance_ids:
        if len(dispatch_failures) > 1:
            raise builtins.ExceptionGroup(
                "All Azure termination dispatch groups failed",
                dispatch_failures,
            )
        raise dispatch_failures[0]
    return _TerminationDispatchResult(
        provider_data=termination_provider_data,
        successful_instance_ids=successful_instance_ids,
        failures=failure_details,
    )


class AzureTerminationService:
    """Own Azure termination from validation through handler dispatch to result shaping."""

    def __init__(
        self,
        *,
        logger: LoggingPort,
        handler_provider: AzureProviderStrategy,
        record_pending_cleanup: Callable[[AzureReleaseHostsResult | None], None],
        default_resource_group: Optional[str],
    ) -> None:
        self._logger = logger
        self._handler_provider = handler_provider
        self._record_pending_cleanup = record_pending_cleanup
        self._default_resource_group = default_resource_group

    async def terminate_instances_async(
        self,
        operation: ProviderOperation,
        *,
        is_dry_run: bool,
    ) -> ProviderResult:
        """Validate, dispatch, and shape the result for an Azure terminate-instances operation."""
        context = self._build_context(operation, is_dry_run=is_dry_run)
        if is_dry_run:
            return _dry_run_result(context)

        dispatch_result = await _dispatch_release_groups_async(
            handler=context.handler,
            instance_ids=context.instance_ids,
            grouped_resource_mapping=context.grouped_resource_mapping,
            default_resource_id=context.default_resource_id,
            context=context.release_context,
            logger=self._logger,
            record_pending_cleanup=self._record_pending_cleanup,
        )
        return _termination_result(context.instance_ids, dispatch_result)

    def _build_context(
        self,
        operation: ProviderOperation,
        *,
        is_dry_run: bool,
    ) -> _TerminationOperationContext:
        """Validate and resolve a termination operation into a dispatch context."""
        instance_ids = operation.parameters.get("instance_ids", [])
        if not instance_ids:
            raise AzureValidationError(
                "Instance IDs are required for termination",
                error_code="MISSING_INSTANCE_IDS",
            )

        provider_api = resolve_operation_provider_api(operation)
        if provider_api is None:
            raise AzureValidationError(
                "provider_api is required for Azure termination",
                error_code="MISSING_PROVIDER_API",
            )

        provider_api_value = provider_api.value
        handler = self._handler_provider.resolve_handler(provider_api)
        if handler is None:
            raise AzureValidationError(
                f"No handler available for provider_api: {provider_api_value}",
                error_code="HANDLER_NOT_FOUND",
            )

        raw_resource_mapping = operation.parameters.get("resource_mapping", {})
        grouped_resource_mapping = group_instance_ids_by_resource(
            instance_ids, raw_resource_mapping
        )
        request_metadata = operation.parameters.get("request_metadata") or {}

        default_resource_id = operation.parameters.get("resource_id")
        if not default_resource_id and grouped_resource_mapping:
            default_resource_id = next(iter(grouped_resource_mapping.keys()))
        if not default_resource_id and provider_api_value == AzureProviderApi.CYCLECLOUD.value:
            cyclecloud_cluster_name = request_metadata.get("cluster_name")
            if cyclecloud_cluster_name not in (None, ""):
                default_resource_id = str(cyclecloud_cluster_name)
        if not default_resource_id and not is_dry_run:
            raise AzureValidationError(
                "resource_id or resource_mapping is required for Azure termination",
                error_code="MISSING_RESOURCE_ID",
            )

        resolved_resource_group = resolve_operation_resource_group(
            operation, self._default_resource_group
        )
        cyclecloud_request_context = CycleCloudRequestContext.from_mapping(request_metadata)
        release_context = AzureReleaseContext(
            resource_group=resolved_resource_group,
            resource_id=(default_resource_id or None),
            cyclecloud_request_context=cyclecloud_request_context,
        )

        return _TerminationOperationContext(
            instance_ids=instance_ids,
            grouped_resource_mapping=grouped_resource_mapping,
            release_context=release_context,
            handler=handler,
            default_resource_id=default_resource_id or "",
        )


def _dry_run_result(context: _TerminationOperationContext) -> ProviderResult:
    """Return a success result describing what a real termination would do."""
    return ProviderResult.success_result(
        {
            "success": True,
            "terminated_count": len(context.instance_ids),
        },
        {
            "operation": "terminate_instances",
            "instance_ids": context.instance_ids,
            "method": "dry_run",
            "provider_data": {"dry_run": True},
        },
    )


def _termination_result(
    instance_ids: list[str],
    dispatch_result: _TerminationDispatchResult,
) -> ProviderResult:
    """Build the final termination result from handler responses."""
    provider_data = (
        {
            "termination_requests": dispatch_result.provider_data,
        }
        if dispatch_result.provider_data
        else {}
    )

    if dispatch_result.failures:
        failed_instances = [
            instance_id
            for failure in dispatch_result.failures
            for instance_id in failure.instance_ids
        ]
        failure_details = [
            {
                "resource_id": failure.resource_id,
                "instance_ids": failure.instance_ids,
                "error_message": failure.error_message,
                "error_type": failure.error_type,
            }
            for failure in dispatch_result.failures
        ]
        return ProviderResult.error_result(
            "Azure termination partially failed",
            "AZURE_TERMINATION_PARTIAL_FAILURE",
            {
                "operation": "terminate_instances",
                "instance_ids": instance_ids,
                "method": "handler",
                "provider_data": {
                    **provider_data,
                    "dispatch_failures": failure_details,
                    "successful_instance_ids": dispatch_result.successful_instance_ids,
                    "failed_instance_ids": failed_instances,
                },
            },
        )

    return ProviderResult.success_result(
        {
            "success": True,
            "terminated_count": len(instance_ids),
        },
        {
            "operation": "terminate_instances",
            "instance_ids": instance_ids,
            "method": "handler",
            "provider_data": provider_data,
        },
    )
