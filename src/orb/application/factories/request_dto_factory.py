"""Request DTO factory for data transformation."""

from orb.application.request.dto import MachineReferenceDTO, RequestDTO
from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType


class RequestDTOFactory:
    """Factory for creating RequestDTOs from domain objects."""

    def create_from_domain(self, request: Request, machines: list[Machine] = None) -> RequestDTO:  # type: ignore[assignment]
        """Create RequestDTO from domain objects."""
        if machines is None:
            machines = []

        # Convert machines to DTOs
        machine_references = [
            MachineReferenceDTO(
                machine_id=str(machine.machine_id.value),
                name=machine.private_ip or str(machine.machine_id.value),
                result=self.map_machine_status_to_result(
                    machine.status.value, request.request_type
                ),
                status=machine.status.value,
                private_ip_address=machine.private_ip or "",
                public_ip_address=machine.public_ip,
                launch_time=int(machine.launch_time.timestamp() if machine.launch_time else 0),
            )
            for machine in machines
        ]

        # Create RequestDTO using existing factory method
        return RequestDTO.from_domain(request, machine_references=machine_references)

    def map_machine_status_to_result(self, status: str, request_type: RequestType) -> str:
        """Map machine status to result code."""
        if request_type == RequestType.RETURN:
            # For return requests, terminated is success, pending is executing
            if status in ["terminated", "stopped"]:
                return "succeed"
            elif status in ["pending", "terminating", "shutting-down", "stopping"]:
                return "executing"
            else:
                return "fail"
        # For acquire requests, running is success, pending is executing
        elif status == "running":
            return "succeed"
        elif status in ["pending", "launching"]:
            return "executing"
        else:
            return "fail"
