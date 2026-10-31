"""Base domain entities - foundation for all domain objects."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Annotated, Any, Optional, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

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


class PersistedRevision:
    """Version of the stored state an aggregate instance derives from.

    ``value`` is ``None`` for an aggregate that was never loaded or saved. The
    holder is shared by every copy derived from one load (mutators rebuild the
    aggregate through ``model_dump`` / ``model_validate``, which keep it), so a
    save through any copy moves the baseline for all of them. Only repositories
    set it; domain code never reads it.
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value: Optional[int] = None

    def mark_loaded(self, version: int) -> None:
        """Record the version the aggregate was read at."""
        self.value = version

    def mark_saved(self, version: int) -> None:
        """Record the version that was written."""
        self.value = version

    @classmethod
    def __get_pydantic_json_schema__(cls, _core_schema: Any, _handler: Any) -> dict[str, Any]:
        # Bookkeeping, not part of any published schema
        return {}

    def __eq__(self, other: object) -> bool:
        # Bookkeeping, not state: two aggregates with equal state are equal
        return isinstance(other, PersistedRevision)

    def __hash__(self) -> int:
        return 0


def _fresh_if_none(value: Any) -> Any:
    """A JSON round trip emits null for the holder; give the rebuilt aggregate a new one."""
    return PersistedRevision() if value is None else value


def _omit_from_json(_value: Any) -> None:
    """Serialise the holder as null in JSON output."""
    return None


class AggregateRoot(Entity):
    """Base class for aggregate roots."""

    persisted_revision: Annotated[
        PersistedRevision,
        BeforeValidator(_fresh_if_none),
        PlainSerializer(_omit_from_json, return_type=type(None), when_used="json"),
    ] = Field(default_factory=PersistedRevision, repr=False)

    def __init__(self, **data) -> None:
        """Initialize the instance."""
        super().__init__(**data)
        self._domain_events: list[Any] = []

    def __eq__(self, other: object) -> bool:
        """Aggregate roots are equal by identity, same as Entity.

        The pending-events queue (``_domain_events``) is transient publish
        state, not part of the aggregate's identity, so it deliberately does
        not participate in equality.
        """
        return super().__eq__(other)

    def __hash__(self) -> int:
        """Hash based on entity ID (see Entity.__hash__)."""
        return super().__hash__()

    def add_domain_event(self, event: Any) -> None:
        """Add a domain event to be published."""
        self._domain_events.append(event)

    def clear_domain_events(self) -> None:
        """Clear all domain events."""
        self._domain_events.clear()

    def get_domain_events(self) -> list[Any]:
        """Get all domain events."""
        return self._domain_events.copy()

    @abstractmethod
    def get_id(self) -> Any:
        """Get the aggregate root identifier."""
