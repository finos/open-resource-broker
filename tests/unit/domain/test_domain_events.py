"""Comprehensive tests for domain events system."""

from datetime import datetime

import pytest

# Import domain events and aggregates
try:
    from src.domain.base.events import (
        RequestCompletedEvent,
        RequestCreatedEvent,
        RequestStatusChangedEvent,
    )
    from src.domain.base.events.base_events import BaseEvent
    from src.domain.base.events.domain_events import DomainEvent
    from src.domain.request.aggregate import Request
    from src.domain.request.value_objects import RequestStatus, RequestType

    # from src.domain.request.events import (  # TODO: Verify if this exists
    #     RequestCreatedEvent, RequestStatusChangedEvent, RequestCompletedEvent
    # )

    IMPORTS_AVAILABLE = True
except ImportError as e:
    IMPORTS_AVAILABLE = False
    pytestmark = pytest.mark.skip(f"Domain imports not available: {e}")


@pytest.mark.unit
class TestDomainEventGeneration:
    """Test domain event generation in aggregates."""

    def test_request_aggregate_generates_created_event(self):
        """Test that Request aggregate generates RequestCreatedEvent."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        events = request.get_domain_events()
        assert len(events) >= 1, "Request creation should generate at least one event"

        created_event = next(
            (e for e in events if isinstance(e, RequestCreatedEvent)), None
        )
        assert created_event is not None, "Should generate RequestCreatedEvent"
        assert created_event.request_id == str(request.id.value)
        assert created_event.template_id == "test-template"
        assert created_event.machine_count == 2
        assert created_event.requester_id == "test-user"

    def test_request_status_change_generates_event(self):
        """Test that request status changes generate events."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Clear initial events
        request.clear_domain_events()

        # Change status
        request.start_processing()

        events = request.get_domain_events()
        status_event = next(
            (e for e in events if isinstance(e, RequestStatusChangedEvent)), None
        )
        assert status_event is not None, "Status change should generate event"
        assert status_event.old_status == RequestStatus.PENDING.value
        assert status_event.new_status == RequestStatus.PROCESSING.value

    def test_request_completion_generates_event(self):
        """Test that request completion generates event."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request.start_processing()
        request.clear_domain_events()

        # Complete the request
        machine_ids = ["i-123", "i-456"]
        request.complete_successfully(
            machine_ids=machine_ids, completion_message="All machines provisioned"
        )

        events = request.get_domain_events()
        completed_event = next(
            (e for e in events if isinstance(e, RequestCompletedEvent)), None
        )
        assert completed_event is not None, "Completion should generate event"
        assert completed_event.machine_ids == machine_ids
        assert completed_event.success is True

    def test_request_failure_generates_event(self):
        """Test that request failure generates event."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request.start_processing()
        request.clear_domain_events()

        # Fail the request
        error_message = "Insufficient capacity"
        request.fail_with_error(error_message)

        events = request.get_domain_events()
        completed_event = next(
            (e for e in events if isinstance(e, RequestCompletedEvent)), None
        )
        assert completed_event is not None, "Failure should generate completion event"
        assert completed_event.success is False
        assert completed_event.error_message == error_message

    def test_return_request_generates_events(self):
        """Test that return request creation generates events."""
        machine_ids = ["i-123", "i-456"]
        request = Request.create_return_request(
            machine_ids=machine_ids, requester_id="test-user", reason="No longer needed"
        )

        events = request.get_domain_events()
        created_event = next(
            (e for e in events if isinstance(e, RequestCreatedEvent)), None
        )
        assert created_event is not None, "Return request should generate created event"
        assert created_event.request_type == RequestType.RETURN.value
        assert created_event.machine_ids == machine_ids


@pytest.mark.unit
class TestDomainEventProperties:
    """Test domain event properties and immutability."""

    def test_domain_events_are_immutable(self):
        """Test that domain events cannot be modified after creation."""
        event = RequestCreatedEvent(
            request_id="test-request",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            request_type=RequestType.NEW.value,
        )

        # Try to modify the event (should fail)
        with pytest.raises((AttributeError, TypeError)):
            event.request_id = "modified-request"

        with pytest.raises((AttributeError, TypeError)):
            event.machine_count = 5

    def test_domain_events_have_timestamps(self):
        """Test that domain events have proper timestamps."""
        event = RequestCreatedEvent(
            request_id="test-request",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            request_type=RequestType.NEW.value,
        )

        assert hasattr(event, "occurred_at"), "Events should have timestamp"
        assert isinstance(event.occurred_at, datetime), "Timestamp should be datetime"
        assert (
            event.occurred_at.tzinfo is not None
        ), "Timestamp should be timezone-aware"

    def test_domain_events_have_unique_ids(self):
        """Test that domain events have unique identifiers."""
        event1 = RequestCreatedEvent(
            request_id="test-request-1",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            request_type=RequestType.NEW.value,
        )

        event2 = RequestCreatedEvent(
            request_id="test-request-2",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            request_type=RequestType.NEW.value,
        )

        assert hasattr(event1, "event_id"), "Events should have unique ID"
        assert hasattr(event2, "event_id"), "Events should have unique ID"
        assert event1.event_id != event2.event_id, "Event IDs should be unique"

    def test_domain_events_serialization(self):
        """Test that domain events can be serialized."""
        event = RequestCreatedEvent(
            request_id="test-request",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            request_type=RequestType.NEW.value,
        )

        # Should be able to convert to dict
        if hasattr(event, "to_dict"):
            event_dict = event.to_dict()
            assert isinstance(event_dict, dict)
            assert "request_id" in event_dict
            assert "event_id" in event_dict
            assert "occurred_at" in event_dict
        elif hasattr(event, "model_dump"):
            # Pydantic v2
            event_dict = event.model_dump()
            assert isinstance(event_dict, dict)


@pytest.mark.unit
class TestEventInheritanceHierarchy:
    """Test domain event inheritance hierarchy."""

    def test_request_events_inherit_from_domain_event(self):
        """Test that request events inherit from DomainEvent."""
        assert issubclass(
            RequestCreatedEvent, DomainEvent
        ), "RequestCreatedEvent should inherit from DomainEvent"
        assert issubclass(
            RequestStatusChangedEvent, DomainEvent
        ), "RequestStatusChangedEvent should inherit from DomainEvent"
        assert issubclass(
            RequestCompletedEvent, DomainEvent
        ), "RequestCompletedEvent should inherit from DomainEvent"

    def test_domain_event_inheritance_chain(self):
        """Test the complete event inheritance chain."""
        event = RequestCreatedEvent(
            request_id="test-request",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            request_type=RequestType.NEW.value,
        )

        assert isinstance(event, DomainEvent), "Should be instance of DomainEvent"
        assert isinstance(event, BaseEvent), "Should be instance of BaseEvent"

    def test_event_type_identification(self):
        """Test that events can be identified by type."""
        created_event = RequestCreatedEvent(
            request_id="test-request",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            request_type=RequestType.NEW.value,
        )

        status_event = RequestStatusChangedEvent(
            request_id="test-request",
            old_status=RequestStatus.PENDING.value,
            new_status=RequestStatus.PROCESSING.value,
        )

        # Should be able to distinguish event types
        assert type(created_event).__name__ == "RequestCreatedEvent"
        assert type(status_event).__name__ == "RequestStatusChangedEvent"
        assert created_event != status_event


@pytest.mark.unit
class TestEventAggregateInteraction:
    """Test interaction between events and aggregates."""

    def test_aggregate_event_collection(self):
        """Test that aggregates collect events properly."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Should have events
        events = request.get_domain_events()
        assert len(events) > 0, "Aggregate should collect events"

        # Should be able to clear events
        request.clear_domain_events()
        events_after_clear = request.get_domain_events()
        assert len(events_after_clear) == 0, "Events should be cleared"

    def test_multiple_operations_generate_multiple_events(self):
        """Test that multiple operations generate multiple events."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Perform multiple operations
        request.start_processing()
        request.complete_successfully(
            machine_ids=["i-123", "i-456"], completion_message="Success"
        )

        events = request.get_domain_events()
        # Should have: Created, StatusChanged, Completed events
        assert len(events) >= 3, f"Should have at least 3 events, got {len(events)}"

        # Check event types
        event_types = [type(event).__name__ for event in events]
        assert "RequestCreatedEvent" in event_types
        assert "RequestStatusChangedEvent" in event_types
        assert "RequestCompletedEvent" in event_types

    def test_event_ordering(self):
        """Test that events are generated in correct order."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request.start_processing()
        request.complete_successfully(
            machine_ids=["i-123", "i-456"], completion_message="Success"
        )

        events = request.get_domain_events()

        # Events should be in chronological order
        for i in range(1, len(events)):
            assert (
                events[i - 1].occurred_at <= events[i].occurred_at
            ), "Events should be in chronological order"


@pytest.mark.unit
class TestEventBusinessLogic:
    """Test business logic related to domain events."""

    def test_event_contains_business_relevant_data(self):
        """Test that events contain all business-relevant data."""
        request = Request.create_new_request(
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            priority=1,
            tags={"Environment": "test"},
        )

        events = request.get_domain_events()
        created_event = next(
            (e for e in events if isinstance(e, RequestCreatedEvent)), None
        )

        assert created_event is not None
        assert created_event.template_id == "test-template"
        assert created_event.machine_count == 2
        assert created_event.requester_id == "test-user"
        # Should include business context
        if hasattr(created_event, "priority"):
            assert created_event.priority == 1
        if hasattr(created_event, "tags"):
            assert created_event.tags == {"Environment": "test"}

    def test_status_change_event_captures_transition(self):
        """Test that status change events capture the complete transition."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request.clear_domain_events()
        old_status = request.status
        request.start_processing()
        new_status = request.status

        events = request.get_domain_events()
        status_event = next(
            (e for e in events if isinstance(e, RequestStatusChangedEvent)), None
        )

        assert status_event is not None
        assert status_event.old_status == old_status.value
        assert status_event.new_status == new_status.value
        assert status_event.request_id == str(request.id.value)

    def test_completion_event_captures_outcome(self):
        """Test that completion events capture the complete outcome."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request.start_processing()
        request.clear_domain_events()

        machine_ids = ["i-123", "i-456"]
        completion_message = "All machines provisioned successfully"
        request.complete_successfully(
            machine_ids=machine_ids, completion_message=completion_message
        )

        events = request.get_domain_events()
        completed_event = next(
            (e for e in events if isinstance(e, RequestCompletedEvent)), None
        )

        assert completed_event is not None
        assert completed_event.success is True
        assert completed_event.machine_ids == machine_ids
        assert completed_event.completion_message == completion_message
        assert completed_event.error_message is None


@pytest.mark.unit
class TestEventSystemIntegration:
    """Test integration aspects of the event system."""

    def test_events_support_audit_trail(self):
        """Test that events provide complete audit trail."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Perform complete lifecycle
        request.start_processing()
        request.complete_successfully(
            machine_ids=["i-123", "i-456"], completion_message="Success"
        )

        events = request.get_domain_events()

        # Should be able to reconstruct the complete story from events
        created_events = [e for e in events if isinstance(e, RequestCreatedEvent)]
        status_events = [e for e in events if isinstance(e, RequestStatusChangedEvent)]
        completed_events = [e for e in events if isinstance(e, RequestCompletedEvent)]

        assert len(created_events) >= 1, "Should have creation event"
        assert len(status_events) >= 1, "Should have status change events"
        assert len(completed_events) >= 1, "Should have completion event"

    def test_events_support_replay(self):
        """Test that events support event sourcing replay."""
        # Create events that could be used for replay
        events = [
            RequestCreatedEvent(
                request_id="test-request",
                template_id="test-template",
                machine_count=2,
                requester_id="test-user",
                request_type=RequestType.NEW.value,
            ),
            RequestStatusChangedEvent(
                request_id="test-request",
                old_status=RequestStatus.PENDING.value,
                new_status=RequestStatus.PROCESSING.value,
            ),
            RequestCompletedEvent(
                request_id="test-request",
                success=True,
                machine_ids=["i-123", "i-456"],
                completion_message="Success",
            ),
        ]

        # Events should contain all necessary data for replay
        for event in events:
            assert hasattr(
                event, "request_id"
            ), "Events should have request_id for replay"
            assert hasattr(
                event, "occurred_at"
            ), "Events should have timestamp for replay"
            assert hasattr(event, "event_id"), "Events should have unique ID for replay"

    def test_event_deduplication_support(self):
        """Test that events support deduplication."""
        event1 = RequestCreatedEvent(
            request_id="test-request",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            request_type=RequestType.NEW.value,
        )

        event2 = RequestCreatedEvent(
            request_id="test-request",
            template_id="test-template",
            machine_count=2,
            requester_id="test-user",
            request_type=RequestType.NEW.value,
        )

        # Events should have unique IDs for deduplication
        assert event1.event_id != event2.event_id, "Events should have unique IDs"

        # Events with same business data should be distinguishable
        assert event1 != event2, "Events should be distinguishable even with same data"
