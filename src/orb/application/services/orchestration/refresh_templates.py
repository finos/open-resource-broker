"""Orchestrator for refreshing templates."""

from __future__ import annotations

from orb.application.commands.system import RefreshTemplatesCommand
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    RefreshTemplatesInput,
    RefreshTemplatesOutput,
)
from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.template.dtos import TemplateDTO


class RefreshTemplatesOrchestrator(OrchestratorBase[RefreshTemplatesInput, RefreshTemplatesOutput]):
    """Orchestrator for refreshing templates from all sources."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: RefreshTemplatesInput) -> RefreshTemplatesOutput:  # type: ignore[return]
        self._logger.info(
            "RefreshTemplatesOrchestrator: provider_name=%s",
            input.provider_name,
        )

        command = RefreshTemplatesCommand(provider_name=input.provider_name)
        await self._command_bus.execute(command)

        result = command.result or {}
        raw_templates: list[dict] = result.get("templates", [])
        templates = [TemplateDTO.from_dict(t) for t in raw_templates if isinstance(t, dict)]
        return RefreshTemplatesOutput(templates=templates)
