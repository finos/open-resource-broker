"""Unit tests for Request aggregate."""

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.request.aggregate import Request
from src.domain.request.exceptions import (
    InvalidRequestStateError,
    RequestNotFoundError,
    RequestProcessingError,
    RequestValidationError,
)
from src.domain.request.value_objects import (
    RequestConfiguration,
    RequestId,
    RequestStatus,
    RequestType,
)

# Try to import optional classes - create mocks if not available
try:
    from src.domain.request.value_objects import Priority

    PRIORITY_AVAILABLE = True
except ImportError:
    PRIORITY_AVAILABLE = False

    class Priority:
        def __init__(self, value):
            if not isinstance(value, (int, str)):
                raise ValueError("Invalid priority")
            self.value = value


try:
    from src.domain.request.request_metadata import MachineCount

    MACHINE_COUNT_AVAILABLE = True
except ImportError:
    MACHINE_COUNT_AVAILABLE = False

    class MachineCount:
        def __init__(self, value):
            if not isinstance(value, int) or value < 0:
                raise ValueError("Invalid machine count")
            self.value = value


@pytest.mark.unit
class TestRequestAggregate:
    """Test cases for Request aggregate."""

    def test_create_new_request(self):
        """Test creating a new request."""
        request = Request.create_new_request(
            template_id="template-001",
            machine_count=3,
            requester_id="user-123",
            priority=1,
            tags={"Environment": "test", "Project": "hostfactory"},
        )

        assert request.template_id == "template-001"
        assert request.machine_count == 3
        assert request.requester_id == "user-123"
        assert request.priority == 1
        assert request.status == RequestStatus.PENDING
        assert request.request_type == RequestType.NEW
        assert request.tags["Environment"] == "test"
        assert request.tags["Project"] == "hostfactory"
        assert request.created_at is not None
        assert request.updated_at is not None
        assert request.id is not None

    def test_create_return_request(self):
        """Test creating a return request."""
        machine_ids = ["machine-001", "machine-002", "machine-003"]
        request = Request.create_return_request(
            machine_ids=machine_ids,
            requester_id="user-123",
            reason="No longer needed",
            tags={"Environment": "test"},
        )

        assert request.machine_ids == machine_ids
        assert request.machine_count == len(machine_ids)
        assert request.requester_id == "user-123"
        assert request.return_reason == "No longer needed"
        assert request.status == RequestStatus.PENDING
        assert request.request_type == RequestType.RETURN
        assert request.tags["Environment"] == "test"
        assert request.created_at is not None
        assert request.updated_at is not None
        assert request.id is not None

    def test_request_status_transitions(self):
        """Test valid request status transitions."""
        request = Request.create_new_request(
            template_id="template-001", machine_count=2, requester_id="user-123"
        )

        # PENDING -> PROCESSING
        request.start_processing()
        assert request.status == RequestStatus.PROCESSING
        assert request.processing_started_at is not None

        # PROCESSING -> COMPLETED
        request.complete_successfully(
            machine_ids=["machine-001", "machine-002"],
            completion_message="All machines provisioned successfully",
        )
        assert request.status == RequestStatus.COMPLETED
        assert request.machine_ids == ["machine-001", "machine-002"]
        assert request.completion_message == "All machines provisioned successfully"
        assert request.completed_at is not None

    def test_request_failure_transition(self):
        """Test request failure transition."""
        request = Request.create_new_request(
            template_id="template-001", machine_count=2, requester_id="user-123"
        )

        # PENDING -> PROCESSING
        request.start_processing()

        # PROCESSING -> FAILED
        error_message = "Failed to provision machines: Insufficient capacity"
        request.fail_with_error(error_message)

        assert request.status == RequestStatus.FAILED
        assert request.error_message == error_message
        assert request.failed_at is not None

    def test_request_cancellation(self):
        """Test request cancellation."""
        request = Request.create_new_request(
            template_id="template-001", machine_count=2, requester_id="user-123"
        )

        # Cancel from PENDING
        request.cancel("User requested cancellation")

        assert request.status == RequestStatus.CANCELLED
        assert request.cancellation_reason == "User requested cancellation"
        assert request.cancelled_at is not None

    def test_invalid_status_transitions(self):
        """Test invalid request status transitions."""
        request = Request.create_new_request(
            template_id="template-001", machine_count=2, requester_id="user-123"
        )

        # Complete without processing
        with pytest.raises(InvalidRequestStateError):
            request.complete_successfully(machine_ids=["machine-001", "machine-002"])

        # Start processing after completion
        request.start_processing()
        request.complete_successfully(machine_ids=["machine-001", "machine-002"])

        with pytest.raises(InvalidRequestStateError):
            request.start_processing()

    def test_request_validation_machine_count(self):
        """Test request validation for machine count."""
        # Valid machine counts
        valid_counts = [1, 5, 10, 50, 100]
        for count in valid_counts:
            request = Request.create_new_request(
                template_id="template-001", machine_count=count, requester_id="user-123"
            )
            assert request.machine_count == count

        # Invalid machine counts
        invalid_counts = [0, -1, -10]
        for count in invalid_counts:
            with pytest.raises((ValueError, RequestValidationError)):
                Request.create_new_request(
                    template_id="template-001",
                    machine_count=count,
                    requester_id="user-123",
                )

    def test_request_validation_priority(self):
        """Test request validation for priority."""
        # Valid priorities
        valid_priorities = [1, 2, 3, 4, 5]
        for priority in valid_priorities:
            request = Request.create_new_request(
                template_id="template-001",
                machine_count=1,
                requester_id="user-123",
                priority=priority,
            )
            assert request.priority == priority

        # Invalid priorities
        invalid_priorities = [0, -1, 6, 10]
        for priority in invalid_priorities:
            with pytest.raises((ValueError, RequestValidationError)):
                Request.create_new_request(
                    template_id="template-001",
                    machine_count=1,
                    requester_id="user-123",
                    priority=priority,
                )

    def test_request_validation_required_fields(self):
        """Test request validation for required fields."""
        # Missing template_id for new request
        with pytest.raises((ValueError, RequestValidationError)):
            Request.create_new_request(template_id="", machine_count=1, requester_id="user-123")

        # Missing requester_id
        with pytest.raises((ValueError, RequestValidationError)):
            Request.create_new_request(template_id="template-001", machine_count=1, requester_id="")

        # Missing machine_ids for return request
        with pytest.raises((ValueError, RequestValidationError)):
            Request.create_return_request(machine_ids=[], requester_id="user-123")

    def test_request_timeout_handling(self):
        """Test request timeout handling."""
        request = Request.create_new_request(
            template_id="template-001",
            machine_count=2,
            requester_id="user-123",
            timeout_minutes=30,
        )

        assert request.timeout_minutes == 30

        # Test timeout calculation
        expected_timeout = request.created_at + timedelta(minutes=30)
        assert request.get_timeout_at() == expected_timeout

        # Test if request is timed out
        assert not request.is_timed_out()

        # Simulate timeout by setting created_at to past
        request.created_at = datetime.now(timezone.utc) - timedelta(minutes=31)
        assert request.is_timed_out()

    def test_request_progress_tracking(self):
        """Test request progress tracking."""
        request = Request.create_new_request(
            template_id="template-001", machine_count=5, requester_id="user-123"
        )

        request.start_processing()

        # Update progress
        request.update_progress(completed_count=2, status_message="2 out of 5 machines provisioned")

        assert request.completed_machine_count == 2
        assert request.status_message == "2 out of 5 machines provisioned"
        assert request.get_progress_percentage() == 40.0  # 2/5 * 100

        # Update progress again
        request.update_progress(completed_count=5, status_message="All machines provisioned")

        assert request.completed_machine_count == 5
        assert request.get_progress_percentage() == 100.0

    def test_request_retry_logic(self):
        """Test request retry logic."""
        request = Request.create_new_request(
            template_id="template-001",
            machine_count=2,
            requester_id="user-123",
            max_retries=3,
        )

        assert request.max_retries == 3
        assert request.retry_count == 0
        assert request.can_retry()

        # Increment retry count
        request.increment_retry_count("First retry attempt")
        assert request.retry_count == 1
        assert request.can_retry()

        # Reach max retries
        request.increment_retry_count("Second retry attempt")
        request.increment_retry_count("Third retry attempt")
        assert request.retry_count == 3
        assert not request.can_retry()

        # Try to increment beyond max
        with pytest.raises(RequestProcessingError):
            request.increment_retry_count("Fourth retry attempt")

    def test_request_configuration(self):
        """Test request configuration handling."""
        config = {
            "instance_type": "t2.small",
            "spot_price": "0.05",
            "user_data": "#!/bin/bash\necho 'custom config'",
        }

        request = Request.create_new_request(
            template_id="template-001",
            machine_count=2,
            requester_id="user-123",
            configuration=config,
        )

        assert request.configuration == config
        assert request.configuration["instance_type"] == "t2.small"
        assert request.configuration["spot_price"] == "0.05"

    def test_request_tags_operations(self):
        """Test request tags operations."""
        request = Request.create_new_request(
            template_id="template-001",
            machine_count=2,
            requester_id="user-123",
            tags={"Environment": "test"},
        )

        # Add tag
        request.tags["Project"] = "hostfactory"
        assert request.tags["Project"] == "hostfactory"

        # Update tag
        request.tags["Environment"] = "production"
        assert request.tags["Environment"] == "production"

        # Check tag existence
        assert "Environment" in request.tags
        assert "Project" in request.tags
        assert "NonExistent" not in request.tags

    def test_request_equality(self):
        """Test request equality based on ID."""
        request1 = Request.create_new_request(
            template_id="template-001", machine_count=2, requester_id="user-123"
        )

        # Create another request with same ID
        request2 = Request(
            id=request1.id,
            template_id="template-002",  # Different template
            machine_count=5,  # Different count
            requester_id="user-456",  # Different requester
            status=RequestStatus.COMPLETED,  # Different status
            request_type=RequestType.RETURN,  # Different type
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        # Create request with different ID
        request3 = Request.create_new_request(
            template_id="template-001", machine_count=2, requester_id="user-123"
        )

        assert request1 == request2  # Same ID
        assert request1 != request3  # Different ID
        assert request2 != request3  # Different ID

    def test_request_hash(self):
        """Test request hashing."""
        request1 = Request.create_new_request(
            template_id="template-001", machine_count=2, requester_id="user-123"
        )

        request2 = Request(
            id=request1.id,  # Same ID
            template_id="template-002",
            machine_count=5,
            requester_id="user-456",
            status=RequestStatus.COMPLETED,
            request_type=RequestType.RETURN,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        assert hash(request1) == hash(request2)  # Same ID should have same hash

    def test_request_serialization(self):
        """Test request serialization to dict."""
        request = Request.create_new_request(
            template_id="template-001",
            machine_count=2,
            requester_id="user-123",
            priority=2,
            tags={"Environment": "test"},
            configuration={"instance_type": "t2.small"},
        )

        request_dict = request.model_dump()

        assert request_dict["template_id"] == "template-001"
        assert request_dict["machine_count"] == 2
        assert request_dict["requester_id"] == "user-123"
        assert request_dict["priority"] == 2
        assert request_dict["status"] == RequestStatus.PENDING.value
        assert request_dict["request_type"] == RequestType.NEW.value
        assert request_dict["tags"] == {"Environment": "test"}
        assert request_dict["configuration"] == {"instance_type": "t2.small"}
        assert "id" in request_dict
        assert "created_at" in request_dict
        assert "updated_at" in request_dict

    def test_request_deserialization(self):
        """Test request deserialization from dict."""
        request_dict = {
            "id": "req-12345678",
            "template_id": "template-001",
            "machine_count": 2,
            "requester_id": "user-123",
            "priority": 2,
            "status": "pending",
            "request_type": "new",
            "tags": {"Environment": "test"},
            "configuration": {"instance_type": "t2.small"},
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z",
        }

        request = Request(**request_dict)

        assert request.id == "req-12345678"
        assert request.template_id == "template-001"
        assert request.machine_count == 2
        assert request.requester_id == "user-123"
        assert request.priority == 2
        assert request.status == RequestStatus.PENDING
        assert request.request_type == RequestType.NEW
        assert request.tags == {"Environment": "test"}
        assert request.configuration == {"instance_type": "t2.small"}

    def test_request_domain_events(self):
        """Test request domain events generation."""
        # Create new request should generate RequestCreatedEvent
        request = Request.create_new_request(
            template_id="template-001", machine_count=2, requester_id="user-123"
        )

        events = request.get_domain_events()
        assert len(events) > 0

        # Start processing should generate RequestStatusChangedEvent
        request.clear_domain_events()
        request.start_processing()

        events = request.get_domain_events()
        assert len(events) > 0

        # Complete request should generate RequestCompletedEvent
        request.clear_domain_events()
        request.complete_successfully(machine_ids=["machine-001", "machine-002"])

        events = request.get_domain_events()
        assert len(events) > 0

    def test_request_string_representation(self):
        """Test request string representation."""
        request = Request.create_new_request(
            template_id="template-001", machine_count=2, requester_id="user-123"
        )

        str_repr = str(request)
        assert request.id in str_repr
        assert "template-001" in str_repr
        assert "user-123" in str_repr

        repr_str = repr(request)
        assert "Request" in repr_str
        assert request.id in repr_str


@pytest.mark.unit
class TestRequestValueObjects:
    """Test cases for Request-specific value objects."""

    def test_request_id_creation(self):
        """Test RequestId creation."""
        request_id = RequestId("req-12345678")
        assert str(request_id) == "req-12345678"
        assert request_id.value == "req-12345678"

    def test_request_status_enum(self):
        """Test RequestStatus enum."""
        assert RequestStatus.PENDING.value == "pending"
        assert RequestStatus.PROCESSING.value == "processing"
        assert RequestStatus.COMPLETED.value == "completed"
        assert RequestStatus.FAILED.value == "failed"
        assert RequestStatus.CANCELLED.value == "cancelled"

        # Test enum comparison
        assert RequestStatus.PENDING != RequestStatus.PROCESSING
        assert RequestStatus.COMPLETED == RequestStatus.COMPLETED

    def test_request_type_enum(self):
        """Test RequestType enum."""
        assert RequestType.NEW.value == "new"
        assert RequestType.RETURN.value == "return"

        # Test enum comparison
        assert RequestType.NEW != RequestType.RETURN
        assert RequestType.NEW == RequestType.NEW

    def test_priority_validation(self):
        """Test Priority value object validation."""
        # Valid priorities (1-5)
        for priority in range(1, 6):
            p = Priority(priority)
            assert p.value == priority

        # Invalid priorities
        invalid_priorities = [0, -1, 6, 10]
        for priority in invalid_priorities:
            with pytest.raises((ValueError, RequestValidationError)):
                Priority(priority)

    def test_machine_count_validation(self):
        """Test MachineCount value object validation."""
        # Valid machine counts
        valid_counts = [1, 5, 10, 50, 100]
        for count in valid_counts:
            mc = MachineCount(count)
            assert mc.value == count

        # Invalid machine counts
        invalid_counts = [0, -1, -10]
        for count in invalid_counts:
            with pytest.raises((ValueError, RequestValidationError)):
                MachineCount(count)

    def test_request_configuration_creation(self):
        """Test RequestConfiguration creation."""
        config_dict = {
            "instance_type": "t2.small",
            "spot_price": "0.05",
            "user_data": "#!/bin/bash\necho 'test'",
        }

        config = RequestConfiguration(config_dict)
        assert config.value == config_dict
        assert config["instance_type"] == "t2.small"
        assert config["spot_price"] == "0.05"
        assert config["user_data"] == "#!/bin/bash\necho 'test'"

    def test_request_configuration_operations(self):
        """Test RequestConfiguration operations."""
        config = RequestConfiguration({"key1": "value1"})

        # Test get
        assert config.get("key1") == "value1"
        assert config.get("nonexistent") is None
        assert config.get("nonexistent", "default") == "default"

        # Test contains
        assert "key1" in config
        assert "nonexistent" not in config

        # Test iteration
        keys = list(config.keys())
        assert "key1" in keys

        values = list(config.values())
        assert "value1" in values


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
        error = RequestNotFoundError("Request not found", request_id="req-123")
        assert str(error) == "Request not found"
        assert error.request_id == "req-123"

    def test_invalid_request_state_error(self):
        """Test InvalidRequestStateError."""
        error = InvalidRequestStateError(
            "Cannot transition from completed to processing",
            current_state="completed",
            attempted_state="processing",
        )
        assert "Cannot transition" in str(error)
        assert error.current_state == "completed"
        assert error.attempted_state == "processing"

    def test_request_processing_error(self):
        """Test RequestProcessingError."""
        error = RequestProcessingError("Failed to process request", request_id="req-123")
        assert str(error) == "Failed to process request"
        assert error.request_id == "req-123"
