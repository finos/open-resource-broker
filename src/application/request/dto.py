"""Data Transfer Objects for request domain."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from src.application.dto.base import BaseDTO
from src.domain.request.aggregate import Request
from src.domain.request.value_objects import MachineReference


class MachineReferenceDTO(BaseDTO):
    """Data Transfer Object for machine reference."""

    machine_id: str
    name: str
    result: str  # 'executing', 'fail', or 'succeed'
    status: str
    private_ip_address: str  # Already using the expected API field name
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
        # Extract fields from metadata if available
        metadata = machine_ref.metadata or {}

        return cls(
            machine_id=str(machine_ref.machine_id),
            name=machine_ref.name,
            result=cls.serialize_enum(machine_ref.result),  # Fixed: cls instead of self
            status=cls.serialize_enum(machine_ref.status),  # Fixed: cls instead of self
            private_ip_address=machine_ref.private_ip,
            public_ip_address=machine_ref.public_ip,
            instance_type=metadata.get("instance_type"),  # Fixed: snake_case
            price_type=metadata.get("price_type"),  # Fixed: snake_case
            instance_tags=metadata.get("instance_tags"),  # Fixed: snake_case
            cloud_host_id=metadata.get("cloud_host_id"),  # Fixed: snake_case
            launch_time=metadata.get("launch_time"),  # Fixed: snake_case
            message=machine_ref.message,
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary format matching the expected API format.

        Returns:
            Dictionary representation with expected field names
        """
        # Create a dictionary with the expected field names
        result = {
            "machineId": self.machine_id,
            "name": self.name,
            "result": self.result,
            "status": self.status,
            "privateIpAddress": self.private_ip_address,
            "message": self.message,
        }

        # Add optional fields if they exist
        if self.public_ip_address:
            result["publicIpAddress"] = self.public_ip_address

        if self.instance_type:
            result["instanceType"] = self.instance_type

        if self.price_type:
            result["priceType"] = self.price_type

        if self.instance_tags:
            result["instanceTags"] = self.instance_tags

        if self.cloud_host_id:
            result["cloudHostId"] = self.cloud_host_id

        if self.launch_time:
            result["launchtime"] = str(self.launch_time)

        return result


class RequestDTO(BaseDTO):
    """Data Transfer Object for request responses."""

    request_id: str
    status: str
    template_id: Optional[str] = None
    requested_count: int
    created_at: datetime
    last_status_check: Optional[datetime] = None
    first_status_check: Optional[datetime] = None
    machine_references: List[MachineReferenceDTO] = Field(default_factory=list)
    message: str = ""
    resource_id: Optional[str] = None
    provider_api: Optional[str] = None
    launch_template_id: Optional[str] = None
    launch_template_version: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    request_type: str = "acquire"
    long: bool = False  # Flag to indicate whether to include detailed information

    @classmethod
    def from_domain(cls, request: Request, long: bool = False) -> "RequestDTO":
        """
        Create DTO from domain object.

        Args:
            request: Request domain object
            long: Whether to include detailed information

        Returns:
            RequestDTO instance
        """
        # Convert machine references
        machine_refs = []

        # Get existing machine references
        if hasattr(request, "machine_references") and request.machine_references:
            machine_refs = [MachineReferenceDTO.from_domain(
                m) for m in request.machine_references]

        # Create the DTO with all available fields
        return cls(
            request_id=str(request.request_id),
            status=cls.serialize_enum(request.status),
            template_id=str(request.template_id) if request.template_id else None,
            requested_count=request.requested_count,  # Fixed: snake_case field name
            created_at=request.created_at,
            last_status_check=None,  # Not available in current domain model
            first_status_check=None,  # Not available in current domain model
            machine_references=machine_refs,
            message=request.status_message or "",  # Provide empty string if None
            resource_id=None,  # Not available in current domain model
            provider_api=None,  # Not available in current domain model
            launch_template_id=None,  # Not available in current domain model
            launch_template_version=None,  # Not available in current domain model
            metadata=request.metadata,
            request_type=cls.serialize_enum(request.request_type),
            long=long,
        )

    def to_dict(self, long: Optional[bool] = None) -> Dict[str, Any]:
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

    requests: List[Dict[str, Any]]
    status: str = "complete"
    message: str = "Status retrieved successfully."
    errors: Optional[List[Dict[str, Any]]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary format matching the expected API format.

        Returns:
            Dictionary with only the requests field
        """
        # According to input-output.md, only the requests field should be included
        return {"requests": self.requests}


class ReturnRequestResponse(BaseDTO):
    """Response object for return request operations."""

    requests: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = "complete"
    message: str = "Return requests retrieved successfully."
    errors: Optional[List[Dict[str, Any]]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
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
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary format matching the expected API format.

        Returns:
            Dictionary with requestId and message fields
        """
        return {"requestId": self.request_id, "message": self.message}


class RequestReturnMachinesResponse(BaseDTO):
    """Response object for request return machines operations."""

    request_id: Optional[str] = None
    message: str = "Delete VM success."
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
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
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
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
    machine_statuses: Dict[str, int]
    created_at: datetime
    updated_at: Optional[datetime] = None
    duration: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
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
