"""JSON storage strategy implementation using componentized architecture."""

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, cast

from orb.domain.base.exceptions import ConcurrencyError
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.base.strategy import BaseStorageStrategy

# Import components
from orb.infrastructure.storage.components import (
    FileManager,
    JSONSerializer,
    LockManager,
)
from orb.infrastructure.storage.exceptions import StorageError
from orb.infrastructure.storage.json.transaction_scope import JSONTransactionScope
from orb.infrastructure.storage.metrics_decorators import instrument_storage

_R = TypeVar("_R")


@dataclass
class _StagedWrite:
    """The last buffered operation on one record inside a transaction."""

    op: str  # "put" or "delete"
    data: Optional[dict[str, Any]]
    # Version the first buffered write expected; later writes keep it, since it
    # guards the state the record had before the transaction began
    expected: Optional[int]
    # Two writes expected different versions of one record
    conflict: bool = False


class JSONStorageStrategy(BaseStorageStrategy):
    """
    JSON storage strategy using componentized architecture.

    Orchestrates components for file operations, locking and serialization.
    Inside a transaction (see ``JSONTransactionScope``) writes are buffered and
    reads see them; they reach the file only when the scope commits.
    """

    supports_versioned_save = True

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
        # A strategy used on its own commits by itself; a unit of work replaces
        # this with the scope it shares with its other strategies
        self._scope = JSONTransactionScope()
        self._scope.register(self)
        self._buffer: dict[str, _StagedWrite] = {}

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
            self.logger.warning("JSON storage health probe failed for %s: %s", path, exc)
            details["reason"] = "storage file could not be read"
            return False, details

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "save")
    def save(self, entity_id: str, data: dict[str, Any]) -> None:
        """
        Save entity data to JSON file.

        Args:
            entity_id: Unique identifier for the entity
            data: Entity data to save
        """

        if self._stage_put(entity_id, data, None):
            return

        def apply(section: dict[str, dict[str, Any]]) -> tuple[bool, None]:
            section[entity_id] = data
            return True, None

        self._mutate(apply, f"save entity {entity_id}")
        self.logger.debug("Saved %s entity: %s", self.entity_type, entity_id)

    def save_versioned(
        self, entity_id: str, data: dict[str, Any], expected_version: Optional[int]
    ) -> int:
        """Save one entity and return the version written.

        With ``expected_version`` the record is written only while the stored
        version still equals it; a record that changed or disappeared raises
        ``ConcurrencyError`` and the file is left untouched. Without it the
        write is an unconditional upsert and stores the entity's own version.
        Inside a transaction the check is made at commit.
        """
        return self.save_versioned_batch([(entity_id, data, expected_version)])[0]

    def save_versioned_batch(
        self, items: list[tuple[str, dict[str, Any], Optional[int]]]
    ) -> list[int]:
        """Save several entities with one file write; nothing is written on conflict."""
        if not items:
            return []
        rows = [
            (entity_id, {**data, "version": self._written_version(data, expected)}, expected)
            for entity_id, data, expected in items
        ]
        versions = [row["version"] for _, row, _ in rows]

        with self.lock_manager.write_lock():
            if self._scope.is_active:
                for entity_id, row, expected in rows:
                    self._stage_put_locked(entity_id, row, expected)
                return versions

        def apply(section: dict[str, dict[str, Any]]) -> tuple[bool, None]:
            for entity_id, _, expected in rows:
                if expected is not None:
                    self._check_expected(entity_id, section.get(entity_id), expected)
            for entity_id, row, _ in rows:
                section[entity_id] = row
            return True, None

        self._mutate(apply, "save entities")
        self.logger.debug("Saved %s %s entities", len(rows), self.entity_type)
        return versions

    @staticmethod
    def _written_version(data: dict[str, Any], expected: Optional[int]) -> int:
        """Version a write stores: the entity's own, and always above the baseline."""
        local = data.get("version")
        local = local if isinstance(local, int) else 0
        return local if expected is None else max(local, expected + 1)

    @staticmethod
    def _check_expected(entity_id: str, stored: Optional[dict[str, Any]], expected: int) -> None:
        """Raise ``ConcurrencyError`` unless the stored record is at ``expected``."""
        version = stored.get("version") if stored is not None else None
        if stored is None or (version if isinstance(version, int) else 0) != expected:
            raise ConcurrencyError(
                f"Concurrent write detected for entity '{entity_id}': the stored record is no "
                f"longer at version {expected} (it was changed or deleted by another writer). "
                f"Re-read the entity, re-apply your change, and retry.",
                details={"entity_id": entity_id, "expected_version": expected},
            )

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
                entity_data = self._lookup(entity_id)

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
                all_data = self._visible_records()
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

        if self._stage_delete([entity_id]):
            return

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
                exists = self._lookup(entity_id) is not None
                self.logger.debug("%s entity %s exists: %s", self.entity_type, entity_id, exists)
                return exists

            except StorageError:
                raise
            except Exception as e:
                self.logger.error(
                    "Failed to check existence of %s entity %s: %s",
                    self.entity_type,
                    entity_id,
                    e,
                )
                raise StorageError(
                    f"Failed to check existence of {self.entity_type} entity {entity_id}: {e}"
                ) from e

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
                all_data = self._visible_records()
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

        with self.lock_manager.write_lock():
            if self._scope.is_active:
                for entity_id, data in entities.items():
                    self._stage_put_locked(entity_id, data, None)
                return

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

        if self._stage_delete(entity_ids):
            return

        def apply(section: dict[str, dict[str, Any]]) -> tuple[bool, None]:
            for entity_id in entity_ids:
                section.pop(entity_id, None)
            return True, None

        self._mutate(apply, "delete batch")
        self.logger.debug("Deleted batch of %s %s entities", len(entity_ids), self.entity_type)

    def join_scope(self, scope: JSONTransactionScope) -> None:
        """Share a commit scope with the other strategies of a unit of work."""
        self._scope = scope
        scope.register(self)

    def begin_transaction(self) -> None:
        """Start buffering writes."""
        self._scope.begin()

    def commit_transaction(self) -> None:
        """Write the buffered changes (of every strategy in the scope) to the files."""
        self._scope.commit()

    def rollback_transaction(self) -> None:
        """Discard the buffered changes."""
        self._scope.rollback()

    def defer_until_commit(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` after the transaction commits; now when there is none."""
        self._scope.defer(callback)

    # -- buffered writes ------------------------------------------------------

    def has_buffered_writes(self) -> bool:
        return bool(self._buffer)

    def has_buffered_puts(self) -> bool:
        return any(staged.op == "put" for staged in self._buffer.values())

    def clear_buffer(self) -> None:
        self._buffer = {}

    def _stage_put(self, entity_id: str, data: dict[str, Any], expected: Optional[int]) -> bool:
        """Buffer a write when a transaction is active; False means write it now."""
        with self.lock_manager.write_lock():
            if not self._scope.is_active:
                return False
            self._stage_put_locked(entity_id, data, expected)
            return True

    def _stage_put_locked(
        self, entity_id: str, data: dict[str, Any], expected: Optional[int]
    ) -> None:
        previous = self._buffer.get(entity_id)
        if (
            previous is not None
            and previous.op == "put"
            and expected is not None
            and expected == cast("dict[str, Any]", previous.data).get("version")
        ):
            # Based on a reload of this transaction's own staged put: the same
            # change continued, still guarded by the earlier write's baseline
            self._buffer[entity_id] = _StagedWrite(
                "put", data, previous.expected, previous.conflict
            )
            return
        # The first baseline guards the state before the transaction; a delete or
        # baseline-less put before it carries none, so the first one given counts
        first = (
            previous.expected
            if previous is not None and previous.expected is not None
            else expected
        )
        conflict = previous is not None and (
            previous.conflict
            or (
                previous.expected is not None
                and expected is not None
                and previous.expected != expected
            )
        )
        self._buffer[entity_id] = _StagedWrite("put", data, first, conflict)

    def _stage_delete(self, entity_ids: list[str]) -> bool:
        with self.lock_manager.write_lock():
            if not self._scope.is_active:
                return False
            for entity_id in entity_ids:
                previous = self._buffer.get(entity_id)
                self._buffer[entity_id] = _StagedWrite(
                    "delete",
                    None,
                    previous.expected if previous is not None else None,
                    previous.conflict if previous is not None else False,
                )
            return True

    def apply_buffer_to(self, section: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """The section with the buffered writes applied, checking their versions first.

        Raises ``ConcurrencyError`` for a version-checked write whose record
        moved on, or for two writes that expected different versions.
        """
        result = dict(section)
        for entity_id, staged in self._buffer.items():
            if staged.conflict:
                raise ConcurrencyError(
                    f"Entity '{entity_id}' was saved from two different stored versions in "
                    f"one unit of work. Re-read the entity, re-apply your change, and retry.",
                    details={"entity_id": entity_id},
                )
            if staged.expected is not None:
                self._check_expected(entity_id, section.get(entity_id), staged.expected)
            if staged.op == "put":
                result[entity_id] = cast("dict[str, Any]", staged.data)
            else:
                result.pop(entity_id, None)
        return result

    # -- reads that see the transaction's own buffered writes -------------------

    def _lookup(self, entity_id: str) -> Optional[dict[str, Any]]:
        """One record: the buffered write first, then the stored record."""
        if self._buffer:
            staged = self._buffer.get(entity_id)
            if staged is not None:
                return staged.data if staged.op == "put" else None
        return self._load_data().get(entity_id)

    def _visible_records(self) -> dict[str, dict[str, Any]]:
        """All records as the transaction sees them (merged only when it has writes)."""
        base = self._load_data()
        if not self._buffer:
            return base
        merged = dict(base)
        for entity_id, staged in self._buffer.items():
            if staged.op == "put":
                merged[entity_id] = cast("dict[str, Any]", staged.data)
            else:
                merged.pop(entity_id, None)
        return merged

    def cleanup(self) -> None:
        """Clean up resources."""
        self._invalidate_cache()
        self._buffer = {}
        self.logger.debug("Cleaned up JSON storage strategy for %s", self.entity_type)

    @instrument_storage(lambda self: cast("JSONStorageStrategy", self)._metrics, "count")
    def count(self) -> int:
        """
        Count total number of entities.
        """
        with self.lock_manager.read_lock():
            try:
                all_data = self._visible_records()
                count = len(all_data)
                self.logger.debug(f"Counted {count} {self.entity_type} entities")
                return count
            except StorageError:
                raise
            except Exception as e:
                self.logger.error(f"Failed to count {self.entity_type} entities: {e}")
                raise StorageError(f"Failed to count {self.entity_type} entities: {e}") from e

    def drop_cache(self) -> None:
        """Forget the cached records (the files changed behind this strategy)."""
        self._invalidate_cache()

    def parse_file(self, content: str) -> dict[str, Any]:
        """The file's full content as a dict (a missing or non-object file is empty)."""
        if not content.strip():
            return {}
        full_data = self.serializer.deserialize(content)
        return full_data if isinstance(full_data, dict) else {}

    def section_of(self, full_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """This entity type's records within the full file content."""
        section = full_data.get(self.entity_type)
        return section if isinstance(section, dict) else {}

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

        file_path = self.file_manager.file_path
        try:
            content, signature = self.file_manager.read_file_with_signature()
            entity_data = self._section_from(content)
        except OSError as e:
            # The file could not be read; it is not known to be damaged
            raise StorageError(f"{file_path}: {e}") from e
        except ValueError as e:
            # The content does not parse (bad JSON or bad encoding)
            self.logger.error("Storage file %s does not parse: %s", file_path, e)
            entity_data, signature = self._recover_from_backup(e)
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"{file_path}: {e}") from e

        self._data_cache = entity_data
        self._cache_signature = signature
        self._cache_valid = True

        return entity_data

    def _recover_from_backup(
        self, error: Exception
    ) -> tuple[dict[str, dict[str, Any]], Optional[tuple[int, int, int, int]]]:
        """Replace a file that does not parse with its newest backup that does.

        Runs under the cross-process lock. The file is re-read first, in case
        another process has rewritten it since. The damaged file is copied
        aside before it is replaced, so records written after the backup can
        still be recovered by hand. When no backup parses the file is left
        untouched and ``StorageError`` is raised.
        """
        file_manager = self.file_manager
        file_path = file_manager.file_path
        with file_manager.exclusive_write_lock():
            try:
                content, signature = file_manager.read_file_with_signature()
                return self._section_from(content), signature
            except OSError as e:
                raise StorageError(f"{file_path}: {e}") from e
            except ValueError:
                pass

            for backup in file_manager.list_backups():
                try:
                    backup_content = backup.read_text(encoding="utf-8")
                    backup_data = self.parse_file(backup_content)
                except (OSError, ValueError) as e:
                    self.logger.warning("Backup %s is not usable: %s", backup, e)
                    continue

                corrupt_copy = self._corrupt_copy_path()
                try:
                    shutil.copy2(file_path, corrupt_copy)
                    file_manager.write_file(backup_content)
                except OSError as e:
                    raise StorageError(
                        f"{file_path}: {error}; restoring backup {backup} failed: {e}"
                    ) from e
                self.logger.warning(
                    "Storage file %s did not parse (%s); restored backup %s. Writes made "
                    "after that backup are not in the restored file; the damaged file "
                    "was kept as %s.",
                    file_path,
                    error,
                    backup,
                    corrupt_copy,
                )
                return self.section_of(backup_data), file_manager.current_signature()

        raise StorageError(
            f"{file_path}: {error}; no valid backup in {file_manager.backup_dir}; "
            "restore one or move the file aside"
        )

    def _corrupt_copy_path(self) -> Path:
        """A free name beside the file for keeping a damaged copy."""
        file_path = self.file_manager.file_path
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate = file_path.with_name(f"{file_path.name}.corrupt-{stamp}")
        suffix = 1
        while candidate.exists():
            candidate = file_path.with_name(f"{file_path.name}.corrupt-{stamp}-{suffix}")
            suffix += 1
        return candidate

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
                with self.file_manager.exclusive_write_lock():
                    content, _ = self.file_manager.read_file_with_signature()
                    full_data = self.parse_file(content)
                    current = self.section_of(full_data)

                    working = dict(current)
                    changed, result = apply(working)
                    if not changed:
                        return result

                    full_data[self.entity_type] = working
                    # The file parsed, so the backup holds exactly the content
                    # about to be replaced (never a damaged file)
                    self.file_manager.create_backup()
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
