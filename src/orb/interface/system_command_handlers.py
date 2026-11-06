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


# Operations ``providers exec`` may run. Anything that changes the state of ORB's own
# requests or machines (terminate, create, start, stop) has a dedicated command and is
# deliberately absent: exec is for provider-side maintenance that is safe to run by hand.
EXEC_ALLOWED_OPERATIONS: frozenset[str] = frozenset({"cleanup_orphaned_resources"})


def _operation_name(operation: Any) -> str:
    return str(getattr(operation, "value", operation))


def _display_name(operation: str) -> str:
    return operation.replace("_", "-")


def _executable_operations(capabilities: Any) -> list[str]:
    """Return the allowlisted operations a provider declares, in declaration order."""
    declared = [_operation_name(op) for op in getattr(capabilities, "supported_operations", [])]
    return [op for op in dict.fromkeys(declared) if op in EXEC_ALLOWED_OPERATIONS]


def _parse_exec_parameters(args) -> dict[str, Any]:
    """Merge ``--params`` JSON and repeated ``--param key=value`` into one dict."""
    import json

    parameters: dict[str, Any] = {}
    raw_json = getattr(args, "params", None)
    if raw_json:
        loaded = json.loads(raw_json)
        if not isinstance(loaded, dict):
            raise ValueError("--params must be a JSON object")
        parameters.update(loaded)
    for item in getattr(args, "param", None) or []:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise ValueError(f"--param expects KEY=VALUE, got '{item}'")
        try:
            parameters[key] = json.loads(value)
        except ValueError:
            parameters[key] = value
    if getattr(args, "dry_run", False):
        parameters["dry_run"] = True
    return parameters


def _exec_error(message: str, **extra: Any) -> InterfaceResponse:
    return InterfaceResponse(data={"success": False, "message": message, **extra}, exit_code=1)


def _list_exec_operations(args, port: Any) -> InterfaceResponse:
    from orb.domain.base.ports.configuration_port import ConfigurationPort

    provider_name = getattr(args, "provider_name", None)
    if provider_name:
        names = [provider_name]
    else:
        names = [p.name for p in args._container.get(ConfigurationPort).get_active_providers()]

    providers = []
    for name in names:
        capabilities = port.get_strategy_capabilities(name)
        providers.append(
            {
                "provider": name,
                "supported_operations": [
                    _operation_name(op) for op in getattr(capabilities, "supported_operations", [])
                ],
                "executable_operations": [
                    _display_name(op) for op in _executable_operations(capabilities)
                ],
            }
        )
    return InterfaceResponse(data={"success": True, "providers": providers}, exit_code=0)


@handle_interface_exceptions(context="execute_provider_operation", interface_type="cli")
async def handle_execute_provider_operation(args) -> InterfaceResponse:
    """Run an allowlisted maintenance operation against a provider instance.

    The provider decides what the operation does and what is safe to touch; this
    handler checks the operation is allowed and declared by the provider, then routes it.
    """
    from orb.domain.base.operations import Operation, OperationType
    from orb.domain.base.ports import ProviderSelectionPort

    port = args._container.get(ProviderSelectionPort)

    if getattr(args, "list_operations", False):
        return _list_exec_operations(args, port)

    requested = getattr(args, "operation", None)
    if not requested:
        return _exec_error("An operation is required. Use --list to see what is available.")

    normalized = requested.strip().lower().replace("-", "_")
    try:
        operation_type = OperationType(normalized)
    except ValueError:
        operation_type = None

    selection = port.select_active_provider(
        provider_name=getattr(args, "provider_name", None),
        provider_type=getattr(args, "provider_type", None),
    )
    provider_name = selection.provider_name
    capabilities = port.get_strategy_capabilities(provider_name)
    executable = [_display_name(op) for op in _executable_operations(capabilities)]
    supported_text = ", ".join(executable) if executable else "none"

    if operation_type is None or operation_type.value not in EXEC_ALLOWED_OPERATIONS:
        allowed = ", ".join(_display_name(op) for op in sorted(EXEC_ALLOWED_OPERATIONS))
        return _exec_error(
            f"Operation '{requested}' cannot be run with 'providers exec'. Allowed: {allowed}",
            provider=provider_name,
        )

    supports = getattr(capabilities, "supports_operation", None)  # getattr: port returns Any
    if supports is None or not supports(operation_type):
        return _exec_error(
            f"Provider '{provider_name}' does not support '{_display_name(operation_type.value)}'. "
            f"Supported: {supported_text}",
            provider=provider_name,
            supported_operations=executable,
        )

    try:
        parameters = _parse_exec_parameters(args)
    except ValueError as exc:
        return _exec_error(f"Invalid operation parameters: {exc}", provider=provider_name)

    result = await port.execute_operation(
        provider_name, Operation(operation_type=operation_type, parameters=parameters)
    )
    if not result.success:
        # A partly completed operation carries its report: what was done, what failed.
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
