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
    migration_lock,
)

pytestmark = pytest.mark.unit


def test_postgres_takes_a_transaction_scoped_advisory_lock():
    connection = MagicMock()
    connection.dialect.name = "postgresql"
    with migration_lock(connection) as taken:
        assert taken is True
    statement, params = connection.execute.call_args.args
    assert "pg_advisory_xact_lock" in str(statement)
    assert params == {"key": MIGRATION_LOCK_KEY}


def test_mysql_holds_and_releases_a_named_lock():
    connection = MagicMock()
    connection.dialect.name = "mysql"
    connection.execute.return_value.scalar.return_value = 1
    with migration_lock(connection) as taken:
        assert taken is True
    statements = [str(c.args[0]) for c in connection.execute.call_args_list]
    assert "GET_LOCK" in statements[0]
    assert "RELEASE_LOCK" in statements[-1]


def test_mysql_releases_the_lock_when_the_migration_fails():
    connection = MagicMock()
    connection.dialect.name = "mysql"
    connection.execute.return_value.scalar.return_value = 1

    def fail() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError), migration_lock(connection):
        fail()
    assert "RELEASE_LOCK" in str(connection.execute.call_args.args[0])


def test_mysql_lock_timeout_aborts_the_migration():
    connection = MagicMock()
    connection.dialect.name = "mysql"
    connection.execute.return_value.scalar.return_value = 0
    with pytest.raises(RuntimeError), migration_lock(connection):
        pytest.fail("body must not run without the lock")


def test_sqlite_takes_no_lock():
    connection = MagicMock()
    connection.dialect.name = "sqlite"
    with migration_lock(connection) as taken:
        assert taken is False
    connection.execute.assert_not_called()


def test_env_py_takes_the_lock_inside_the_migration_transaction():
    root = Path(__file__).resolve().parents[3]
    env = (root / "src/orb/infrastructure/storage/sql/migrations/env.py").read_text()
    assert "with context.begin_transaction(), migration_lock(connection):" in env


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
