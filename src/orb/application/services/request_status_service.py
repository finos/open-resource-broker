"""Request status service for business logic."""

from typing import Optional, Tuple

from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports.logging_port import LoggingPort
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
        """Determine request status from machine states."""
        try:
            db_machine_count = len(db_machines)

            # Determine new status based on request type
            if request.request_type.value == "return":
                follow_up_pending_message = (
                    "Return in progress: awaiting provider follow-up cleanup"
                )
                if provider_metadata.get("termination_follow_up_failed"):
                    details = provider_metadata.get("termination_follow_up_details", [{}])
                    error = details[0].get("last_delete_error") if details else None
                    message = "Return request failed: provider follow-up cleanup failed"
                    if error:
                        message = f"{message}: {error}"
                    return RequestStatus.FAILED.value, message
                termination_follow_up_pending = bool(
                    provider_metadata.get("termination_follow_up_pending", False)
                )

                # For return requests, provider disappearance is only final once
                # any provider-owned follow-up cleanup has also completed.
                if not provider_machines:
                    if termination_follow_up_pending:
                        return (
                            RequestStatus.IN_PROGRESS.value,
                            follow_up_pending_message,
                        )
                    return (
                        RequestStatus.COMPLETED.value,
                        f"Return request completed: all machines terminated "
                        f"(no longer visible in provider) (total in DB: {db_machine_count})",
                    )

                shutting_down_count = sum(
                    1 for m in provider_machines if m.status.value in ["shutting-down", "stopping"]
                )
                terminated_count = sum(
                    1 for m in provider_machines if m.status.value in ["terminated", "stopped"]
                )
                running_count = sum(1 for m in provider_machines if m.status.value == "running")
                failed_count = sum(1 for m in provider_machines if m.status.value == "failed")
                total_count = len(provider_machines)

                # shutting-down/stopping are transient — only terminated/stopped are truly done
                effectively_done_count = terminated_count
                if (
                    effectively_done_count == total_count
                    and running_count == 0
                    and termination_follow_up_pending
                ):
                    return (
                        RequestStatus.IN_PROGRESS.value,
                        follow_up_pending_message,
                    )
                if effectively_done_count == total_count and running_count == 0:
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
            # Acquisition request logic
            else:
                machines_to_check = provider_machines if provider_machines else db_machines

                fleet_errors = provider_metadata.get("fleet_errors") or []
                fleet_capacity = provider_metadata.get("fleet_capacity_fulfilment") or {}
                capacity_state = str(fleet_capacity.get("state") or "").lower()
                terminal_error_message = provider_metadata.get("terminal_error_message")
                unfulfilled_count = provider_metadata.get("unfulfilled_count")
                if terminal_error_message and unfulfilled_count is not None and not isinstance(
                    unfulfilled_count, int
                ):
                    return (
                        RequestStatus.FAILED.value,
                        "Provider reported malformed unfulfilled_count metadata",
                    )
                planned_terminal_shortfall = bool(terminal_error_message) and (
                    unfulfilled_count is None or unfulfilled_count > 0
                )

                if not machines_to_check:
                    if fleet_errors:
                        error_message = next(
                            (
                                str(error.get("error_message"))
                                for error in fleet_errors
                                if isinstance(error, dict) and error.get("error_message")
                            ),
                            None,
                        )
                        error_code = next(
                            (
                                str(error.get("error_code"))
                                for error in fleet_errors
                                if isinstance(error, dict) and error.get("error_code")
                            ),
                            None,
                        )
                        detail = error_message or error_code or "Provider reported provisioning errors"
                        return RequestStatus.FAILED.value, detail

                    if capacity_state == "failed":
                        return (
                            RequestStatus.FAILED.value,
                            "Provider reported failed state before any instances were visible",
                        )

                    if planned_terminal_shortfall:
                        return RequestStatus.FAILED.value, str(terminal_error_message)

                    return None, None

                running_count = sum(1 for m in machines_to_check if m.status.value == "running")
                pending_count = sum(
                    1 for m in machines_to_check if m.status.value in ["pending", "starting"]
                )
                failed_count = sum(1 for m in machines_to_check if m.status.value == "failed")
                total_count = len(machines_to_check)

                # Use fleet capacity metrics when available (fleets/ASGs report their own
                # target and fulfilled capacity). Fall back to instance counts otherwise.
                effective_target = (
                    fleet_capacity.get("target_capacity_units") or request.requested_count
                )
                fulfilled_from_metadata = fleet_capacity.get("fulfilled_capacity_units")
                effective_fulfilled: float = (
                    float(fulfilled_from_metadata)
                    if fulfilled_from_metadata is not None
                    else float(running_count + pending_count)
                )

                # Fleet metadata (FulfilledCapacity) can lag or use floating-point values
                # that don't exactly match target. Use running instance count as the
                # authoritative signal when it meets the requested count.
                fulfillment_final = fleet_capacity.get("fulfillment_final", False)
                fleet_errors = provider_metadata.get("fleet_errors") or []

                instance_target = request.requested_count
                if running_count >= instance_target and failed_count == 0:
                    return RequestStatus.COMPLETED.value, "All instances running successfully"

                # Capacity metadata can confirm that the provider fulfilled the requested
                # count, but it must not promote the request to complete while any
                # instance is still in a pending/starting state.
                if (
                    pending_count == 0
                    and effective_fulfilled >= effective_target
                    and failed_count == 0
                ):
                    return RequestStatus.COMPLETED.value, "All instances running successfully"
                elif planned_terminal_shortfall and pending_count == 0:
                    if running_count > 0:
                        return (
                            RequestStatus.PARTIAL.value,
                            f"{running_count}/{instance_target} instances running: {terminal_error_message}",
                        )
                    return RequestStatus.FAILED.value, str(terminal_error_message)
                elif fulfillment_final and pending_count == 0:
                    error_detail = (
                        f": {'; '.join(e.get('error_code', '') for e in fleet_errors if e.get('error_code'))}"
                        if fleet_errors
                        else ""
                    )
                    if running_count > 0:
                        return (
                            RequestStatus.PARTIAL.value,
                            f"{running_count}/{instance_target} instances running{error_detail}",
                        )
                    else:
                        return RequestStatus.FAILED.value, f"All instances failed{error_detail}"
                elif failed_count == total_count and total_count > 0:
                    return RequestStatus.FAILED.value, "All instances failed"
                elif pending_count > 0:
                    return (
                        RequestStatus.IN_PROGRESS.value,
                        f"{running_count}/{effective_target} instances running, waiting for {pending_count} more",
                    )
                elif running_count > 0 and (running_count + failed_count) >= effective_target:
                    return (
                        RequestStatus.PARTIAL.value,
                        f"{running_count}/{effective_target} instances running",
                    )
                elif running_count > 0:
                    return (
                        RequestStatus.IN_PROGRESS.value,
                        f"{running_count}/{effective_target} instances running, waiting for more",
                    )
                else:
                    return RequestStatus.IN_PROGRESS.value, "Instances starting"

        except Exception as e:
            self.logger.error(f"Failed to determine status from machines: {e}")
            return RequestStatus.IN_PROGRESS.value, "Status determination failed — will retry"

    async def update_request_status(self, request: Request, status: str, message: str) -> Request:
        """Update request status."""
        try:
            status_enum = RequestStatus(status)

            # Save updated request
            with self.uow_factory.create_unit_of_work() as uow:
                current_request = uow.requests.get_by_id(request.request_id) or request
                updated_request = current_request.update_status(status_enum, message)
                uow.requests.save(updated_request)

            self.logger.info(f"Updated request {request.request_id.value} status to {status}")
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
