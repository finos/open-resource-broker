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

# Module-level logger for static helpers that have no instance ``self.logger``.
_module_logger = get_logger(__name__)


def _id_str(value_obj: Any) -> str:
    """Extract string from a value object or plain string."""
    return str(value_obj.value) if hasattr(value_obj, "value") else str(value_obj)


def _unwrap_provider_data(raw: Any) -> dict[str, Any]:
    """Normalise the stored ``provider_data`` dict to a flat envelope.

    Old records were persisted with a nested wrapper shape written by the AWS
    provisioning adapter before the envelope was flattened:

        {"method": "provisioning_adapter", "provider_data": {"target_units": 3, ...}}

    The new shape places all keys at the top level:

        {"method": "provisioning_adapter", "target_units": 3, ...}

    This shim detects the legacy shape on read (presence of a "provider_data"
    dict value nested inside the top-level provider_data dict) and promotes its
    contents to the top level, removing the redundant nested key.  It is
    idempotent: records already written in the flat format pass through
    unchanged.
    """
    if not isinstance(raw, dict):
        return raw or {}

    nested = raw.get("provider_data")
    if not isinstance(nested, dict):
        return raw  # already flat or no nesting — nothing to do

    # Promote nested keys; caller's top-level keys take priority.
    merged: dict[str, Any] = {**nested, **raw}
    del merged["provider_data"]
    return merged


class RequestSerializer(BaseEntitySerializer):
    """Handles Request aggregate serialization/deserialization."""

    def __init__(self) -> None:
        """Initialize the instance."""
        super().__init__()
        self._dt = GenericEntitySerializer(Request, "Request", "request_id")

    @staticmethod
    def _apply_nullable_defaults(data: dict[str, Any]) -> dict[str, Any]:
        """Coerce nullable JSON columns to safe empty containers.

        Applied unconditionally so NULL values stored in legacy rows before
        the NOT NULL migration are never passed as Python None to from_dict
        for fields that expect a list or dict.

        machine_ids handling differs by request_type:
          - acquire:  NULL coerced to [] (some acquire flows may legitimately
                      not yet have machine_ids stored).
          - return:   NULL is a domain-model invariant violation — a return
                      request without machine_ids is malformed (you cannot
                      return machines you haven't identified).  Raises
                      ValueError so the caller surfaces the failure rather
                      than treating the request as having zero machines to
                      return (which would let the status poller declare it
                      COMPLETED immediately).
        """
        if data.get("metadata") is None:
            data["metadata"] = {}
        if data.get("error_details") is None:
            data["error_details"] = {}
        if data.get("provider_data") is None:
            data["provider_data"] = {}
        if data.get("resource_ids") is None:
            data["resource_ids"] = []

        if data.get("machine_ids") is None:
            request_type = data.get("request_type")
            if request_type == "return":
                raise ValueError(
                    f"Return request {data.get('request_id', '<unknown>')} has NULL "
                    "machine_ids — this is a domain-model invariant violation. "
                    "A return request must reference the machines being returned. "
                    "Inspect the record and repair it before reprocessing."
                )
            # For acquire requests, NULL machine_ids is acceptable (machines may
            # not yet be assigned during early provisioning stages).
            data["machine_ids"] = []

        return data

    @staticmethod
    def _parse_fulfilment_diagnostic(raw: Any) -> Any:
        """Parse a persisted fulfilment_diagnostic into a dict for the aggregate.

        Accepts a JSON string, an already-parsed dict, or None. Returns a plain
        dict (which ``Request.model_validate`` coerces into the value object) or
        None. Malformed payloads degrade to None rather than failing the load —
        a diagnostic is advisory, never load-critical.

        "Malformed" covers BOTH shape failures (non-JSON string, non-dict JSON)
        AND semantic failures: a valid-JSON dict that does not satisfy the
        ``FulfilmentDiagnostic`` schema (e.g. an unknown ``category`` written by
        a newer version, or a missing ``occurred_at``) is degraded to None too.
        Returning such a dict verbatim would make ``Request.model_validate``
        raise and blow up the entire request load — violating the advisory,
        never-load-critical contract this method promises.
        """
        if raw is None:
            return None

        parsed: Any
        if isinstance(raw, dict):
            parsed = raw
        elif isinstance(raw, str):
            import json

            try:
                loaded = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return None
            if not isinstance(loaded, dict):
                return None
            parsed = loaded
        else:
            return None

        # Semantic validation: confirm the dict actually satisfies the value
        # object schema. Degrade to None (with a warning) on a validation error
        # so version-skewed / partial diagnostics never make the whole request
        # unloadable.
        from pydantic import ValidationError

        from orb.domain.base.diagnostic import FulfilmentDiagnostic

        try:
            FulfilmentDiagnostic.model_validate(parsed)
        except ValidationError as exc:
            _module_logger.warning(
                "Discarding malformed fulfilment_diagnostic (advisory-only) on load: %s",
                exc,
            )
            return None
        return parsed

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
                "machine_count": request.requested_count,  # Legacy key — kept for backward compat
                "requested_count": request.requested_count,
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
                # Legacy HF wire-format key — written as an alias for status_message so
                # that records read back by older ORB versions still have a "message" key.
                # On read, from_dict treats "message" as a fallback for status_message.
                "message": request.status_message,
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
                "first_status_check": self._dt.serialize_datetime(request.first_status_check),
                "last_status_check": self._dt.serialize_datetime(request.last_status_check),
                # Fulfilment state machine
                "deadline_at": self._dt.serialize_datetime(request.deadline_at),
                "partial_since": self._dt.serialize_datetime(request.partial_since),
                "last_transition_at": self._dt.serialize_datetime(request.last_transition_at),
                "fulfilment_diagnostic": (
                    request.fulfilment_diagnostic.model_dump_json()
                    if request.fulfilment_diagnostic is not None
                    else None
                ),
                # Versioning
                "version": request.version,
                # Legacy fields for backward compatibility
                "timeout": request.metadata.get("timeout"),
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
            data = dict(data)  # shallow copy — never mutate the caller's dict
            data = self._apply_nullable_defaults(data)

            # Parse datetime fields using shared helper
            created_at = datetime.fromisoformat(data["created_at"])
            started_at = self._dt.deserialize_datetime(data.get("started_at"))
            completed_at = self._dt.deserialize_datetime(data.get("completed_at"))

            # Fulfilment diagnostic may arrive as a JSON string (file backends)
            # or an already-parsed dict (SQL serializer auto-parses JSON TEXT).
            fulfilment_diagnostic = self._parse_fulfilment_diagnostic(
                data.get("fulfilment_diagnostic")
            )

            # Build request data with additional fields
            request_data = {
                # Core request fields - handle RequestId properly
                "request_id": self._parse_request_id(data["request_id"]),
                "template_id": data["template_id"],
                "requested_count": data.get("requested_count", data.get("machine_count", 1)),
                "desired_capacity": data.get(
                    "desired_capacity", data.get("machine_count", data.get("requested_count", 1))
                ),  # Default to requested_count if not present
                "request_type": RequestType(data["request_type"]),
                "status": RequestStatus(data["status"]),
                # "message" is the legacy HF wire-format key written by older serializer
                # versions.  Prefer the canonical "status_message"; fall back to
                # "error_message" (even older alias), then "message".
                "status_message": data.get("status_message")
                or data.get("error_message")
                or data.get("message"),
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
                # Results and instances
                "successful_count": data.get("successful_count", 0),
                "failed_count": data.get("failed_count", 0),
                # Metadata and error details
                "metadata": data.get("metadata", {}),
                "error_details": data.get("error_details", {}),
                "provider_data": _unwrap_provider_data(data.get("provider_data", {})),
                # Timestamps
                "created_at": created_at,
                "started_at": started_at,
                "completed_at": completed_at,
                "first_status_check": self._dt.deserialize_datetime(data.get("first_status_check")),
                "last_status_check": self._dt.deserialize_datetime(data.get("last_status_check")),
                # Fulfilment state machine
                "deadline_at": self._dt.deserialize_datetime(data.get("deadline_at")),
                "partial_since": self._dt.deserialize_datetime(data.get("partial_since")),
                "last_transition_at": self._dt.deserialize_datetime(data.get("last_transition_at")),
                "fulfilment_diagnostic": fulfilment_diagnostic,
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
            seen: set[str] = set()
            result = []
            for r in pending + in_progress:
                if r.request_id not in seen:
                    seen.add(r.request_id)
                    result.append(r)
            return result
        except Exception as e:
            self.logger.error("Failed to find active requests: %s", e)
            raise

    @handle_infrastructure_exceptions(context="request_repository_find_by_date_range")
    def find_by_date_range(self, start_date: datetime, end_date: datetime) -> list[Request]:
        """Find requests within date range."""
        from datetime import timezone

        try:
            if start_date.tzinfo is None:
                start_date = start_date.replace(tzinfo=timezone.utc)
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)

            all_requests = self.find_all()
            filtered_requests = []

            for request in all_requests:
                request_date = request.created_at
                if request_date.tzinfo is None:
                    request_date = request_date.replace(tzinfo=timezone.utc)

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

    def count_by_status(self) -> dict[str, int]:
        """Return ``{status: count}`` for all requests.

        Delegates to ``storage_strategy.count_by_column("status")`` when the
        underlying strategy supports it (SQL fast path).  Falls back to the
        domain-interface default (list all + group) for file-based backends.
        """
        strategy = getattr(self, "storage_strategy", None)
        if strategy is not None and hasattr(strategy, "count_by_column"):
            result = strategy.count_by_column("status")
            if result:
                return result
        # Slow path: list all rows and group in Python.
        return super().count_by_status()

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
