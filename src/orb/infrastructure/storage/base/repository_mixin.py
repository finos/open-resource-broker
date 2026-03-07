"""Mixin providing common storage+deserialize patterns shared across repositories."""

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Minimal protocol for storage backends (both StoragePort and BaseStorageStrategy)."""

    def find_by_id(self, entity_id: str) -> Optional[dict[str, Any]]: ...
    def find_by_criteria(self, criteria: dict[str, Any]) -> list[dict[str, Any]]: ...
    def find_all(self) -> Optional[dict[str, Any]]: ...
    def delete(self, entity_id: str) -> None: ...
    def exists(self, entity_id: str) -> bool: ...


class StorageRepositoryMixin:
    """
    Mixin that eliminates repeated storage+deserialize boilerplate across repositories.

    Requires the subclass to expose:
      - self._storage: a StorageBackend-compatible object
      - self._deserialize(data): converts a dict to the entity type
    """

    def _get_storage(self) -> Any:
        """Return the storage backend, supporting both attribute names used in the codebase."""
        if hasattr(self, "storage_port"):
            return self.storage_port  # type: ignore[attr-defined]
        if hasattr(self, "storage_strategy"):
            return self.storage_strategy  # type: ignore[attr-defined]
        raise AttributeError(
            f"{type(self).__name__} must define 'storage_port' or 'storage_strategy'"
        )

    def _deserialize(self, data: dict[str, Any]) -> Any:
        """Deserialize a dict to an entity. Subclasses must override or set self.serializer."""
        if hasattr(self, "serializer"):
            return self.serializer.from_dict(data)  # type: ignore[attr-defined]
        raise NotImplementedError(
            f"{type(self).__name__} must define 'serializer' or override '_deserialize'"
        )

    def _load_by_id(self, entity_id: str) -> Optional[Any]:
        """Fetch a single entity by ID and deserialize it, or return None."""
        data = self._get_storage().find_by_id(entity_id)
        if data:
            return self._deserialize(data)
        return None

    def _load_by_criteria(self, criteria: dict[str, Any]) -> list[Any]:
        """Fetch entities matching criteria and deserialize them."""
        data_list = self._get_storage().find_by_criteria(criteria)
        return [self._deserialize(data) for data in data_list]

    def _load_all(self) -> list[Any]:
        """Fetch all entities and deserialize them."""
        all_data = self._get_storage().find_all()
        if isinstance(all_data, dict):
            return [self._deserialize(data) for data in all_data.values()]
        return [self._deserialize(data) for data in all_data]

    def _delete_by_id(self, entity_id: str) -> None:
        """Delete an entity by ID from storage."""
        self._get_storage().delete(entity_id)

    def _check_exists(self, entity_id: str) -> bool:
        """Check whether an entity exists in storage."""
        return self._get_storage().exists(entity_id)
