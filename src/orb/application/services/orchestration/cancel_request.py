"""Orchestrator for cancelling a request."""

from __future__ import annotations

from orb.application.dto.commands import CancelRequestCommand
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import CancelRequestInput, CancelRequestOutput
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.request.request_types import RequestStatus


class CancelRequestOrchestrator(OrchestratorBase[CancelRequestInput, CancelRequestOutput]):
    """Orchestrator for cancelling a request."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus  # reserved for future query-side operations
        self._logger = logger

    async def execute(self, input: CancelRequestInput) -> CancelRequestOutput:  # type: ignore[return]
        self._logger.info(
            "CancelRequestOrchestrator: request_id=%s reason=%s",
            input.request_id,
            input.reason,
        )

        command = CancelRequestCommand(request_id=input.request_id, reason=input.reason)
        await self._command_bus.execute(command)

        status = (
            command.final_status
            if command.cancelled and command.final_status
            else RequestStatus.CANCELLED.value
        )
        request_dict = {"request_id": input.request_id, "status": status}
        return CancelRequestOutput(
            request_id=input.request_id,
            status=status,
            raw=request_dict,
            requests=[request_dict],
        )
