"""Flat command registry for CLI dispatch.

Maps (resource, action) pairs to handler callables.
Replaces the 3-tier dispatch in CLICommandFactoryOrchestrator._route_command.
"""

from __future__ import annotations

import argparse
from typing import Any, Callable, TypeVar

Handler = Callable[[argparse.Namespace], Any]

# Registers a command group's handler classes in a container; idempotent.
GroupRegistrar = Callable[[Any], None]

_H = TypeVar("_H")

# Singular → plural aliases
_ALIASES: dict[str, str] = {
    "template": "templates",
    "request": "requests",
    "machine": "machines",
    "provider": "providers",
    "infra": "infrastructure",
}

_REGISTRY: dict[tuple[str, str], Handler] = {}


def register(resource: str, action: str, handler: Handler) -> None:
    """Register a handler for (resource, action)."""
    _REGISTRY[(resource, action)] = handler


def lookup(resource: str, action: str) -> Handler | None:
    """Resolve aliases and return the handler, or None if not found."""
    resolved = _ALIASES.get(resource, resource)
    return _REGISTRY.get((resolved, action))


def _current_container() -> Any:
    """Return the process-wide container (the single dispatch-time lookup in this module)."""
    from orb.infrastructure.di.container import get_container

    return get_container()


def bind(handler_cls: type[_H], method: str, *, group: GroupRegistrar) -> Handler:
    """Return a CLI handler that runs ``handler_cls.method`` on a container-built instance.

    The handler class is registered through ``group`` on the current container and
    resolved at dispatch time, so importing the registry never builds a container.
    Only the resolution is wrapped by the interface exception decorator; the
    awaited method keeps its own, so a failure is handled once.
    """
    from orb.infrastructure.error.decorators import handle_interface_exceptions

    @handle_interface_exceptions(context=f"{handler_cls.__name__}.{method}", interface_type="cli")
    def _resolve() -> _H:
        container = _current_container()
        group(container)
        return container.get(handler_cls)

    async def _bound(args: argparse.Namespace) -> Any:
        handler = _resolve()
        return await getattr(handler, method)(args)

    _bound.__name__ = f"{handler_cls.__name__}.{method}"
    return _bound


def _make_bus_handler(factory_method_name: str) -> Handler:
    """Create an async handler that calls the named factory method and dispatches to the bus."""

    async def _handler(args: argparse.Namespace) -> Any:
        import inspect

        from orb.application.dto.base import BaseCommand
        from orb.application.ports.command_bus_port import CommandBusPort
        from orb.application.ports.query_bus_port import QueryBusPort
        from orb.cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator

        orchestrator = CLICommandFactoryOrchestrator()

        # Try query first, then command
        factory_fn = getattr(orchestrator, f"create_{factory_method_name}_query", None)
        if factory_fn is None:
            factory_fn = getattr(orchestrator, f"create_{factory_method_name}_command", None)
        if factory_fn is None:
            raise ValueError(f"No factory method for: {factory_method_name}")

        # Build kwargs from args — pass only what the factory method accepts.
        # For each parameter k, check args_dict[k] first (positional), then
        # args_dict['flag_'+k] as fallback (e.g. --machine-id stored as flag_machine_id).
        sig = inspect.signature(factory_fn)
        args_dict = vars(args).copy()
        kwargs = {}
        for k in sig.parameters:
            if k in args_dict and args_dict[k] is not None:
                kwargs[k] = args_dict[k]
            elif f"flag_{k}" in args_dict and args_dict[f"flag_{k}"] is not None:
                kwargs[k] = args_dict[f"flag_{k}"]
        cqrs_obj = factory_fn(**kwargs)

        container = _current_container()
        if isinstance(cqrs_obj, BaseCommand):
            return await container.get(CommandBusPort).execute(cqrs_obj)  # type: ignore[arg-type]
        else:
            return await container.get(QueryBusPort).execute(cqrs_obj)

    _handler.__name__ = f"bus_handler_{factory_method_name}"
    return _handler


def build_registry() -> None:
    """Populate _REGISTRY with all (resource, action) → handler pairs."""
    if _REGISTRY:
        return

    # --- init ---
    from orb.interface.init_command_handler import handle_init

    register("init", "*", handle_init)

    # --- mcp ---
    # The MCP server needs the optional ``mcp`` extra. Skip registering its
    # commands when the extra is not installed so the rest of the CLI still
    # loads; ``orb mcp`` then reports an unknown command rather than the whole
    # registry failing to build.
    # Handlers are wrapped so the MCP stack is only imported when an ``orb mcp``
    # command actually runs.
    import importlib.util

    if importlib.util.find_spec("mcp") is not None:

        async def _mcp_serve(args: Any) -> Any:
            from orb.interface.mcp.catalog_server import handle_mcp_serve

            return await handle_mcp_serve(args)

        async def _mcp_validate(args: Any) -> Any:
            from orb.interface.mcp.catalog_server import handle_mcp_validate

            return await handle_mcp_validate(args)

        register("mcp", "serve", _mcp_serve)
        register("mcp", "validate", _mcp_validate)

    # --- infrastructure ---
    from orb.interface.infrastructure_command_handler import (
        handle_infrastructure_discover,
        handle_infrastructure_show,
        handle_infrastructure_validate,
    )

    register("infrastructure", "discover", handle_infrastructure_discover)
    register("infrastructure", "show", handle_infrastructure_show)
    register("infrastructure", "validate", handle_infrastructure_validate)

    # --- providers ---
    from orb.interface.provider_config_handler import (
        handle_provider_add,
        handle_provider_get,
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
    register("providers", "get", handle_provider_get)
    register("providers", "show", handle_provider_show)
    from orb.interface.system_command_handlers import (
        handle_execute_provider_operation,
        handle_list_providers,
        handle_provider_health,
        handle_provider_metrics,
        handle_select_provider_strategy,
    )

    register("providers", "list", handle_list_providers)
    register("providers", "health", handle_provider_health)
    register("providers", "metrics", handle_provider_metrics)
    register("providers", "exec", handle_execute_provider_operation)
    register("providers", "select", handle_select_provider_strategy)

    # --- system ---
    from orb.interface.system_command_handlers import (
        handle_reload_provider_config,
        handle_system_health,
        handle_system_metrics,
        handle_system_status,
    )

    register("system", "status", handle_system_status)
    register("system", "health", handle_system_health)
    register("system", "metrics", handle_system_metrics)
    register("system", "reload", handle_reload_provider_config)

    # --- server (process lifecycle) ---
    from orb.interface.server_command_handlers import (
        handle_server_logs,
        handle_server_reload,
        handle_server_restart,
        handle_server_start,
        handle_server_status,
        handle_server_stop,
        handle_server_ui_export,
    )

    register("server", "start", handle_server_start)
    register("server", "stop", handle_server_stop)
    register("server", "status", handle_server_status)
    register("server", "restart", handle_server_restart)
    register("server", "reload", handle_server_reload)
    register("server", "logs", handle_server_logs)
    register("server", "ui-export", handle_server_ui_export)

    # --- templates ---
    from orb.interface.template_command_handlers import (
        handle_create_template,
        handle_delete_template,
        handle_get_multiple_templates,
        handle_get_template,
        handle_list_templates,
        handle_refresh_templates,
        handle_update_template,
        handle_validate_template,
    )
    from orb.interface.templates_generate_handler import handle_templates_generate

    register("templates", "list", handle_list_templates)
    register("templates", "show", handle_get_template)
    register("templates", "create", handle_create_template)
    register("templates", "update", handle_update_template)
    register("templates", "delete", handle_delete_template)
    register("templates", "validate", handle_validate_template)
    register("templates", "refresh", handle_refresh_templates)
    register("templates", "generate", handle_templates_generate)
    register("templates", "get-bulk", handle_get_multiple_templates)

    # --- requests ---
    from orb.cli.handler_wiring import register_request_handlers
    from orb.interface.request_command_handlers import RequestWriteHandlers
    from orb.interface.request_query_handlers import RequestReadHandlers

    def _request_queries(method: str) -> Handler:
        return bind(RequestReadHandlers, method, group=register_request_handlers)

    def _request_commands(method: str) -> Handler:
        return bind(RequestWriteHandlers, method, group=register_request_handlers)

    register("requests", "show", _request_queries("get_request_status"))
    register("requests", "status", _request_queries("get_request_status"))
    register("requests", "list", _request_queries("list_requests"))
    register("requests", "cancel", _request_commands("cancel_request"))
    register("requests", "confirm-terminated", _request_commands("confirm_terminated"))
    register("requests", "list-returns", _request_queries("get_return_requests"))
    register("requests", "watch", _request_queries("watch_request_status"))
    register("requests", "get-bulk", _request_queries("get_multiple_requests"))

    # --- machines ---
    from orb.cli.handler_wiring import register_machine_handlers
    from orb.interface.machine_command_handlers import MachineHandlers

    def _machines(method: str) -> Handler:
        return bind(MachineHandlers, method, group=register_machine_handlers)

    register("machines", "list", _machines("list_machines"))
    register("machines", "show", _machines("get_machine"))
    register("machines", "status", _machines("get_machine_status"))
    register("machines", "request", _request_commands("request_machines"))
    register("machines", "return", _request_commands("request_return_machines"))
    register("machines", "terminate", _request_commands("request_return_machines"))
    register("machines", "stop", _machines("stop_machines"))
    register("machines", "start", _machines("start_machines"))
    register("machines", "get-bulk", _machines("get_multiple_machines"))

    # --- scheduler ---
    from orb.interface.scheduler_command_handlers import (
        handle_list_scheduler_strategies,
        handle_show_scheduler_config,
        handle_validate_scheduler_config,
    )

    register("scheduler", "list", handle_list_scheduler_strategies)
    register("scheduler", "show", handle_show_scheduler_config)
    register("scheduler", "validate", handle_validate_scheduler_config)

    # --- storage ---
    from orb.interface.storage_command_handlers import (
        handle_list_storage_strategies,
        handle_show_storage_config,
        handle_storage_health,
        handle_storage_metrics,
        handle_storage_migrate,
        handle_test_storage,
        handle_validate_storage_config,
    )

    register("storage", "list", handle_list_storage_strategies)
    register("storage", "show", handle_show_storage_config)
    register("storage", "health", handle_storage_health)
    register("storage", "metrics", handle_storage_metrics)
    register("storage", "migrate", handle_storage_migrate)
    register("storage", "test", handle_test_storage)
    register("storage", "validate", handle_validate_storage_config)

    # --- config ---
    from orb.interface.config_command_handlers import (
        handle_get_configuration,
        handle_get_system_config,
        handle_set_configuration,
        handle_validate_provider_config,
    )

    register("config", "show", handle_get_system_config)
    register("config", "get", handle_get_configuration)
    register("config", "set", handle_set_configuration)
    register("config", "validate", handle_validate_provider_config)
    register("config", "reload", handle_reload_provider_config)
