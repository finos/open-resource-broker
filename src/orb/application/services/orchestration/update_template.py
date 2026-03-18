"""Orchestrator for updating a template."""

from __future__ import annotations

from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import UpdateTemplateInput, UpdateTemplateOutput
from orb.application.template.commands import UpdateTemplateCommand
from orb.domain.base.ports.logging_port import LoggingPort


class UpdateTemplateOrchestrator(OrchestratorBase[UpdateTemplateInput, UpdateTemplateOutput]):
    """Orchestrator for updating an existing template."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: UpdateTemplateInput) -> UpdateTemplateOutput:  # type: ignore[return]
        self._logger.info("UpdateTemplateOrchestrator: template_id=%s", input.template_id)

        command = UpdateTemplateCommand(
            template_id=input.template_id,
            name=input.name,
            description=input.description,
            instance_type=input.instance_type,
            image_id=input.image_id,
            configuration=input.configuration,
        )
        await self._command_bus.execute(command)

        return UpdateTemplateOutput(
            template_id=input.template_id,
            updated=command.updated,
            validation_errors=command.validation_errors or [],
            raw={
                "template_id": input.template_id,
                "status": "updated" if command.updated else "validation_failed",
                "updated": command.updated,
                "validation_errors": command.validation_errors or [],
            },
        )
