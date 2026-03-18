"""System-related command handlers for the interface layer."""

from typing import Any

from orb.domain.constants import PROVIDER_TYPE_AWS
from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.monitoring.metrics import MetricsCollector


@handle_interface_exceptions(context="system_health", interface_type="cli")
async def handle_system_health(args) -> dict[str, Any]:
    """Handle system health check."""
    import asyncio

    from orb.interface.health_command_handler import handle_health_check

    # Run sync health check in executor
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, handle_health_check, args)

    return {"status": "success" if result == 0 else "error"}


@handle_interface_exceptions(context="provider_health", interface_type="cli")
async def handle_provider_health(args) -> dict[str, Any]:
    """Handle provider health operations."""
    from orb.application.services.orchestration.dtos import GetProviderHealthInput
    from orb.application.services.orchestration.get_provider_health import (
        GetProviderHealthOrchestrator,
    )

    container = get_container()
    orchestrator = container.get(GetProviderHealthOrchestrator)
    result = await orchestrator.execute(GetProviderHealthInput())
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


@handle_interface_exceptions(context="reload_provider_config", interface_type="cli")
async def handle_reload_provider_config(args) -> dict[str, Any]:
    """Handle reload provider config operations."""
    return {
        "error": "Not implemented",
        "endpoint": "reload_provider_config",
        "message": "Provider configuration reload is planned but not yet available.",
    }


@handle_interface_exceptions(context="select_provider_strategy", interface_type="cli")
async def handle_select_provider_strategy(args) -> dict[str, Any]:
    """Handle select provider strategy operations."""
    # Get first available provider as default
    default_provider = PROVIDER_TYPE_AWS  # Keep as fallback
    try:
        from orb.application.services.provider_registry_service import ProviderRegistryService

        registry_service = get_container().get(ProviderRegistryService)
        registered_types = registry_service.get_available_strategies()
        if registered_types:
            default_provider = registered_types[0]
    except Exception as e:
        from orb.infrastructure.logging.logger import get_logger

        logger = get_logger(__name__)
        logger.debug(f"Failed to get default provider: {e}")  # Use fallback

    provider = getattr(args, "provider", default_provider)
    return {
        "result": {"selected_provider": provider},
        "message": "Provider strategy selected successfully",
    }


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
        GetProviderMetricsInput(provider_name=getattr(args, "provider", None))
    )
    return {"metrics": result.metrics, "message": result.message}


@handle_interface_exceptions(context="system_status", interface_type="cli")
async def handle_system_status(args) -> dict[str, Any]:
    """Handle system status query."""
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    query_bus = container.get(QueryBus)

    from orb.application.queries.system import GetSystemStatusQuery

    query = GetSystemStatusQuery(
        include_provider_health=True, detailed=getattr(args, "detailed", False)
    )
    status = await query_bus.execute(query)

    return {"system_status": status, "message": "System status retrieved successfully"}


@handle_interface_exceptions(context="system_metrics", interface_type="cli")
async def handle_system_metrics(args) -> dict[str, Any]:
    """Handle get system metrics operations."""
    container = get_container()
    try:
        metrics = container.get_optional(MetricsCollector)
    except Exception:
        metrics = None

    if not metrics:
        return {"metrics": {}, "message": "MetricsCollector not available"}

    try:
        return {
            "metrics": metrics.get_metrics(),
            "message": "System metrics retrieved successfully",
        }
    except Exception as e:
        # Gracefully handle any issues retrieving metrics
        return {"metrics": {}, "error": str(e), "message": "Failed to retrieve system metrics"}
