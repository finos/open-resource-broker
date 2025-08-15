"""Single request repository implementation using storage strategy composition."""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from domain.base.events import (
    DomainEvent,
    RepositoryOperationCompletedEvent,
    RepositoryOperationFailedEvent,
    RepositoryOperationStartedEvent,
    SlowQueryDetectedEvent,
)
from domain.base.ports.storage_port import StoragePort
from domain.base.value_objects import InstanceId  # Add InstanceId import
from domain.request.aggregate import Request
from domain.request.repository import (
    RequestRepository as RequestRepositoryInterface,
)
from domain.request.value_objects import RequestId, RequestStatus, RequestType
from infrastructure.error.decorators import handle_infrastructure_exceptions
from infrastructure.logging.logger import get_logger


class RequestSerializer:
    """Handles Request aggregate serialization/deserialization."""

    def __init__(self):
        """Initialize the instance."""
        self.logger = get_logger(__name__)

    def to_dict(self, request: Request) -> Dict[str, Any]:
        """Convert Request aggregate to dictionary with additional fields."""
        try:
            return {
                # Core request fields
                "request_id": str(request.request_id.value),
                "template_id": request.template_id,
                "machine_count": request.requested_count,
                "request_type": request.request_type.value,
                "status": request.status.value,
                "status_message": request.status_message,
                # Provider tracking fields
                "provider_name": request.provider_name,
                "provider_api": request.provider_api,
                "provider_type": request.provider_type,
                # Resource tracking fields
                "resource_ids": request.resource_ids,
                # HF output fields
                "message": request.message,
                # Results and instances
                "machine_ids": [str(instance_id.value) for instance_id in request.instance_ids],
                "successful_count": request.successful_count,
                "failed_count": request.failed_count,
                # Metadata and error details
                "metadata": request.metadata or {},
                "error_details": request.error_details or {},
                "provider_data": request.provider_data or {},
                # Timestamps
                "created_at": request.created_at.isoformat(),
                "started_at": (request.started_at.isoformat() if request.started_at else None),
                "completed_at": (
                    request.completed_at.isoformat() if request.completed_at else None
                ),
                # Versioning
                "version": request.version,
                # Legacy fields for backward compatibility
                "timeout": request.metadata.get("timeout"),
                "tags": request.metadata.get("tags", {}),
                "error_message": request.status_message,  # Legacy field name
                # Schema version for migration support
                "schema_version": "2.0.0",
            }
        except Exception as e:
            self.logger.error(f"Failed to serialize request {request.request_id}: {e}")
            raise

    def from_dict(self, data: Dict[str, Any]) -> Request:
        """Convert dictionary to Request aggregate with additional field support."""
        try:
            # Parse datetime fields
            created_at = datetime.fromisoformat(data["created_at"])
            started_at = (
                datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            )
            completed_at = (
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            )

            # Build request data with additional fields
            request_data = {
                # Core request fields
                "request_id": RequestId(value=data["request_id"]),
                "template_id": data["template_id"],
                "requested_count": data.get("machine_count", data.get("requested_count", 1)),
                "request_type": RequestType(data["request_type"]),
                "status": RequestStatus(data["status"]),
                "status_message": data.get("status_message", data.get("error_message")),
                # Provider tracking fields
                "provider_name": data.get("provider_name"),
                "provider_api": data.get("provider_api"),
                "provider_type": data.get("provider_type", "aws"),
                # Resource tracking fields
                "resource_ids": data.get("resource_ids", []),
                # HF output fields
                "message": data.get("message"),
                # Results and instances
                "instance_ids": [
                    InstanceId(value=machine_id) for machine_id in data.get("machine_ids", [])
                ],
                "successful_count": data.get("successful_count", 0),
                "failed_count": data.get("failed_count", 0),
                # Metadata and error details
                "metadata": data.get("metadata", {}),
                "error_details": data.get("error_details", {}),
                "provider_data": data.get("provider_data", {}),
                # Timestamps
                "created_at": created_at,
                "started_at": started_at,
                "completed_at": completed_at,
                # Versioning
                "version": data.get("version", 0),
            }

            # Create request using model_validate to handle all fields correctly
            request = Request.model_validate(request_data)

            return request

        except Exception as e:
            self.logger.error(f"Failed to deserialize request data: {e}")
            raise


class RequestRepositoryImpl(RequestRepositoryInterface):
    """Single request repository implementation using storage strategy composition."""

    def __init__(self, storage_port: StoragePort, event_publisher=None):
        """Initialize repository with storage port and optional event publisher."""
        self.storage_port = storage_port
        self.serializer = RequestSerializer()
        self.logger = get_logger(__name__)
        self.event_publisher = event_publisher
        self.slow_query_threshold_ms = 1000.0  # 1 second threshold

    def _publish_persistence_event(self, event: DomainEvent) -> None:
        """Publish persistence event if publisher is available."""
        if self.event_publisher:
            try:
                self.event_publisher.publish(event)
            except Exception as e:
                self.logger.warning(f"Failed to publish persistence event: {e}")

    @handle_infrastructure_exceptions(context="request_repository_save")
    def save(self, request: Request) -> List[Any]:
        """Save request using storage strategy and return extracted events."""
        operation_id = str(uuid4())
        start_time = time.time()
        entity_id = str(request.request_id.value)

        # Publish operation started event
        self._publish_persistence_event(
            RepositoryOperationStartedEvent(
                aggregate_id=operation_id,
                aggregate_type="RepositoryOperation",
                operation_id=operation_id,
                entity_type="Request",
                entity_id=entity_id,
                storage_strategy=self.storage_port.__class__.__name__,
                operation_type="save",
            )
        )

        try:
            # Save the request
            request_data = self.serializer.to_dict(request)
            self.storage_port.save(entity_id, request_data)

            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Extract events from the aggregate
            events = request.get_domain_events()
            request.clear_domain_events()

            # Publish operation completed event
            self._publish_persistence_event(
                RepositoryOperationCompletedEvent(
                    aggregate_id=operation_id,
                    aggregate_type="RepositoryOperation",
                    operation_id=operation_id,
                    entity_type="Request",
                    entity_id=entity_id,
                    storage_strategy=self.storage_port.__class__.__name__,
                    operation_type="save",
                    duration_ms=duration_ms,
                    success=True,
                    records_affected=1,
                )
            )

            # Check for slow operations
            if duration_ms > self.slow_query_threshold_ms:
                self._publish_persistence_event(
                    SlowQueryDetectedEvent(
                        aggregate_id=operation_id,
                        aggregate_type="Performance",
                        operation_id=operation_id,
                        entity_type="Request",
                        entity_id=entity_id,
                        storage_strategy=self.storage_port.__class__.__name__,
                        operation_type="save",
                        duration_ms=duration_ms,
                        threshold_ms=self.slow_query_threshold_ms,
                        query_details={"data_size": len(str(request_data))},
                    )
                )

            self.logger.debug(
                f"Saved request {request.request_id} and extracted {len(events)} events"
            )
            return events

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            # Publish operation failed event
            self._publish_persistence_event(
                RepositoryOperationFailedEvent(
                    aggregate_id=operation_id,
                    aggregate_type="RepositoryOperation",
                    operation_id=operation_id,
                    entity_type="Request",
                    entity_id=entity_id,
                    storage_strategy=self.storage_port.__class__.__name__,
                    operation_type="save",
                    error_message=str(e),
                    error_code=type(e).__name__,
                    retry_count=0,
                    duration_ms=duration_ms,
                )
            )

            self.logger.error(f"Failed to save request {request.request_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_get_by_id")
    def get_by_id(self, request_id: RequestId) -> Optional[Request]:
        """Get request by ID using storage strategy."""
        try:
            data = self.storage_port.find_by_id(str(request_id.value))
            if data:
                return self.serializer.from_dict(data)
            return None
        except Exception as e:
            self.logger.error(f"Failed to get request {request_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_id")
    def find_by_id(self, request_id: RequestId) -> Optional[Request]:
        """Find request by ID (alias for get_by_id)."""
        return self.get_by_id(request_id)

    @handle_infrastructure_exceptions(context="request_repository_find_by_request_id")
    def find_by_request_id(self, request_id: str) -> Optional[Request]:
        """Find request by request ID string."""
        try:
            return self.get_by_id(RequestId(value=request_id))
        except Exception as e:
            self.logger.error(f"Failed to find request by request_id {request_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_status")
    def find_by_status(self, status: RequestStatus) -> List[Request]:
        """Find requests by status."""
        try:
            criteria = {"status": status.value}
            data_list = self.storage_port.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error(f"Failed to find requests by status {status}: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_template_id")
    def find_by_template_id(self, template_id: str) -> List[Request]:
        """Find requests by template ID."""
        try:
            criteria = {"template_id": template_id}
            data_list = self.storage_port.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error(f"Failed to find requests by template_id {template_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_type")
    def find_by_type(self, request_type: RequestType) -> List[Request]:
        """Find requests by type."""
        try:
            criteria = {"request_type": request_type.value}
            data_list = self.storage_port.find_by_criteria(criteria)
            return [self.serializer.from_dict(data) for data in data_list]
        except Exception as e:
            self.logger.error(f"Failed to find requests by type {request_type}: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_pending_requests")
    def find_pending_requests(self) -> List[Request]:
        """Find pending requests."""
        return self.find_by_status(RequestStatus.PENDING)

    @handle_infrastructure_exceptions(context="request_repository_find_active_requests")
    def find_active_requests(self) -> List[Request]:
        """Find active requests (pending or in_progress)."""
        try:
            pending = self.find_by_status(RequestStatus.PENDING)
            in_progress = self.find_by_status(RequestStatus.IN_PROGRESS)
            return pending + in_progress
        except Exception as e:
            self.logger.error(f"Failed to find active requests: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_date_range")
    def find_by_date_range(self, start_date: datetime, end_date: datetime) -> List[Request]:
        """Find requests within date range."""
        try:
            all_requests = self.find_all()
            filtered_requests = []

            for request in all_requests:
                if start_date <= request.created_at <= end_date:
                    filtered_requests.append(request)

            return filtered_requests
        except Exception as e:
            self.logger.error(f"Failed to find requests by date range: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_all")
    def find_all(self) -> List[Request]:
        """Find all requests."""
        try:
            all_data = self.storage_port.find_all()
            return [self.serializer.from_dict(data) for data in all_data.values()]
        except Exception as e:
            self.logger.error(f"Failed to find all requests: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_delete")
    def delete(self, request_id: RequestId) -> None:
        """Delete request by ID."""
        try:
            self.storage_port.delete(str(request_id.value))
            self.logger.debug(f"Deleted request {request_id}")
        except Exception as e:
            self.logger.error(f"Failed to delete request {request_id}: {e}")
            raise

    @handle_infrastructure_exceptions(context="request_repository_exists")
    def exists(self, request_id: RequestId) -> bool:
        """Check if request exists."""
        try:
            return self.storage_port.exists(str(request_id.value))
        except Exception as e:
            self.logger.error(f"Failed to check if request {request_id} exists: {e}")
            raise
