"""Serialises schema migrations across concurrent runs.

``orb storage migrate up`` can be started more than once at the same time (two
Helm upgrades, a hook retry overlapping a manual run). On PostgreSQL the
migration transaction takes a transaction-scoped advisory lock first, so a second
runner waits, then finds the schema already at head and changes nothing.
"""

from __future__ import annotations

from typing import Any

# Arbitrary constant shared by every ORB migration runner.
MIGRATION_LOCK_KEY = 7_411_902_025_314_001


def acquire_migration_lock(connection: Any) -> bool:
    """Take the migration lock for the current transaction. Returns True if one was taken.

    Only PostgreSQL has advisory locks; SQLite serialises writers itself.
    """
    if connection.dialect.name != "postgresql":
        return False
    from sqlalchemy import text

    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
    return True
