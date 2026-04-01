"""Azure terminate-instance orchestration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from orb.providers.azure.infrastructure.handlers.azure_handler import AzureHandler
from orb.providers.base.strategy import ProviderOperation, ProviderResult


@dataclass
class TerminationOperationContext:
    """Resolved parameters needed to execute a termination dispatch."""

    instance_ids: list[str]
    grouped_resource_mapping: dict[str, list[str]]
    release_context: dict[str, Any]
    handler: AzureHandler
    default_resource_id: str


class AzureTerminationService:
    """Own Azure termination preparation and result shaping."""

    @staticmethod
    def build_termination_operation_context(
            *,
        operation: ProviderOperation,
        is_dry_run: bool,
        resolve_operation_provider_api: Callable[[ProviderOperation], Optional[Any]],
        provider_api_key: Callable[[Any], str],
        handlers: dict[str, AzureHandler],
        group_instance_ids_by_resource: Callable[[list[str], dict[str, Any]], dict[str, list[str]]],
        build_cyclecloud_request_metadata: Callable[..., dict[str, Any]],
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
        if not default_resource_id and not is_dry_run:
            return ProviderResult.error_result(
                "resource_id or resource_mapping is required for Azure termination",
                "MISSING_RESOURCE_ID",
            )

        release_context = build_cyclecloud_request_metadata(
            operation=operation,
            resource_group=resolve_operation_resource_group(operation),
        )
        if default_resource_id:
            release_context["resource_id"] = default_resource_id

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
        termination_provider_data: list[dict[str, Any]],
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
