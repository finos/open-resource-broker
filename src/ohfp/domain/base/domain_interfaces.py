"""Core domain interfaces and abstract base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, Protocol, TypeVar

from .entity import AggregateRoot, Entity

T = TypeVar("T")  # Generic type for domain entities/aggregates
A = TypeVar("A", bound=AggregateRoot)


class RepositoryProtocol(Protocol[T]):
    """
    Protocol for repository operations.

    This protocol defines the structural interface for repositories,
    allowing for domain-specific parameter names in implementations.
    Unlike the Repository abstract base class, this Protocol doesn't
    enforce specific parameter names, only their types.
    """

    def save(self, obj: T) -> None:
        """Save an entity."""
        ...

    def find_by_id(self, id_value: Any) -> Optional[T]:
        """Find entity by ID."""
        ...

    def delete(self, id_value: Any) -> None:
        """Delete entity by ID."""
        ...

    def find_all(self) -> list[T]:
        """Find all entities."""
        ...


class Repository(Generic[T], ABC):
    """
    Generic repository interface.

    This interface defines the common operations for all repositories.
    Subclasses should implement these methods with domain-specific parameter names.
    """

    @abstractmethod
    def save(self, entity: T) -> None:
        """Save an entity."""

    @abstractmethod
    def find_by_id(self, entity_id: Any) -> Optional[T]:
        """Find entity by ID."""

    @abstractmethod
    def delete(self, entity_id: Any) -> None:
        """Delete entity by ID."""

    @abstractmethod
    def find_all(self) -> list[T]:
        """Find all entities."""


class AggregateRepository(Generic[A], ABC):
    """Base repository interface for aggregate roots."""

    @abstractmethod
    def save(self, aggregate: A) -> None:
        """Save an aggregate root."""

    @abstractmethod
    def find_by_id(self, aggregate_id: str) -> Optional[A]:
        """Find aggregate by ID."""

    @abstractmethod
    def delete(self, aggregate_id: str) -> None:
        """Delete aggregate by ID."""


class UnitOfWork(Protocol):
    """Unit of work pattern for transaction management."""

    def __enter__(self) -> UnitOfWork:
        """Enter the unit of work context."""
        ...

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the unit of work context."""
        ...

    def register_new(self, entity: Entity) -> None:
        """Register a new entity."""
        ...

    def register_dirty(self, entity: Entity) -> None:
        """Register a dirty entity."""
        ...

    def register_removed(self, entity: Entity) -> None:
        """Register a removed entity."""
        ...

    @abstractmethod
    def begin(self) -> None:
        """Begin a new transaction."""

    @abstractmethod
    def commit(self) -> None:
        """Commit the current transaction."""

    @abstractmethod
    def rollback(self) -> None:
        """Rollback the current transaction."""

    @property
    @abstractmethod
    def requests(self) -> Any:
        """Get request repository."""

    @property
    @abstractmethod
    def machines(self) -> Any:
        """Get machine repository."""


class UnitOfWorkFactory(ABC):
    """Interface for unit of work factory."""

    @abstractmethod
    def create_unit_of_work(self) -> UnitOfWork:
        """
        Create a new unit of work.

        Returns:
            New unit of work instance

        Raises:
            InfrastructureError: If unit of work creation fails
        """
