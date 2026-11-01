"""Serialises schema migrations across concurrent runs.

``orb storage migrate up`` can be started more than once at the same time (two
Helm upgrades, a hook retry overlapping a manual run). A second runner waits for
the first, then finds the schema already at head and changes nothing.

- PostgreSQL: transaction-scoped advisory lock, released when the migration
  transaction ends.
- MySQL: named ``GET_LOCK`` held on the migration connection and released
  afterwards. MySQL DDL commits implicitly, so a transaction-scoped lock is not
  available.
- SQLite: writers are already serialised by the database file lock.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# Arbitrary constant shared by every ORB migration runner.
MIGRATION_LOCK_KEY = 7_411_902_025_314_001
MYSQL_LOCK_NAME = "orb_schema_migration"
MYSQL_LOCK_TIMEOUT_SECONDS = 600


@contextmanager
def migration_lock(connection: Any) -> Iterator[bool]:
    """Hold the migration lock for the body. Yields True if a lock was taken."""
    from sqlalchemy import text

    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        yield True
    elif dialect in ("mysql", "mariadb"):
        got = connection.execute(
            text("SELECT GET_LOCK(:name, :timeout)"),
            {"name": MYSQL_LOCK_NAME, "timeout": MYSQL_LOCK_TIMEOUT_SECONDS},
        ).scalar()
        if got != 1:
            raise RuntimeError("Timed out waiting for another schema migration to finish")
        try:
            yield True
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": MYSQL_LOCK_NAME})
    else:
        yield False
