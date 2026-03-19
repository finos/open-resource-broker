"""Orchestrator for acquiring (requesting) machines."""

from __future__ import annotations

import asyncio

from orb.application.dto.commands import CreateRequestCommand
from orb.application.dto.queries import GetRequestQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import AcquireMachinesInput, AcquireMachinesOutput
from orb.domain.base.exceptions import ApplicationError
from orb.domain.base.ports.logging_port import LoggingPort

_TERMINAL_STATUSES = {"completed", "complete", "failed", "error", "cancelled", "canceled"}
_MAX_CONSECUTIVE_POLL_ERRORS = 3


class AcquireMachinesOrchestrator(OrchestratorBase[AcquireMachinesInput, AcquireMachinesOutput]):
    """Orchestrator for requesting machines via a template."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: AcquireMachinesInput) -> AcquireMachinesOutput:  # type: ignore[return]
        self._logger.info(
            "AcquireMachinesOrchestrator: template=%s count=%d",
            input.template_id,
            input.requested_count,
        )

        command = CreateRequestCommand(
            template_id=input.template_id,
            requested_count=input.requested_count,
            additional_data=input.additional_data,
        )
        await self._command_bus.execute(command)
        request_id: str = command.created_request_id or ""

        status = "pending"
        machine_ids: list[str] = []

        if input.wait and request_id:
            status, machine_ids = await self._poll_until_terminal(request_id, input.timeout_seconds)

        return AcquireMachinesOutput(
            request_id=request_id,
            status=status,
            machine_ids=machine_ids,
        )

    async def _poll_until_terminal(
        self, request_id: str, timeout_seconds: int
    ) -> tuple[str, list[str]]:
        """Poll GetRequestQuery until terminal status or timeout."""
        elapsed = 0
        interval = 2
        consecutive_errors = 0
        while elapsed < timeout_seconds:
            try:
                query = GetRequestQuery(request_id=request_id, lightweight=True)
                result = await self._query_bus.execute(query)
                consecutive_errors = 0
                status_val = getattr(result, "status", None)
                status_str = (
                    status_val.value  # type: ignore[union-attr]
                    if status_val is not None and hasattr(status_val, "value")
                    else str(status_val or "")
                )
                if status_str.lower() in _TERMINAL_STATUSES:
                    machines = getattr(result, "machine_references", []) or []
                    machine_ids = [str(getattr(m, "machine_id", m)) for m in machines]
                    return status_str, machine_ids
            except Exception as exc:
                consecutive_errors += 1
                self._logger.warning(
                    "Poll error for %s (%d/%d): %s",
                    request_id,
                    consecutive_errors,
                    _MAX_CONSECUTIVE_POLL_ERRORS,
                    exc,
                )
                if consecutive_errors >= _MAX_CONSECUTIVE_POLL_ERRORS:
                    raise ApplicationError(
                        f"Polling request {request_id} aborted after "
                        f"{consecutive_errors} consecutive errors: {exc}"
                    ) from exc
            await asyncio.sleep(interval)
            elapsed += interval
        return "timeout", []
