"""Orchestrator for cancelling a request.

Cancel semantics in ORB are "tear down everything associated with this
request". For a request that is not yet settled this runs in three steps:

1. Provider-side provisioning is aborted for every resource of the request
   (fleet, Spot Fleet, Auto Scaling group, ...) with the ``CANCEL_PROVISIONING``
   operation. This stops capacity from growing, whether or not any machine has
   been recorded yet, and also covers partially fulfilled requests whose fleet
   would otherwise keep fulfilling after the request reads cancelled. The
   instances still attached to the aborted resources are listed read-only by
   the provider.
2. Those machines, together with the machines already recorded for the request,
   are returned to the provider.
3. The request itself is flipped to ``CANCELLED``.

If any step fails, the request is left in its current state so the cancel can
be retried; aborting a resource that is already gone is a success.
"""

from __future__ import annotations

from typing import Any, Optional

from orb.application.dto.commands import CancelRequestCommand
from orb.application.dto.queries import GetMachineQuery, SyncAndGetRequestQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.orchestration.base import OrchestratorBase
from orb.application.services.orchestration.dtos import (
    CancelRequestInput,
    CancelRequestOutput,
    ReturnMachinesInput,
)
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.application.services.provisioning_abort import abort_provisioning
from orb.domain.base.exceptions import (
    ApplicationError,
    EntityNotFoundError,
    ProviderContractError,
)
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_selection_port import ProviderSelectionPort
from orb.domain.request.request_types import RequestStatus

# Return outcomes that mean some machines may still be running; the request
# must not be marked cancelled in that case so the cancel can be retried.
_BLOCKING_RETURN_STATUSES = frozenset({"failed", "error", "partial", "timeout"})

# Requests in these states have no provisioning left to abort: the return flow
# deletes the resources of a completed request, and a failed one has nothing
# running. A cancelled request is not listed: if an earlier abort did not take
# effect (a throttled call, an open breaker) its resources are still recorded on
# it, and cancelling again aborts them once more. Aborting is idempotent.
_SETTLED_STATUSES = frozenset(
    {
        RequestStatus.COMPLETED.value,
        RequestStatus.FAILED.value,
    }
)

# Outcomes of aborting one resource that mean no more capacity will be added.
_ABORT_OK_OUTCOMES = frozenset({"aborted", "already_gone"})


class CancelRequestOrchestrator(OrchestratorBase[CancelRequestInput, CancelRequestOutput]):
    """Orchestrator for cancelling a request.

    Aborts provider-side provisioning for requests that are not settled, composes
    ``ReturnMachinesOrchestrator`` for the machines to return, then dispatches
    ``CancelRequestCommand`` to flip the request status. The request is only
    flipped when the abort succeeded and its machines are known and were
    returned (or there are none): a failed abort, an unknown machine set or a
    failed / partial return raises ``ApplicationError`` and leaves the request in
    its current state so the cancel can be retried.
    """

    def __init__(
        self,
        command_bus: CommandBusPort,
        query_bus: QueryBusPort,
        return_orchestrator: ReturnMachinesOrchestrator,
        logger: LoggingPort,
        provider_selection_port: Optional[ProviderSelectionPort] = None,
    ) -> None:
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._return_orchestrator = return_orchestrator
        self._logger = logger
        self._provider_selection_port = provider_selection_port

    async def execute(self, input: CancelRequestInput) -> CancelRequestOutput:  # type: ignore[return]
        self._logger.info(
            "CancelRequestOrchestrator: request_id=%s reason=%s",
            input.request_id,
            input.reason,
        )

        context: dict[str, Any] = {}
        loaded = await self._load_request(input.request_id, context)
        machine_ids = list(loaded["machine_ids"])
        return_status: str | None = None
        return_message: str = ""

        provider_abort: list[dict[str, Any]] = []
        abort_ran = False
        if self._needs_abort(loaded):
            provider_abort, listed_ids = await self._abort_provisioning(
                input.request_id, loaded, context
            )
            abort_ran = any(o["outcome"] in _ABORT_OK_OUTCOMES for o in provider_abort)
            # Machines listed on the aborted resources join the recorded ones when
            # ORB knows them; returning is idempotent, so overlap is fine.
            extra_ids = await self._known_machines(
                input.request_id, [i for i in listed_ids if i not in machine_ids]
            )
            machine_ids.extend(extra_ids)

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

        # A request that had already settled (partial / timeout / complete) stays in
        # that state; report its true status rather than claiming it was cancelled.
        status = command.final_status or RequestStatus.CANCELLED.value

        # Creation may have recorded resources after this cancel loaded the request
        # (a store without versioning can even have put the request back in progress).
        # Re-read it and abort anything that appeared.
        late = await self._abort_resources_recorded_since(
            input.request_id, loaded, provider_abort, context
        )
        if late:
            provider_abort.extend(late)
            abort_ran = True
        request_dict: dict = {"request_id": input.request_id, "status": status}
        if return_status is not None:
            request_dict["return_status"] = return_status
        if return_message:
            request_dict["return_message"] = return_message
        if provider_abort:
            request_dict["provider_abort"] = provider_abort
        return CancelRequestOutput(
            request_id=input.request_id,
            status=status,
            requests=[request_dict],
            # The cancel took effect when the request was flipped, or when it was
            # already settled as partial/timeout but its provisioning was aborted
            # and its machines returned.
            cancel_effective=bool(command.cancelled or abort_ran),
        )

    async def _known_machines(self, request_id: str, candidate_ids: list[str]) -> list[str]:
        """Return the candidates ORB has a machine record for.

        The return flow rejects machines it has no record of. Instances launched by an
        aborted resource that ORB never recorded are terminated by the abort itself
        (fleet delete with termination, forced group delete), so they are reported
        and left out of the return rather than failing the cancel.
        """
        known: list[str] = []
        unknown: list[str] = []
        for machine_id in candidate_ids:
            try:
                machine = await self._query_bus.execute(GetMachineQuery(machine_id=machine_id))
            except Exception:
                machine = None
            (known if machine else unknown).append(machine_id)
        if unknown:
            self._logger.warning(
                "CancelRequestOrchestrator: %s instance(s) of request %s have no machine record "
                "and are left to the provider-side abort: %s",
                len(unknown),
                request_id,
                unknown,
                extra={
                    "event": "request.cancel_unrecorded_members",
                    "request_id": request_id,
                    "instance_ids": unknown,
                },
            )
        return known

    async def _abort_resources_recorded_since(
        self,
        request_id: str,
        loaded: dict[str, Any],
        done: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Abort resources that appeared on the request after it was loaded.

        Returns the outcomes for those resources (empty when there are none).
        """
        try:
            reloaded = await self._load_request(request_id, {}, lightweight=True)
        except Exception as exc:
            self._logger.warning(
                "CancelRequestOrchestrator: could not re-read %s after cancelling: %s",
                request_id,
                exc,
            )
            return []
        known = set(loaded["resource_ids"]) | {str(o.get("resource_id")) for o in done}
        new_ids = [r for r in reloaded["resource_ids"] if r not in known]
        if not new_ids or not reloaded.get("provider_api"):
            return []
        if (reloaded.get("status") or "").lower() != RequestStatus.CANCELLED.value:
            # The request was put back in progress; cancel it again.
            await self._dispatch(
                "CancelRequest",
                self._command_bus.execute(
                    CancelRequestCommand(request_id=request_id, reason="Cancelled during creation")
                ),
            )
        outcomes, _ = await self._abort_provisioning(
            request_id, {**reloaded, "resource_ids": new_ids}, context
        )
        return outcomes

    @staticmethod
    def _needs_abort(loaded: dict[str, Any]) -> bool:
        """True when the request has resources whose provisioning may still be running."""
        status = loaded.get("status")
        return (
            isinstance(status, str)
            and status.lower() not in _SETTLED_STATUSES
            and bool(loaded.get("resource_ids"))
            and bool(loaded.get("provider_api"))
        )

    async def _abort_provisioning(
        self, request_id: str, loaded: dict[str, Any], context: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Abort provider-side provisioning; return per-resource outcomes and listed machine ids.

        A provider that cannot abort is recorded as ``unsupported`` and the cancel
        continues. Any failed resource raises, leaving the request unchanged.
        """
        resource_ids = list(loaded["resource_ids"])
        provider_name = loaded.get("provider_name")
        provider_api = loaded["provider_api"]

        def recorded(outcome: str, message: str) -> list[dict[str, Any]]:
            return [
                {"resource_id": rid, "outcome": outcome, "message": message} for rid in resource_ids
            ]

        if self._provider_selection_port is None or not provider_name:
            reason = "no provider is bound to this request"
            self._logger.warning(
                "CancelRequestOrchestrator: cannot abort provisioning of %s: %s", request_id, reason
            )
            return recorded("unsupported", reason), []

        try:
            abort = await abort_provisioning(
                self._provider_selection_port,
                provider_name,
                request_id,
                provider_api,
                resource_ids,
            )
        except Exception as exc:
            raise self._cancel_failed(
                request_id,
                f"aborting provisioning failed ({exc}); resources may still be launching",
                context,
            ) from exc
        if not abort.supported:
            self._logger.warning(
                "CancelRequestOrchestrator: cannot abort provisioning of %s: %s",
                request_id,
                abort.reason,
            )
            return recorded("unsupported", abort.reason), []

        outcomes = list(abort.outcomes)
        listed = list(abort.members)
        if not abort.success:
            raise self._cancel_failed(
                request_id,
                f"aborting provisioning failed ({abort.reason}); resources may still be launching",
                {**context, "provider_abort": outcomes},
            )
        if not outcomes:
            outcomes = recorded("aborted", "")
        self._logger.info(
            "CancelRequestOrchestrator: aborted provisioning of %s (%s)",
            request_id,
            ", ".join(f"{o['resource_id']}={o['outcome']}" for o in outcomes),
        )
        return outcomes, listed

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
                    SyncAndGetRequestQuery(
                        request_id=return_request_id, lightweight=False, skip_cache=True
                    )
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

    async def _load_request(
        self,
        request_id: str,
        context: Optional[dict[str, Any]] = None,
        lightweight: bool = False,
    ) -> dict[str, Any]:
        """Load the request through the query bus.

        Returns its status, machine ids, resource ids and provider coordinates.
        The machine ids are empty only when the request was loaded and has no
        machines. A failed or empty lookup raises ``ApplicationError``:
        cancelling without knowing the machine set would flip the request
        while its machines keep running. Provider details of the loaded request
        are copied into *context* for the audit record.
        """
        context = context if context is not None else {}
        try:
            query = SyncAndGetRequestQuery(
                request_id=request_id, verbose=False, lightweight=lightweight
            )
            try:
                request = await self._query_bus.execute(query)
            except ProviderContractError:
                if lightweight:
                    raise
                # A request whose creation is still in flight has no provider verdict
                # to sync yet. Its stored state is enough to cancel it: the creation
                # path aborts whatever it creates once it sees the cancel.
                self._logger.warning(
                    "CancelRequestOrchestrator: %s has no provider status yet; "
                    "cancelling from its stored state",
                    request_id,
                )
                request = await self._query_bus.execute(
                    SyncAndGetRequestQuery(request_id=request_id, verbose=False, lightweight=True)
                )
        except EntityNotFoundError:
            # An unknown request is a not-found outcome, not a failed cancel.
            raise
        except Exception as exc:
            raise self._cancel_failed(
                request_id, f"could not load its machines ({exc})", context
            ) from exc
        if request is None:
            raise self._cancel_failed(request_id, "request could not be loaded", context)

        def field(key: str) -> Any:
            # getattr: aggregate, DTO and dict shapes differ
            return request.get(key) if isinstance(request, dict) else getattr(request, key, None)

        # Accept aggregate, DTO, or dict shapes — the query bus returns
        # whichever the handler emits.
        for key in ("provider_name", "provider_type", "provider_api", "resource_id", "region"):
            value = field(key)
            if isinstance(value, str):
                context[key] = value
        ids = field("machine_ids")
        status = field("status")
        status_value = getattr(status, "value", status)
        resource_ids = field("resource_ids")
        provider_name = field("provider_name")
        provider_api = field("provider_api")
        return {
            "machine_ids": list(ids) if ids else [],
            "status": status_value if isinstance(status_value, str) else None,
            "resource_ids": (
                [str(r) for r in resource_ids] if isinstance(resource_ids, (list, tuple)) else []
            ),
            "provider_name": provider_name if isinstance(provider_name, str) else None,
            "provider_api": provider_api if isinstance(provider_api, str) else None,
        }
