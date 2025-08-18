"""Request aggregate - core request domain logic."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from domain.base.entity import AggregateRoot
from domain.base.events import (
    RequestCompletedEvent,
    RequestCreatedEvent,
    RequestStatusChangedEvent,
)
from domain.base.value_objects import InstanceId
from domain.request.request_types import RequestStatus
from domain.request.value_objects import RequestId, RequestType


class Request(AggregateRoot):
    """Request aggregate root."""

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        populate_by_name=True,  # Allow both field names and aliases
    )

    # Core request identification
    request_id: RequestId
    request_type: RequestType
    provider_type: str
    provider_instance: Optional[str] = None

    # Request configuration
    template_id: str
    requested_count: int = 1

    # Provider tracking (which provider was used)
    provider_name: Optional[str] = None  # Specific provider instance name
    provider_api: Optional[str] = None  # Provider API/service used

    # Resource tracking (what was created)
    # Provider resource identifiers
    resource_ids: List[str] = Field(default_factory=list)

    # Request state
    status: RequestStatus = Field(default=RequestStatus.PENDING)
    status_message: Optional[str] = None

    # HF output fields
    message: Optional[str] = None

    # Results
    instance_ids: List[InstanceId] = Field(default_factory=list)
    successful_count: int = 0
    failed_count: int = 0

    # Lifecycle timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Request metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error_details: Dict[str, Any] = Field(default_factory=dict)

    # Provider-specific data
    provider_data: Dict[str, Any] = Field(default_factory=dict)

    # Versioning
    version: int = Field(default=0)

    def __init__(self, **data):
        """Initialize the instance."""
        # Set default ID if not provided
        if "id" not in data:
            data["id"] = data.get("request_id", f"request-{datetime.utcnow().isoformat()}")

        # Set default timestamps if not provided
        if "created_at" not in data:
            data["created_at"] = datetime.utcnow()

        super().__init__(**data)

    def get_id(self) -> str:
        """Get the request ID."""
        return str(self.request_id)

    def start_processing(self) -> "Request":
        """Mark request as started processing."""
        if self.status != RequestStatus.PENDING:
            raise ValueError(f"Cannot start processing request in status: {self.status}") from e

        old_status = self.status
        data = self.model_dump()
        data["status"] = RequestStatus.IN_PROGRESS
        data["started_at"] = datetime.utcnow()
        data["version"] = self.version + 1

        updated_request = Request.model_validate(data)

        # Add domain event for status change
        status_event = RequestStatusChangedEvent.create(
            request_id=str(self.request_id),
            old_status=old_status.value,
            new_status=RequestStatus.IN_PROGRESS.value,
            message="Request processing started",
        )
        updated_request.add_domain_event(status_event)

        return updated_request

    def add_instance(self, instance_id: InstanceId) -> "Request":
        """Add a successfully created instance."""
        data = self.model_dump()
        data["instance_ids"] = self.instance_ids + [instance_id]
        data["successful_count"] = self.successful_count + 1
        data["version"] = self.version + 1

        # Check if request is complete
        if data["successful_count"] + self.failed_count >= self.requested_count:
            data["status"] = (
                RequestStatus.COMPLETED if self.failed_count == 0 else RequestStatus.PARTIAL
            )
            data["completed_at"] = datetime.utcnow()

        return Request.model_validate(data)

    def add_failure(
        self, error_message: str, error_details: Optional[Dict[str, Any]] = None
    ) -> "Request":
        """Add a failed instance creation."""
        data = self.model_dump()
        data["failed_count"] = self.failed_count + 1
        data["version"] = self.version + 1

        # Update error details
        if error_details:
            current_errors = dict(self.error_details)
            current_errors[f"error_{self.failed_count}"] = {
                "message": error_message,
                "details": error_details,
                "timestamp": datetime.utcnow().isoformat(),
            }
            data["error_details"] = current_errors

        # Check if request is complete
        if self.successful_count + data["failed_count"] >= self.requested_count:
            data["status"] = (
                RequestStatus.PARTIAL if self.successful_count > 0 else RequestStatus.FAILED
            )
            data["completed_at"] = datetime.utcnow()
            data["status_message"] = f"Request completed with {data['failed_count']} failures"

        return Request.model_validate(data)

    def cancel(self, reason: str) -> "Request":
        """Cancel the request."""
        if self.status in [
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
        ]:
            raise ValueError(f"Cannot cancel request in status: {self.status}") from e

        data = self.model_dump()
        data["status"] = RequestStatus.CANCELLED
        data["status_message"] = reason
        data["completed_at"] = datetime.utcnow()
        data["version"] = self.version + 1

        return Request.model_validate(data)

    def complete(self, message: Optional[str] = None) -> "Request":
        """Mark request as completed."""
        old_status = self.status
        data = self.model_dump()
        data["status"] = RequestStatus.COMPLETED
        data["status_message"] = message or "Request completed successfully"
        data["completed_at"] = datetime.utcnow()
        data["version"] = self.version + 1

        updated_request = Request.model_validate(data)

        # Add domain events
        status_event = RequestStatusChangedEvent.create(
            request_id=str(self.request_id),
            old_status=old_status.value,
            new_status=RequestStatus.COMPLETED.value,
            message=message or "Request completed successfully",
        )
        updated_request.add_domain_event(status_event)

        completion_event = RequestCompletedEvent.create(
            request_id=str(self.request_id),
            successful_count=self.successful_count,
            failed_count=self.failed_count,
            total_requested=self.requested_count,
        )
        updated_request.add_domain_event(completion_event)

        return updated_request

    def fail(self, error_message: str, error_details: Optional[Dict[str, Any]] = None) -> "Request":
        """Mark request as failed."""
        data = self.model_dump()
        data["status"] = RequestStatus.FAILED
        data["status_message"] = error_message
        data["completed_at"] = datetime.utcnow()
        data["version"] = self.version + 1

        if error_details:
            data["error_details"] = error_details

        return Request.model_validate(data)

    def set_provider_data(self, provider_data: Dict[str, Any]) -> "Request":
        """Set provider-specific data."""
        data = self.model_dump()
        data["provider_data"] = provider_data
        data["version"] = self.version + 1
        return Request.model_validate(data)

    def get_provider_data(self, key: str, default: Any = None) -> Any:
        """Get provider-specific data value."""
        return self.provider_data.get(key, default)

    def add_resource_id(self, resource_id: str) -> "Request":
        """Add a provider resource ID"""
        if resource_id not in self.resource_ids:
            data = self.model_dump()
            data["resource_ids"] = self.resource_ids + [resource_id]
            data["version"] = self.version + 1
            return Request.model_validate(data)
        return self

    def remove_resource_id(self, resource_id: str) -> "Request":
        """Remove a resource ID"""
        if resource_id in self.resource_ids:
            data = self.model_dump()
            data["resource_ids"] = [rid for rid in self.resource_ids if rid != resource_id]
            data["version"] = self.version + 1
            return Request.model_validate(data)
        return self

    @property
    def is_complete(self) -> bool:
        """Check if request is complete."""
        return self.status in [
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
            RequestStatus.PARTIAL,
        ]

    @property
    def is_successful(self) -> bool:
        """Check if request was successful."""
        return self.status == RequestStatus.COMPLETED

    @property
    def success_rate(self) -> float:
        """Get success rate as percentage."""
        if self.requested_count == 0:
            return 0.0
        return (self.successful_count / self.requested_count) * 100

    @property
    def duration(self) -> Optional[int]:
        """Get request duration in seconds."""
        if self.started_at and self.completed_at:
            return int((self.completed_at - self.started_at).total_seconds())
        elif self.started_at:
            return int((datetime.utcnow() - self.started_at).total_seconds())
        return None

    def to_provider_format(self, provider_type: str) -> Dict[str, Any]:
        """Convert request to provider-specific format."""
        base_format = {
            "request_id": self.request_id,
            "request_type": self.request_type.value,
            "provider_type": self.provider_type,
            "template_id": self.template_id,
            "requested_count": self.requested_count,
            "status": self.status.value,
            "status_message": self.status_message,
            "instance_ids": [id.value for id in self.instance_ids],
            "successful_count": self.successful_count,
            "failed_count": self.failed_count,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
            "error_details": self.error_details,
            "provider_data": self.provider_data,
            "version": self.version,
        }

        # Add optional timestamps
        if self.started_at:
            base_format["started_at"] = self.started_at.isoformat()
        if self.completed_at:
            base_format["completed_at"] = self.completed_at.isoformat()

        return base_format

    @classmethod
    def create_new_request(
        cls,
        request_type: RequestType,
        template_id: str,
        machine_count: int,
        provider_type: str,  # Provider type must be explicitly specified
        provider_instance: Optional[str] = None,  # Specific provider instance
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Request":
        """
        Create a new request with domain event generation.

        Args:
            request_type: Type of request (CREATE, TERMINATE, etc.)
            template_id: Template identifier
            machine_count: Number of machines requested
            provider_type: Cloud provider type
            provider_instance: Specific provider instance name (optional)
            metadata: Optional metadata

        Returns:
            New Request instance with creation event
        """
        # Generate appropriate RequestId using the value object's generate method
        request_id = RequestId.generate(request_type)

        # Create request
        request = cls(
            request_id=request_id,
            request_type=request_type,
            template_id=template_id,
            requested_count=machine_count,
            provider_type=provider_type,
            provider_instance=provider_instance,
            status=RequestStatus.PENDING,
            metadata=metadata or {},
            created_at=datetime.utcnow(),
            version=0,
        )

        # Add domain event
        creation_event = RequestCreatedEvent(
            # DomainEvent required fields
            aggregate_id=str(request_id.value),  # Use .value for string representation
            aggregate_type="Request",
            # RequestEvent required fields
            request_id=str(request_id.value),  # Use .value for string representation
            request_type=request_type.value,
            # RequestCreatedEvent specific fields
            template_id=template_id,
            machine_count=machine_count,
            timeout=metadata.get("timeout") if metadata else None,
            tags=metadata.get("tags", {}) if metadata else {},
        )
        request.add_domain_event(creation_event)

        return request

    @classmethod
    def create_return_request(
        cls,
        machine_refs: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Request":
        """
        Create a return/terminate request.

        Args:
            machine_refs: List of machine references to return
            metadata: Optional metadata

        Returns:
            New return Request instance with creation event
        """
        request_id = f"ret-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{len(machine_refs):04d}"

        # Extract instance IDs from machine references
        instance_ids = []
        for ref in machine_refs:
            if isinstance(ref, dict) and "instance_id" in ref:
                instance_ids.append(InstanceId(value=ref["instance_id"]))
            elif hasattr(ref, "instance_id"):
                instance_ids.append(InstanceId(value=str(ref.instance_id)))

        # Create return request
        request = cls(
            request_id=request_id,
            request_type=RequestType.TERMINATE,
            template_id="return-request",
            requested_count=len(machine_refs),
            provider_type=(
                machine_refs[0].get("provider_type", "unknown") if machine_refs else "unknown"
            ),  # Extract from machine refs
            status=RequestStatus.PENDING,
            instance_ids=instance_ids,
            metadata=metadata or {},
            created_at=datetime.utcnow(),
            version=0,
        )

        # Add domain event
        creation_event = RequestCreatedEvent.create(
            request_id=request_id,
            request_type=RequestType.TERMINATE.value,
            template_id="return-request",
            machine_count=len(machine_refs),
            metadata=metadata or {},
        )
        request.add_domain_event(creation_event)

        return request

    @classmethod
    def from_provider_format(cls, data: Dict[str, Any], provider_type: str) -> "Request":
        """Create request from provider-specific format."""
        core_data = {
            "request_id": data.get("request_id"),
            "request_type": RequestType(data.get("request_type", RequestType.CREATE.value)),
            "provider_type": provider_type,
            "template_id": data.get("template_id"),
            "requested_count": data.get("requested_count", 1),
            "status": RequestStatus(data.get("status", RequestStatus.PENDING.value)),
            "status_message": data.get("status_message"),
            "instance_ids": [InstanceId(value=id) for id in data.get("instance_ids", [])],
            "successful_count": data.get("successful_count", 0),
            "failed_count": data.get("failed_count", 0),
            "created_at": datetime.fromisoformat(
                data.get("created_at", datetime.utcnow().isoformat())
            ),
            "metadata": data.get("metadata", {}),
            "error_details": data.get("error_details", {}),
            "provider_data": data.get("provider_data", {}),
            "version": data.get("version", 0),
        }

        # Handle optional timestamps
        if "started_at" in data and data["started_at"]:
            core_data["started_at"] = datetime.fromisoformat(data["started_at"])
        if "completed_at" in data and data["completed_at"]:
            core_data["completed_at"] = datetime.fromisoformat(data["completed_at"])

        return cls.model_validate(core_data)

    def update_with_provisioning_result(self, provisioning_result: Dict[str, Any]) -> "Request":
        """
        Update request with provider provisioning results.

        Args:
            provisioning_result: Results from provider provisioning operation

        Returns:
            Updated Request instance
        """
        data = self.model_dump()

        # Extract instance IDs from provisioning result
        if "instance_ids" in provisioning_result:
            instance_ids = [InstanceId(value=id) for id in provisioning_result["instance_ids"]]
            data["instance_ids"] = self.instance_ids + instance_ids
            data["successful_count"] = len(data["instance_ids"])

        # Update provider data
        if "provider_data" in provisioning_result:
            current_provider_data = dict(self.provider_data)
            current_provider_data.update(provisioning_result["provider_data"])
            data["provider_data"] = current_provider_data

        # Update status if provisioning was successful
        if provisioning_result.get("success", False):
            data["status"] = RequestStatus.IN_PROGRESS
            if not self.started_at:
                data["started_at"] = datetime.utcnow()

        data["version"] = self.version + 1

        return Request.model_validate(data)

    def update_status(self, status: RequestStatus, message: Optional[str] = None) -> "Request":
        """
        Update request status.

        Args:
            status: New status
            message: Optional status message

        Returns:
            Updated Request instance
        """
        data = self.model_dump()
        data["status"] = status
        data["status_message"] = message
        data["version"] = self.version + 1

        if status in [
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
        ]:
            data["completed_at"] = datetime.utcnow()

        return Request.model_validate(data)
