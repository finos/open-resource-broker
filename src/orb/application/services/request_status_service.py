"""Request status service for business logic.

Acquire path (fulfilment-based)
---------------------------------
The application layer trusts the provider's ``ProviderFulfilment`` verdict
exclusively.  No count math.  No provider-specific key inspection.

Every provider's ``check_hosts_status`` MUST return a ``CheckHostsStatusResult``
with a ``ProviderFulfilment``.  If the fulfilment is missing the service raises
``ProviderContractError`` — a hard error, not a silent fallback.

Return path
-----------
``determine_status_from_machines`` still uses the existing machine-status
counting for return requests because termination is observable via instance
states (shutting-down → terminated) without a fleet-level capacity concept.
The return path is unchanged.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Optional, Tuple

from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import ProviderContractError
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import (
    FulfilmentEvent,
    FulfilmentStateMachine,
)
from orb.domain.request.request_types import RequestStatus, RequestType

# Default grace period used only when no FulfilmentStateMachine is injected
# (e.g. lightweight unit-test construction). Production always injects the
# config-driven machine via DI.
_DEFAULT_GRACE_PERIOD_SECONDS = 3600


class RequestStatusService:
    """Business logic for request status management."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        state_machine: Optional[FulfilmentStateMachine] = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.logger = logger
        self._state_machine = state_machine or FulfilmentStateMachine(
            grace_period_seconds=_DEFAULT_GRACE_PERIOD_SECONDS
        )

    def determine_status_from_machines(
        self,
        db_machines: list[Machine],
        provider_machines: list[Machine],
        request: Request,
        provider_metadata: dict,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Determine request status from machine states.

        For acquire requests the provider MUST supply a ``ProviderFulfilment``
        via ``provider_metadata["provider_fulfilment"]``.  Any legacy
        ``fleet_capacity_fulfilment`` key is ignored — the provider contract
        is the only truth.

        For return requests the existing machine-state counting logic is used.
        """
        try:
            if request.request_type.value == "return":
                return self._determine_return_status(
                    db_machines, provider_machines, request, provider_metadata
                )
            else:
                return self._determine_acquire_status(
                    db_machines, provider_machines, request, provider_metadata
                )
        except ProviderContractError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to determine status from machines: {e}")
            return RequestStatus.IN_PROGRESS.value, "Status determination failed — will retry"

    # ------------------------------------------------------------------
    # Acquire path — trusts ProviderFulfilment exclusively
    # ------------------------------------------------------------------

    def _determine_acquire_status(
        self,
        db_machines: list[Machine],
        provider_machines: list[Machine],
        request: Request,
        provider_metadata: dict,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Map ProviderFulfilment state to RequestStatus for acquire requests."""
        fulfilment: Optional[ProviderFulfilment] = provider_metadata.get("provider_fulfilment")

        if fulfilment is None:
            raise ProviderContractError(
                f"Provider {getattr(request, 'provider_name', 'unknown')} did not emit "
                "ProviderFulfilment for acquire request. Every provider's "
                "check_hosts_status must return CheckHostsStatusResult with fulfilment."
            )

        state_map: dict[str, str] = {
            "fulfilled": RequestStatus.COMPLETED.value,
            "in_progress": RequestStatus.IN_PROGRESS.value,
            "partial": RequestStatus.PARTIAL.value,
            "failed": RequestStatus.FAILED.value,
        }
        mapped = state_map.get(fulfilment.state)
        if mapped is None:
            # Unknown state — treat as in_progress to be safe
            self.logger.warning(
                "Unknown fulfilment state '%s', treating as in_progress", fulfilment.state
            )
            return RequestStatus.IN_PROGRESS.value, fulfilment.message

        return mapped, fulfilment.message

    # ------------------------------------------------------------------
    # Return path — machine-state counting (unchanged)
    # ------------------------------------------------------------------

    def _determine_return_status(
        self,
        db_machines: list[Machine],
        provider_machines: list[Machine],
        request: Request,
        provider_metadata: dict,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Determine return request status from machine termination states."""
        db_machine_count = len(db_machines)

        # For return requests: empty provider_machines *with* DB records means all
        # instances are gone from AWS — genuinely terminated.  But if we have
        # neither DB records nor provider records we cannot distinguish "all gone"
        # from a transient gap (e.g. provider API hiccup before any machines were
        # ever stored).  Treat that ambiguous case as IN_PROGRESS to avoid
        # prematurely stamping COMPLETED when provider_machines came back empty
        # before the instances ever appeared.
        if not provider_machines:
            if db_machines:
                # We had machines on record, now provider reports none — genuinely terminated.
                return (
                    RequestStatus.COMPLETED.value,
                    f"Return request completed: all machines terminated "
                    f"(no longer visible in provider) (total in DB: {db_machine_count})",
                )
            # Neither our records nor the provider have any machines.  This is not
            # sufficient evidence of termination — could be a transient DB/provider
            # gap.  Await further polls before flipping to a terminal state.
            return (
                RequestStatus.IN_PROGRESS.value,
                "Awaiting provider confirmation of termination",
            )

        shutting_down_count = sum(
            1 for m in provider_machines if m.status.value in ["shutting-down", "stopping"]
        )
        terminated_count = sum(
            1 for m in provider_machines if m.status.value in ["terminated", "stopped"]
        )
        running_count = sum(1 for m in provider_machines if m.status.value == "running")
        failed_count = sum(1 for m in provider_machines if m.status.value == "failed")

        # Compare against the number of machines the caller submitted for return.
        completion_target = request.requested_count

        effectively_done_count = terminated_count
        if effectively_done_count >= completion_target and running_count == 0:
            return (
                RequestStatus.COMPLETED.value,
                f"Return request completed: {terminated_count} terminated, "
                f"{shutting_down_count} shutting down "
                f"(total in DB: {db_machine_count})",
            )
        elif running_count > 0:
            return (
                RequestStatus.IN_PROGRESS.value,
                f"Return in progress: {running_count} machines still running, "
                f"awaiting termination (total in DB: {db_machine_count})",
            )
        elif failed_count > 0:
            return (
                RequestStatus.FAILED.value,
                f"Return request failed: {failed_count} machines failed to terminate "
                f"(total in DB: {db_machine_count})",
            )
        else:
            return RequestStatus.IN_PROGRESS.value, "Instances terminating"

    async def update_request_status(
        self,
        request: Request,
        status: str,
        message: str,
        provider_metadata: Optional[dict] = None,
    ) -> Request:
        """Route a status update through the fulfilment state machine and persist.

        The state machine is the single write authority for ``Request.status``.
        The ``PARTIAL_PENDING`` holding state and the deadline sweep replace the
        old terminal-``PARTIAL`` upgrade hack: an illegal transition (e.g. any
        write to an already-terminal request other than an allowed upgrade) is
        rejected by the state machine and surfaces here as a no-op — the request
        is returned unchanged and nothing is persisted.

        When ``provider_metadata`` carries a ``provider_fulfilment`` the verdict
        is fed to the machine as a ``PROVIDER_VERDICT`` event (the machine owns
        the state mapping, including the deadline-dependent partial resolution)
        and the fulfilment snapshot is cached in ``metadata["last_fulfilment"]``
        so ``RequestDTO.from_domain`` can surface capacity fields.
        """
        from orb.domain.request.exceptions import InvalidRequestStateError

        now = datetime.now(timezone.utc)
        fulfilment: Optional[ProviderFulfilment] = (
            provider_metadata.get("provider_fulfilment") if provider_metadata else None
        )

        try:
            if fulfilment is not None:
                updated_request = self._state_machine.apply(
                    request,
                    FulfilmentEvent.PROVIDER_VERDICT,
                    now=now,
                    fulfilment=fulfilment,
                    message=message,
                )
            else:
                # No provider fulfilment (return path, or explicit status set):
                # synthesise a verdict from the requested status string so the
                # same state-machine policy applies uniformly.
                synthetic = self._synthesise_fulfilment(status, message)
                if synthetic is None:
                    self.logger.debug(
                        "Unknown status %r for request %s; leaving unchanged",
                        status,
                        request.request_id.value,
                    )
                    return request
                updated_request = self._state_machine.apply(
                    request,
                    FulfilmentEvent.PROVIDER_VERDICT,
                    now=now,
                    fulfilment=synthetic,
                    message=message,
                )
        except InvalidRequestStateError as exc:
            # Illegal transition (e.g. terminal request, backward step). This is
            # not an error — it is the state machine refusing an out-of-order
            # write. Leave the request untouched, matching the previous
            # "terminal requests stay put" behaviour.
            self.logger.debug(
                "State machine rejected transition for request %s: %s",
                request.request_id.value,
                exc,
            )
            return request

        # Reconcile the persisted ``successful_count`` against the authoritative
        # machine_ids count for still-progressing/terminal-success states. The
        # state machine does not touch successful_count; instant-fulfilment
        # providers (EC2Fleet instant) report "fulfilled" without emitting
        # instance_ids, so machine_ids is the source of truth. This runs even on
        # an idempotent status re-application so a lagging counter still heals.
        changed = updated_request is not request
        if updated_request.status in (
            RequestStatus.COMPLETED,
            RequestStatus.PARTIAL,
            RequestStatus.PARTIAL_PENDING,
            RequestStatus.IN_PROGRESS,
        ):
            actual_count = len(updated_request.machine_ids)
            if actual_count and actual_count != updated_request.successful_count:
                updated_request = updated_request.model_copy(
                    update={"successful_count": actual_count}
                )
                changed = True

        # Cache the latest ProviderFulfilment snapshot so DTO callers can surface it.
        if fulfilment is not None:
            snapshot = dataclasses.asdict(fulfilment)
            # ``diagnostic`` is a pydantic value object inside a frozen dataclass;
            # dataclasses.asdict leaves it as a nested value — drop it from the
            # metadata snapshot (the structured diagnostic lives on the request
            # aggregate itself, not in the capacity snapshot).
            snapshot.pop("diagnostic", None)
            updated_request = updated_request.with_last_fulfilment(snapshot)
            changed = True

        # Nothing actually changed (idempotent re-application with no counter or
        # snapshot delta) — skip the write.
        if not changed:
            return request

        try:
            with self.uow_factory.create_unit_of_work() as uow:
                uow.requests.save(updated_request)
        except Exception as e:
            self.logger.error(f"Failed to persist request status update: {e}")
            raise

        self.logger.info(
            "Updated request %s status to %s",
            request.request_id.value,
            updated_request.status.value,
        )
        return updated_request

    @staticmethod
    def _synthesise_fulfilment(status: str, message: str) -> Optional[ProviderFulfilment]:
        """Build a ProviderFulfilment verdict from a plain status string.

        Used for the return path (machine-count based) and any caller that
        supplies a status without a provider verdict, so all writes flow through
        the same state-machine policy. Returns None for unknown statuses.
        """
        try:
            target = RequestStatus(status)
        except ValueError:
            return None

        state_map: dict[RequestStatus, str] = {
            RequestStatus.COMPLETED: "fulfilled",
            RequestStatus.IN_PROGRESS: "in_progress",
            RequestStatus.ACQUIRING: "in_progress",
            RequestStatus.PARTIAL: "partial",
            RequestStatus.PARTIAL_PENDING: "partial",
            RequestStatus.FAILED: "failed",
        }
        mapped = state_map.get(target)
        if mapped is None:
            return None
        return ProviderFulfilment(state=mapped, message=message)  # type: ignore[arg-type]

    def map_machine_status_to_result(self, status: str, request_type: RequestType) -> str:
        """Map machine status to result code."""
        if request_type == RequestType.RETURN:
            if status in ["terminated", "stopped"]:
                return "succeed"
            elif status in ["pending", "terminating", "shutting-down", "stopping", "running"]:
                return "executing"
            else:
                return "fail"
        elif status == "running":
            return "succeed"
        elif status in ["pending", "launching"]:
            return "executing"
        else:
            return "fail"
