"""Storage writer port - focused interface for write operations."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class StorageWriterPort(ABC, Generic[T]):
    """Focused port for storage write operations only.

    This interface follows ISP by providing only write operations,
    allowing clients that only need to write data to depend on a minimal interface.
    """

    @abstractmethod
    def save(self, entity: T) -> None:
        """Save an entity to storage.

        Args:
            entity: Entity to save
        """

    @abstractmethod
    def delete(self, entity_id: str) -> None:
        """Delete entity by ID.

        Args:
            entity_id: Entity identifier
        """
