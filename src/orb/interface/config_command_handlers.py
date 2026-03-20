"""Config command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

from orb.infrastructure.di.container import get_container
from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.interface.response_formatting_service import ResponseFormattingService

if TYPE_CHECKING:
    from orb.application.dto.interface_response import InterfaceResponse


@handle_interface_exceptions(context="get_system_config", interface_type="cli")
async def handle_get_system_config(args: Any) -> "Union[dict[str, Any], InterfaceResponse]":
    from orb.cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    formatter = container.get(ResponseFormattingService)
    factory = CLICommandFactoryOrchestrator()
    query = factory.create_get_system_config_query(verbose=getattr(args, "verbose", False))
    result = await container.get(QueryBus).execute(query)
    raw: dict[str, Any] = (
        result.model_dump()
        if hasattr(result, "model_dump")
        else (result if isinstance(result, dict) else vars(result))
    )
    return formatter.format_config(raw)


@handle_interface_exceptions(context="get_configuration", interface_type="cli")
async def handle_get_configuration(args: Any) -> "Union[dict[str, Any], InterfaceResponse]":
    from orb.cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    formatter = container.get(ResponseFormattingService)
    factory = CLICommandFactoryOrchestrator()
    key: str | None = getattr(args, "key", None) or getattr(args, "flag_key", None)
    if not key:
        return formatter.format_error("Configuration key is required")
    query = factory.create_get_configuration_query(key=key)
    result = await container.get(QueryBus).execute(query)
    raw: dict[str, Any] = result if isinstance(result, dict) else {"key": key, "value": result}
    return formatter.format_config(raw)


@handle_interface_exceptions(context="set_configuration", interface_type="cli")
async def handle_set_configuration(args: Any) -> "Union[dict[str, Any], InterfaceResponse]":
    from orb.cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator
    from orb.infrastructure.di.buses import CommandBus

    container = get_container()
    formatter = container.get(ResponseFormattingService)
    factory = CLICommandFactoryOrchestrator()
    key: str | None = getattr(args, "key", None)
    value: Any = getattr(args, "value", None)
    if not key or value is None:
        return formatter.format_error("Key and value are required")
    cmd = factory.create_set_configuration_command(key=key, value=value)
    result = await container.get(CommandBus).execute(cmd)
    raw: dict[str, Any] = (
        result if isinstance(result, dict) else {"key": key, "value": value, "success": True}
    )
    return formatter.format_config(raw)


@handle_interface_exceptions(context="validate_provider_config", interface_type="cli")
async def handle_validate_provider_config(args: Any) -> "Union[dict[str, Any], InterfaceResponse]":
    from orb.cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator
    from orb.infrastructure.di.buses import QueryBus

    container = get_container()
    formatter = container.get(ResponseFormattingService)
    factory = CLICommandFactoryOrchestrator()
    query = factory.create_validate_provider_config_query(verbose=getattr(args, "verbose", False))
    result = await container.get(QueryBus).execute(query)
    raw: dict[str, Any] = result if isinstance(result, dict) else vars(result)
    return formatter.format_config(raw)
