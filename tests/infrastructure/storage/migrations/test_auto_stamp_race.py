"""Tests for the auto-stamp race condition fix (T6).

Strategy: simulate multiple concurrent workers calling _auto_stamp_head()
simultaneously via threads, then assert that the alembic_version table ends
up with exactly ONE row (no duplicates, no missing row).

The test also verifies the single-worker path: that a fresh pre-existing
install is stamped correctly and subsequent calls are no-ops.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

# ---------------------------------------------------------------------------
# Locate the strategy module
# ---------------------------------------------------------------------------
_SRC_DIR = Path(__file__).parent.parent.parent.parent.parent / "src"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(url: str) -> sa.Engine:
    return sa.create_engine(url, echo=False)


def _create_app_tables(engine: sa.Engine) -> None:
    """Create the minimal application tables (no alembic_version) to simulate
    a pre-existing install that predates Alembic management."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS machines ("
                "  machine_id    VARCHAR(255) PRIMARY KEY,"
                "  instance_type VARCHAR(50)  NOT NULL,"
                "  image_id      VARCHAR(255) NOT NULL,"
                "  template_id   VARCHAR(255) NOT NULL,"
                "  provider_api  VARCHAR(255) NOT NULL,"
                "  provider_name VARCHAR(255) NOT NULL,"
                "  version       INTEGER NOT NULL DEFAULT 0"
                ")"
            )
        )
        conn.commit()


def _stamp_count(engine: sa.Engine) -> int:
    """Return the number of rows in alembic_version (0 if table absent)."""
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT COUNT(*) FROM alembic_version")).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _stamp_value(engine: sa.Engine) -> str | None:
    """Return the version_num from alembic_version (None if absent)."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT version_num FROM alembic_version LIMIT 1")
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Build a minimal SQLStorageStrategy-like object that exposes _auto_stamp_head
# without requiring a full DI container.
# ---------------------------------------------------------------------------


def _make_strategy(engine: sa.Engine) -> Any:
    """Import SQLStorageStrategy and return an instance wired to *engine*."""
    import sys

    sys.path.insert(0, str(_SRC_DIR))
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    # Patch _initialize_table so the constructor doesn't open the DB.
    with patch.object(SQLStorageStrategy, "_initialize_table"):
        strategy = SQLStorageStrategy.__new__(SQLStorageStrategy)

    strategy.table_name = "machines"
    strategy.columns = {"machine_id": "VARCHAR(255) PRIMARY KEY", "provider_api": "VARCHAR(255)"}

    # Minimal logger stub.
    import logging

    strategy.logger = logging.getLogger("test.auto_stamp")

    # Inject a connection manager that returns our engine.
    mock_cm = MagicMock()
    mock_cm.get_engine.return_value = engine
    strategy.connection_manager = mock_cm

    return strategy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pre_existing_db():
    """Temp-file SQLite DB with app tables but NO alembic_version; yields engine."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{db_path}"
    engine = _make_engine(url)
    _create_app_tables(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except OSError:
            # Best-effort teardown: tmpfile may be gone already.
            return


# ---------------------------------------------------------------------------
# Single-worker tests
# ---------------------------------------------------------------------------


class TestAutoStampSingleWorker:
    """Verify _auto_stamp_head works correctly in the simple (non-concurrent) case."""

    def test_stamps_head_revision(self, pre_existing_db):
        """A single call must insert a version_num into alembic_version."""
        strategy = _make_strategy(pre_existing_db)
        strategy._auto_stamp_head(pre_existing_db)

        assert _stamp_count(pre_existing_db) == 1

    def test_stamps_known_head_revision(self, pre_existing_db):
        """The inserted revision must equal the current Alembic head."""
        strategy = _make_strategy(pre_existing_db)
        strategy._auto_stamp_head(pre_existing_db)

        value = _stamp_value(pre_existing_db)
        assert value == "55e3b5553d98", f"Expected head revision 55e3b5553d98 but got {value!r}"

    def test_second_call_is_no_op(self, pre_existing_db):
        """Calling _auto_stamp_head twice must not create a second row."""
        strategy = _make_strategy(pre_existing_db)
        strategy._auto_stamp_head(pre_existing_db)
        strategy._auto_stamp_head(pre_existing_db)  # second call

        assert _stamp_count(pre_existing_db) == 1

    def test_already_stamped_db_is_skipped(self, pre_existing_db):
        """If alembic_version already has a row, the stamp must be skipped."""
        # Pre-insert a row to simulate another worker having stamped first.
        with pre_existing_db.connect() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            conn.execute(sa.text("INSERT INTO alembic_version VALUES ('somerev')"))
            conn.commit()

        strategy = _make_strategy(pre_existing_db)
        strategy._auto_stamp_head(pre_existing_db)

        # Must not change the existing row.
        assert _stamp_count(pre_existing_db) == 1
        assert _stamp_value(pre_existing_db) == "somerev"


# ---------------------------------------------------------------------------
# Multi-worker concurrency tests
# ---------------------------------------------------------------------------


class TestAutoStampConcurrentWorkers:
    """Verify that N concurrent workers produce exactly ONE alembic_version row."""

    def _run_n_workers(self, engine: sa.Engine, n: int = 5) -> list[Exception | None]:
        """Spawn *n* threads each calling _auto_stamp_head and collect exceptions."""
        errors: list[Exception | None] = [None] * n
        barrier = threading.Barrier(n)

        def worker(idx: int) -> None:
            strategy = _make_strategy(engine)
            try:
                barrier.wait()  # synchronise all workers at the start line
                strategy._auto_stamp_head(engine)
            except Exception as exc:
                errors[idx] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        return errors

    def test_exactly_one_row_after_concurrent_stamp(self, pre_existing_db):
        """N concurrent workers must produce exactly one alembic_version row."""
        errors = self._run_n_workers(pre_existing_db, n=5)

        # All workers must have completed (no join timeout left a thread running).
        count = _stamp_count(pre_existing_db)
        assert count == 1, (
            f"Expected exactly 1 alembic_version row after concurrent stamp, got {count}. "
            f"Worker errors: {[e for e in errors if e is not None]}"
        )

    def test_revision_is_correct_after_concurrent_stamp(self, pre_existing_db):
        """The stamped revision must equal the Alembic head after concurrent writes."""
        self._run_n_workers(pre_existing_db, n=5)
        value = _stamp_value(pre_existing_db)
        assert value == "55e3b5553d98"

    def test_workers_do_not_raise_on_contention(self, pre_existing_db):
        """Workers that lose the race must log INFO and return cleanly (no exception)."""
        errors = self._run_n_workers(pre_existing_db, n=5)
        failing = [e for e in errors if e is not None]
        assert not failing, f"Some workers raised unexpected exceptions: {failing}"
