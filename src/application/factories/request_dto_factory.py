"""Request DTO factory for data transformation."""

from domain.request.aggregate import Request
from domain.machine.aggregate import Machine
from domain.request.request_types import RequestType
from application.request.dto import RequestDTO, MachineReferenceDTO


class RequestDTOFactory:
    """Factory for creating RequestDTOs from domain objects."""

    def create_from_domain(
        self, 
        request: Request, 
        machines: list[Machine] = None
    ) -> RequestDTO:
        """Create RequestDTO from domain objects."""
        if machines is None:
            machines = []
            
        # Convert machines to DTOs
        machine_references = [
            MachineReferenceDTO(
                machine_id=str(machine.machine_id.value),
                name=machine.private_ip or str(machine.machine_id.value),
                result=self.map_machine_status_to_result(machine.status.value, request.request_type),
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
            elif status in ["pending", "terminating"]:
                return "executing"
            else:
                return "fail"
        else:
            # For acquire requests, running is success, pending is executing
            if status == "running":
                return "succeed"
            elif status in ["pending", "launching"]:
                return "executing"
            else:
                return "fail"
