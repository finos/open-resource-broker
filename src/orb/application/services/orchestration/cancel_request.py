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

from typing import Any, Optional

from orb.application.dto.commands import CancelRequestCommand
from orb.application.dto.queries import SyncAndGetRequestQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    CancelRequestInput,
    CancelRequestOutput,
    ReturnMachinesInput,
)
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.domain.base.exceptions import ApplicationError
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.request.request_types import RequestStatus

# Return outcomes that mean some machines may still be running; the request
# must not be marked cancelled in that case so the cancel can be retried.
_BLOCKING_RETURN_STATUSES = frozenset({"failed", "error", "partial", "timeout"})


class CancelRequestOrchestrator(OrchestratorBase[CancelRequestInput, CancelRequestOutput]):
    """Orchestrator for cancelling a request.

    Composes ``ReturnMachinesOrchestrator`` for the machines-already-
    allocated case, then dispatches ``CancelRequestCommand`` to flip
    the request status. The request is only flipped when its machines are
    known and were returned (or there are none): an unknown machine set or
    a failed / partial return raises ``ApplicationError`` and leaves the
    request in its current state so the cancel can be retried.
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

        context: dict[str, Any] = {}
        machine_ids = await self._collect_machine_ids(input.request_id, context)
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
            except Exception as exc:
                raise self._cancel_failed(
                    input.request_id,
                    f"returning machines failed ({exc}); machines may still be running",
                    context,
                ) from exc
            return_status = return_result.status
            return_message = return_result.message or ""
            if str(return_status).lower() not in _BLOCKING_RETURN_STATUSES:
                # Without waiting, the orchestrator reports "pending" even when
                # termination already failed, so read each return request's
                # recorded outcome.
                return_status = await self._worst_return_request_status(
                    return_result, return_status
                )
            if str(return_status).lower() in _BLOCKING_RETURN_STATUSES:
                raise self._cancel_failed(
                    input.request_id,
                    f"machine return ended with status '{return_status}'; "
                    "machines may still be running",
                    context,
                )

        command = CancelRequestCommand(request_id=input.request_id, reason=input.reason)
        await self._dispatch("CancelRequest", self._command_bus.execute(command))

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

    async def _worst_return_request_status(self, return_result: Any, default: str) -> str:
        """Return the first blocking status among the created return requests.

        Deprovisioning runs synchronously while the return request is created,
        so its failure or partial outcome is already stored. A request that
        cannot be read counts as a failure: the cancel must not be reported
        while the outcome of the termination is unknown.
        """
        request_ids = list(return_result.request_ids or [])
        if not request_ids and return_result.request_id:
            request_ids = [return_result.request_id]
        for return_request_id in request_ids:
            try:
                request = await self._query_bus.execute(
                    SyncAndGetRequestQuery(request_id=return_request_id, lightweight=True)
                )
            except Exception as exc:
                self._logger.error(
                    "Could not read return request %s during cancel: %s", return_request_id, exc
                )
                return "failed"
            status_val = getattr(request, "status", None)
            status = str(getattr(status_val, "value", status_val) or "").lower()
            if status in _BLOCKING_RETURN_STATUSES:
                return status
        return default

    def _cancel_failed(
        self, request_id: str, reason: str, context: dict[str, Any]
    ) -> ApplicationError:
        """Log the structured audit record for a failed cancel and build the error."""
        self._logger.error(
            "Cancel of request %s aborted: %s",
            request_id,
            reason,
            extra={
                "event": "request.cancel_failed",
                "request_id": request_id,
                "reason": reason,
                **{k: v for k, v in context.items() if v},
            },
        )
        return ApplicationError(f"Cancel of request {request_id} aborted: {reason}")

    async def _collect_machine_ids(
        self, request_id: str, context: Optional[dict[str, Any]] = None
    ) -> list[str]:
        """Resolve the current machine_ids for ``request_id`` via the query bus.

        Returns an empty list only when the request was loaded and has no
        machines. A failed or empty lookup raises ``ApplicationError``:
        cancelling without knowing the machine set would flip the request
        while its machines keep running. Provider details of the loaded request
        are copied into *context* for the audit record.
        """
        context = context if context is not None else {}
        try:
            query = SyncAndGetRequestQuery(request_id=request_id, verbose=False)
            request = await self._query_bus.execute(query)
        except Exception as exc:
            raise self._cancel_failed(
                request_id, f"could not load its machines ({exc})", context
            ) from exc
        if request is None:
            raise self._cancel_failed(request_id, "request could not be loaded", context)
        # Accept aggregate, DTO, or dict shapes — the query bus returns
        # whichever the handler emits.
        for key in ("provider_name", "provider_type", "provider_api", "resource_id", "region"):
            value = (
                request.get(key) if isinstance(request, dict) else getattr(request, key, None)
            )  # getattr: aggregate, DTO and dict shapes differ
            if isinstance(value, str):
                context[key] = value
        ids = getattr(request, "machine_ids", None)
        if ids is None and isinstance(request, dict):
            ids = request.get("machine_ids")
        return list(ids) if ids else []
