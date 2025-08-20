"""Base domain entities - foundation for all domain objects."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T", bound="Entity")


class Entity(BaseModel, ABC):
    """Base class for all domain entities."""

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        arbitrary_types_allowed=True,  # Entities are mutable
    )

    id: Optional[Any] = None  # Entity identifier (can be any type)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __eq__(self, other: object) -> bool:
        """Entities are equal if they have the same ID and type."""
        if not isinstance(other, self.__class__):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash based on entity ID."""
        return hash((self.__class__, self.id))


class AggregateRoot(Entity):
    """Base class for aggregate roots."""

    def __init__(self, **data) -> None:
        """Initialize the instance."""
        super().__init__(**data)
        self._domain_events: List[Any] = []

    def add_domain_event(self, event: Any) -> None:
        """Add a domain event to be published."""
        self._domain_events.append(event)

    def clear_domain_events(self) -> None:
        """Clear all domain events."""
        self._domain_events.clear()

    def get_domain_events(self) -> List[Any]:
        """Get all domain events."""
        return self._domain_events.copy()

    @abstractmethod
    def get_id(self) -> Any:
        """Get the aggregate root identifier."""
