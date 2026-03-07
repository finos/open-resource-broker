"""Storage writer interface for write operations."""

from abc import ABC, abstractmethod
from typing import Any


class StorageWriter(ABC):
    """Interface for write storage operations."""

    @abstractmethod
    def save(self, entity_id: str, data: dict[str, Any]) -> None:
        """Save entity data.

        Args:
            entity_id: Entity ID
            data: Entity data
        """

    @abstractmethod
    def delete(self, entity_id: str) -> None:
        """Delete entity.

        Args:
            entity_id: Entity ID
        """
