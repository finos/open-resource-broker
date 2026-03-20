"""Orchestrator for returning machines."""

from __future__ import annotations

import asyncio

from orb.application.dto.commands import CreateReturnRequestCommand
from orb.application.dto.queries import GetRequestQuery, ListMachinesQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import ReturnMachinesInput, ReturnMachinesOutput
from orb.domain.base.exceptions import ApplicationError
from orb.domain.base.ports.logging_port import LoggingPort

_TERMINAL_STATUSES = {"completed", "complete", "failed", "error", "cancelled", "canceled"}
_MAX_CONSECUTIVE_POLL_ERRORS = 3


class ReturnMachinesOrchestrator(OrchestratorBase[ReturnMachinesInput, ReturnMachinesOutput]):
    """Orchestrator for returning machines to the provider."""

    def __init__(
        self, command_bus: CommandBusPort, query_bus: QueryBusPort, logger: LoggingPort
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def execute(self, input: ReturnMachinesInput) -> ReturnMachinesOutput:  # type: ignore[return]
        self._logger.info(
            "ReturnMachinesOrchestrator: machines=%s all=%s force=%s",
            input.machine_ids,
            input.all_machines,
            input.force,
        )

        if input.all_machines:
            machine_dtos = (
                await self._query_bus.execute(ListMachinesQuery(all_resources=True)) or []
            )
            machine_ids = [dto.machine_id for dto in machine_dtos]
            if not machine_ids:
                self._logger.warning(
                    "ReturnMachinesOrchestrator: --all requested but no active machines found"
                )
                return ReturnMachinesOutput(
                    request_id=None,
                    status="no_machines",
                    message="No active machines found",
                )
        else:
            machine_ids = list(input.machine_ids)

        command = CreateReturnRequestCommand(
            machine_ids=machine_ids,
            force_return=input.force,
        )
        await self._command_bus.execute(command)

        if not command.created_request_ids:
            skipped = [str(m) for m in (command.skipped_machines or [])]
            self._logger.warning(
                "CreateReturnRequestCommand produced no request IDs; skipped=%s", skipped
            )
            return ReturnMachinesOutput(
                request_id=None,
                status="no_op",
                skipped_machines=skipped,
            )
        request_id = command.created_request_ids[0]
        status = "pending"

        if input.wait:
            status = await self._poll_until_terminal(request_id, input.timeout_seconds)

        return ReturnMachinesOutput(
            request_id=request_id,
            status=status,
        )

    async def _poll_until_terminal(self, request_id: str, timeout_seconds: int) -> str:
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
                    return status_str
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
        return "timeout"
