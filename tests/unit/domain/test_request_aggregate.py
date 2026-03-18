"""Unit tests for Request aggregate."""

from datetime import datetime, timezone

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.exceptions import (
    InvalidRequestStateError,
    RequestNotFoundError,
    RequestProcessingError,
    RequestValidationError,
)
from orb.domain.request.value_objects import (
    RequestId,
    RequestStatus,
    RequestType,
)

# Try to import optional classes - create mocks if not available
try:
    from orb.domain.request.value_objects import Priority

    PRIORITY_AVAILABLE = True
except ImportError:
    PRIORITY_AVAILABLE = False

    class Priority:
        def __init__(self, value):
            if not isinstance(value, (int, str)):
                raise ValueError("Invalid priority")
            self.value = value


try:
    from orb.domain.request.request_metadata import MachineCount

    MACHINE_COUNT_AVAILABLE = True
except ImportError:
    MACHINE_COUNT_AVAILABLE = False

    class MachineCount:
        def __init__(self, value):
            if not isinstance(value, int) or value < 0:
                raise ValueError("Invalid machine count")
            self.value = value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(machine_count=2, template_id="template-001"):
    """Create a minimal valid new request."""
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id=template_id,
        machine_count=machine_count,
        provider_type="aws",
    )


def _make_return_request(machine_ids=None):
    """Create a minimal valid return request."""
    if machine_ids is None:
        machine_ids = ["i-001", "i-002"]
    return Request.create_return_request(
        machine_ids=machine_ids,
        provider_type="aws",
        provider_name="aws-us-east-1",
    )


@pytest.mark.unit
class TestRequestAggregate:
    """Test cases for Request aggregate."""

    def test_create_new_request(self):
        """Test creating a new request."""
        request = _make_request(machine_count=3, template_id="template-001")

        assert request.template_id == "template-001"
        assert request.requested_count == 3
        assert request.status == RequestStatus.PENDING
        assert request.request_type == RequestType.ACQUIRE
        assert request.created_at is not None
        assert request.request_id is not None

    def test_create_return_request(self):
        """Test creating a return request."""
        machine_ids = ["i-001", "i-002", "i-003"]
        request = _make_return_request(machine_ids=machine_ids)

        assert request.machine_ids == machine_ids
        assert request.requested_count == len(machine_ids)
        assert request.status == RequestStatus.PENDING
        assert request.request_type == RequestType.RETURN
        assert request.created_at is not None
        assert request.request_id is not None

    def test_request_status_transitions(self):
        """Test valid request status transitions."""
        request = _make_request()

        # PENDING -> IN_PROGRESS
        request = request.start_processing()
        assert request.status == RequestStatus.IN_PROGRESS
        assert request.started_at is not None

        # IN_PROGRESS -> COMPLETED
        request = request.complete(message="All machines provisioned successfully")
        assert request.status == RequestStatus.COMPLETED
        assert request.completed_at is not None

    def test_request_failure_transition(self):
        """Test request failure transition."""
        request = _make_request()

        # PENDING -> IN_PROGRESS
        request = request.start_processing()

        # IN_PROGRESS -> FAILED
        error_message = "Failed to provision machines: Insufficient capacity"
        request = request.fail(error_message)

        assert request.status == RequestStatus.FAILED
        assert request.status_message == error_message
        assert request.completed_at is not None

    def test_request_cancellation(self):
        """Test request cancellation."""
        request = _make_request()

        # Cancel from PENDING
        request = request.cancel("User requested cancellation")

        assert request.status == RequestStatus.CANCELLED
        assert request.completed_at is not None

    def test_invalid_status_transitions(self):
        """Test invalid request status transitions."""
        request = _make_request()

        # Start processing after completion
        request = request.start_processing()
        request = request.complete()

        with pytest.raises(InvalidRequestStateError):
            request.start_processing()

    def test_request_validation_machine_count(self):
        """Test request validation for machine count."""
        # Valid machine counts
        valid_counts = [1, 5, 10, 50, 100]
        for count in valid_counts:
            r = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id="template-001",
                machine_count=count,
                provider_type="aws",
            )
            assert r.requested_count == count

    def test_request_validation_required_fields(self):
        """Test request validation for required fields."""
        # provider_type is required — omitting it raises
        with pytest.raises((ValueError, TypeError)):
            Request.create_new_request(  # type: ignore[call-arg]
                request_type=RequestType.ACQUIRE,
                template_id="template-001",
                machine_count=1,
            )

    def test_request_configuration(self):
        """Test request metadata handling."""
        meta = {"machine_types": {"t2.small": 1}, "spot_price": "0.05"}

        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="template-001",
            machine_count=2,
            provider_type="aws",
            metadata=meta,
        )

        assert request.metadata == meta

    def test_request_equality(self):
        """Test request equality based on ID."""
        request1 = _make_request()

        # Create another request with same request_id
        request2 = Request(
            id=request1.id,
            request_id=request1.request_id,
            request_type=RequestType.RETURN,
            template_id="template-002",
            requested_count=5,
            provider_type="aws",
            provider_name="aws-us-west-2",
            status=RequestStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
        )

        request3 = _make_request()

        assert request1 == request2  # Same ID
        assert request1 != request3  # Different ID
        assert request2 != request3  # Different ID

    def test_request_hash(self):
        """Test request hashing."""
        request1 = _make_request()

        request2 = Request(
            id=request1.id,
            request_id=request1.request_id,
            request_type=RequestType.RETURN,
            template_id="template-002",
            requested_count=5,
            provider_type="aws",
            status=RequestStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
        )

        assert hash(request1) == hash(request2)  # Same ID should have same hash

    def test_request_serialization(self):
        """Test request serialization to dict."""
        request = _make_request(machine_count=2, template_id="template-001")

        request_dict = request.model_dump()

        assert request_dict["template_id"] == "template-001"
        assert request_dict["requested_count"] == 2
        assert request_dict["status"] == RequestStatus.PENDING.value
        assert request_dict["request_type"] == RequestType.ACQUIRE.value
        assert "request_id" in request_dict
        assert "created_at" in request_dict

    def test_request_deserialization(self):
        """Test request deserialization from dict."""
        import uuid

        req_id = f"req-{uuid.uuid4()}"
        request_dict = {
            "request_id": RequestId(value=req_id),
            "request_type": RequestType.ACQUIRE,
            "template_id": "template-001",
            "requested_count": 2,
            "desired_capacity": 2,
            "provider_type": "aws",
            "status": RequestStatus.PENDING,
            "created_at": datetime.now(timezone.utc),
        }

        request = Request(**request_dict)

        assert str(request.request_id) == req_id
        assert request.template_id == "template-001"
        assert request.requested_count == 2
        assert request.status == RequestStatus.PENDING
        assert request.request_type == RequestType.ACQUIRE

    def test_request_domain_events(self):
        """Test request domain events generation."""
        request = _make_request()

        events = request.get_domain_events()
        assert len(events) > 0

        # Start processing should generate RequestStatusChangedEvent
        request.clear_domain_events()
        request = request.start_processing()

        events = request.get_domain_events()
        assert len(events) > 0

        # Complete request should generate events
        request.clear_domain_events()
        request = request.complete()

        events = request.get_domain_events()
        assert len(events) > 0

    def test_request_string_representation(self):
        """Test request string representation."""
        request = _make_request(template_id="template-001")

        repr_str = repr(request)
        assert "Request" in repr_str or str(request.request_id) in repr_str


@pytest.mark.unit
class TestRequestValueObjects:
    """Test cases for Request-specific value objects."""

    def test_request_id_creation(self):
        """Test RequestId creation."""
        import uuid

        valid_id = f"req-{uuid.uuid4()}"
        request_id = RequestId(value=valid_id)
        assert str(request_id) == valid_id
        assert request_id.value == valid_id

    def test_request_status_enum(self):
        """Test RequestStatus enum."""
        assert RequestStatus.PENDING.value == "pending"
        assert RequestStatus.IN_PROGRESS.value == "in_progress"
        assert RequestStatus.COMPLETED.value == "complete"
        assert RequestStatus.FAILED.value == "failed"
        assert RequestStatus.CANCELLED.value == "cancelled"

        # Test enum comparison
        assert RequestStatus.PENDING != RequestStatus.IN_PROGRESS
        assert RequestStatus.COMPLETED == RequestStatus.COMPLETED

    def test_request_type_enum(self):
        """Test RequestType enum."""
        assert RequestType.ACQUIRE.value == "acquire"
        assert RequestType.RETURN.value == "return"

        # Test enum comparison
        assert RequestType.ACQUIRE != RequestType.RETURN
        assert RequestType.ACQUIRE == RequestType.ACQUIRE

    def test_machine_count_validation(self):
        """Test MachineCount value object validation."""
        # Valid machine counts
        valid_counts = [1, 5, 10, 50, 100]
        for count in valid_counts:
            mc = MachineCount(value=count)
            assert mc.value == count

        # Invalid machine counts
        invalid_counts = [0, -1, -10]
        for count in invalid_counts:
            with pytest.raises((ValueError, RequestValidationError)):
                MachineCount(value=count)


@pytest.mark.unit
class TestRequestExceptions:
    """Test cases for Request-specific exceptions."""

    def test_request_validation_error(self):
        """Test RequestValidationError."""
        error = RequestValidationError("Invalid request data")
        assert str(error) == "Invalid request data"
        assert isinstance(error, Exception)

    def test_request_not_found_error(self):
        """Test RequestNotFoundError."""
        # Constructor signature: __init__(self, request_id: str)
        error = RequestNotFoundError("req-123")
        assert "req-123" in str(error)

    def test_invalid_request_state_error(self):
        """Test InvalidRequestStateError."""
        # Constructor signature: __init__(self, current_state: str, attempted_state: str)
        error = InvalidRequestStateError(
            current_state="complete",
            attempted_state="in_progress",
        )
        assert "Cannot transition" in str(error)
        assert error.details["current_state"] == "complete"
        assert error.details["attempted_state"] == "in_progress"

    def test_request_processing_error(self):
        """Test RequestProcessingError."""
        # Inherits DomainException(message, error_code, details)
        error = RequestProcessingError(
            "Failed to process request",
            details={"request_id": "req-123"},
        )
        assert str(error) == "Failed to process request"
