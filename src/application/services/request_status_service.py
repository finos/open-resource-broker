"""Request status service for business logic."""

from typing import Optional, Tuple

from domain.base.ports.logging_port import LoggingPort
from domain.base import UnitOfWorkFactory
from domain.request.aggregate import Request
from domain.machine.aggregate import Machine
from domain.request.request_types import RequestStatus, RequestType


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
        provider_metadata: dict
    ) -> Tuple[Optional[str], Optional[str]]:
        """Determine request status from machine states."""
        try:
            # Use provider machines if available, otherwise DB machines
            machines_to_check = provider_machines if provider_machines else db_machines
            
            if not machines_to_check:
                # No machines yet, check if request is still in progress
                if request.status in [RequestStatus.PENDING, RequestStatus.IN_PROGRESS]:
                    return None, None  # Keep current status
                return None, None
            
            # Count machine states
            running_count = sum(1 for m in machines_to_check if m.status.value == "running")
            failed_count = sum(1 for m in machines_to_check if m.status.value in ["terminated", "failed"])
            total_count = len(machines_to_check)
            
            # Determine new status based on request type
            if request.request_type.value == "return":
                # Return request logic - termination states
                if failed_count == total_count:
                    return RequestStatus.COMPLETED.value, "All instances terminated successfully"
                elif failed_count > 0:
                    return RequestStatus.PARTIAL.value, f"{failed_count}/{total_count} instances terminated"
                else:
                    # All pending/terminating
                    return RequestStatus.IN_PROGRESS.value, "Instances terminating"
            else:
                # Acquisition request logic - running states
                if running_count == total_count:
                    return RequestStatus.COMPLETED.value, "All instances running successfully"
                elif failed_count == total_count:
                    return RequestStatus.FAILED.value, "All instances failed"
                elif running_count > 0:
                    return RequestStatus.PARTIAL.value, f"{running_count}/{total_count} instances running"
                else:
                    # All pending/starting
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
            # For return requests, terminated is success
            return "succeed" if status in ["terminated", "stopped"] else "fail"
        else:
            # For acquire requests, running is success
            return "succeed" if status == "running" else "fail"
