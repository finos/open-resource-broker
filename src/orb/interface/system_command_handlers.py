"""System-related command handlers for the interface layer."""

import asyncio
from typing import Any, cast

from orb.application.dto.interface_response import InterfaceResponse
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.monitoring.metrics import MetricsCollector


@handle_interface_exceptions(context="system_health", interface_type="cli")
async def handle_system_health(args) -> InterfaceResponse:
    """Handle system health check."""
    from orb.interface.health_command_handler import handle_health_check

    loop = asyncio.get_event_loop()
    result = cast(dict[str, Any], await loop.run_in_executor(None, handle_health_check, args))
    return InterfaceResponse(data=result, exit_code=0 if result.get("success") else 1)


@handle_interface_exceptions(context="provider_health", interface_type="cli")
async def handle_provider_health(args) -> dict[str, Any]:
    """Handle provider health operations."""
    from orb.application.services.orchestration.dtos import GetProviderHealthInput
    from orb.application.services.orchestration.get_provider_health import (
        GetProviderHealthOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(GetProviderHealthOrchestrator)
    result = await orchestrator.execute(
        GetProviderHealthInput(provider_name=getattr(args, "provider", None))
    )
    return {"health": result.health, "message": result.message}


@handle_interface_exceptions(context="list_providers", interface_type="cli")
async def handle_list_providers(args) -> dict[str, Any]:
    """Handle list available providers with real capabilities from configuration."""
    from orb.application.services.orchestration.dtos import ListProvidersInput
    from orb.application.services.orchestration.list_providers import ListProvidersOrchestrator

    container = get_container()
    orchestrator = container.get(ListProvidersOrchestrator)
    result = await orchestrator.execute(ListProvidersInput())
    return {
        "providers": result.providers,
        "count": result.count,
        "selection_policy": result.selection_policy,
        "message": result.message,
    }


@handle_interface_exceptions(context="provider_config", interface_type="cli")
async def handle_provider_config(args) -> dict[str, Any]:
    """Handle get provider config operations."""
    from orb.application.services.orchestration.dtos import GetProviderConfigInput
    from orb.application.services.orchestration.get_provider_config import (
        GetProviderConfigOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(GetProviderConfigOrchestrator)
    result = await orchestrator.execute(GetProviderConfigInput())
    return {"config": result.config, "message": result.message}


@handle_interface_exceptions(context="validate_provider_config", interface_type="cli")
async def handle_validate_provider_config(args) -> dict[str, Any]:
    """Handle validate provider config operations."""
    return {
        "error": "Not implemented",
        "endpoint": "validate_provider_config",
        "message": "Provider configuration validation is planned but not yet available.",
    }


@handle_interface_exceptions(context="select_provider_strategy", interface_type="cli")
async def handle_select_provider_strategy(args) -> dict[str, Any]:
    """Handle select provider strategy — delegates to set-default config write."""
    from orb.interface.provider_config_handler import handle_provider_set_default

    return await handle_provider_set_default(args)


@handle_interface_exceptions(context="execute_provider_operation", interface_type="cli")
async def handle_execute_provider_operation(args) -> dict[str, Any]:
    """Handle execute provider operation operations."""
    return {
        "error": "Not implemented",
        "endpoint": "execute_provider_operation",
        "message": "Generic provider operation execution is planned but not yet available.",
    }


@handle_interface_exceptions(context="provider_metrics", interface_type="cli")
async def handle_provider_metrics(args) -> dict[str, Any]:
    """Handle get provider metrics operations."""
    from orb.application.services.orchestration.dtos import GetProviderMetricsInput
    from orb.application.services.orchestration.get_provider_metrics import (
        GetProviderMetricsOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(GetProviderMetricsOrchestrator)
    result = await orchestrator.execute(
        GetProviderMetricsInput(
            provider_name=getattr(args, "provider", None),
            timeframe=getattr(args, "timeframe", "24h"),
        )
    )
    return {"metrics": result.metrics, "message": result.message}


@handle_interface_exceptions(context="reload_provider_config", interface_type="cli")
async def handle_reload_provider_config(args) -> InterfaceResponse:
    """Handle reload provider config operations."""
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.interface.response_formatting_service import ResponseFormattingService

    container = get_container()
    formatter = container.get(ResponseFormattingService)
    try:
        registry = container.get(ProviderRegistryService)
        if hasattr(registry, "reload"):
            await cast(Any, registry).reload()
            return formatter.format_success({"message": "Provider configuration reloaded"})
        return formatter.format_error("Reload not supported by current provider registry")
    except Exception as e:
        return formatter.format_error(f"Reload failed: {e}")


@handle_interface_exceptions(context="system_status", interface_type="cli")
async def handle_system_status(args) -> InterfaceResponse:
    """Handle system status query."""
    from orb.infrastructure.di.buses import QueryBus
    from orb.interface.response_formatting_service import ResponseFormattingService

    container = get_container()
    query_bus = container.get(QueryBus)
    formatter = container.get(ResponseFormattingService)

    from orb.application.queries.system import GetSystemStatusQuery

    query = GetSystemStatusQuery(
        include_provider_health=True, verbose=getattr(args, "verbose", False)
    )
    status = await query_bus.execute(query)

    return formatter.format_system_status(status)


@handle_interface_exceptions(context="system_metrics", interface_type="cli")
async def handle_system_metrics(args) -> InterfaceResponse:
    """Handle get system metrics operations."""
    container = get_container()
    try:
        metrics = container.get_optional(MetricsCollector)
    except Exception:
        metrics = None

    if not metrics:
        return InterfaceResponse(
            data={"metrics": {}, "message": "MetricsCollector not available"}, exit_code=0
        )

    try:
        return InterfaceResponse(
            data={
                "metrics": metrics.get_metrics(),
                "message": "System metrics retrieved successfully",
            },
            exit_code=0,
        )
    except Exception as e:
        return InterfaceResponse(
            data={"metrics": {}, "error": str(e), "message": "Failed to retrieve system metrics"},
            exit_code=1,
        )
