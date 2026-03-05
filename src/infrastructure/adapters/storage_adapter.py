"""Storage adapter implementing domain storage ports."""

from typing import Any, Optional

from domain.base.ports.storage_reader_port import StorageReaderPort
from domain.base.ports.storage_writer_port import StorageWriterPort
from infrastructure.storage.base.strategy import StorageStrategy


class StorageReaderAdapter(StorageReaderPort[dict[str, Any]]):
    """Adapter for storage read operations."""

    def __init__(self, storage_strategy: StorageStrategy) -> None:
        """Initialize with storage strategy.

        Args:
            storage_strategy: Underlying storage implementation
        """
        self._storage = storage_strategy

    def find_by_id(self, entity_id: str) -> Optional[dict[str, Any]]:
        """Find entity by ID."""
        return self._storage.find_by_id(entity_id)

    def find_all(self) -> list[dict[str, Any]]:
        """Find all entities."""
        result = self._storage.find_all()
        if isinstance(result, dict):
            return list(result.values())
        return result

    def exists(self, entity_id: str) -> bool:
        """Check if entity exists."""
        return self._storage.exists(entity_id)


class StorageWriterAdapter(StorageWriterPort[dict[str, Any]]):
    """Adapter for storage write operations."""

    def __init__(self, storage_strategy: StorageStrategy) -> None:
        """Initialize with storage strategy.

        Args:
            storage_strategy: Underlying storage implementation
        """
        self._storage = storage_strategy

    def save(self, entity: dict[str, Any]) -> None:
        """Save entity to storage."""
        entity_id = entity.get("id") or entity.get("entity_id")
        if not entity_id:
            raise ValueError("Entity must have 'id' or 'entity_id' field")
        self._storage.save(entity_id, entity)

    def delete(self, entity_id: str) -> None:
        """Delete entity by ID."""
        self._storage.delete(entity_id)
