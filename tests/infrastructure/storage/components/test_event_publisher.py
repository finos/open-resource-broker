"""Tests for EventPublisher component."""

from dataclasses import dataclass

import pytest

from orb.infrastructure.storage.components.event_publisher import (
    EventPublisher,
    InMemoryEventPublisher,
    LoggingEventPublisher,
    NoOpEventPublisher,
)


@dataclass
class DomainTestEvent:
    """Test event for publisher tests."""

    event_type: str
    data: str


class TestEventPublisherInterface:
    """Test EventPublisher interface."""

    def test_event_publisher_interface_is_abstract(self):
        """Test that EventPublisher is abstract."""
        with pytest.raises(TypeError):
            EventPublisher()


class TestLoggingEventPublisher:
    """Test LoggingEventPublisher implementation."""

    def test_initialization(self):
        """Test publisher initialization."""
        publisher = LoggingEventPublisher()
        assert publisher.logger is not None

    def test_publish_single_event(self):
        """Test publishing single event."""
        publisher = LoggingEventPublisher()
        event = DomainTestEvent("test", "data")

        # Should not raise exception
        publisher.publish_event(event)

    def test_publish_multiple_events(self):
        """Test publishing multiple events."""
        publisher = LoggingEventPublisher()
        events = [
            DomainTestEvent("test1", "data1"),
            DomainTestEvent("test2", "data2"),
        ]

        # Should not raise exception
        publisher.publish_events(events)


class TestNoOpEventPublisher:
    """Test NoOpEventPublisher implementation."""

    def test_publish_event_does_nothing(self):
        """Test that publish_event does nothing."""
        publisher = NoOpEventPublisher()
        event = DomainTestEvent("test", "data")

        # Should not raise exception
        publisher.publish_event(event)

    def test_publish_events_does_nothing(self):
        """Test that publish_events does nothing."""
        publisher = NoOpEventPublisher()
        events = [
            DomainTestEvent("test1", "data1"),
            DomainTestEvent("test2", "data2"),
        ]

        # Should not raise exception
        publisher.publish_events(events)


class TestInMemoryEventPublisher:
    """Test InMemoryEventPublisher implementation."""

    def test_initialization(self):
        """Test publisher initialization."""
        publisher = InMemoryEventPublisher()
        assert publisher.logger is not None
        assert publisher.published_events == []

    def test_publish_single_event(self):
        """Test publishing single event."""
        publisher = InMemoryEventPublisher()
        event = DomainTestEvent("test", "data")

        publisher.publish_event(event)

        assert len(publisher.published_events) == 1
        assert publisher.published_events[0] == event

    def test_publish_multiple_events(self):
        """Test publishing multiple events."""
        publisher = InMemoryEventPublisher()
        events = [
            DomainTestEvent("test1", "data1"),
            DomainTestEvent("test2", "data2"),
        ]

        publisher.publish_events(events)

        assert len(publisher.published_events) == 2
        assert publisher.published_events == events

    def test_get_published_events(self):
        """Test getting published events."""
        publisher = InMemoryEventPublisher()
        event = DomainTestEvent("test", "data")

        publisher.publish_event(event)
        result = publisher.get_published_events()

        assert result == [event]
        # Should return copy, not original list
        assert result is not publisher.published_events

    def test_clear_events(self):
        """Test clearing published events."""
        publisher = InMemoryEventPublisher()
        event = DomainTestEvent("test", "data")

        publisher.publish_event(event)
        publisher.clear_events()

        assert publisher.published_events == []
        assert publisher.get_published_events() == []

    def test_mixed_publish_methods(self):
        """Test mixing single and batch event publishing."""
        publisher = InMemoryEventPublisher()
        event1 = DomainTestEvent("test1", "data1")
        events = [
            DomainTestEvent("test2", "data2"),
            DomainTestEvent("test3", "data3"),
        ]

        publisher.publish_event(event1)
        publisher.publish_events(events)

        assert len(publisher.published_events) == 3
        assert publisher.published_events[0] == event1
        assert publisher.published_events[1:] == events
