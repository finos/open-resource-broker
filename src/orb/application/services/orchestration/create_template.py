"""Orchestrator for creating a template."""

from __future__ import annotations

from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import CreateTemplateInput, CreateTemplateOutput
from orb.application.template.commands import CreateTemplateCommand
from orb.domain.base.ports.logging_port import LoggingPort


class CreateTemplateOrchestrator(OrchestratorBase[CreateTemplateInput, CreateTemplateOutput]):
    """Orchestrator for creating a new template."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus  # reserved for future query-side operations
        self._logger = logger

    async def execute(self, input: CreateTemplateInput) -> CreateTemplateOutput:  # type: ignore[return]
        self._logger.info("CreateTemplateOrchestrator: template_id=%s", input.template_id)

        command = CreateTemplateCommand(
            template_id=input.template_id,
            provider_api=input.provider_api,
            image_id=input.image_id,
            name=input.name,
            description=input.description,
            instance_type=input.instance_type,
            tags=input.tags,
            configuration=input.configuration,
        )
        await self._command_bus.execute(command)

        return CreateTemplateOutput(
            template_id=input.template_id,
            created=command.created,
            validation_errors=command.validation_errors or [],
        )
