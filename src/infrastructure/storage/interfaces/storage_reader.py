"""Storage reader interface for read-only operations."""

from abc import ABC, abstractmethod
from typing import Any, Optional, Union


class StorageReader(ABC):
    """Interface for read-only storage operations."""

    @abstractmethod
    def find_by_id(self, entity_id: str) -> Optional[dict[str, Any]]:
        """Find entity by ID.

        Args:
            entity_id: Entity ID

        Returns:
            Entity data if found, None otherwise
        """

    @abstractmethod
    def find_all(self) -> Union[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Find all entities.

        Returns:
            List of entity data or dictionary of entity ID to entity data
        """

    @abstractmethod
    def exists(self, entity_id: str) -> bool:
        """Check if entity exists.

        Args:
            entity_id: Entity ID

        Returns:
            True if entity exists, False otherwise
        """

    @abstractmethod
    def find_by_criteria(self, criteria: dict[str, Any]) -> list[dict[str, Any]]:
        """Find entities by criteria.

        Args:
            criteria: Dictionary of field-value pairs to match

        Returns:
            List of matching entity data
        """
