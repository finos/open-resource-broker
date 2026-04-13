"""Azure create-instance orchestration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, cast

from orb.domain.request.aggregate import Request
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions import AzureValidationError
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureAcquireHostsResult,
    AzureHandler,
)
from orb.providers.base.strategy import ProviderOperation, ProviderResult


AzureProviderApiRef = AzureProviderApi | str


@dataclass
class CreateOperationContext:
    """Context object encapsulating all necessary information for handling a create operation."""
    template_config: dict[str, Any]
    count: int
    provider_api: AzureProviderApiRef
    provider_api_key: str
    handler: AzureHandler
    azure_template: AzureTemplate


def get_create_template_config(operation: ProviderOperation) -> dict[str, Any]:
    """Extract the template configuration from the provider operation."""
    return dict(operation.parameters.get("template_config") or {})

def get_create_count(operation: ProviderOperation) -> int:
    """Extract the instance count from the provider operation."""
    return operation.parameters.get("count", 1)

def validate_create_template_config(
    template_config: dict[str, Any],
) -> None:
    """Validate that the template configuration is present."""
    if template_config:
        return
    raise AzureValidationError(
        "Template configuration is required for instance creation",
        error_code="MISSING_TEMPLATE_CONFIG",
    )

def provider_api_key(provider_api: AzureProviderApiRef) -> str:
    """Get the string key for the provider API value."""
    if isinstance(provider_api, AzureProviderApi):
        return provider_api.value
    return provider_api

def resolve_create_provider_api(
    template_config: dict[str, Any],
    normalize_provider_api: Callable[[Any], Any],
) -> AzureProviderApiRef:
    """Resolve the provider API from the template config, normalizing as needed."""
    provider_api = template_config.get("provider_api", AzureProviderApi.VMSS)
    return cast(AzureProviderApiRef, normalize_provider_api(provider_api))

def create_instances_dry_run_result(
    create_context: CreateOperationContext,
) -> ProviderResult:
    """Return a dry-run result for create instances operation."""
    return ProviderResult.success_result(
        {
            "resource_ids": ["dry-run-resource-id"],
            "instances": [],
            "provider_api": create_context.provider_api_key,
            "count": create_context.count,
            "template_id": create_context.azure_template.template_id,
        },
        {
            "operation": "create_instances",
            "template_config": create_context.template_config,
            "handler_used": create_context.provider_api_key,
            "method": "dry_run",
            "provider_data": {"dry_run": True},
        },
    )


class AzureProvisioningService:
    """Own Azure create-operation orchestration and handler result shaping."""

    def build_create_operation_context(
        self,
        *,
        operation: ProviderOperation,
        normalize_provider_api: Callable[[Any], Any],
        resolve_handler: Callable[[AzureProviderApiRef], Optional[AzureHandler]],
        build_template: Callable[[dict[str, Any]], AzureTemplate],
    ) -> CreateOperationContext:
        """Build and validate the context required for a create operation."""
        template_config = get_create_template_config(operation)
        count = get_create_count(operation)
        validate_create_template_config(template_config)

        provider_api = resolve_create_provider_api(template_config, normalize_provider_api)
        provider_api_value = provider_api_key(provider_api)
        handler = resolve_handler(provider_api)
        if handler is None:
            raise AzureValidationError(
                f"No handler available for provider_api: {provider_api_value}",
                error_code="HANDLER_NOT_FOUND",
            )

        return CreateOperationContext(
            template_config=template_config,
            count=count,
            provider_api=provider_api,
            provider_api_key=provider_api_value,
            handler=handler,
            azure_template=build_template(template_config),
        )

    def build_create_request(
        self,
        *,
        operation: ProviderOperation,
        azure_template: AzureTemplate,
        count: int,
        provider_api: AzureProviderApiRef,
        provider_instance_name: str,
    ) -> Request:
        """Build a request object for the create operation handler."""
        from orb.domain.request.value_objects import RequestType

        request_metadata = dict(operation.parameters.get("request_metadata", {}) or {})
        request_id = operation.parameters.get("request_id") or (
            operation.context.get("request_id") if operation.context else None
        )
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id=azure_template.template_id,
            machine_count=count,
            provider_type="azure",
            provider_name=provider_instance_name,
            metadata=request_metadata,
            request_id=request_id,
        )
        request.provider_api = provider_api_key(provider_api)
        return request

    def normalize_handler_create_result(
        self,
        handler_result: AzureAcquireHostsResult,
        *,
        template_config: dict[str, Any],
        provider_api: AzureProviderApiRef,
        count: int,
        template_id: str,
    ) -> ProviderResult:
        """Normalize the result from the handler into a ProviderResult."""
        resource_ids = handler_result["resource_ids"]
        instances = handler_result["instances"]
        success = handler_result["success"]
        error_message = handler_result.get("error_message")
        provider_data = handler_result.get("provider_data", {})

        if not success:
            return ProviderResult.error_result(
                f"Provisioning failed: {error_message}",
                "PROVISIONING_ADAPTER_ERROR",
                {
                    "operation": "create_instances",
                    "template_config": template_config,
                    "handler_used": provider_api_key(provider_api),
                    "method": "handler",
                    "provider_data": provider_data,
                },
            )

        return ProviderResult.success_result(
            {
                "resource_ids": resource_ids,
                "instances": instances,
                "provider_api": provider_api_key(provider_api),
                "count": count,
                "template_id": template_id,
            },
            {
                "operation": "create_instances",
                "template_config": template_config,
                "handler_used": provider_api_key(provider_api),
                "method": "handler",
                "provider_data": provider_data,
            },
        )

    def execute_create_handler(
        self,
        *,
        create_context: CreateOperationContext,
        request: Request,
    ) -> ProviderResult:
        """Execute the handler's acquire_hosts method and normalize the result."""
        handler_result = create_context.handler.acquire_hosts(
            request, create_context.azure_template
        )
        return self.normalize_handler_create_result(
            handler_result,
            template_config=create_context.template_config,
            provider_api=create_context.provider_api,
            count=create_context.count,
            template_id=create_context.azure_template.template_id,
        )
