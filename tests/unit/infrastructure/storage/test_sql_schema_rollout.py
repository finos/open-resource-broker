"""Existing SQL databases reach the current schema, or are told how to."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import alembic.command
import alembic.config
import pytest
from sqlalchemy import create_engine, inspect, text

import orb
from orb.infrastructure.storage.sql.registration import create_sql_entity_strategy
from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

OLD_REVISION = "55e3b5553d98"
HEAD = "b7d41c9e2a60"
INI = os.path.join(
    os.path.dirname(os.path.abspath(orb.__file__)),
    "infrastructure",
    "storage",
    "sql",
    "migrations",
    "alembic.ini",
)


@pytest.fixture
def keep_logging_config():
    """alembic's env.py reconfigures logging and disables existing loggers."""
    import logging

    loggers = [
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    state = [(logger, logger.disabled, logger.level, logger.propagate) for logger in loggers]
    root_level, root_handlers = logging.root.level, list(logging.root.handlers)
    yield
    for logger, disabled, level, propagate in state:
        logger.disabled, logger.level, logger.propagate = disabled, level, propagate
    logging.root.setLevel(root_level)
    logging.root.handlers[:] = root_handlers


_capture: list[logging.Handler] = []


@pytest.fixture(autouse=True)
def _fresh_warning_state(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(SQLStorageStrategy, "_schema_checked", set())
    _capture[:] = [caplog.handler]


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "orb.db"
    monkeypatch.setenv("ORB_SQL_URL", f"sqlite:///{path}")
    return path


def _migrate_to(revision: str) -> None:
    alembic.command.upgrade(alembic.config.Config(INI), revision)
    # alembic's env.py reconfigures logging: it replaces the root handlers (including
    # the one capturing this test's logs) and disables the loggers that exist
    logging.root.handlers[:] = [*logging.root.handlers, *_capture]
    logging.root.setLevel(logging.DEBUG)
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("orb."):
            existing = logging.root.manager.loggerDict[name]
            if isinstance(existing, logging.Logger):
                existing.disabled = False


def _open(path: Path) -> None:
    config = {"storage": {"strategy": "sql", "sql_strategy": {"type": "sqlite", "name": str(path)}}}
    create_sql_entity_strategy(config, "machines")


def _launch_time_type(path: Path) -> str:
    engine = create_engine(f"sqlite:///{path}")
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("machines")}
        return str(columns["launch_time"]["type"]).upper()
    finally:
        engine.dispose()


def _scalar(path: Path, sql: str) -> object:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def _execute(path: Path, sql: str) -> None:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def _insert_machine(path: Path, launch_time: str) -> None:
    _execute(
        path,
        "INSERT INTO machines (machine_id, instance_type, provider_api, provider_name,"
        " template_id, image_id, version, launch_time)"
        f" VALUES ('i-1', 't3', 'RunInstances', 'aws', 't', 'ami-1', 0, '{launch_time}')",
    )


@pytest.mark.unit
class TestDatabaseWithoutAVersion:
    """Tables from before Alembic managed the schema are stamped at the matching revision."""

    def test_old_layout_is_stamped_then_upgraded_to_head(
        self, database: Path, keep_logging_config: None
    ) -> None:
        _migrate_to(OLD_REVISION)
        _insert_machine(database, "2026-03-01T12:00:00Z")
        _execute(database, "DROP TABLE alembic_version")
        assert _launch_time_type(database) == "BIGINT"

        _open(database)

        assert _launch_time_type(database) == "TEXT"
        assert _scalar(database, "SELECT version_num FROM alembic_version") == HEAD
        assert _scalar(database, "SELECT launch_time FROM machines") == "2026-03-01T12:00:00Z"

    def test_migrate_up_is_a_no_op_afterwards(
        self, database: Path, keep_logging_config: None
    ) -> None:
        _migrate_to(OLD_REVISION)
        _execute(database, "DROP TABLE alembic_version")
        _open(database)

        _migrate_to("head")  # must not try to repeat the conversion

        assert _launch_time_type(database) == "TEXT"

    def test_current_layout_is_stamped_at_head_without_migrating(
        self, database: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _open(database)  # a new database: tables created from the model, no version row
        assert (
            _scalar(database, "SELECT COUNT(*) FROM sqlite_master WHERE name='alembic_version'")
            == 0
        )

        with caplog.at_level(logging.INFO):
            _open(database)

        assert _scalar(database, "SELECT version_num FROM alembic_version") == HEAD
        assert "Upgraded the SQL schema" not in caplog.text

    def test_a_failing_upgrade_warns_and_leaves_startup_alone(
        self,
        database: Path,
        keep_logging_config: None,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import alembic.script

        _migrate_to(OLD_REVISION)
        _execute(database, "DROP TABLE alembic_version")

        def broken(*_a: object, **_k: object) -> None:
            raise RuntimeError("no migration today")

        monkeypatch.setattr(alembic.script.ScriptDirectory, "_upgrade_revs", broken)
        with caplog.at_level(logging.WARNING):
            _open(database)

        assert "orb storage migrate up" in caplog.text
        assert _launch_time_type(database) == "BIGINT"


@pytest.mark.unit
class TestVersionedDatabaseBehindHead:
    """A database with a version is never upgraded behind the operator's back."""

    def test_warns_naming_both_revisions_and_the_command(
        self, database: Path, keep_logging_config: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        _migrate_to(OLD_REVISION)

        with caplog.at_level(logging.WARNING):
            _open(database)

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        message = next(w for w in warnings if "pending migrations" in w)
        assert OLD_REVISION in message and HEAD in message
        assert "orb storage migrate up" in message
        assert _scalar(database, "SELECT version_num FROM alembic_version") == OLD_REVISION
        assert _launch_time_type(database) == "BIGINT"

    def test_warns_once_per_database_not_once_per_table(
        self, database: Path, keep_logging_config: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        _migrate_to(OLD_REVISION)
        config = {
            "storage": {
                "strategy": "sql",
                "sql_strategy": {"type": "sqlite", "name": str(database)},
            }
        }

        with caplog.at_level(logging.WARNING):
            for table in ("requests", "machines", "templates"):
                create_sql_entity_strategy(config, table)

        assert caplog.text.count("pending migrations") == 1

    def test_loads_the_migration_scripts_once_per_database(
        self, database: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import alembic.script

        _migrate_to("head")
        loads: list[int] = []
        original = alembic.script.ScriptDirectory.from_config

        def counting(*args: Any, **kwargs: Any) -> Any:
            loads.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(alembic.script.ScriptDirectory, "from_config", counting)
        config = {
            "storage": {
                "strategy": "sql",
                "sql_strategy": {"type": "sqlite", "name": str(database)},
            }
        }

        for _ in range(3):
            for table in ("requests", "machines", "templates"):
                create_sql_entity_strategy(config, table)

        assert len(loads) == 1

    def test_no_warning_at_head(
        self, database: Path, keep_logging_config: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        _migrate_to("head")

        with caplog.at_level(logging.WARNING):
            _open(database)

        assert "pending migrations" not in caplog.text


class _RecordingConnection:
    """A SQLAlchemy-like connection that records the statements run on it."""

    def __init__(self, dialect: object, existing: object = None) -> None:
        self.dialect = dialect
        self.statements: list[tuple[str, object]] = []
        self.committed = False
        self._existing = existing

    def __enter__(self) -> _RecordingConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, statement: object, params: object = None) -> object:
        from unittest.mock import MagicMock

        self.statements.append((str(statement), params))
        result = MagicMock()
        result.first.return_value = self._existing
        return result

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        return None


@pytest.mark.unit
class TestPostgresStampWithPg8000:
    """The stamp works on the pg8000 driver, which has no psycopg2-only calls."""

    def _engine(self, existing: object = None) -> tuple[object, _RecordingConnection]:
        from unittest.mock import MagicMock

        import pg8000.dbapi
        from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000

        connection = _RecordingConnection(PGDialect_pg8000(), existing)
        engine = MagicMock()
        engine.dialect = connection.dialect
        engine.url = "postgresql+pg8000://orb@db.example/orb"
        # A pg8000 DBAPI connection: no set_isolation_level
        engine.raw_connection.return_value = MagicMock(spec=pg8000.dbapi.Connection)
        engine.connect.return_value = connection
        return engine, connection

    def _strategy(self) -> SQLStorageStrategy:
        from unittest.mock import MagicMock

        strategy = SQLStorageStrategy.__new__(SQLStorageStrategy)
        strategy.logger = MagicMock()
        strategy.table_name = "machines"
        return strategy

    def test_stamps_head_under_the_advisory_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from orb.infrastructure.storage.sql import strategy as strategy_module

        monkeypatch.setattr(strategy_module, "_launch_time_is_integer", lambda _e: False)
        engine, connection = self._engine()
        strategy = self._strategy()

        strategy._auto_stamp_head(engine)

        sql = [statement for statement, _ in connection.statements]
        assert "pg_advisory_xact_lock" in sql[0]
        assert any("CREATE TABLE IF NOT EXISTS alembic_version" in s for s in sql)
        inserts = [p for s, p in connection.statements if s.startswith("INSERT")]
        assert inserts == [{"revision": HEAD}]
        assert connection.committed
        strategy.logger.warning.assert_not_called()

    def test_already_stamped_inserts_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from orb.infrastructure.storage.sql import strategy as strategy_module

        monkeypatch.setattr(strategy_module, "_launch_time_is_integer", lambda _e: False)
        engine, connection = self._engine(existing=(HEAD,))
        strategy = self._strategy()

        strategy._auto_stamp_head(engine)

        assert not any(s.startswith("INSERT") for s, _ in connection.statements)
        assert not connection.committed
        strategy.logger.warning.assert_not_called()
