"""Adapter for existing StorageStrategy to segregated interfaces."""

from typing import Any, Optional, Union

from ..base.strategy import StorageStrategy
from ..interfaces.batch_storage import BatchStorage
from ..interfaces.storage_reader import StorageReader
from ..interfaces.storage_writer import StorageWriter
from ..interfaces.transactional_storage import TransactionalStorage


class StorageStrategyAdapter(StorageReader, StorageWriter, BatchStorage, TransactionalStorage):
    """Adapter that wraps existing StorageStrategy to provide segregated interfaces."""

    def __init__(self, storage_strategy: StorageStrategy) -> None:
        """Initialize adapter with existing storage strategy.

        Args:
            storage_strategy: Existing storage strategy to wrap
        """
        self._storage = storage_strategy

    # StorageReader interface
    def find_by_id(self, entity_id: str) -> Optional[dict[str, Any]]:
        """Find entity by ID."""
        return self._storage.find_by_id(entity_id)

    def find_all(self) -> Union[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Find all entities."""
        return self._storage.find_all()

    def exists(self, entity_id: str) -> bool:
        """Check if entity exists."""
        return self._storage.exists(entity_id)

    def find_by_criteria(self, criteria: dict[str, Any]) -> list[dict[str, Any]]:
        """Find entities by criteria."""
        return self._storage.find_by_criteria(criteria)

    # StorageWriter interface
    def save(self, entity_id: str, data: dict[str, Any]) -> None:
        """Save entity data."""
        self._storage.save(entity_id, data)

    def delete(self, entity_id: str) -> None:
        """Delete entity."""
        self._storage.delete(entity_id)

    # BatchStorage interface
    def save_batch(self, entities: dict[str, dict[str, Any]]) -> None:
        """Save multiple entities in a single operation."""
        self._storage.save_batch(entities)

    def delete_batch(self, entity_ids: list[str]) -> None:
        """Delete multiple entities in a single operation."""
        self._storage.delete_batch(entity_ids)

    # TransactionalStorage interface
    def begin_transaction(self) -> None:
        """Begin a transaction."""
        self._storage.begin_transaction()

    def commit_transaction(self) -> None:
        """Commit the current transaction."""
        self._storage.commit_transaction()

    def rollback_transaction(self) -> None:
        """Rollback the current transaction."""
        self._storage.rollback_transaction()
