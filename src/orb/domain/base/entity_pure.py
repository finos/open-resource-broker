"""Pure domain entities - foundation for all domain objects without infrastructure dependencies."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional, TypeVar

T = TypeVar("T", bound="Entity")


class Entity(ABC):
    """Base class for all domain entities - pure Python implementation."""

    def __init__(
        self,
        id: Optional[Any] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize entity with identity and timestamps.

        Args:
            id: Entity identifier (can be any type)
            created_at: Creation timestamp
            updated_at: Last update timestamp
            **kwargs: Additional attributes for subclasses
        """
        self.id = id
        self.created_at = created_at or datetime.utcnow()
        self.updated_at = updated_at or datetime.utcnow()

        # Store additional attributes for subclasses
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __eq__(self, other: object) -> bool:
        """Entities are equal if they have the same ID and type."""
        if not isinstance(other, self.__class__):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash based on entity ID."""
        return hash((self.__class__, self.id))


class AggregateRoot(Entity):
    """Base class for aggregate roots - pure Python implementation."""

    def __init__(self, **data: Any) -> None:
        """Initialize aggregate root with domain events tracking."""
        super().__init__(**data)
        self._domain_events: list[Any] = []

    def add_domain_event(self, event: Any) -> None:
        """Add a domain event to be published.

        Args:
            event: Domain event to add
        """
        self._domain_events.append(event)

    def clear_domain_events(self) -> None:
        """Clear all domain events."""
        self._domain_events.clear()

    def get_domain_events(self) -> list[Any]:
        """Get all domain events.

        Returns:
            Copy of domain events list
        """
        return self._domain_events.copy()

    @abstractmethod
    def get_id(self) -> Any:
        """Get the aggregate root identifier.

        Returns:
            Aggregate identifier
        """
        pass
