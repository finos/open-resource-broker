"""Commit scope shared by the JSON strategies of one unit of work.

While a scope is active its strategies buffer their writes instead of touching
the files. Rolling back discards the buffers; committing validates every
buffered version-checked write against the current file contents and only then
writes, so a conflict anywhere writes nothing.

Locking order, always the same so that processes and threads cannot form a
cycle: first the in-process write locks of all involved strategies (sorted by
real path, then entity type), then one file lock per distinct real path in
sorted order. Strategies that share a file (single-file mode) are committed with
one read and one write of that file, and that file is locked once.

Residual risk, documented rather than hidden: there is no atomic rename across
files and no directory fsync. A failure while writing a later file restores the
earlier ones from their pre-images; only a process crash (or a failing restore)
can leave files from different strategies out of step. "Committed" means
renamed, not durable.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any, Optional

from orb.domain.base.exceptions import ConcurrencyError
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.exceptions import StorageError

# The strategy module imports this one, so the strategy type is not imported here
JSONStorageStrategy = Any

# The request file anchors the others: written first when the commit puts rows,
# last when it only deletes them, so a partial failure never leaves rows that
# refer to a missing request.
_ANCHOR_ENTITY = "requests"


class JSONTransactionScope:
    """Buffered commit shared by a set of JSON strategies."""

    def __init__(self) -> None:
        self._strategies: list[JSONStorageStrategy] = []
        self._after_commit: list[Callable[[], None]] = []
        self._active = False
        self.logger = get_logger(__name__)

    @property
    def is_active(self) -> bool:
        """True while writes are buffered."""
        return self._active

    def register(self, strategy: JSONStorageStrategy) -> None:
        """Make ``strategy`` part of this scope."""
        if strategy not in self._strategies:
            self._strategies.append(strategy)

    def begin(self) -> None:
        if self._active:
            raise RuntimeError("Transaction already active")
        self._active = True
        self._discard()

    def defer(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` after the commit; immediately when nothing is buffered."""
        if self._active:
            self._after_commit.append(callback)
        else:
            callback()

    def rollback(self) -> None:
        """Discard every buffered write."""
        if not self._active:
            self.logger.warning("No active JSON transaction to rollback")
            return
        self._active = False
        self._discard()

    def commit(self) -> None:
        """Apply the buffered writes of all strategies, or none of them."""
        if not self._active:
            raise RuntimeError("No active transaction to commit")
        callbacks = list(self._after_commit)
        try:
            pending = [s for s in self._strategies if s.has_buffered_writes()]
            if pending:
                self._apply(pending)
        finally:
            # A failed commit ends the transaction; the buffers are not replayed
            self._active = False
            self._discard()
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:  # the commit itself succeeded
                self.logger.error("Post-commit callback failed: %s", type(exc).__name__)

    def _discard(self) -> None:
        for strategy in self._strategies:
            strategy.clear_buffer()
        self._after_commit.clear()

    def _apply(self, pending: list[JSONStorageStrategy]) -> None:
        ordered = sorted(pending, key=lambda s: (s.file_manager.real_path, s.entity_type))
        groups: dict[str, list[JSONStorageStrategy]] = {}
        for strategy in ordered:
            groups.setdefault(strategy.file_manager.real_path, []).append(strategy)
        has_puts = any(s.has_buffered_puts() for s in ordered)

        with ExitStack() as locks:
            for strategy in ordered:
                locks.enter_context(strategy.lock_manager.write_lock())
            for strategies in groups.values():
                locks.enter_context(strategies[0].file_manager.exclusive_write_lock())

            images: dict[str, Optional[str]] = {}
            plans: dict[str, dict[str, Any]] = {}
            try:
                for path, strategies in groups.items():
                    file_manager = strategies[0].file_manager
                    content, signature = file_manager.read_file_with_signature()
                    images[path] = content if signature is not None else None
                    full_data = strategies[0].parse_file(content)
                    for strategy in strategies:
                        full_data[strategy.entity_type] = strategy.apply_buffer_to(
                            strategy.section_of(full_data)
                        )
                    plans[path] = full_data
            except ConcurrencyError:
                # What the strategies cached is evidently behind the files
                for strategy in ordered:
                    strategy.drop_cache()
                raise

            # Every file parsed, so each backup is the exact content about to be
            # replaced (a corrupt file is never copied into the backup ring)
            for strategies in groups.values():
                strategies[0].file_manager.create_backup()

            # Every buffered write has been validated; from here on files are written
            def holds_anchor(path: str) -> bool:
                return any(s.entity_type == _ANCHOR_ENTITY for s in groups[path])

            order = sorted(
                plans,
                key=lambda p: (holds_anchor(p) != has_puts, p),
            )
            written: list[str] = []
            try:
                for path in order:
                    strategy = groups[path][0]
                    strategy.file_manager.write_file(strategy.serializer.serialize(plans[path]))
                    written.append(path)
            except Exception as exc:
                self._restore(written, groups, images)
                raise StorageError("Failed to write the unit of work to storage") from exc
            finally:
                for strategy in ordered:
                    strategy.drop_cache()

    def _restore(
        self,
        written: list[str],
        groups: dict[str, list[JSONStorageStrategy]],
        images: dict[str, Optional[str]],
    ) -> None:
        """Put already replaced files back as they were (best effort)."""
        for path in reversed(written):
            file_manager = groups[path][0].file_manager
            try:
                image = images[path]
                if image is None:
                    if os.path.exists(file_manager.file_path):
                        os.unlink(file_manager.file_path)
                else:
                    file_manager.write_file(image)
            except Exception as exc:
                self.logger.error(
                    "Could not restore %s after a failed commit: %s",
                    file_manager.file_path.name,
                    type(exc).__name__,
                )
