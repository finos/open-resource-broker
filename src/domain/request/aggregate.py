"""Request aggregate - core request domain logic."""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ConfigDict, Field

from domain.base.entity import AggregateRoot
from domain.base.events import RequestCompletedEvent, RequestCreatedEvent, RequestStatusChangedEvent
from domain.request.exceptions import InvalidRequestStateError, RequestValidationError
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
    provider_name: Optional[str] = None

    # Request configuration
    template_id: str
    requested_count: int = 1
    desired_capacity: int = (
        1  # Initially set to same as requested_count, can be modified for capacity management
    )

    # Provider tracking (which provider was used)
    provider_api: Optional[str] = None  # Provider API/service used

    # Resource tracking (what was created)
    # Provider resource identifiers
    resource_ids: list[str] = Field(default_factory=list)
    machine_ids: list[str] = Field(default_factory=list)

    # Request state
    status: RequestStatus = Field(default=RequestStatus.PENDING)
    status_message: Optional[str] = None

    # HF output fields
    message: Optional[str] = None

    # Results
    successful_count: int = 0
    failed_count: int = 0

    # Lifecycle timestamps
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Request metadata
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_details: dict[str, Any] = Field(default_factory=dict)

    # Provider-specific data
    provider_data: dict[str, Any] = Field(default_factory=dict)

    # Versioning
    version: int = Field(default=0)

    def __init__(self, **data) -> None:
        """Initialize the instance."""
        # Set default ID if not provided
        if "id" not in data:
            data["id"] = data.get("request_id", f"request-{datetime.now(timezone.utc).isoformat()}")

        # Set default timestamps if not provided
        if "created_at" not in data:
            data["created_at"] = datetime.now(timezone.utc)

        super().__init__(**data)

    def get_id(self) -> str:
        """Get the request ID."""
        return str(self.request_id)

    @property
    def resource_id(self) -> Optional[str]:
        """Get the primary resource ID (first in list), or None if no resources."""
        return self.resource_ids[0] if self.resource_ids else None

    def start_processing(self) -> "Request":
        """Mark request as started processing."""
        if self.status != RequestStatus.PENDING:
            raise InvalidRequestStateError(self.status.value, RequestStatus.IN_PROGRESS.value)

        old_status = self.status
        fields = self.model_dump()
        fields["status"] = RequestStatus.IN_PROGRESS
        fields["started_at"] = datetime.utcnow()
        fields["version"] = self.version + 1

        updated_request = Request.model_validate(fields)

        # Add domain event for status change
        status_event = RequestStatusChangedEvent(
            aggregate_id=str(self.request_id),
            aggregate_type="Request",
            request_id=str(self.request_id),
            request_type=self.request_type.value,
            old_status=old_status.value,
            new_status=RequestStatus.IN_PROGRESS.value,
        )
        updated_request.add_domain_event(status_event)

        return updated_request

    def add_failure(
        self, error_message: str, error_details: Optional[dict[str, Any]] = None
    ) -> "Request":
        """Add a failed instance creation."""
        fields = self.model_dump()
        fields["failed_count"] = self.failed_count + 1
        fields["version"] = self.version + 1

        # Update error details
        if error_details:
            current_errors = dict(self.error_details)
            current_errors[f"error_{self.failed_count}"] = {
                "message": error_message,
                "details": error_details,
                "timestamp": datetime.utcnow().isoformat(),
            }
            fields["error_details"] = current_errors

        # Check if request is complete
        if self.successful_count + fields["failed_count"] >= self.requested_count:
            fields["status"] = (
                RequestStatus.PARTIAL if self.successful_count > 0 else RequestStatus.FAILED
            )
            fields["completed_at"] = datetime.utcnow()
            fields["status_message"] = f"Request completed with {fields['failed_count']} failures"

        return Request.model_validate(fields)

    def cancel(self, reason: str) -> "Request":
        """Cancel the request."""
        if self.status in [
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
        ]:
            raise InvalidRequestStateError(self.status.value, RequestStatus.CANCELLED.value)

        fields = self.model_dump()
        fields["status"] = RequestStatus.CANCELLED
        fields["status_message"] = reason
        fields["completed_at"] = datetime.utcnow()
        fields["version"] = self.version + 1

        return Request.model_validate(fields)

    def complete(self, message: Optional[str] = None) -> "Request":
        """Mark request as completed."""
        old_status = self.status
        fields = self.model_dump()
        fields["status"] = RequestStatus.COMPLETED
        fields["status_message"] = message or "Request completed successfully"
        fields["completed_at"] = datetime.utcnow()
        fields["version"] = self.version + 1

        updated_request = Request.model_validate(fields)

        # Add domain events
        status_event = RequestStatusChangedEvent(
            aggregate_id=str(self.request_id),
            aggregate_type="Request",
            request_id=str(self.request_id),
            request_type=self.request_type.value,
            old_status=old_status.value,
            new_status=RequestStatus.COMPLETED.value,
        )
        updated_request.add_domain_event(status_event)

        completion_event = RequestCompletedEvent(
            aggregate_id=str(self.request_id),
            aggregate_type="Request",
            request_id=str(self.request_id),
            request_type=self.request_type.value,
            completion_status=RequestStatus.COMPLETED.value,
            machine_ids=self.machine_ids,
        )
        updated_request.add_domain_event(completion_event)

        return updated_request

    def fail(self, error_message: str, error_details: Optional[dict[str, Any]] = None) -> "Request":
        """Mark request as failed."""
        fields = self.model_dump()
        fields["status"] = RequestStatus.FAILED
        fields["status_message"] = error_message
        fields["completed_at"] = datetime.utcnow()
        fields["version"] = self.version + 1

        if error_details:
            fields["error_details"] = error_details

        return Request.model_validate(fields)

    def set_provider_data(self, provider_data: dict[str, Any]) -> "Request":
        """Set provider-specific data."""
        fields = self.model_dump()
        fields["provider_data"] = provider_data
        fields["version"] = self.version + 1
        return Request.model_validate(fields)

    def update_metadata(self, updates: dict) -> "Request":
        new_metadata = {**self.metadata, **updates}
        fields = self.model_dump()
        fields["metadata"] = new_metadata
        fields["version"] = self.version + 1
        return Request.model_validate(fields)

    def with_launch_template_info(self, template_id: str, version: str) -> "Request":
        new_provider_data = {
            **self.provider_data,
            "launch_template_id": template_id,
            "launch_template_version": version,
        }
        fields = self.model_dump()
        fields["provider_data"] = new_provider_data
        fields["version"] = self.version + 1
        return Request.model_validate(fields)

    def get_provider_data(self, key: str, default: Any = None) -> Any:
        """Get provider-specific data value."""
        return self.provider_data.get(key, default)

    def add_resource_id(self, resource_id: str) -> "Request":
        """Add a provider resource ID"""
        if resource_id not in self.resource_ids:
            fields = self.model_dump()
            fields["resource_ids"] = [*self.resource_ids, resource_id]
            fields["version"] = self.version + 1
            return Request.model_validate(fields)
        return self

    def remove_resource_id(self, resource_id: str) -> "Request":
        """Remove a resource ID"""
        if resource_id in self.resource_ids:
            fields = self.model_dump()
            fields["resource_ids"] = [rid for rid in self.resource_ids if rid != resource_id]
            fields["version"] = self.version + 1
            return Request.model_validate(fields)
        return self

    def add_machine_ids(self, machine_ids: list[str]) -> "Request":
        """Add machine IDs to request."""
        updated_ids = list(set(self.machine_ids + machine_ids))
        return self.model_copy(update={"machine_ids": updated_ids})

    def update_machine_ids(self, machine_ids: list[str]) -> "Request":
        """Update machine IDs (for population)."""
        return self.model_copy(update={"machine_ids": machine_ids})

    def needs_machine_id_population(self) -> bool:
        """Check if request needs machine ID population."""
        return bool(
            not self.machine_ids and self.resource_ids and self.request_type != RequestType.RETURN
        )

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

    def to_provider_format(self, provider_type: str) -> dict[str, Any]:
        """Convert request to provider-specific format."""
        base_format = {
            "request_id": self.request_id,
            "request_type": self.request_type.value,
            "provider_type": self.provider_type,
            "template_id": self.template_id,
            "requested_count": self.requested_count,
            "desired_capacity": self.desired_capacity,
            "status": self.status.value,
            "status_message": self.status_message,
            "successful_count": self.successful_count,
            "failed_count": self.failed_count,
            "created_at": self.created_at.isoformat()
            if self.created_at
            else datetime.now(timezone.utc).isoformat(),
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
        provider_name: Optional[str] = None,  # Specific provider instance
        metadata: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,  # Allow external ID to be provided
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
            request_id: Optional external request ID (if not provided, will be generated)

        Returns:
            New Request instance with creation event
        """
        # Use provided request_id or generate one if not provided
        if request_id:
            # If request_id doesn't have prefix, add it based on request_type
            if not request_id.startswith(("req-", "ret-")):
                prefix = "req-" if request_type == RequestType.ACQUIRE else "ret-"
                request_id_obj = RequestId(value=f"{prefix}{request_id}")
            else:
                # Validate that existing prefix matches request_type
                expected_prefix = "req-" if request_type == RequestType.ACQUIRE else "ret-"
                if not request_id.startswith(expected_prefix):
                    raise RequestValidationError(
                        f"Request ID prefix mismatch: ID '{request_id}' has wrong prefix for "
                        f"request_type '{request_type.value}'. Expected prefix: '{expected_prefix}'"
                    )
                request_id_obj = RequestId(value=request_id)
        else:
            request_id_obj = RequestId.generate(request_type)

        # Create request
        request = cls(
            request_id=request_id_obj,
            request_type=request_type,
            template_id=template_id,
            requested_count=machine_count,
            desired_capacity=machine_count,  # Initially set to same as requested_count
            provider_type=provider_type,
            provider_name=provider_name,
            status=RequestStatus.PENDING,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
            version=0,
        )

        # Add domain event
        creation_event = RequestCreatedEvent(
            # DomainEvent required fields
            aggregate_id=str(request_id_obj.value),  # Use .value for string representation
            aggregate_type="Request",
            # RequestEvent required fields
            request_id=str(request_id_obj.value),  # Use .value for string representation
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
        machine_ids: list[str],
        provider_type: str,
        provider_name: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "Request":
        """Create a return/terminate request with machine IDs."""
        request_id = RequestId.generate(RequestType.RETURN)

        request = cls(
            request_id=request_id,
            request_type=RequestType.RETURN,
            template_id="return-request",
            requested_count=len(machine_ids),
            desired_capacity=len(machine_ids),
            provider_type=provider_type,
            provider_name=provider_name,
            machine_ids=machine_ids,
            status=RequestStatus.PENDING,
            metadata=metadata or {},
            created_at=datetime.utcnow(),
            version=0,
        )

        # Add domain event
        creation_event = RequestCreatedEvent(
            aggregate_id=str(request_id.value),
            aggregate_type="Request",
            request_id=str(request_id.value),
            request_type=RequestType.RETURN.value,
            template_id="return-request",
            machine_count=len(machine_ids),
            timeout=metadata.get("timeout") if metadata else None,
            tags=metadata.get("tags", {}) if metadata else {},
        )
        request.add_domain_event(creation_event)

        return request

    @classmethod
    def from_provider_format(cls, data: dict[str, Any], provider_type: str) -> "Request":
        """Create request from provider-specific format."""
        core_data = {
            "request_id": data.get("request_id"),
            "request_type": RequestType(data.get("request_type", RequestType.ACQUIRE.value)),
            "provider_type": provider_type,
            "template_id": data.get("template_id"),
            "requested_count": data.get("requested_count", 1),
            "desired_capacity": data.get("desired_capacity", data.get("requested_count", 1)),
            "status": RequestStatus(data.get("status", RequestStatus.PENDING.value)),
            "status_message": data.get("status_message"),
            "successful_count": data.get("successful_count", 0),
            "failed_count": data.get("failed_count", 0),
            "created_at": datetime.fromisoformat(
                data.get("created_at") or datetime.utcnow().isoformat()
            ),
            "metadata": data.get("metadata", {}),
            "error_details": data.get("error_details", {}),
            "provider_data": data.get("provider_data", {}),
            "version": data.get("version", 0),
        }

        # Handle optional timestamps
        if data.get("started_at"):
            core_data["started_at"] = datetime.fromisoformat(data["started_at"])
        if data.get("completed_at"):
            core_data["completed_at"] = datetime.fromisoformat(data["completed_at"])

        return cls.model_validate(core_data)

    def update_with_provisioning_result(self, provisioning_result: dict[str, Any]) -> "Request":
        """
        Update request with provider provisioning results.

        Args:
            provisioning_result: Results from provider provisioning operation

        Returns:
            Updated Request instance
        """
        fields = self.model_dump()

        # Update successful count from provisioning result
        if "instance_ids" in provisioning_result:
            fields["successful_count"] = self.successful_count + len(
                provisioning_result["instance_ids"]
            )

        # Update provider data
        if "provider_data" in provisioning_result:
            current_provider_data = dict(self.provider_data)
            current_provider_data.update(provisioning_result["provider_data"])
            fields["provider_data"] = current_provider_data

        # Update status if provisioning was successful
        if provisioning_result.get("success", False):
            fields["status"] = RequestStatus.IN_PROGRESS
            if not self.started_at:
                fields["started_at"] = datetime.utcnow()

        fields["version"] = self.version + 1

        return Request.model_validate(fields)

    def update_status(self, status: RequestStatus, message: Optional[str] = None) -> "Request":
        """
        Update request status.

        Args:
            status: New status
            message: Optional status message

        Returns:
            Updated Request instance
        """
        fields = self.model_dump()
        fields["status"] = status
        fields["status_message"] = message
        fields["version"] = self.version + 1

        if status in [
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
        ]:
            fields["completed_at"] = datetime.now(timezone.utc)

        return Request.model_validate(fields)
