"""Config command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    from orb.application.dto.interface_response import InterfaceResponse
    from orb.application.ports.command_bus_port import CommandBusPort
    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.interface.response_formatting_service import ResponseFormattingService


class ConfigHandlers:
    """CLI handlers that read and change the configuration."""

    def __init__(
        self,
        query_bus: QueryBusPort,
        command_bus: CommandBusPort,
        formatter: ResponseFormattingService,
    ) -> None:
        self._query_bus = query_bus
        self._command_bus = command_bus
        self._formatter = formatter

    @handle_interface_exceptions(context="get_system_config", interface_type="cli")
    async def get_system_config(self, args: Any) -> dict[str, Any] | InterfaceResponse:
        from orb.cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator

        formatter = self._formatter
        factory = CLICommandFactoryOrchestrator()
        query = factory.create_get_system_config_query(verbose=getattr(args, "verbose", False))
        result = await self._query_bus.execute(query)
        raw: dict[str, Any] = (
            result.model_dump()
            if hasattr(result, "model_dump")
            else (result if isinstance(result, dict) else vars(result))
        )
        return formatter.format_config(raw)

    @handle_interface_exceptions(context="get_configuration", interface_type="cli")
    async def get_configuration(self, args: Any) -> dict[str, Any] | InterfaceResponse:
        from orb.cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator

        formatter = self._formatter
        factory = CLICommandFactoryOrchestrator()
        key: str | None = getattr(args, "key", None) or getattr(args, "flag_key", None)
        if not key:
            return formatter.format_error("Configuration key is required")
        query = factory.create_get_configuration_query(key=key)
        result = await self._query_bus.execute(query)
        raw: dict[str, Any] = result if isinstance(result, dict) else {"key": key, "value": result}
        return formatter.format_config(raw)

    @handle_interface_exceptions(context="set_configuration", interface_type="cli")
    async def set_configuration(self, args: Any) -> dict[str, Any] | InterfaceResponse:
        from orb.cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator

        formatter = self._formatter
        factory = CLICommandFactoryOrchestrator()
        key: str | None = getattr(args, "key", None)
        value: Any = getattr(args, "value", None)
        if not key or value is None:
            return formatter.format_error("Key and value are required")
        persist: bool = getattr(args, "persist", True)
        cmd = factory.create_set_configuration_command(key=key, value=value, persist=persist)
        result = await self._command_bus.execute(cmd)
        raw: dict[str, Any] = (
            result if isinstance(result, dict) else {"key": key, "value": value, "success": True}
        )
        return formatter.format_config(raw)

    @handle_interface_exceptions(context="validate_provider_config", interface_type="cli")
    async def validate_provider_config(self, args: Any) -> dict[str, Any] | InterfaceResponse:
        from orb.cli.factories.cli_command_factory_orchestrator import CLICommandFactoryOrchestrator

        formatter = self._formatter
        factory = CLICommandFactoryOrchestrator()
        query = factory.create_validate_provider_config_query(
            verbose=getattr(args, "verbose", False)
        )
        result = await self._query_bus.execute(query)
        raw: dict[str, Any] = result if isinstance(result, dict) else vars(result)
        return formatter.format_config(raw)
