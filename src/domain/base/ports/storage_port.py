"""Domain port for storage operations."""

from abc import ABC, abstractmethod
from typing import Generic, List, Optional, TypeVar

T = TypeVar("T")


class StoragePort(ABC, Generic[T]):
    """Domain port for storage operations."""

    @abstractmethod
    def save(self, entity: T) -> None:
        """Save an entity to storage."""

    @abstractmethod
    def find_by_id(self, entity_id: str) -> Optional[T]:
        """Find entity by ID."""

    @abstractmethod
    def find_all(self) -> List[T]:
        """Find all entities."""

    @abstractmethod
    def delete(self, entity_id: str) -> None:
        """Delete entity by ID."""

    @abstractmethod
    def exists(self, entity_id: str) -> bool:
        """Check if entity exists."""

    @abstractmethod
    def count(self) -> int:
        """Count total entities."""

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up storage resources."""
