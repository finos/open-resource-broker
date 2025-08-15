"""Event publisher port for domain events."""

from abc import ABC, abstractmethod
from typing import List

from domain.base.events import DomainEvent


class EventPublisherPort(ABC):
    """Port for publishing domain events."""

    @abstractmethod
    def publish(self, event: DomainEvent) -> None:
        """Publish a single domain event."""

    @abstractmethod
    def publish_batch(self, events: List[DomainEvent]) -> None:
        """Publish multiple domain events."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Check if event publishing is enabled."""
