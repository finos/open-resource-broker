"""System-related command handlers for the interface layer."""

import asyncio
from typing import Any, cast

from orb.application.dto.interface_response import InterfaceResponse
from orb.infrastructure.error.decorators import handle_interface_exceptions


@handle_interface_exceptions(context="system_health", interface_type="cli")
async def handle_system_health(args) -> InterfaceResponse:
    """Handle system health check."""
    from orb.interface.health_command_handler import handle_health_check

    loop = asyncio.get_event_loop()
    result = cast(dict[str, Any], await loop.run_in_executor(None, handle_health_check, args))
    return InterfaceResponse(data=result, exit_code=0 if result.get("success") else 1)


@handle_interface_exceptions(context="provider_health", interface_type="cli")
async def handle_provider_health(args) -> InterfaceResponse:
    """Check health status of cloud providers, optionally filtered by name or type."""
    from orb.application.services.orchestration.dtos import GetProviderHealthInput
    from orb.interface.catalog import OPERATION_CATALOG, Interface
    from orb.interface.response_formatting_service import ResponseFormattingService

    entry = OPERATION_CATALOG["get_provider_health"]
    container = args._container
    orchestrator = container.get(entry.orchestrator)
    formatter = container.get(ResponseFormattingService)
    result = await orchestrator.execute(
        GetProviderHealthInput(
            provider_name=getattr(args, "provider_name", None),
            provider_type=getattr(args, "provider_type", None),
        )
    )
    return entry.renderer_for(Interface.CLI)(formatter, result)


@handle_interface_exceptions(context="list_providers", interface_type="cli")
async def handle_list_providers(args) -> InterfaceResponse:
    """List configured cloud providers with their capabilities and selection policy."""
    from orb.application.services.orchestration.dtos import ListProvidersInput
    from orb.interface.catalog import OPERATION_CATALOG, Interface
    from orb.interface.response_formatting_service import ResponseFormattingService

    entry = OPERATION_CATALOG["list_providers"]
    container = args._container
    orchestrator = container.get(entry.orchestrator)
    formatter = container.get(ResponseFormattingService)
    result = await orchestrator.execute(
        ListProvidersInput(
            provider_name=getattr(args, "provider_name", None),
            provider_type=getattr(args, "provider_type", None),
            filter_expressions=getattr(args, "filter", None) or [],
        )
    )
    return entry.renderer_for(Interface.CLI)(formatter, result)


@handle_interface_exceptions(context="provider_config", interface_type="cli")
async def handle_provider_config(args) -> InterfaceResponse:
    """Retrieve the current provider configuration."""
    from orb.application.services.orchestration.dtos import GetProviderConfigInput
    from orb.interface.catalog import OPERATION_CATALOG, Interface
    from orb.interface.response_formatting_service import ResponseFormattingService

    entry = OPERATION_CATALOG["get_provider_config"]
    container = args._container
    orchestrator = container.get(entry.orchestrator)
    formatter = container.get(ResponseFormattingService)
    result = await orchestrator.execute(GetProviderConfigInput())
    return entry.renderer_for(Interface.CLI)(formatter, result)


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


@handle_interface_exceptions(context="cleanup_launch_templates", interface_type="cli")
async def handle_cleanup_launch_templates(args) -> InterfaceResponse:
    """Delete the launch templates ORB created that nothing uses any more.

    An explicit maintenance operation: nothing runs it in the background. The
    provider decides what is safe to delete; this handler only routes the request.
    """
    from orb.domain.base.operations import Operation, OperationType
    from orb.domain.base.ports import ProviderSelectionPort

    container = args._container
    port = container.get(ProviderSelectionPort)
    selection = port.select_active_provider(
        provider_name=getattr(args, "provider_name", None),
        provider_type=getattr(args, "provider_type", None),
    )
    provider_name = selection.provider_name

    capabilities = port.get_strategy_capabilities(provider_name)
    supports = getattr(capabilities, "supports_operation", None)  # getattr: port returns Any
    if supports is None or not supports(OperationType.CLEANUP_ORPHANED_RESOURCES):
        return InterfaceResponse(
            data={
                "success": False,
                "provider": provider_name,
                "message": f"Provider '{provider_name}' has no launch templates to clean up",
            },
            exit_code=1,
        )

    result = await port.execute_operation(
        provider_name,
        Operation(
            operation_type=OperationType.CLEANUP_ORPHANED_RESOURCES,
            parameters={
                "dry_run": bool(getattr(args, "dry_run", False)),
                "min_age_hours": getattr(args, "min_age_hours", 24),
                "force": bool(getattr(args, "force", False)),
            },
        ),
    )
    if not result.success:
        # A partly completed sweep carries its report: what was deleted, what failed.
        return InterfaceResponse(
            data={
                "success": False,
                "provider": provider_name,
                "message": result.error_message,
                **(result.data if isinstance(result.data, dict) else {}),
            },
            exit_code=1,
        )
    return InterfaceResponse(
        data={"success": True, "provider": provider_name, **(result.data or {})}, exit_code=0
    )


@handle_interface_exceptions(context="provider_metrics", interface_type="cli")
async def handle_provider_metrics(args) -> InterfaceResponse:
    """Retrieve provider usage metrics for a given timeframe, optionally filtered by provider."""
    from orb.application.services.orchestration.dtos import GetProviderMetricsInput
    from orb.interface.catalog import OPERATION_CATALOG, Interface
    from orb.interface.response_formatting_service import ResponseFormattingService

    entry = OPERATION_CATALOG["get_provider_metrics"]
    container = args._container
    orchestrator = container.get(entry.orchestrator)
    formatter = container.get(ResponseFormattingService)
    result = await orchestrator.execute(
        GetProviderMetricsInput(
            provider_name=getattr(args, "provider_name", None),
            timeframe=getattr(args, "timeframe", "24h"),
        )
    )
    return entry.renderer_for(Interface.CLI)(formatter, result)


@handle_interface_exceptions(context="reload_provider_config", interface_type="cli")
async def handle_reload_provider_config(args) -> InterfaceResponse:
    """Handle reload provider config operations."""
    from orb.application.services.provider_registry_service import ProviderRegistryService
    from orb.interface.response_formatting_service import ResponseFormattingService

    container = args._container
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

    container = args._container
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
    """Handle get system metrics operations.

    Scrapes the prometheus_client global REGISTRY (which includes OTel
    PrometheusMetricReader output and native k8s/process metrics) and returns
    a dict of ``{metric_name: sample_value}`` pairs for the latest sample of
    each series.  Returns an empty dict when prometheus_client is not installed
    (optional [monitoring] extra).
    """
    try:
        from prometheus_client import REGISTRY, generate_latest

        raw = generate_latest(REGISTRY).decode("utf-8")
        # Parse the Prometheus text format into a flat name→value dict.
        # Each non-comment, non-empty line has the form:
        #   metric_name[{labels}] value [timestamp]
        metrics_dict: dict[str, Any] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                name_with_labels = parts[0]
                value_str = parts[1]
                # Strip label set to get the bare metric name for the key.
                bare_name = name_with_labels.split("{")[0]
                try:
                    value = float(value_str)
                except ValueError:
                    value = value_str  # type: ignore[assignment]
                metrics_dict[bare_name] = value
        return InterfaceResponse(
            data={"metrics": metrics_dict, "message": "System metrics retrieved successfully"},
            exit_code=0,
        )
    except ImportError:
        return InterfaceResponse(
            data={
                "metrics": {},
                "message": "prometheus_client not available (install [monitoring] extra)",
            },
            exit_code=0,
        )
    except Exception as e:
        return InterfaceResponse(
            data={"metrics": {}, "error": str(e), "message": "Failed to retrieve system metrics"},
            exit_code=1,
        )
