"""Azure terminate-instance orchestration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.infrastructure.cyclecloud_session import CycleCloudRequestContext
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureHandler,
    AzureReleaseContext,
    AzureReleaseProviderData,
)
from orb.providers.base.strategy import ProviderOperation, ProviderResult

AzureProviderApiRef = AzureProviderApi | str


@dataclass
class TerminationOperationContext:
    """Resolved parameters needed to execute a termination dispatch."""

    instance_ids: list[str]
    grouped_resource_mapping: dict[str, list[str]]
    release_context: AzureReleaseContext
    handler: AzureHandler
    default_resource_id: str


class AzureTerminationService:
    """Own Azure termination preparation and result shaping."""

    @staticmethod
    def build_termination_operation_context(
            *,
        operation: ProviderOperation,
        is_dry_run: bool,
        resolve_operation_provider_api: Callable[[ProviderOperation], Optional[AzureProviderApiRef]],
        provider_api_key: Callable[[AzureProviderApiRef], str],
        handlers: dict[str, AzureHandler],
        group_instance_ids_by_resource: Callable[[list[str], dict[str, Any]], dict[str, list[str]]],
        resolve_operation_resource_group: Callable[[ProviderOperation], Optional[str]],
    ) -> TerminationOperationContext | ProviderResult:
        """Validate and resolve a termination operation into a dispatch context or an error."""
        instance_ids = operation.parameters.get("instance_ids", [])
        if not instance_ids:
            return ProviderResult.error_result(
                "Instance IDs are required for termination",
                "MISSING_INSTANCE_IDS",
            )

        provider_api = resolve_operation_provider_api(operation)
        if provider_api in (None, ""):
            return ProviderResult.error_result(
                "provider_api is required for Azure termination",
                "MISSING_PROVIDER_API",
            )

        provider_api_value = provider_api_key(provider_api)
        handler = handlers.get(provider_api_value)
        if handler is None:
            return ProviderResult.error_result(
                f"No handler available for provider_api: {provider_api_value}",
                "HANDLER_NOT_FOUND",
            )

        raw_resource_mapping = operation.parameters.get("resource_mapping", {})
        grouped_resource_mapping = group_instance_ids_by_resource(instance_ids, raw_resource_mapping)
        default_resource_id = operation.parameters.get("resource_id")
        if not default_resource_id and grouped_resource_mapping:
            default_resource_id = next(iter(grouped_resource_mapping.keys()))
        if not default_resource_id and provider_api_value == AzureProviderApi.CYCLECLOUD.value:
            request_metadata = operation.parameters.get("request_metadata", {}) or {}
            cyclecloud_cluster_name = request_metadata.get("cluster_name")
            if cyclecloud_cluster_name not in (None, ""):
                default_resource_id = str(cyclecloud_cluster_name)
        if not default_resource_id and not is_dry_run:
            return ProviderResult.error_result(
                "resource_id or resource_mapping is required for Azure termination",
                "MISSING_RESOURCE_ID",
            )

        resolved_resource_group = resolve_operation_resource_group(operation)
        request_metadata = operation.parameters.get("request_metadata", {}) or {}
        cyclecloud_request_context = CycleCloudRequestContext.from_mapping(request_metadata)
        release_context = AzureReleaseContext(
            resource_group=resolved_resource_group,
            resource_id=(default_resource_id or None),
            cyclecloud_request_context=cyclecloud_request_context,
        )

        return TerminationOperationContext(
            instance_ids=instance_ids,
            grouped_resource_mapping=grouped_resource_mapping,
            release_context=release_context,
            handler=handler,
            default_resource_id=default_resource_id or "",
        )

    @staticmethod
    def terminate_instances_dry_run_result(
        termination_context: TerminationOperationContext,
    ) -> ProviderResult:
        """Return a success result describing what a real termination would do."""
        return ProviderResult.success_result(
            {
                "success": True,
                "terminated_count": len(termination_context.instance_ids),
            },
            {
                "operation": "terminate_instances",
                "instance_ids": termination_context.instance_ids,
                "method": "dry_run",
                "provider_data": {"dry_run": True},
            },
        )

    @staticmethod
    def terminate_instances_result(
        *,
        instance_ids: list[str],
        termination_provider_data: list[AzureReleaseProviderData],
    ) -> ProviderResult:
        """Build the final termination result from handler responses."""
        return ProviderResult.success_result(
            {
                "success": True,
                "terminated_count": len(instance_ids),
            },
            {
                "operation": "terminate_instances",
                "instance_ids": instance_ids,
                "method": "handler",
                "provider_data": {
                    "termination_requests": termination_provider_data,
                }
                if termination_provider_data
                else {},
            },
        )
