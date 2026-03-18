"""Orchestrator for deleting a template."""

from __future__ import annotations

from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import DeleteTemplateInput, DeleteTemplateOutput
from orb.application.template.commands import DeleteTemplateCommand
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports.logging_port import LoggingPort


class DeleteTemplateOrchestrator(OrchestratorBase[DeleteTemplateInput, DeleteTemplateOutput]):
    """Orchestrator for deleting a template."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus  # reserved for future query-side operations
        self._logger = logger

    async def execute(self, input: DeleteTemplateInput) -> DeleteTemplateOutput:  # type: ignore[return]
        self._logger.info("DeleteTemplateOrchestrator: template_id=%s", input.template_id)

        command = DeleteTemplateCommand(template_id=input.template_id)
        try:
            await self._command_bus.execute(command)
        except EntityNotFoundError:
            return DeleteTemplateOutput(
                template_id=input.template_id,
                deleted=False,
                raw={
                    "template_id": input.template_id,
                    "status": "not_found",
                    "deleted": False,
                },
            )

        return DeleteTemplateOutput(
            template_id=input.template_id,
            deleted=command.deleted,
            raw={
                "template_id": input.template_id,
                "status": "deleted",
                "deleted": command.deleted,
            },
        )
