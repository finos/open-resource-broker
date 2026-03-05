"""Batch storage interface for bulk operations."""

from abc import ABC, abstractmethod
from typing import Any


class BatchStorage(ABC):
    """Interface for batch storage operations."""

    @abstractmethod
    def save_batch(self, entities: dict[str, dict[str, Any]]) -> None:
        """Save multiple entities in a single operation.

        Args:
            entities: Dictionary of entity ID to entity data
        """

    @abstractmethod
    def delete_batch(self, entity_ids: list[str]) -> None:
        """Delete multiple entities in a single operation.

        Args:
            entity_ids: List of entity IDs to delete
        """
