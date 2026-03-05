"""Flat command registry for CLI dispatch.

Maps (resource, action) pairs to handler callables.
Replaces the 3-tier dispatch in CLICommandFactoryOrchestrator._route_command.
"""

from __future__ import annotations

import argparse
from typing import Any, Callable

Handler = Callable[[argparse.Namespace], Any]

# Singular → plural aliases
_ALIASES: dict[str, str] = {
    "template": "templates",
    "request": "requests",
    "machine": "machines",
    "provider": "providers",
    "infra": "infrastructure",
}

_REGISTRY: dict[tuple[str, str], Handler] = {}
_built: bool = False


def register(resource: str, action: str, handler: Handler) -> None:
    """Register a handler for (resource, action)."""
    _REGISTRY[(resource, action)] = handler


def lookup(resource: str, action: str) -> Handler | None:
    """Resolve aliases and return the handler, or None if not found."""
    resolved = _ALIASES.get(resource, resource)
    return _REGISTRY.get((resolved, action))


def _make_bus_handler(factory_method_name: str) -> Handler:
    """Create an async handler that calls the named factory method and dispatches to the bus."""

    async def _handler(args: argparse.Namespace) -> Any:
        import inspect

        from application.dto.base import BaseCommand
        from cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator
        from infrastructure.di.buses import CommandBus, QueryBus
        from infrastructure.di.container import get_container

        orchestrator = CLICommandFactoryOrchestrator()

        # Try query first, then command
        factory_fn = getattr(orchestrator, f"create_{factory_method_name}_query", None)
        if factory_fn is None:
            factory_fn = getattr(orchestrator, f"create_{factory_method_name}_command", None)
        if factory_fn is None:
            raise ValueError(f"No factory method for: {factory_method_name}")

        # Build kwargs from args — pass only what the factory method accepts
        sig = inspect.signature(factory_fn)
        args_dict = vars(args).copy()
        kwargs = {k: args_dict.get(k) for k in sig.parameters if k in args_dict}
        cqrs_obj = factory_fn(**kwargs)

        container = get_container()
        if isinstance(cqrs_obj, BaseCommand):
            return await container.get(CommandBus).execute(cqrs_obj)  # type: ignore[arg-type]
        else:
            return await container.get(QueryBus).execute(cqrs_obj)

    _handler.__name__ = f"bus_handler_{factory_method_name}"
    return _handler


def build_registry() -> None:
    """Populate _REGISTRY with all (resource, action) → handler pairs."""
    global _built
    if _built:
        return
    _built = True

    # --- init ---
    from interface.init_command_handler import handle_init

    register("init", "*", handle_init)

    # --- mcp ---
    from interface.mcp.server.handler import handle_mcp_serve
    from interface.mcp_command_handlers import handle_mcp_validate

    register("mcp", "serve", handle_mcp_serve)
    register("mcp", "validate", handle_mcp_validate)

    async def _handle_mcp_tools(args: argparse.Namespace) -> Any:
        from interface.mcp_command_handlers import (
            handle_mcp_tools_call,
            handle_mcp_tools_info,
            handle_mcp_tools_list,
        )

        tools_action = getattr(args, "tools_action", None)
        if tools_action == "list":
            return await handle_mcp_tools_list(args)
        elif tools_action == "call":
            return await handle_mcp_tools_call(args)
        elif tools_action == "info":
            return await handle_mcp_tools_info(args)
        else:
            raise ValueError(f"Unknown MCP tools action: {tools_action}")

    register("mcp", "tools", _handle_mcp_tools)

    # --- infrastructure ---
    from interface.infrastructure_command_handler import (
        handle_infrastructure_discover,
        handle_infrastructure_show,
        handle_infrastructure_validate,
    )

    register("infrastructure", "discover", handle_infrastructure_discover)
    register("infrastructure", "show", handle_infrastructure_show)
    register("infrastructure", "validate", handle_infrastructure_validate)

    # --- providers ---
    from interface.provider_config_handler import (
        handle_provider_add,
        handle_provider_get_default,
        handle_provider_remove,
        handle_provider_set_default,
        handle_provider_show,
        handle_provider_update,
    )

    register("providers", "add", handle_provider_add)
    register("providers", "remove", handle_provider_remove)
    register("providers", "update", handle_provider_update)
    register("providers", "set-default", handle_provider_set_default)
    register("providers", "get-default", handle_provider_get_default)
    register("providers", "show", handle_provider_show)
    register("providers", "list", _make_bus_handler("list_available_providers"))
    register("providers", "health", _make_bus_handler("get_provider_health"))
    register("providers", "metrics", _make_bus_handler("get_provider_metrics"))
    register("providers", "exec", _make_bus_handler("execute_provider_operation"))
    register("providers", "select", _make_bus_handler("get_provider_strategy_config"))

    # --- system ---
    from interface.serve_command_handler import handle_serve_api
    from interface.system_command_handlers import (
        handle_reload_provider_config,
        handle_system_health,
        handle_system_metrics,
        handle_system_status,
    )

    register("system", "serve", handle_serve_api)
    register("system", "status", handle_system_status)
    register("system", "health", handle_system_health)
    register("system", "metrics", handle_system_metrics)
    register("system", "reload", handle_reload_provider_config)

    # --- templates ---
    from interface.template_command_handlers import (
        handle_create_template,
        handle_delete_template,
        handle_get_template,
        handle_list_templates,
        handle_refresh_templates,
        handle_update_template,
        handle_validate_template,
    )
    from interface.templates_generate_handler import handle_templates_generate

    register("templates", "list", handle_list_templates)
    register("templates", "show", handle_get_template)
    register("templates", "create", handle_create_template)
    register("templates", "update", handle_update_template)
    register("templates", "delete", handle_delete_template)
    register("templates", "validate", handle_validate_template)
    register("templates", "refresh", handle_refresh_templates)
    register("templates", "generate", handle_templates_generate)

    # --- requests ---
    from interface.request_command_handlers import (
        handle_cancel_request,
        handle_get_request_status,
        handle_request_machines,
        handle_request_return_machines,
    )

    register("requests", "create", handle_request_machines)
    register("requests", "show", handle_get_request_status)
    register("requests", "status", handle_get_request_status)
    register("requests", "list", _make_bus_handler("list_requests"))
    register("requests", "cancel", handle_cancel_request)
    register("requests", "return", handle_request_return_machines)

    # --- machines ---
    from interface.machine_command_handlers import (
        handle_get_machine_status,
        handle_list_machines,
        handle_start_machines,
        handle_stop_machines,
    )

    register("machines", "list", handle_list_machines)
    register("machines", "show", _make_bus_handler("get_machine"))
    register("machines", "status", handle_get_machine_status)
    register("machines", "request", handle_request_machines)
    register("machines", "return", handle_request_return_machines)
    register("machines", "terminate", handle_request_return_machines)
    register("machines", "stop", handle_stop_machines)
    register("machines", "start", handle_start_machines)

    # --- scheduler ---
    from interface.scheduler_command_handlers import (
        handle_list_scheduler_strategies,
        handle_show_scheduler_config,
        handle_validate_scheduler_config,
    )

    register("scheduler", "list", handle_list_scheduler_strategies)
    register("scheduler", "show", handle_show_scheduler_config)
    register("scheduler", "validate", handle_validate_scheduler_config)

    # --- storage ---
    from interface.storage_command_handlers import (
        handle_list_storage_strategies,
        handle_show_storage_config,
        handle_storage_health,
        handle_storage_metrics,
        handle_test_storage,
    )

    register("storage", "list", handle_list_storage_strategies)
    register("storage", "show", handle_show_storage_config)
    register("storage", "health", handle_storage_health)
    register("storage", "metrics", handle_storage_metrics)
    register("storage", "test", handle_test_storage)

    # --- config ---
    register("config", "show", _make_bus_handler("get_provider_config"))
    register("config", "get", _make_bus_handler("get_configuration"))
    register("config", "set", _make_bus_handler("set_configuration"))
    register("config", "validate", _make_bus_handler("validate_provider_config"))
    register("config", "reload", handle_reload_provider_config)
