"""Data Transfer Objects for request domain."""

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from orb.application.dto.base import BaseDTO
from orb.application.machine.result_mapping import map_machine_status_to_result
from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType


class MachineReferenceDTO(BaseDTO):
    """Data Transfer Object for machine reference."""

    machine_id: str
    name: str = ""
    result: str  # 'executing', 'fail', or 'succeed'
    status: str
    private_ip_address: str = ""
    public_ip_address: Optional[str] = None  # Already using the expected API field name
    instance_type: Optional[str] = None
    price_type: Optional[str] = None
    tags: Optional[dict[str, str]] = None
    cloud_host_id: Optional[str] = None
    launch_time: Optional[int] = None
    request_id: Optional[str] = None
    return_request_id: Optional[str] = None
    message: str = ""

    @classmethod
    def from_machine(cls, machine: Machine, request_type: RequestType) -> "MachineReferenceDTO":
        """
        Create DTO from a Machine aggregate and its request type.

        This is the preferred constructor — it populates all HF-required fields
        directly from the Machine aggregate and computes ``result`` using the
        correct request-type-aware mapping.

        Args:
            machine: Machine aggregate
            request_type: RequestType of the owning request (drives result mapping)

        Returns:
            MachineReferenceDTO instance
        """
        status = machine.status.value if hasattr(machine.status, "value") else str(machine.status)
        rt_str = request_type.value if hasattr(request_type, "value") else str(request_type)

        return cls(
            machine_id=str(machine.machine_id.value),
            name=machine.display_name,
            result=map_machine_status_to_result(status, request_type=rt_str),
            status=status,
            private_ip_address=machine.private_ip or "",
            public_ip_address=machine.public_ip,
            instance_type=str(machine.instance_type) if machine.instance_type else None,
            price_type=machine.price_type,
            launch_time=int(machine.launch_time.timestamp()) if machine.launch_time else None,
            cloud_host_id=machine.provider_data.get("cloud_host_id"),
            request_id=machine.request_id,
            return_request_id=machine.return_request_id,
            tags=machine.tags.tags if machine.tags and machine.tags.tags else None,
            message=machine.status_reason or "",
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary using snake_case (domain format).
        Scheduler strategies handle format conversion as needed.

        Returns:
            Dictionary with snake_case field names
        """
        d = self.model_dump(exclude_none=True)
        # launch_time and cloud_host_id must always be present (even if null)
        if "launch_time" not in d:
            d["launch_time"] = None
        if "cloud_host_id" not in d:
            d["cloud_host_id"] = None
        return d


class RequestDTO(BaseDTO):
    """Data Transfer Object for request responses."""

    request_id: str
    status: str
    template_id: Optional[str] = None
    requested_count: int
    created_at: datetime
    last_status_check: Optional[datetime] = None
    first_status_check: Optional[datetime] = None
    machine_references: list[MachineReferenceDTO] = Field(default_factory=list)
    machine_ids: list[str] = Field(default_factory=list)
    message: str = ""
    resource_id: Optional[str] = None
    provider_api: Optional[str] = None
    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    launch_template_id: Optional[str] = None
    launch_template_version: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    request_type: str = "acquire"
    desired_capacity: int = 1
    duration: Optional[int] = None
    success_rate: Optional[float] = None
    successful_count: int = 0
    failed_count: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_details: dict[str, Any] = Field(default_factory=dict)
    provider_data: dict[str, Any] = Field(default_factory=dict)
    version: int = 0
    resource_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_domain(
        cls,
        request: Request,
        verbose: bool = False,
        machine_references: Optional[list["MachineReferenceDTO"]] = None,
    ) -> "RequestDTO":
        """
        Create DTO from domain object.

        Args:
            request: Request domain object
            verbose: Whether to include detailed information
            machine_references: Optional fresh machine references to use instead of domain object's

        Returns:
            RequestDTO instance
        """
        # Use provided machine references or convert from domain
        if machine_references is not None:
            machine_refs = machine_references
        else:
            machine_refs = []
            # Get existing machine references (domain model may not have this field)
            domain_refs = getattr(request, "machine_references", None)
            if domain_refs:
                machine_refs = [
                    MachineReferenceDTO.from_machine(m, request.request_type) for m in domain_refs
                ]

        # Create the DTO with all available fields
        return cls(
            request_id=str(request.request_id),
            status=cls.serialize_enum(request.status) or "",
            template_id=str(request.template_id) if request.template_id else None,
            requested_count=request.requested_count,
            created_at=request.created_at,  # type: ignore[arg-type]
            last_status_check=request.last_status_check,
            first_status_check=request.first_status_check,
            machine_references=machine_refs,
            machine_ids=[mid for mid in (request.machine_ids or []) if mid is not None],
            message=request.status_message or "",
            resource_id=request.resource_ids[0] if request.resource_ids else None,
            provider_api=request.provider_api,
            provider_name=request.provider_name,
            provider_type=request.provider_type,
            launch_template_id=request.provider_data.get("launch_template_id"),
            launch_template_version=request.provider_data.get("launch_template_version"),
            metadata=request.metadata,
            request_type=cls.serialize_enum(request.request_type) or "",
            desired_capacity=request.desired_capacity,
            duration=request.duration,
            success_rate=request.success_rate,
            successful_count=request.successful_count,
            failed_count=request.failed_count,
            started_at=request.started_at,
            completed_at=request.completed_at,
            error_details=request.error_details,
            provider_data=request.provider_data,
            version=request.version,
            resource_ids=request.resource_ids,
        )

    def to_dict(self, verbose: bool = False) -> dict[str, Any]:
        """
        Convert to dictionary format - returns snake_case for internal use.
        External format conversion should be handled at scheduler strategy level.

        Args:
            verbose: Whether to include detailed information.

        Returns:
            Dictionary representation with snake_case keys
        """
        include_details = verbose

        # Get clean snake_case data using stable API
        result = super().to_dict()

        # Handle machines field for compatibility
        result["machines"] = (
            [m.to_dict() for m in self.machine_references] if self.machine_references else []
        )

        # Remove machine_references field as it's replaced by machines
        result.pop("machine_references", None)

        # Remove fields based on detail level
        if not include_details:
            result.pop("metadata", None)
            result.pop("first_status_check", None)
            result.pop("last_status_check", None)
            result.pop("launch_template_id", None)
            result.pop("launch_template_version", None)

        return result


class RequestStatusResponse(BaseDTO):
    """Response object for request status operations."""

    requests: list[dict[str, Any]]
    status: str = "complete"
    message: str = "Status retrieved successfully."
    errors: Optional[list[dict[str, Any]]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary format matching the expected API format.

        Returns:
            Dictionary with only the requests field
        """
        # According to input-output.md, only the requests field should be included
        return {"requests": self.requests}


class ReturnRequestResponse(BaseDTO):
    """Response object for return request operations."""

    requests: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "complete"
    message: str = "Return requests retrieved successfully."
    errors: Optional[list[dict[str, Any]]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary format matching the expected API format.

        Returns:
            Dictionary with only the requests field
        """
        # According to input-output.md, only the requests field should be included
        return {"requests": self.requests}


class RequestMachinesResponse(BaseDTO):
    """Response object for request machines operations."""

    request_id: str
    message: str = "Request VM success."
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary format.

        Returns:
            Dictionary with snake_case keys
        """
        return {"request_id": self.request_id, "message": self.message}


class RequestReturnMachinesResponse(BaseDTO):
    """Response object for request return machines operations."""

    request_id: Optional[str] = None
    message: str = "Delete VM success."
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary format.

        Returns:
            Dictionary with snake_case keys
        """
        return {
            "request_id": self.request_id if self.request_id else "",
            "message": self.message,
        }


class CleanupResourcesResponse(BaseDTO):
    """Response object for cleanup resources operations."""

    message: str = "All resources cleaned up successfully"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary format matching the expected API format.

        Returns:
            Dictionary with only the message field
        """
        return {"message": self.message}


class RequestSummaryDTO(BaseDTO):
    """Data transfer object for request summary."""

    request_id: str
    status: str
    total_machines: int
    machine_statuses: dict[str, int]
    created_at: datetime
    updated_at: Optional[datetime] = None
    duration: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary format.

        Returns:
            Dictionary with snake_case keys
        """
        result: dict[str, Any] = {
            "request_id": self.request_id,
            "status": self.status,
            "total_machines": self.total_machines,
            "machine_statuses": self.machine_statuses,
        }

        # Format datetime fields
        if self.created_at:
            result["created_at"] = self.created_at.isoformat()
        if self.updated_at:
            result["updated_at"] = self.updated_at.isoformat()
        if self.duration is not None:
            result["duration"] = self.duration

        return result
