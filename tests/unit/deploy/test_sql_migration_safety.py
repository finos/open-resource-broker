"""Safety properties of the SQL migration path used by the chart's hook."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orb.config.schemas.storage_schema import SqlStrategyConfig
from orb.infrastructure.storage.sql.migration_lock import (
    MIGRATION_LOCK_KEY,
    acquire_migration_lock,
)

pytestmark = pytest.mark.unit


def test_postgres_takes_a_transaction_scoped_advisory_lock():
    connection = MagicMock()
    connection.dialect.name = "postgresql"
    assert acquire_migration_lock(connection) is True
    statement, params = connection.execute.call_args.args
    assert "pg_advisory_xact_lock" in str(statement)
    assert params == {"key": MIGRATION_LOCK_KEY}


def test_sqlite_takes_no_lock():
    connection = MagicMock()
    connection.dialect.name = "sqlite"
    assert acquire_migration_lock(connection) is False
    connection.execute.assert_not_called()


def test_env_py_takes_the_lock_inside_the_migration_transaction():
    root = Path(__file__).resolve().parents[3]
    env = (root / "src/orb/infrastructure/storage/sql/migrations/env.py").read_text()
    begin = env.index("with context.begin_transaction():\n            # Held")
    assert env.index("acquire_migration_lock(connection)", begin) < env.index(
        "context.run_migrations()", begin
    )


def test_upgrade_head_is_idempotent_and_safe_to_repeat(tmp_path):
    pytest.importorskip("alembic")
    import subprocess
    import sys

    import orb

    ini = Path(orb.__file__).parent / "infrastructure/storage/sql/migrations/alembic.ini"
    db = tmp_path / "m.db"
    env = {**os.environ, "ORB_SQL_URL": f"sqlite:///{db}"}

    def upgrade() -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "alembic", "--config", str(ini), "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    first, second = upgrade(), upgrade()
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*) from alembic_version").fetchone()[0] == 1


def test_password_is_not_part_of_the_config_repr():
    cfg = SqlStrategyConfig(
        type="postgresql", host="db", port=5432, name="orb", username="orb", password="s3cr3t-pw"
    )
    assert "s3cr3t-pw" not in repr(cfg)
    assert "s3cr3t-pw" not in str(cfg)
