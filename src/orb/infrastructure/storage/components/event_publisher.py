"""Event publishing components for domain events."""

from abc import ABC, abstractmethod
from typing import Any, List

from orb.infrastructure.logging.logger import get_logger


class EventPublisher(ABC):
    """Base interface for domain event publishing."""

    @abstractmethod
    def publish_events(self, events: List[Any]) -> None:
        """Publish list of domain events."""

    @abstractmethod
    def publish_event(self, event: Any) -> None:
        """Publish single domain event."""


class LoggingEventPublisher(EventPublisher):
    """Event publisher that logs events (for development/testing)."""

    def __init__(self) -> None:
        """Initialize event publisher."""
        self.logger = get_logger(__name__)

    def publish_events(self, events: List[Any]) -> None:
        """Publish list of domain events."""
        for event in events:
            self.publish_event(event)

    def publish_event(self, event: Any) -> None:
        """Publish single domain event."""
        self.logger.info("Publishing domain event: %s", type(event).__name__)
        self.logger.debug("Event details: %s", event)


class NoOpEventPublisher(EventPublisher):
    """No-operation event publisher that doesn't publish events."""

    def publish_events(self, events: List[Any]) -> None:
        """Do nothing (no event publishing)."""
        pass

    def publish_event(self, event: Any) -> None:
        """Do nothing (no event publishing)."""
        pass


class InMemoryEventPublisher(EventPublisher):
    """In-memory event publisher for testing."""

    def __init__(self) -> None:
        """Initialize event publisher."""
        self.published_events: List[Any] = []
        self.logger = get_logger(__name__)

    def publish_events(self, events: List[Any]) -> None:
        """Publish list of domain events."""
        self.published_events.extend(events)
        self.logger.debug("Published %d events", len(events))

    def publish_event(self, event: Any) -> None:
        """Publish single domain event."""
        self.published_events.append(event)
        self.logger.debug("Published event: %s", type(event).__name__)

    def get_published_events(self) -> List[Any]:
        """Get all published events."""
        return self.published_events.copy()

    def clear_events(self) -> None:
        """Clear all published events."""
        self.published_events.clear()
