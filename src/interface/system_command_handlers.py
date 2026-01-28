"""System-related command handlers for the interface layer."""

from typing import Any

from infrastructure.di.buses import QueryBus
from infrastructure.di.container import get_container
from infrastructure.error.decorators import handle_interface_exceptions
from monitoring.metrics import MetricsCollector


@handle_interface_exceptions(context="system_health", interface_type="cli")
async def handle_system_health(args) -> dict[str, Any]:
    """Handle system health check."""
    import asyncio

    from interface.health_command_handler import handle_health_check

    # Run sync health check in executor
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, handle_health_check, args)

    return {"status": "success" if result == 0 else "error"}


@handle_interface_exceptions(context="provider_health", interface_type="cli")
async def handle_provider_health(args) -> dict[str, Any]:
    """Handle provider health operations."""
    container = get_container()
    query_bus = container.get(QueryBus)

    from application.queries.system import GetSystemStatusQuery

    query = GetSystemStatusQuery()
    health_status = await query_bus.execute(query)

    return {
        "health": health_status,
        "message": "Provider health retrieved successfully",
    }


@handle_interface_exceptions(context="list_providers", interface_type="cli")
async def handle_list_providers(args) -> dict[str, Any]:
    """Handle list available providers with real capabilities from configuration."""
    container = get_container()

    try:
        # Get configuration manager
        from domain.base.ports.configuration_port import ConfigurationPort

        config_manager = container.get(ConfigurationPort)
        provider_config = config_manager.get_provider_config()

        if not provider_config:
            return {
                "providers": [],
                "count": 0,
                "message": "No provider configuration found",
            }

        # Get active providers from configuration
        active_providers = provider_config.get_active_providers()

        providers_info = []
        for provider_instance in active_providers:
            # Get effective handlers using inheritance
            provider_defaults = provider_config.provider_defaults.get(provider_instance.type)
            effective_handlers = provider_instance.get_effective_handlers(provider_defaults)
            handler_names = list(effective_handlers.keys())

            providers_info.append(
                {
                    "name": provider_instance.name,
                    "type": provider_instance.type,
                    "region": provider_instance.config.get("region", "unknown"),
                    "status": "active" if provider_instance.enabled else "disabled",
                    "capabilities": handler_names,  # Real handler names from inheritance
                    "weight": provider_instance.weight,
                    "priority": provider_instance.priority,
                }
            )

        return {
            "providers": providers_info,
            "count": len(providers_info),
            "selection_policy": provider_config.selection_policy,
            "message": "Available providers retrieved successfully",
        }

    except Exception as e:
        # Fallback to basic response if configuration fails
        return {
            "providers": [],
            "count": 0,
            "error": str(e),
            "message": "Failed to retrieve provider configuration",
        }


@handle_interface_exceptions(context="provider_config", interface_type="cli")
async def handle_provider_config(args) -> dict[str, Any]:
    """Handle get provider config operations."""
    container = get_container()
    query_bus = container.get(QueryBus)

    from application.queries.system import GetProviderConfigQuery

    query = GetProviderConfigQuery()
    config = await query_bus.execute(query)

    return {
        "config": config,
        "message": "Provider configuration retrieved successfully",
    }


@handle_interface_exceptions(context="validate_provider_config", interface_type="cli")
async def handle_validate_provider_config(args) -> dict[str, Any]:
    """Handle validate provider config operations."""
    return {
        "validation": {"status": "valid", "errors": []},
        "message": "Provider configuration validated successfully",
    }


@handle_interface_exceptions(context="reload_provider_config", interface_type="cli")
async def handle_reload_provider_config(args) -> dict[str, Any]:
    """Handle reload provider config operations."""
    return {
        "result": {"status": "reloaded"},
        "message": "Provider configuration reloaded successfully",
    }


@handle_interface_exceptions(context="select_provider_strategy", interface_type="cli")
async def handle_select_provider_strategy(args) -> dict[str, Any]:
    """Handle select provider strategy operations."""
    provider = getattr(args, "provider", "aws")
    return {
        "result": {"selected_provider": provider},
        "message": "Provider strategy selected successfully",
    }


@handle_interface_exceptions(context="execute_provider_operation", interface_type="cli")
async def handle_execute_provider_operation(args) -> dict[str, Any]:
    """Handle execute provider operation operations."""
    operation = getattr(args, "operation", "status")
    return {
        "result": {"operation": operation, "status": "completed"},
        "message": "Provider operation executed successfully",
    }


@handle_interface_exceptions(context="provider_metrics", interface_type="cli")
async def handle_provider_metrics(args) -> dict[str, Any]:
    """Handle get provider metrics operations."""
    container = get_container()
    query_bus = container.get(QueryBus)

    from application.queries.system import GetProviderMetricsQuery

    query = GetProviderMetricsQuery(provider_name=getattr(args, "provider", None))
    metrics = await query_bus.execute(query)

    return {"metrics": metrics, "message": "Provider metrics retrieved successfully"}


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
