"""Orchestrator for cancelling a request.

Cancel semantics in ORB are "tear down everything associated with this
request": if the request has allocated machines, those machines are
returned to the provider (terminated at AWS) before the request itself
is flipped to ``CANCELLED``. Without this, marking the request cancelled
would orphan the running instances and silently rack up cost.

Provider-level abort of an in-flight fleet/ASG that has *not yet*
produced any machines (the resource exists but is still fulfilling) is
out of scope here — that requires a new ``CANCEL_PROVISIONING`` op on
every provider handler. Tracked separately.
"""

from __future__ import annotations

from orb.application.dto.commands import CancelRequestCommand
from orb.application.dto.queries import GetRequestQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    CancelRequestInput,
    CancelRequestOutput,
    ReturnMachinesInput,
)
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.request.request_types import RequestStatus


class CancelRequestOrchestrator(OrchestratorBase[CancelRequestInput, CancelRequestOutput]):
    """Orchestrator for cancelling a request.

    Composes ``ReturnMachinesOrchestrator`` for the machines-already-
    allocated case, then dispatches ``CancelRequestCommand`` to flip
    the request status.
    """

    def __init__(
        self,
        command_bus: CommandBusPort,
        query_bus: QueryBusPort,
        return_orchestrator: ReturnMachinesOrchestrator,
        logger: LoggingPort,
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._return_orchestrator = return_orchestrator
        self._logger = logger

    async def execute(self, input: CancelRequestInput) -> CancelRequestOutput:  # type: ignore[return]
        self._logger.info(
            "CancelRequestOrchestrator: request_id=%s reason=%s",
            input.request_id,
            input.reason,
        )

        machine_ids = await self._collect_machine_ids(input.request_id)
        return_status: str | None = None
        return_message: str = ""

        if machine_ids:
            self._logger.info(
                "CancelRequestOrchestrator: returning %s machine(s) before cancelling %s",
                len(machine_ids),
                input.request_id,
            )
            try:
                return_result = await self._return_orchestrator.execute(
                    ReturnMachinesInput(machine_ids=list(machine_ids), force=True)
                )
                return_status = return_result.status
                return_message = return_result.message or ""
            except Exception as exc:
                # Surface the failure to the caller but still attempt to
                # mark the request cancelled so the operator sees the
                # state change. The cost is that the machines may keep
                # running; ``return_status`` carries the error.
                self._logger.error(
                    "Return machines failed during cancel of %s: %s",
                    input.request_id,
                    exc,
                )
                return_status = "failed"
                return_message = f"Return failed: {exc}"

        command = CancelRequestCommand(request_id=input.request_id, reason=input.reason)
        await self._command_bus.execute(command)

        status = (
            command.final_status
            if command.cancelled and command.final_status
            else RequestStatus.CANCELLED.value
        )
        request_dict: dict = {"request_id": input.request_id, "status": status}
        if return_status is not None:
            request_dict["return_status"] = return_status
        if return_message:
            request_dict["return_message"] = return_message
        return CancelRequestOutput(
            request_id=input.request_id,
            status=status,
            requests=[request_dict],
        )

    async def _collect_machine_ids(self, request_id: str) -> list[str]:
        """Resolve the current machine_ids for ``request_id`` via the query bus.

        Returns an empty list on any failure; the caller will fall back
        to the DB-only cancel path in that case.
        """
        try:
            query = GetRequestQuery(request_id=request_id, verbose=False)
            request = await self._query_bus.execute(query)
        except Exception as exc:
            self._logger.warning(
                "CancelRequestOrchestrator: failed to load request %s: %s",
                request_id,
                exc,
            )
            return []
        if request is None:
            return []
        # Accept aggregate, DTO, or dict shapes — the query bus returns
        # whichever the handler emits.
        ids = getattr(request, "machine_ids", None)
        if ids is None and isinstance(request, dict):
            ids = request.get("machine_ids")
        return list(ids) if ids else []
