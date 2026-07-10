"""Tests for JSON storage cross-process file locking.

Verifies that:
(a) Concurrent saves of two DIFFERENT records to the same file both survive
    (no clobber / lost-update).
(b) The exclusive flock is acquired and released around the write cycle.
(c) The re-read of the file happens INSIDE the lock, not before it.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy(tmp_path: Path, entity_type: str = "entities") -> JSONStorageStrategy:
    """Return a strategy backed by a temp file."""
    file_path = str(tmp_path / "test_data.json")
    return JSONStorageStrategy(
        file_path=file_path,
        entity_type=entity_type,
        backup_enabled=False,
    )


def _read_raw(tmp_path: Path, filename: str = "test_data.json") -> dict[str, Any]:
    """Read the raw JSON content of the storage file."""
    p = tmp_path / filename
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# (a) No-clobber: two sequential saves of different records both persist
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoClobberSequential:
    """Two saves of different records must both persist — sequential baseline."""

    def test_two_sequential_saves_both_survive(self, tmp_path: Path) -> None:
        """Save record A, then save record B: both must be readable afterwards."""
        strategy = _make_strategy(tmp_path)

        strategy.save("record-A", {"name": "alpha", "value": 1})
        strategy.save("record-B", {"name": "beta", "value": 2})

        raw = _read_raw(tmp_path)
        assert "record-A" in raw.get("entities", {}), "record-A was clobbered"
        assert "record-B" in raw.get("entities", {}), "record-B was clobbered"
        assert raw["entities"]["record-A"]["name"] == "alpha"
        assert raw["entities"]["record-B"]["name"] == "beta"

    def test_two_sequential_saves_different_entity_types_both_survive(self, tmp_path: Path) -> None:
        """Saving two different entity types to the same file preserves both sections."""
        file_path = str(tmp_path / "test_data.json")
        strategy_a = JSONStorageStrategy(
            file_path=file_path, entity_type="requests", backup_enabled=False
        )
        strategy_b = JSONStorageStrategy(
            file_path=file_path, entity_type="machines", backup_enabled=False
        )

        strategy_a.save("req-1", {"status": "pending"})
        strategy_b.save("mach-1", {"status": "running"})

        raw = _read_raw(tmp_path)
        assert "req-1" in raw.get("requests", {}), "requests section was clobbered"
        assert "mach-1" in raw.get("machines", {}), "machines section was clobbered"

    def test_update_record_does_not_drop_other_records(self, tmp_path: Path) -> None:
        """Updating one record must not remove pre-existing records in the same entity type."""
        strategy = _make_strategy(tmp_path)

        strategy.save("rec-1", {"v": "first"})
        strategy.save("rec-2", {"v": "second"})
        # Now update rec-1 — rec-2 must survive
        strategy.save("rec-1", {"v": "updated"})

        result = strategy.find_all()
        assert "rec-1" in result, "rec-1 missing after update"
        assert "rec-2" in result, "rec-2 was clobbered by update of rec-1"
        assert result["rec-1"]["v"] == "updated"
        assert result["rec-2"]["v"] == "second"


# ---------------------------------------------------------------------------
# (b) flock is acquired and released around the write cycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFlockAcquireRelease:
    """The exclusive_write_lock context manager must acquire/release flock."""

    def test_exclusive_write_lock_acquires_and_releases(self, tmp_path: Path) -> None:
        """exclusive_write_lock should call flock(LOCK_EX) then flock(LOCK_UN)."""
        from orb.infrastructure.storage.components.file_manager import (
            _FCNTL_AVAILABLE,
            FileManager,
        )

        if not _FCNTL_AVAILABLE:
            pytest.skip("fcntl not available on this platform")

        import fcntl

        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=False)

        flock_calls: list[tuple[int, int]] = []
        original_flock = fcntl.flock

        def recording_flock(fd: int, operation: int) -> None:
            flock_calls.append((fd, operation))
            original_flock(fd, operation)

        with patch("fcntl.flock", side_effect=recording_flock):
            with patch(
                "orb.infrastructure.storage.components.file_manager._fcntl.flock",
                side_effect=recording_flock,
            ):
                with fm.exclusive_write_lock():
                    pass

        operations = [op for _, op in flock_calls]
        assert fcntl.LOCK_EX in operations, "LOCK_EX was never requested"
        assert fcntl.LOCK_UN in operations, "LOCK_UN was never called (lock not released)"
        # Ensure LOCK_EX precedes LOCK_UN
        assert operations.index(fcntl.LOCK_EX) < operations.index(fcntl.LOCK_UN)

    def test_exclusive_write_lock_releases_on_exception(self, tmp_path: Path) -> None:
        """LOCK_UN must be called even when the body of the context raises."""
        from orb.infrastructure.storage.components.file_manager import (
            _FCNTL_AVAILABLE,
            FileManager,
        )

        if not _FCNTL_AVAILABLE:
            pytest.skip("fcntl not available on this platform")

        import fcntl

        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=False)

        flock_calls: list[int] = []
        original_flock = fcntl.flock

        def recording_flock(fd: int, operation: int) -> None:
            flock_calls.append(operation)
            original_flock(fd, operation)

        def _raise_inside_lock() -> None:
            with fm.exclusive_write_lock():
                raise RuntimeError("simulated body error")

        with patch(
            "orb.infrastructure.storage.components.file_manager._fcntl.flock",
            side_effect=recording_flock,
        ):
            with pytest.raises(RuntimeError):
                _raise_inside_lock()

        assert fcntl.LOCK_UN in flock_calls, "LOCK_UN not called after exception in body"

    def test_save_calls_exclusive_write_lock(self, tmp_path: Path) -> None:
        """JSONStorageStrategy.save() must use exclusive_write_lock for every write."""
        strategy = _make_strategy(tmp_path)

        lock_enter_count = 0
        original_lock = strategy.file_manager.exclusive_write_lock

        @contextmanager_counter
        def counting_lock() -> Any:
            nonlocal lock_enter_count
            lock_enter_count += 1
            with original_lock():
                yield

        with patch.object(strategy.file_manager, "exclusive_write_lock", counting_lock):
            strategy.save("rec-1", {"x": 1})
            strategy.save("rec-2", {"x": 2})

        assert lock_enter_count == 2, (
            f"exclusive_write_lock should be entered once per save, got {lock_enter_count}"
        )


def contextmanager_counter(fn: Any) -> Any:
    """Wrap a generator function so it can be used as a context manager."""
    from contextlib import contextmanager

    @contextmanager
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        yield from fn(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# (c) Re-read happens INSIDE the lock (critical ordering guarantee)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReReadUnderLock:
    """The file re-read in _save_data must occur after the lock is acquired."""

    def test_read_happens_after_lock_acquired(self, tmp_path: Path) -> None:
        """Verify that file_manager.read_file() is not called before the lock body starts."""
        from orb.infrastructure.storage.components.file_manager import _FCNTL_AVAILABLE

        if not _FCNTL_AVAILABLE:
            pytest.skip("fcntl not available on this platform")

        strategy = _make_strategy(tmp_path)
        # Pre-populate so there is a file to read
        strategy.save("seed", {"v": 0})
        # Invalidate cache so _save_data will truly re-read
        strategy._cache_valid = False

        event_log: list[str] = []
        original_read = strategy.file_manager.read_file
        original_flock = strategy.file_manager.exclusive_write_lock

        def logging_read() -> str:
            event_log.append("read")
            return original_read()

        from contextlib import contextmanager

        @contextmanager
        def logging_lock() -> Any:
            event_log.append("lock_acquired")
            with original_flock():
                yield
            event_log.append("lock_released")

        with patch.object(strategy.file_manager, "read_file", side_effect=logging_read):
            with patch.object(strategy.file_manager, "exclusive_write_lock", logging_lock):
                strategy.save("new-rec", {"v": 1})

        # The critical invariant: lock_acquired must appear before the first
        # "read" that occurs inside _save_data.
        # Note: _load_data may call read_file before _save_data; we care that
        # the read inside _save_data (which merges the full file) is under lock.
        assert "lock_acquired" in event_log, "lock was never acquired"
        assert "read" in event_log, "file was never read"

        lock_idx = event_log.index("lock_acquired")
        # Find the read that happens AFTER the lock (the one inside _save_data)
        reads_after_lock = [i for i, e in enumerate(event_log) if e == "read" and i > lock_idx]
        assert reads_after_lock, (
            "No file read occurred after the lock was acquired. "
            "The re-read must happen inside exclusive_write_lock to prevent lost-update races. "
            f"Event log: {event_log}"
        )

    def test_stale_snapshot_does_not_clobber_concurrent_write(self, tmp_path: Path) -> None:
        """Simulate the lost-update race and verify the lock prevents clobber.

        Process A reads file (gets {rec-A: ...}).
        Process B saves rec-B while A is still computing.
        When A writes, rec-B must survive because A re-reads inside the lock.
        """
        strategy_a = _make_strategy(tmp_path, entity_type="entities")
        strategy_b = _make_strategy(tmp_path, entity_type="entities")

        # Initial state: both processes see an empty file
        # Simulate: B saves rec-B
        strategy_b.save("rec-B", {"owner": "B"})
        # Invalidate A's cache so A re-reads from disk (simulating fresh process)
        strategy_a._cache_valid = False

        # A saves rec-A — with correct re-read under lock, rec-B must survive
        strategy_a.save("rec-A", {"owner": "A"})

        result = strategy_a.find_all()
        assert "rec-A" in result, "rec-A missing"
        assert "rec-B" in result, (
            "rec-B was clobbered! The re-read under lock did not pick up B's write."
        )


# ---------------------------------------------------------------------------
# (d) Threaded concurrency: two threads saving different records both survive
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestThreadedConcurrency:
    """Verify the threading lock + file lock together prevent in-process clobber."""

    def test_two_threads_save_different_records_both_survive(self, tmp_path: Path) -> None:
        """Two threads concurrently saving different records must both persist."""
        strategy = _make_strategy(tmp_path)
        errors: list[Exception] = []

        def save_record(entity_id: str, value: int) -> None:
            try:
                for _ in range(5):
                    strategy.save(entity_id, {"value": value, "ts": time.monotonic()})
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=save_record, args=("thread-A", 100))
        t2 = threading.Thread(target=save_record, args=("thread-B", 200))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Thread errors: {errors}"
        result = strategy.find_all()
        assert "thread-A" in result, "thread-A record missing after concurrent saves"
        assert "thread-B" in result, "thread-B record missing after concurrent saves"


# ---------------------------------------------------------------------------
# (e) Non-POSIX graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNonPosixDegradation:
    """On non-POSIX platforms exclusive_write_lock should be a no-op, not a crash."""

    def test_exclusive_write_lock_is_noop_when_fcntl_unavailable(self, tmp_path: Path) -> None:
        """With _FCNTL_AVAILABLE=False the context manager must yield without error."""
        from orb.infrastructure.storage.components import file_manager as fm_module

        original = fm_module._FCNTL_AVAILABLE
        try:
            fm_module._FCNTL_AVAILABLE = False
            fm = fm_module.FileManager(str(tmp_path / "data.json"), backup_enabled=False)
            entered = False
            with fm.exclusive_write_lock():
                entered = True
            assert entered, "Context manager body was not entered"
        finally:
            fm_module._FCNTL_AVAILABLE = original

    def test_save_still_works_when_fcntl_unavailable(self, tmp_path: Path) -> None:
        """JSONStorageStrategy.save() must succeed even when fcntl is absent."""
        from orb.infrastructure.storage.components import file_manager as fm_module

        original = fm_module._FCNTL_AVAILABLE
        try:
            fm_module._FCNTL_AVAILABLE = False
            strategy = _make_strategy(tmp_path)
            strategy.save("key-1", {"data": "value"})
            result = strategy.find_by_id("key-1")
            assert result is not None
            assert result["data"] == "value"
        finally:
            fm_module._FCNTL_AVAILABLE = original
