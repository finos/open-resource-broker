"""Storage reader port - focused interface for read operations."""

from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")


class StorageReaderPort(ABC, Generic[T]):
    """Focused port for storage read operations only.

    This interface follows ISP by providing only read operations,
    allowing clients that only need to read data to depend on a minimal interface.
    """

    @abstractmethod
    def find_by_id(self, entity_id: str) -> Optional[T]:
        """Find entity by ID.

        Args:
            entity_id: Entity identifier

        Returns:
            Entity if found, None otherwise
        """

    @abstractmethod
    def find_all(self) -> list[T]:
        """Find all entities.

        Returns:
            List of all entities
        """

    @abstractmethod
    def find_by_criteria(self, criteria: dict[str, Any]) -> list[T]:
        """Find entities by criteria.

        Args:
            criteria: Dictionary of field-value pairs to match

        Returns:
            List of matching entities
        """

    @abstractmethod
    def exists(self, entity_id: str) -> bool:
        """Check if entity exists.

        Args:
            entity_id: Entity identifier

        Returns:
            True if entity exists, False otherwise
        """

    @abstractmethod
    def count(self) -> int:
        """Count total entities.

        Returns:
            Total number of entities
        """
