"""Orchestrator for returning machines."""

from __future__ import annotations

import asyncio

from orb.application.dto.commands import CreateReturnRequestCommand
from orb.application.dto.queries import ListMachinesQuery, SyncAndGetRequestQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import (
    MAX_CONSECUTIVE_POLL_ERRORS as _MAX_CONSECUTIVE_POLL_ERRORS,
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
    OrchestratorBase,
)
from orb.application.services.orchestration.dtos import (
    Paginated,
    ReturnMachinesInput,
    ReturnMachinesOutput,
)
from orb.domain.base.exceptions import ApplicationError
from orb.domain.base.ports.logging_port import LoggingPort

# Rank semantics: 0=unknown/no-op, 1=completed, 2=failed/cancelled/error, 3=partial/timeout
_STATUS_RANK = {
    "completed": 1,
    "complete": 1,
    "cancelled": 2,
    "canceled": 2,
    "failed": 2,
    "error": 2,
    "partial": 3,
    "timeout": 3,
}


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
            "ReturnMachinesOrchestrator: machines=%s all=%s request_id=%s force=%s",
            input.machine_ids,
            input.all_machines,
            input.request_id,
            input.force,
        )

        if input.all_machines:
            result = await self._query_bus.execute(
                ListMachinesQuery(
                    all_resources=True,
                    provider_name=input.provider_name,
                    provider_type=input.provider_type,
                )
            )
            machine_dtos = result.items if isinstance(result, Paginated) else (result or [])
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
        elif input.request_id:
            result = await self._query_bus.execute(
                ListMachinesQuery(
                    request_id=input.request_id,
                    provider_name=input.provider_name,
                    provider_type=input.provider_type,
                )
            )
            machine_dtos = result.items if isinstance(result, Paginated) else (result or [])
            machine_ids = [dto.machine_id for dto in machine_dtos]
            if not machine_ids:
                self._logger.warning(
                    "ReturnMachinesOrchestrator: request_id=%s requested but no active machines found",
                    input.request_id,
                )
                return ReturnMachinesOutput(
                    request_id=None,
                    status="no_machines",
                    message="No active machines found for request",
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

        # Use the first request ID as the primary for the output, but poll ALL
        # provider-group requests so a failure in any group is not silently ignored.
        primary_request_id = command.created_request_ids[0]
        status = "pending"

        if input.wait:
            statuses = await asyncio.gather(
                *[
                    self._poll_until_terminal(rid, input.timeout_seconds)
                    for rid in command.created_request_ids
                ]
            )
            # Return the worst status: failed > partial/timeout > completed
            status = max(statuses, key=lambda s: _STATUS_RANK.get(s.lower(), 0))

        return ReturnMachinesOutput(
            request_id=primary_request_id,
            status=status,
            machine_ids=machine_ids,
        )

    async def _poll_until_terminal(self, request_id: str, timeout_seconds: int) -> str:
        """Poll SyncAndGetRequestQuery until terminal status or timeout."""
        elapsed = 0
        interval = 2
        consecutive_errors = 0
        while elapsed < timeout_seconds:
            try:
                query = SyncAndGetRequestQuery(request_id=request_id, lightweight=True)
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
