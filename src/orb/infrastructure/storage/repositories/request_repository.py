"""Single request repository implementation using storage strategy composition."""

import time
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from orb.domain.base.events import DomainEvent
from orb.domain.request.aggregate import Request
from orb.domain.request.repository import RequestRepository as RequestRepositoryInterface
from orb.domain.request.value_objects import RequestId, RequestStatus, RequestType
from orb.infrastructure.error.decorators import handle_infrastructure_exceptions
from orb.infrastructure.events import (
    RepositoryOperationCompletedEvent,
    RepositoryOperationFailedEvent,
    RepositoryOperationStartedEvent,
    SlowQueryDetectedEvent,
)
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.base.repository_mixin import StorageRepositoryMixin
from orb.infrastructure.storage.base.strategy import BaseStorageStrategy
from orb.infrastructure.storage.components.entity_serializer import BaseEntitySerializer
from orb.infrastructure.storage.components.generic_serializer import GenericEntitySerializer
from orb.infrastructure.storage.constants import LEGACY_DEFAULT_PROVIDER_TYPE


def _id_str(value_obj: Any) -> str:
    """Extract string from a value object or plain string."""
    return str(value_obj.value) if hasattr(value_obj, "value") else str(value_obj)


class RequestSerializer(BaseEntitySerializer):
    """Handles Request aggregate serialization/deserialization."""

    def __init__(self) -> None:
        """Initialize the instance."""
        super().__init__()
        self._dt = GenericEntitySerializer(Request, "Request", "request_id")

    def _parse_request_id(self, request_id_data: Any) -> RequestId:
        """Parse RequestId from various formats."""
        if isinstance(request_id_data, str):
            # Handle stringified dict format: "{'value': 'req-...'}"
            if request_id_data.startswith("{'value':") or request_id_data.startswith('{"value":'):
                import ast

                try:
                    parsed = ast.literal_eval(request_id_data)
                    if isinstance(parsed, dict) and "value" in parsed:
                        return RequestId(value=parsed["value"])
                except (ValueError, SyntaxError):
                    pass
            # Handle direct string format
            return RequestId(value=request_id_data)
        elif isinstance(request_id_data, dict) and "value" in request_id_data:
            # Handle dict format: {'value': 'req-...'}
            return RequestId(value=request_id_data["value"])
        else:
            # Fallback to string conversion
            return RequestId(value=str(request_id_data))

    def to_dict(self, entity: Any) -> dict[str, Any]:
        """Convert Request aggregate to dictionary with additional fields."""
        request: Request = entity
        try:
            return {
                # Core request fields
                "request_id": _id_str(request.request_id),
                "template_id": request.template_id,
                "machine_count": request.requested_count,
                "desired_capacity": request.desired_capacity,
                "request_type": _id_str(request.request_type),
                "status": _id_str(request.status),
                "status_message": request.status_message,
                # Provider tracking fields
                "provider_name": request.provider_name,
                "provider_api": request.provider_api,
                "provider_type": request.provider_type,
                # Resource tracking fields
                "resource_ids": request.resource_ids,
                "machine_ids": request.machine_ids,
                # HF output fields
                "message": request.message,
                # Results and instances
                "successful_count": request.successful_count,
                "failed_count": request.failed_count,
                # Metadata and error details
                "metadata": request.metadata or {},
                "error_details": request.error_details or {},
                "provider_data": request.provider_data or {},
                # Timestamps
                "created_at": request.created_at.isoformat(),  # type: ignore[union-attr]
                "started_at": self._dt.serialize_datetime(request.started_at),
                "completed_at": self._dt.serialize_datetime(request.completed_at),
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
            self.logger.error("Failed to serialize request %s: %s", request.request_id, e)
            raise

    def from_dict(self, data: dict[str, Any]) -> Request:
        """Convert dictionary to Request aggregate with additional field support."""
        try:
            # Parse datetime fields using shared helper
            created_at = datetime.fromisoformat(data["created_at"])
            started_at = self._dt.deserialize_datetime(data.get("started_at"))
            completed_at = self._dt.deserialize_datetime(data.get("completed_at"))

            # Build request data with additional fields
            request_data = {
                # Core request fields - handle RequestId properly
                "request_id": self._parse_request_id(data["request_id"]),
                "template_id": data["template_id"],
                "requested_count": data.get("machine_count", data.get("requested_count", 1)),
                "desired_capacity": data.get(
                    "desired_capacity", data.get("machine_count", data.get("requested_count", 1))
                ),  # Default to requested_count if not present
                "request_type": RequestType(data["request_type"]),
                "status": RequestStatus(data["status"]),
                "status_message": data.get("status_message", data.get("error_message")),
                # Provider tracking fields
                "provider_name": data.get("provider_name"),
                "provider_api": data.get("provider_api"),
                # Legacy records written before provider_type was persisted default to 'aws'.
                # Do not change this default without a data migration.
                "provider_type": data.get("provider_type", LEGACY_DEFAULT_PROVIDER_TYPE),
                # Resource tracking fields
                "resource_ids": data.get("resource_ids", []),
                "machine_ids": [
                    mid for mid in (data.get("machine_ids", []) or []) if mid is not None
                ],
                # HF output fields
                "message": data.get("message"),
                # Results and instances
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
            self.logger.error("Failed to deserialize request data: %s", e)
            raise


class RequestRepositoryImpl(StorageRepositoryMixin, RequestRepositoryInterface):
    """Single request repository implementation using storage strategy composition."""

    def __init__(self, storage_strategy: BaseStorageStrategy, event_publisher=None) -> None:
        """Initialize repository with storage strategy and optional event publisher."""
        self.storage_strategy = storage_strategy
        self.serializer = RequestSerializer()
        self.logger = get_logger(__name__)
        self.event_publisher = event_publisher
        self.slow_query_threshold_ms = 1000.0  # 1 second threshold

    def _publish_storage_event(self, event: DomainEvent) -> None:
        """Publish storage event if publisher is available."""
        if self.event_publisher:
            try:
                self.event_publisher.publish(event)
            except Exception as e:
                self.logger.warning("Failed to publish storage event: %s", e)

    @handle_infrastructure_exceptions(context="request_repository_save")
    def save(self, request: Request) -> list[Any]:
        """Save request using storage strategy and return extracted events."""
        operation_id = str(uuid4())
        start_time = time.time()
        entity_id = _id_str(request.request_id)

        self._publish_storage_event(
            RepositoryOperationStartedEvent(
                aggregate_id=operation_id,
                aggregate_type="RepositoryOperation",
                operation_id=operation_id,
                entity_type="Request",
                entity_id=entity_id,
                storage_strategy=self.storage_strategy.__class__.__name__,
                operation_type="save",
            )
        )

        try:
            request_data = self.serializer.to_dict(request)
            self.storage_strategy.save(entity_id, request_data)  # type: ignore[call-arg]

            duration_ms = (time.time() - start_time) * 1000

            events = request.get_domain_events()
            request.clear_domain_events()

            self._publish_storage_event(
                RepositoryOperationCompletedEvent(
                    aggregate_id=operation_id,
                    aggregate_type="RepositoryOperation",
                    operation_id=operation_id,
                    entity_type="Request",
                    entity_id=entity_id,
                    storage_strategy=self.storage_strategy.__class__.__name__,
                    operation_type="save",
                    duration_ms=duration_ms,
                    success=True,
                    records_affected=1,
                )
            )

            if duration_ms > self.slow_query_threshold_ms:
                self._publish_storage_event(
                    SlowQueryDetectedEvent(
                        aggregate_id=operation_id,
                        aggregate_type="Performance",
                        operation_id=operation_id,
                        entity_type="Request",
                        entity_id=entity_id,
                        storage_strategy=self.storage_strategy.__class__.__name__,
                        operation_type="save",
                        duration_ms=duration_ms,
                        threshold_ms=self.slow_query_threshold_ms,
                        query_details={"data_size": len(str(request_data))},
                    )
                )

            self.logger.debug(
                "Saved request %s and extracted %s events",
                request.request_id,
                len(events),
            )
            return events

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            self._publish_storage_event(
                RepositoryOperationFailedEvent(
                    aggregate_id=operation_id,
                    aggregate_type="RepositoryOperation",
                    operation_id=operation_id,
                    entity_type="Request",
                    entity_id=entity_id,
                    storage_strategy=self.storage_strategy.__class__.__name__,
                    operation_type="save",
                    error_message=str(e),
                    error_code=type(e).__name__,
                    retry_count=0,
                    duration_ms=duration_ms,
                )
            )

            self.logger.error("Failed to save request %s: %s", request.request_id, e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_get_by_id")
    def get_by_id(self, request_id: RequestId) -> Optional[Request]:
        """Get request by ID using storage strategy."""
        try:
            return self._load_by_id(_id_str(request_id))  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to get request %s: %s", request_id, e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_id")
    def find_by_id(self, request_id: RequestId | str) -> Optional[Request]:
        """Find request by ID (alias for get_by_id)."""
        if isinstance(request_id, str):
            request_id = RequestId(value=request_id)
        return self.get_by_id(request_id)

    @handle_infrastructure_exceptions(context="request_repository_find_by_request_id")
    def find_by_request_id(self, request_id: str) -> Optional[Request]:
        """Find request by request ID string."""
        try:
            return self.get_by_id(RequestId(value=request_id))
        except Exception as e:
            self.logger.error("Failed to find request by request_id %s: %s", request_id, e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_status")
    def find_by_status(self, status: RequestStatus) -> list[Request]:
        """Find requests by status."""
        try:
            return self._load_by_criteria({"status": _id_str(status)})  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find requests by status %s: %s", status, e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_template_id")
    def find_by_template_id(self, template_id: str) -> list[Request]:
        """Find requests by template ID."""
        try:
            return self._load_by_criteria({"template_id": template_id})  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find requests by template_id %s: %s", template_id, e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_type")
    def find_by_type(self, request_type: RequestType) -> list[Request]:
        """Find requests by type."""
        try:
            return self._load_by_criteria({"request_type": _id_str(request_type)})  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find requests by type %s: %s", request_type, e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_pending_requests")
    def find_pending_requests(self) -> list[Request]:
        """Find pending requests."""
        return self.find_by_status(RequestStatus.PENDING)

    @handle_infrastructure_exceptions(context="request_repository_find_active_requests")
    def find_active_requests(self) -> list[Request]:
        """Find active requests (pending or in_progress)."""
        try:
            pending = self.find_by_status(RequestStatus.PENDING)
            in_progress = self.find_by_status(RequestStatus.IN_PROGRESS)
            return pending + in_progress
        except Exception as e:
            self.logger.error("Failed to find active requests: %s", e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_date_range")
    def find_by_date_range(self, start_date: datetime, end_date: datetime) -> list[Request]:
        """Find requests within date range."""
        try:
            all_requests = self.find_all()
            filtered_requests = []

            for request in all_requests:
                request_date = request.created_at

                if request_date.tzinfo is None and start_date.tzinfo is not None:
                    from datetime import timezone

                    request_date = request_date.replace(tzinfo=timezone.utc)
                elif request_date.tzinfo is not None and start_date.tzinfo is None:
                    from datetime import timezone

                    start_date = start_date.replace(tzinfo=timezone.utc)
                    end_date = end_date.replace(tzinfo=timezone.utc)

                if start_date <= request_date <= end_date:
                    filtered_requests.append(request)

            return filtered_requests
        except Exception as e:
            self.logger.error("Failed to find requests by date range: %s", e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_all")
    def find_all(self) -> list[Request]:
        """Find all requests."""
        try:
            return self._load_all()  # type: ignore[return-value]
        except Exception as e:
            self.logger.error("Failed to find all requests: %s", e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_delete")
    def delete(self, request_id: RequestId) -> None:
        """Delete request by ID."""
        try:
            self._delete_by_id(_id_str(request_id))
            self.logger.debug("Deleted request %s", request_id)
        except Exception as e:
            self.logger.error("Failed to delete request %s: %s", request_id, e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_ids")
    def find_by_ids(self, request_ids: list[str]) -> list[Request]:
        """Find requests by multiple request IDs."""
        try:
            requests = []
            for request_id in request_ids:
                request = self.find_by_request_id(request_id)
                if request:
                    requests.append(request)
            return requests
        except Exception as e:
            self.logger.error("Failed to find requests by IDs %s: %s", request_ids, e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_exists")
    def exists(self, request_id: RequestId) -> bool:
        """Check if request exists."""
        try:
            return self._check_exists(_id_str(request_id))
        except Exception as e:
            self.logger.error("Failed to check if request %s exists: %s", request_id, e)
            raise

    def count_by_date_range(self, start_date: datetime, end_date: datetime) -> int:
        """Count requests within date range."""
        return len(self.find_by_date_range(start_date, end_date))

    def count_by_status_and_date_range(
        self, status: RequestStatus, start_date: datetime, end_date: datetime
    ) -> int:
        """Count requests by status within date range."""
        requests = self.find_by_date_range(start_date, end_date)
        return len([r for r in requests if r.status == status])

    def get_metrics_by_date_range(self, start_date: datetime, end_date: datetime) -> dict[str, int]:
        """Get aggregated metrics within date range."""
        requests = self.find_by_date_range(start_date, end_date)

        metrics = {
            "total": len(requests),
            "completed": 0,
            "failed": 0,
            "in_progress": 0,
            "pending": 0,
        }

        for request in requests:
            if request.status == RequestStatus.COMPLETED:
                metrics["completed"] += 1
            elif request.status == RequestStatus.FAILED:
                metrics["failed"] += 1
            elif request.status == RequestStatus.IN_PROGRESS:
                metrics["in_progress"] += 1
            elif request.status == RequestStatus.PENDING:
                metrics["pending"] += 1

        return metrics
