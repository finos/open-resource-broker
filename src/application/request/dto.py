"""Data Transfer Objects for request domain."""

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from application.dto.base import BaseDTO
from domain.request.aggregate import Request
from domain.request.value_objects import MachineReference


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
    instance_tags: Optional[str] = None
    cloud_host_id: Optional[str] = None
    launch_time: Optional[int] = None
    message: str = ""

    @classmethod
    def from_domain(cls, machine_ref: MachineReference) -> "MachineReferenceDTO":
        """
        Create DTO from domain object.

        Args:
            machine_ref: Machine reference domain object

        Returns:
            MachineReferenceDTO instance
        """
        # Extract fields from metadata if available (MachineReference may not have metadata)
        metadata: dict = getattr(machine_ref, "metadata", None) or {}

        return cls(
            machine_id=str(machine_ref.machine_id),
            name=getattr(machine_ref, "name", ""),
            result=cls.serialize_enum(machine_ref.result) or "",
            status=cls.serialize_enum(machine_ref.status) or "",
            private_ip_address=getattr(machine_ref, "private_ip", ""),
            public_ip_address=getattr(machine_ref, "public_ip", None),
            instance_type=metadata.get("instance_type"),
            price_type=metadata.get("price_type"),
            instance_tags=metadata.get("instance_tags"),
            cloud_host_id=metadata.get("cloud_host_id"),
            launch_time=metadata.get("launch_time"),
            message=getattr(machine_ref, "message", machine_ref.error_message or ""),
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
    long: bool = False  # Flag to indicate whether to include detailed information
    desired_capacity: int = 1
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
        long: bool = False,
        machine_references: Optional[list["MachineReferenceDTO"]] = None,
    ) -> "RequestDTO":
        """
        Create DTO from domain object.

        Args:
            request: Request domain object
            long: Whether to include detailed information
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
                machine_refs = [MachineReferenceDTO.from_domain(m) for m in domain_refs]

        # Create the DTO with all available fields
        return cls(
            request_id=str(request.request_id),
            status=cls.serialize_enum(request.status) or "",
            template_id=str(request.template_id) if request.template_id else None,
            requested_count=request.requested_count,
            created_at=request.created_at,  # type: ignore[arg-type]
            last_status_check=None,  # Not available in current domain model
            first_status_check=None,  # Not available in current domain model
            machine_references=machine_refs,
            machine_ids=[mid for mid in (request.machine_ids or []) if mid is not None],
            message=request.status_message or "",
            resource_id=request.resource_ids[0] if request.resource_ids else None,
            provider_api=request.provider_api,
            provider_name=request.provider_name,
            provider_type=request.provider_type,
            launch_template_id=None,  # Not available in current domain model
            launch_template_version=None,  # Not available in current domain model
            metadata=request.metadata,
            request_type=cls.serialize_enum(request.request_type) or "",
            long=long,
            desired_capacity=request.desired_capacity,
            successful_count=request.successful_count,
            failed_count=request.failed_count,
            started_at=request.started_at,
            completed_at=request.completed_at,
            error_details=request.error_details,
            provider_data=request.provider_data,
            version=request.version,
            resource_ids=request.resource_ids,
        )

    def to_dict(self, long: Optional[bool] = None) -> dict[str, Any]:
        """
        Convert to dictionary format - returns snake_case for internal use.
        External format conversion should be handled at scheduler strategy level.

        Args:
            long: Whether to include detailed information. If None, uses the instance's long attribute.

        Returns:
            Dictionary representation with snake_case keys
        """
        # Use provided long parameter or fall back to instance attribute
        include_details = self.long if long is None else long

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
        Convert to dictionary format matching the expected API format.

        Returns:
            Dictionary with requestId and message fields (camelCase for API consumers)
        """
        # Clients must use the full prefixed ID for subsequent requests
        return {"requestId": self.request_id, "message": self.message}


class RequestReturnMachinesResponse(BaseDTO):
    """Response object for request return machines operations."""

    request_id: Optional[str] = None
    message: str = "Delete VM success."
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary format matching the expected API format.

        Returns:
            Dictionary with requestId and message fields
        """
        return {
            "requestId": self.request_id if self.request_id else "",
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
        Convert to dictionary format matching the expected API format.

        Returns:
            Dictionary with summary fields
        """
        result = {
            "requestId": self.request_id,
            "status": self.status,
            "totalMachines": self.total_machines,
            "machineStatuses": self.machine_statuses,
        }

        # Format datetime fields
        if self.created_at:
            result["createdAt"] = self.created_at.isoformat()
        if self.updated_at:
            result["updatedAt"] = self.updated_at.isoformat()
        if self.duration is not None:
            result["duration"] = self.duration

        return result
