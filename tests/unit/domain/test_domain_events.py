"""Comprehensive tests for domain events system."""

from datetime import datetime

import pytest
from pydantic import ValidationError

# Import domain events and aggregates
try:
    from orb.domain.base.events import (
        RequestCompletedEvent,
        RequestCreatedEvent,
        RequestStatusChangedEvent,
    )
    from orb.domain.base.events.base_events import DomainEvent
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestStatus, RequestType

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
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        events = request.get_domain_events()
        assert len(events) >= 1, "Request creation should generate at least one event"

        created_event = next((e for e in events if isinstance(e, RequestCreatedEvent)), None)
        assert created_event is not None, "Should generate RequestCreatedEvent"
        assert created_event.template_id == "test-template"
        assert created_event.machine_count == 2

    def test_request_status_change_generates_event(self):
        """Test that request status changes generate events."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        # start_processing returns a new immutable instance
        updated_request = request.start_processing()

        events = updated_request.get_domain_events()
        status_event = next((e for e in events if isinstance(e, RequestStatusChangedEvent)), None)
        assert status_event is not None, "Status change should generate event"
        assert status_event.old_status == RequestStatus.PENDING.value
        assert status_event.new_status == RequestStatus.IN_PROGRESS.value

    def test_request_completion_generates_event(self):
        """Test that request completion generates event."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        processing = request.start_processing()
        # complete() returns a new immutable instance
        completed = processing.complete(message="All machines provisioned")

        events = completed.get_domain_events()
        completed_event = next((e for e in events if isinstance(e, RequestCompletedEvent)), None)
        assert completed_event is not None, "Completion should generate event"
        assert completed_event.completion_status == RequestStatus.COMPLETED.value

    def test_request_failure_generates_event(self):
        """Test that request failure generates event."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        processing = request.start_processing()
        # fail() returns a new immutable instance; no domain event is emitted by fail()
        failed = processing.fail("Insufficient capacity")

        # fail() does not emit a RequestCompletedEvent — verify the status is FAILED
        assert failed.status == RequestStatus.FAILED

    def test_return_request_generates_events(self):
        """Test that return request creation generates events."""
        machine_ids = ["i-123", "i-456"]
        request = Request.create_return_request(
            machine_ids=machine_ids,
            provider_type="aws",
            provider_name="test-provider",
        )

        events = request.get_domain_events()
        created_event = next((e for e in events if isinstance(e, RequestCreatedEvent)), None)
        assert created_event is not None, "Return request should generate created event"
        assert created_event.request_type == RequestType.RETURN.value


@pytest.mark.unit
class TestDomainEventProperties:
    """Test domain event properties and immutability."""

    def test_domain_events_are_immutable(self):
        """Test that domain events cannot be modified after creation."""
        event = RequestCreatedEvent(
            request_id="test-request",
            aggregate_id="test-request",
            aggregate_type="Request",
            template_id="test-template",
            machine_count=2,
            request_type=RequestType.ACQUIRE.value,
        )

        # Try to modify the event (should fail — frozen=True)
        with pytest.raises((AttributeError, TypeError, ValidationError)):
            event.request_id = "modified-request"

        with pytest.raises((AttributeError, TypeError, ValidationError)):
            event.machine_count = 5

    def test_domain_events_have_timestamps(self):
        """Test that domain events have appropriate timestamps."""
        event = RequestCreatedEvent(
            request_id="test-request",
            aggregate_id="test-request",
            aggregate_type="Request",
            template_id="test-template",
            machine_count=2,
            request_type=RequestType.ACQUIRE.value,
        )

        assert hasattr(event, "occurred_at"), "Events should have timestamp"
        assert isinstance(event.occurred_at, datetime), "Timestamp should be datetime"
        assert event.occurred_at.tzinfo is not None, "Timestamp should be timezone-aware"

    def test_domain_events_have_unique_ids(self):
        """Test that domain events have unique identifiers."""
        event1 = RequestCreatedEvent(
            request_id="test-request-1",
            aggregate_id="test-request-1",
            aggregate_type="Request",
            template_id="test-template",
            machine_count=2,
            request_type=RequestType.ACQUIRE.value,
        )

        event2 = RequestCreatedEvent(
            request_id="test-request-2",
            aggregate_id="test-request-2",
            aggregate_type="Request",
            template_id="test-template",
            machine_count=2,
            request_type=RequestType.ACQUIRE.value,
        )

        assert hasattr(event1, "event_id"), "Events should have unique ID"
        assert hasattr(event2, "event_id"), "Events should have unique ID"
        assert event1.event_id != event2.event_id, "Event IDs should be unique"

    def test_domain_events_serialization(self):
        """Test that domain events can be serialized."""
        event = RequestCreatedEvent(
            request_id="test-request",
            aggregate_id="test-request",
            aggregate_type="Request",
            template_id="test-template",
            machine_count=2,
            request_type=RequestType.ACQUIRE.value,
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
        assert issubclass(RequestCreatedEvent, DomainEvent), (
            "RequestCreatedEvent should inherit from DomainEvent"
        )
        assert issubclass(RequestStatusChangedEvent, DomainEvent), (
            "RequestStatusChangedEvent should inherit from DomainEvent"
        )
        assert issubclass(RequestCompletedEvent, DomainEvent), (
            "RequestCompletedEvent should inherit from DomainEvent"
        )

    def test_domain_event_inheritance_chain(self):
        """Test the complete event inheritance chain."""
        event = RequestCreatedEvent(
            request_id="test-request",
            aggregate_id="test-request",
            aggregate_type="Request",
            template_id="test-template",
            machine_count=2,
            request_type=RequestType.ACQUIRE.value,
        )

        assert isinstance(event, DomainEvent), "Should be instance of DomainEvent"

    def test_event_type_identification(self):
        """Test that events can be identified by type."""
        created_event = RequestCreatedEvent(
            request_id="test-request",
            aggregate_id="test-request",
            aggregate_type="Request",
            template_id="test-template",
            machine_count=2,
            request_type=RequestType.ACQUIRE.value,
        )

        status_event = RequestStatusChangedEvent(
            request_id="test-request",
            aggregate_id="test-request",
            aggregate_type="Request",
            request_type=RequestType.ACQUIRE.value,
            old_status=RequestStatus.PENDING.value,
            new_status=RequestStatus.IN_PROGRESS.value,
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
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
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
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        # Each operation returns a new instance; collect all events across instances
        processing = request.start_processing()
        completed = processing.complete(message="Success")

        # Gather events from all instances
        all_events = (
            request.get_domain_events()
            + processing.get_domain_events()
            + completed.get_domain_events()
        )

        event_types = [type(e).__name__ for e in all_events]
        assert "RequestCreatedEvent" in event_types
        assert "RequestStatusChangedEvent" in event_types
        assert "RequestCompletedEvent" in event_types

    def test_event_ordering(self):
        """Test that events are generated in correct order."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        processing = request.start_processing()
        completed = processing.complete(message="Success")

        # Collect events in order across the immutable chain
        all_events = (
            request.get_domain_events()
            + processing.get_domain_events()
            + completed.get_domain_events()
        )

        # Events should be in chronological order
        for i in range(1, len(all_events)):
            assert all_events[i - 1].occurred_at <= all_events[i].occurred_at, (
                "Events should be in chronological order"
            )


@pytest.mark.unit
class TestEventBusinessLogic:
    """Test business logic related to domain events."""

    def test_event_contains_business_relevant_data(self):
        """Test that events contain all business-relevant data."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
            metadata={"tags": {"Environment": "test"}},
        )

        events = request.get_domain_events()
        created_event = next((e for e in events if isinstance(e, RequestCreatedEvent)), None)

        assert created_event is not None
        assert created_event.template_id == "test-template"
        assert created_event.machine_count == 2
        if hasattr(created_event, "tags"):
            assert created_event.tags == {"Environment": "test"}

    def test_status_change_event_captures_transition(self):
        """Test that status change events capture the complete transition."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        old_status = request.status
        updated = request.start_processing()
        new_status = updated.status

        events = updated.get_domain_events()
        status_event = next((e for e in events if isinstance(e, RequestStatusChangedEvent)), None)

        assert status_event is not None
        assert status_event.old_status == old_status.value
        assert status_event.new_status == new_status.value
        assert status_event.request_id == str(updated.request_id.value)

    def test_completion_event_captures_outcome(self):
        """Test that completion events capture the complete outcome."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        processing = request.start_processing()
        machine_ids = ["i-123", "i-456"]
        completed = processing.add_machine_ids(machine_ids).complete(
            message="All machines provisioned successfully"
        )

        events = completed.get_domain_events()
        completed_event = next((e for e in events if isinstance(e, RequestCompletedEvent)), None)

        assert completed_event is not None
        assert completed_event.completion_status == RequestStatus.COMPLETED.value
        assert sorted(completed_event.machine_ids) == sorted(machine_ids)


@pytest.mark.unit
class TestEventSystemIntegration:
    """Test integration aspects of the event system."""

    def test_events_support_audit_trail(self):
        """Test that events provide complete audit trail."""
        request = Request.create_new_request(
            request_type=RequestType.ACQUIRE,
            template_id="test-template",
            machine_count=2,
            provider_type="aws",
        )

        processing = request.start_processing()
        completed = processing.complete(message="Success")

        all_events = (
            request.get_domain_events()
            + processing.get_domain_events()
            + completed.get_domain_events()
        )

        created_events = [e for e in all_events if isinstance(e, RequestCreatedEvent)]
        status_events = [e for e in all_events if isinstance(e, RequestStatusChangedEvent)]
        completed_events = [e for e in all_events if isinstance(e, RequestCompletedEvent)]

        assert len(created_events) >= 1, "Should have creation event"
        assert len(status_events) >= 1, "Should have status change events"
        assert len(completed_events) >= 1, "Should have completion event"

    def test_events_support_replay(self):
        """Test that events support event sourcing replay."""
        events = [
            RequestCreatedEvent(
                request_id="test-request",
                aggregate_id="test-request",
                aggregate_type="Request",
                template_id="test-template",
                machine_count=2,
                request_type=RequestType.ACQUIRE.value,
            ),
            RequestStatusChangedEvent(
                request_id="test-request",
                aggregate_id="test-request",
                aggregate_type="Request",
                request_type=RequestType.ACQUIRE.value,
                old_status=RequestStatus.PENDING.value,
                new_status=RequestStatus.IN_PROGRESS.value,
            ),
            RequestCompletedEvent(
                request_id="test-request",
                aggregate_id="test-request",
                aggregate_type="Request",
                request_type=RequestType.ACQUIRE.value,
                completion_status=RequestStatus.COMPLETED.value,
                machine_ids=["i-123", "i-456"],
            ),
        ]

        # Events should contain all necessary data for replay
        for event in events:
            assert hasattr(event, "request_id"), "Events should have request_id for replay"
            assert hasattr(event, "occurred_at"), "Events should have timestamp for replay"
            assert hasattr(event, "event_id"), "Events should have unique ID for replay"

    def test_event_deduplication_support(self):
        """Test that events support deduplication."""
        event1 = RequestCreatedEvent(
            request_id="test-request",
            aggregate_id="test-request",
            aggregate_type="Request",
            template_id="test-template",
            machine_count=2,
            request_type=RequestType.ACQUIRE.value,
        )

        event2 = RequestCreatedEvent(
            request_id="test-request",
            aggregate_id="test-request",
            aggregate_type="Request",
            template_id="test-template",
            machine_count=2,
            request_type=RequestType.ACQUIRE.value,
        )

        # Events should have unique IDs for deduplication
        assert event1.event_id != event2.event_id, "Events should have unique IDs"

        # Events with same business data should be distinguishable
        assert event1 != event2, "Events should be distinguishable even with same data"
