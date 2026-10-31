"""JSON storage strategy implementation using componentized architecture."""

import json
import os
from typing import Any, Callable, Optional, TypeVar, cast

from orb.domain.base.exceptions import ConcurrencyError
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.base.strategy import BaseStorageStrategy

# Import components
from orb.infrastructure.storage.components import (
    FileManager,
    JSONSerializer,
    LockManager,
    MemoryTransactionManager,
)
from orb.infrastructure.storage.exceptions import StorageError
from orb.infrastructure.storage.metrics_decorators import instrument_storage

_R = TypeVar("_R")


class JSONStorageStrategy(BaseStorageStrategy):
    """
    JSON storage strategy using componentized architecture.

    Orchestrates components for file operations, locking, serialization,
    and transaction management. Reduced from 935 lines to ~200 lines.
    """

    def __init__(
        self,
        file_path: str,
        create_dirs: bool = True,
        entity_type: str = "entities",
        metrics: Optional[Any] = None,
        backup_count: int = 5,
        backup_enabled: bool = True,
    ) -> None:
        """
        Initialize JSON storage strategy with components.

        Args:
            file_path: Path to JSON file
            create_dirs: Whether to create parent directories
            entity_type: Type of entities being stored (for logging)
            metrics: Optional metrics collector for instrumentation
        """
        super().__init__()

        self.entity_type = entity_type
        self.logger = get_logger(__name__)
        self._metrics: Optional[Any] = metrics

        # Initialize components
        self.file_manager = FileManager(
            file_path, create_dirs, backup_count=backup_count, backup_enabled=backup_enabled
        )
        self.lock_manager = LockManager("reader_writer")
        self.serializer = JSONSerializer()
        self.transaction_manager = MemoryTransactionManager()

        # Read cache. It only serves reads: it is reused while the file still has
        # the signature it was read with, and writes never consult it.
        self._data_cache: Optional[dict[str, dict[str, Any]]] = None
        self._cache_valid = False
        self._cache_signature: Optional[tuple[int, int, int, int]] = None

        self.logger.debug("Initialized JSON storage strategy for %s at %s", entity_type, file_path)

        # Warn operators when the process is running with multiple workers so
        # they know the JSON backend is not suited for that topology.
        # We inspect the environment variable set by common ASGI/WSGI servers
        # (Uvicorn, Gunicorn) rather than importing the full config object to
        # avoid a circular-import risk.  If multi-worker config access is ever
        # wired here more directly the env-var check can be replaced.
        _worker_hint = os.environ.get("WEB_CONCURRENCY", "") or os.environ.get(
            "UVICORN_WORKERS", ""
        )
        try:
            _workers = int(_worker_hint)
        except (ValueError, TypeError):
            _workers = 1
        if _workers > 1:
            self.logger.warning(
                "JSONStorageStrategy: %d workers detected (WEB_CONCURRENCY/UVICORN_WORKERS). "
                "The JSON file backend uses single-host flock-based serialization and is NOT "
                "safe for multi-instance (multi-host) deployments. "
                "Use PostgreSQL storage (strategy: sql) for multi-worker or multi-replica "
                "configurations.",
                _workers,
            )

    def is_healthy(self) -> tuple[bool, dict[str, Any]]:
        """Verify the JSON file is reachable, parses, and has the expected shape.

        Healthy when:
          - the parent directory exists (writable path)
          - either the file is absent (fresh install → empty dataset) OR the
            file parses as a JSON object of ``{entity_id: dict[str, Any]}``
          - a sampled record validates as a dict (cheap drift check; full
            pydantic validation belongs in a separate integrity probe, not
            a health endpoint)
        """
        path = self.file_manager.file_path
        details: dict[str, Any] = {
            "type": "json",
            "file_path": str(path),
            "entity_type": self.entity_type,
        }
        try:
            if not path.parent.exists():
                details["reason"] = "parent directory does not exist"
                return False, details
            if not path.exists():
                details["state"] = "empty"
                return True, details
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                details["reason"] = f"expected JSON object, got {type(payload).__name__}"
                return False, details
            details["entity_count"] = len(payload)
            # Shape sanity: every record should be a dict. Sample the first
            # one rather than scanning all keys (health probe stays O(1)).
            if payload:
                sample_key = next(iter(payload))
                sample = payload[sample_key]
                if not isinstance(sample, dict):
                    details["reason"] = (
                        f"sampled record {sample_key!r} is {type(sample).__name__}, expected dict"
                    )
                    return False, details
                details["sample_keys"] = sorted(sample.keys())[:8]
            details["state"] = "ok"
            return True, details
        except (OSError, json.JSONDecodeError) as exc:
            details["error"] = str(exc)
            return False, details

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "save")
    def save(self, entity_id: str, data: dict[str, Any]) -> None:
        """
        Save entity data to JSON file.

        Args:
            entity_id: Unique identifier for the entity
            data: Entity data to save
        """

        def apply(section: dict[str, dict[str, Any]]) -> tuple[bool, None]:
            section[entity_id] = data
            return True, None

        self._mutate(apply, f"save entity {entity_id}")
        self.logger.debug("Saved %s entity: %s", self.entity_type, entity_id)

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "find_by_id")
    def find_by_id(self, entity_id: str) -> Optional[dict[str, Any]]:
        """
        Find entity by ID.

        Args:
            entity_id: Entity identifier

        Returns:
            Entity data if found, None otherwise
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._load_data()
                entity_data = all_data.get(entity_id)

                if entity_data:
                    self.logger.debug("Found %s entity: %s", self.entity_type, entity_id)
                else:
                    self.logger.debug("%s entity not found: %s", self.entity_type, entity_id)

                return entity_data

            except Exception as e:
                self.logger.error("Failed to find %s entity %s: %s", self.entity_type, entity_id, e)
                raise StorageError(f"Failed to find entity {entity_id}: {e}")

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "find_all")
    def find_all(self) -> dict[str, dict[str, Any]]:
        """
        Find all entities.

        Returns:
            Dictionary of all entities keyed by ID
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._load_data()
                self.logger.debug("Loaded %s %s entities", len(all_data), self.entity_type)
                return all_data.copy()

            except Exception as e:
                self.logger.error("Failed to load all %s entities: %s", self.entity_type, e)
                raise StorageError(f"Failed to load all entities: {e}")

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "delete")
    def delete(self, entity_id: str) -> None:
        """
        Delete entity by ID.

        Args:
            entity_id: Entity identifier
        """

        def apply(section: dict[str, dict[str, Any]]) -> tuple[bool, bool]:
            existed = section.pop(entity_id, None) is not None
            return existed, existed

        existed = self._mutate(apply, f"delete entity {entity_id}")
        if existed:
            self.logger.debug("Deleted %s entity: %s", self.entity_type, entity_id)
        else:
            self.logger.warning("%s entity not found for deletion: %s", self.entity_type, entity_id)

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "exists")
    def exists(self, entity_id: str) -> bool:
        """
        Check if entity exists.

        Args:
            entity_id: Entity identifier

        Returns:
            True if entity exists, False otherwise
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._load_data()
                exists = entity_id in all_data
                self.logger.debug("%s entity %s exists: %s", self.entity_type, entity_id, exists)
                return exists

            except Exception as e:
                self.logger.error(
                    "Failed to check existence of %s entity %s: %s",
                    self.entity_type,
                    entity_id,
                    e,
                )
                return False

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "find_by_criteria")
    def find_by_criteria(self, criteria: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Find entities matching criteria.

        Args:
            criteria: Search criteria

        Returns:
            List of matching entities
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._load_data()
                matching_entities = []

                for entity_data in all_data.values():
                    if self._matches_criteria(entity_data, criteria):
                        matching_entities.append(entity_data)

                self.logger.debug(
                    "Found %s %s entities matching criteria",
                    len(matching_entities),
                    self.entity_type,
                )
                return matching_entities

            except Exception as e:
                self.logger.error("Failed to search %s entities: %s", self.entity_type, e)
                raise StorageError(f"Failed to search entities: {e}")

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "save_batch")
    def save_batch(self, entities: dict[str, dict[str, Any]]) -> None:
        """
        Save multiple entities in batch.

        Args:
            entities: Dictionary of entities to save
        """

        def apply(section: dict[str, dict[str, Any]]) -> tuple[bool, None]:
            section.update(entities)
            return True, None

        self._mutate(apply, "save batch")
        self.logger.debug("Saved batch of %s %s entities", len(entities), self.entity_type)

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "delete_batch")
    def delete_batch(self, entity_ids: list[str]) -> None:
        """
        Delete multiple entities in batch.

        Args:
            entity_ids: List of entity IDs to delete
        """

        def apply(section: dict[str, dict[str, Any]]) -> tuple[bool, None]:
            for entity_id in entity_ids:
                section.pop(entity_id, None)
            return True, None

        self._mutate(apply, "delete batch")
        self.logger.debug("Deleted batch of %s %s entities", len(entity_ids), self.entity_type)

    def begin_transaction(self) -> None:
        """Begin transaction."""
        self.transaction_manager.begin_transaction()

    def commit_transaction(self) -> None:
        """Commit transaction."""
        self.transaction_manager.commit_transaction()

    def rollback_transaction(self) -> None:
        """Rollback transaction."""
        self.transaction_manager.rollback_transaction()

    def cleanup(self) -> None:
        """Clean up resources."""
        self._invalidate_cache()
        self.logger.debug("Cleaned up JSON storage strategy for %s", self.entity_type)

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "count")
    def count(self) -> int:
        """
        Count total number of entities.
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._load_data()
                count = len(all_data)
                self.logger.debug(f"Counted {count} {self.entity_type} entities")
                return count
            except Exception as e:
                self.logger.error(f"Failed to count {self.entity_type} entities: {e}")
                return 0

    def _invalidate_cache(self) -> None:
        self._data_cache = None
        self._cache_valid = False
        self._cache_signature = None

    def _section_from(self, content: str) -> dict[str, dict[str, Any]]:
        """The entity-type section of the file content (empty when absent)."""
        if not content.strip():
            return {}
        full_data = self.serializer.deserialize(content)
        if not isinstance(full_data, dict):
            self.logger.warning("Invalid data format in file, initializing empty data")
            return {}
        section = full_data.get(self.entity_type, {})
        return section if isinstance(section, dict) else {}

    def _load_data(self) -> dict[str, dict[str, Any]]:
        """Load this entity type's records for reading.

        The cached copy is reused only while the file still has the signature it
        was read with, so a write by another strategy or process is picked up.
        """
        if (
            self._cache_valid
            and self._data_cache is not None
            and self.file_manager.current_signature() == self._cache_signature
        ):
            return self._data_cache

        try:
            content, signature = self.file_manager.read_file_with_signature()
            entity_data = self._section_from(content)

            self._data_cache = entity_data
            self._cache_signature = signature
            self._cache_valid = True

            return entity_data

        except Exception as e:
            self.logger.error("Failed to load data: %s", e)
            # Try to recover from backup
            if self.file_manager.recover_from_backup():
                self.logger.info("Recovered data from backup")
                return self._load_data()  # Recursive call after recovery
            else:
                self.logger.warning("No backup available, starting with empty data")
                return {}

    def _mutate(
        self, apply: Callable[[dict[str, dict[str, Any]]], tuple[bool, _R]], what: str
    ) -> _R:
        """Read, change and write this entity type's section under the locks.

        ``apply`` receives a copy of the freshly read section and returns
        ``(changed, result)``; nothing is written when ``changed`` is false.
        The read happens inside the cross-process lock and never uses the cache,
        so a record written by another strategy instance or process since the
        last read cannot be overwritten with an older copy. The cache is replaced
        only after the file was replaced and dropped on any failure.

        LIMITATION: ``flock`` is unreliable on NFS and other network filesystems;
        use SQL storage for multi-host deployments.
        """
        with self.lock_manager.write_lock():
            try:
                # Best effort and outside the critical section: at worst it backs
                # up a slightly stale copy.
                self.file_manager.create_backup()

                with self.file_manager.exclusive_write_lock():
                    content, _ = self.file_manager.read_file_with_signature()
                    if content.strip():
                        full_data = self.serializer.deserialize(content)
                    else:
                        full_data = {}
                    if not isinstance(full_data, dict):
                        full_data = {}
                    section = full_data.get(self.entity_type)
                    current = section if isinstance(section, dict) else {}

                    working = dict(current)
                    changed, result = apply(working)
                    if not changed:
                        return result

                    full_data[self.entity_type] = working
                    self.file_manager.write_file(self.serializer.serialize(full_data))
                    signature = self.file_manager.current_signature()

                # The lock was held from the read to the replace, so the file now
                # carries exactly what was written.
                self._data_cache = working
                self._cache_signature = signature
                self._cache_valid = True
                return result

            except ConcurrencyError:
                self._invalidate_cache()
                raise
            except Exception as e:
                self._invalidate_cache()
                self.logger.error("Failed to %s in %s storage: %s", what, self.entity_type, e)
                raise StorageError(f"Failed to {what}: {e}") from e

    def _matches_criteria(self, entity_data: dict[str, Any], criteria: dict[str, Any]) -> bool:
        """Check if entity matches search criteria."""
        for key, expected_value in criteria.items():
            if key not in entity_data:
                return False

            actual_value = entity_data[key]

            # Handle different comparison types
            if isinstance(expected_value, dict) and "$in" in expected_value:
                if actual_value not in expected_value["$in"]:
                    return False
            elif isinstance(expected_value, dict) and "$regex" in expected_value:
                import re

                pattern = expected_value["$regex"]
                if not re.search(pattern, str(actual_value)):
                    return False
            elif actual_value != expected_value:
                return False

        return True
