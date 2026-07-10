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
from typing import Optional, Tuple

from orb.application.services.request_follow_up_context import get_request_follow_up_context
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import ProviderContractError
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus, RequestType


class RequestStatusService:
    """Business logic for request status management."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
    ) -> None:
        self.uow_factory = uow_factory
        self.logger = logger

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
            provider_name = request.provider_name or "unknown"
            raise ProviderContractError(
                f"Provider {provider_name} did not emit "
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
        follow_up_pending_message = "Return in progress: awaiting provider follow-up cleanup"
        follow_up_context = get_request_follow_up_context(request)
        termination_follow_up_pending = follow_up_context.get("follow_up_kind") == "termination"

        # For return requests: empty provider_machines *with* DB records means all
        # instances are gone from AWS — genuinely terminated.  But if we have
        # neither DB records nor provider records we cannot distinguish "all gone"
        # from a transient gap (e.g. provider API hiccup before any machines were
        # ever stored).  Treat that ambiguous case as IN_PROGRESS to avoid
        # prematurely stamping COMPLETED when provider_machines came back empty
        # before the instances ever appeared.
        if not provider_machines:
            if termination_follow_up_pending:
                return RequestStatus.IN_PROGRESS.value, follow_up_pending_message
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
            if termination_follow_up_pending:
                return RequestStatus.IN_PROGRESS.value, follow_up_pending_message
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
        """Update request status and optionally cache the latest ProviderFulfilment.

        When ``provider_metadata`` is supplied and contains a ``provider_fulfilment``
        key, the fulfilment is serialised and stored as ``request.metadata["last_fulfilment"]``.
        That snapshot is later read by ``RequestDTO.from_domain`` so capacity fields
        (target_units, fulfilled_units, running_count, pending_count) appear in every
        response without requiring the caller to re-pass the fulfilment explicitly.

        Terminal requests are mostly immutable, but PARTIAL is allowed to
        upgrade to COMPLETED. Multi-fleet requests can be stamped PARTIAL on
        a first sync when one fleet is still reporting transient state; once
        every fleet's instances are running, the next sync correctly produces
        a 'fulfilled' verdict and the request should reflect that — not stay
        stuck PARTIAL forever.

        Downgrades (COMPLETED -> PARTIAL/FAILED, CANCELLED -> anything, etc.)
        remain blocked.
        """
        try:
            status_enum = RequestStatus(status)

            # Save updated request
            with self.uow_factory.create_unit_of_work() as uow:
                persisted_request = uow.requests.get_by_id(request.request_id)
                current_request = (
                    persisted_request if isinstance(persisted_request, Request) else request
                )

                if current_request.status.is_terminal():
                    is_upgrade_to_complete = (
                        current_request.status == RequestStatus.PARTIAL
                        and status_enum == RequestStatus.COMPLETED
                    )
                    if not is_upgrade_to_complete:
                        return current_request

                updated_request = current_request.update_status(status_enum, message)

                # Reconcile the persisted counters with reality. The acquire
                # fulfilment path transitions directly via ``update_status``,
                # which does not touch ``successful_count`` — it is only
                # bumped by ``update_with_provisioning_result``. That works
                # for batched-instance providers but not for instant fulfilment
                # (e.g. EC2Fleet instant) where the provider reports
                # "fulfilled" without emitting instance_ids. Use the request's
                # own machine_ids list — which is the authoritative count of
                # machines associated with this request — as the source of
                # truth for ``successful_count`` whenever it disagrees with
                # the persisted value.
                if status_enum in (
                    RequestStatus.COMPLETED,
                    RequestStatus.PARTIAL,
                    RequestStatus.IN_PROGRESS,
                ):
                    actual_count = len(updated_request.machine_ids)
                    if actual_count and actual_count != updated_request.successful_count:
                        updated_request = updated_request.model_copy(
                            update={"successful_count": actual_count}
                        )

                # Cache the latest ProviderFulfilment snapshot so DTO callers can surface it.
                if provider_metadata:
                    fulfilment = provider_metadata.get("provider_fulfilment")
                    if fulfilment is not None:
                        updated_request = updated_request.with_last_fulfilment(
                            dataclasses.asdict(fulfilment)
                        )

                uow.requests.save(updated_request)

                self.logger.info(
                    f"Updated request {current_request.request_id.value} status to {status}"
                    )
                return updated_request

        except Exception as e:
            self.logger.error(f"Failed to update request status: {e}")
            raise

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
