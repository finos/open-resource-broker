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
                # For return requests: empty provider_machines means instances are gone from AWS → COMPLETED
                if not provider_machines:
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

                if not machines_to_check:
                    return None, None

                running_count = sum(1 for m in machines_to_check if m.status.value == "running")
                pending_count = sum(
                    1 for m in machines_to_check if m.status.value in ["pending", "starting"]
                )
                failed_count = sum(1 for m in machines_to_check if m.status.value == "failed")
                total_count = len(machines_to_check)

                # Use fleet capacity metrics when available (fleets/ASGs report their own
                # target and fulfilled capacity). Fall back to instance counts otherwise.
                fleet_capacity = provider_metadata.get("fleet_capacity_fulfilment") or {}
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
                if (running_count >= instance_target and failed_count == 0) or (
                    effective_fulfilled >= effective_target and failed_count == 0
                ):
                    return RequestStatus.COMPLETED.value, "All instances running successfully"
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
            return None, None

    async def update_request_status(self, request: Request, status: str, message: str) -> Request:
        """Update request status."""
        try:
            status_enum = RequestStatus(status)
            updated_request = request.update_status(status_enum, message)

            # Save updated request
            with self.uow_factory.create_unit_of_work() as uow:
                uow.requests.save(updated_request)

            self.logger.info(f"Updated request {request.request_id.value} status to {status}")
            return updated_request

        except Exception as e:
            self.logger.error(f"Failed to update request status: {e}")
            return request

    def map_machine_status_to_result(self, status: str, request_type: RequestType) -> str:
        """Map machine status to result code."""
        if request_type == RequestType.RETURN:
            if status in ["terminated", "stopped"]:
                return "succeed"
            elif status in ["pending", "terminating", "shutting-down", "stopping", "running"]:
                return "executing"
            else:
                return "fail"
        else:
            if status == "running":
                return "succeed"
            elif status in ["pending", "launching"]:
                return "executing"
            else:
                return "fail"
