"""Request DTO factory for data transformation."""

from orb.application.machine.result_mapping import map_machine_status_to_result
from orb.application.request.dto import MachineReferenceDTO, RequestDTO
from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType


class RequestDTOFactory:
    """Factory for creating RequestDTOs from domain objects."""

    def create_from_domain(
        self, request: Request, machines: list[Machine] | None = None
    ) -> RequestDTO:
        """Create RequestDTO from domain objects."""
        if machines is None:
            machines = []

        # Convert machines to DTOs
        machine_references = [
            MachineReferenceDTO(
                machine_id=str(machine.machine_id.value),
                name=machine.display_name,
                result=self.map_machine_status_to_result(
                    machine.status.value, request.request_type
                ),
                status=machine.status.value,
                private_ip_address=machine.private_ip or "",
                public_ip_address=machine.public_ip,
                instance_type=str(machine.instance_type) if machine.instance_type else None,
                price_type=machine.price_type,
                launch_time=int(machine.launch_time.timestamp() if machine.launch_time else 0),
                cloud_host_id=machine.provider_data.get("cloud_host_id"),
                request_id=machine.request_id,
                return_request_id=machine.return_request_id,
                tags=machine.tags.tags if machine.tags.tags else None,
                message=machine.status_reason or "",
            )
            for machine in machines
        ]

        # Create RequestDTO using existing factory method
        return RequestDTO.from_domain(request, machine_references=machine_references)

    def map_machine_status_to_result(self, status: str, request_type: RequestType) -> str:
        """Map machine status to result code."""
        rt_str = request_type.value if hasattr(request_type, "value") else str(request_type)
        return map_machine_status_to_result(status, request_type=rt_str)
