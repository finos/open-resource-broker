"""One SQL engine (and connection pool) per configured database per process."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text

from orb.config.schemas.storage_schema import StorageConfig
from orb.infrastructure.storage.components import engine_registry
from orb.infrastructure.storage.components.engine_registry import (
    dispose_engines,
    engine_key,
    get_or_create_engine,
    registered_engines,
)
from orb.infrastructure.storage.components.sql_connection_manager import SQLConnectionManager
from orb.infrastructure.storage.sql.registration import (
    create_sql_entity_strategy,
    create_sql_strategy,
    create_sql_unit_of_work,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    dispose_engines()
    yield
    dispose_engines()


def _config(name: str) -> dict:
    return {"storage": {"strategy": "sql", "sql_strategy": {"type": "sqlite", "name": name}}}


def _manager_for(name: str) -> MagicMock:
    from orb.config.manager import ConfigurationManager

    manager = MagicMock(spec=ConfigurationManager)
    manager.get_typed.return_value = StorageConfig.model_validate(_config(name)["storage"])
    return manager


def _engine_of(strategy):
    return strategy.connection_manager.engine


@pytest.mark.unit
class TestOneEnginePerDatabase:
    def test_repositories_unit_of_work_and_probe_share_one_engine(self, tmp_path: Path) -> None:
        name = str(tmp_path / "orb.db")
        entity_engines = [
            _engine_of(create_sql_entity_strategy(_config(name), e))
            for e in ("requests", "machines", "templates")
        ]
        probe = create_sql_strategy(_config(name))
        uow = create_sql_unit_of_work(_manager_for(name))

        assert {id(e) for e in entity_engines} == {id(probe.connection_manager.engine)}
        assert uow.engine is entity_engines[0]
        assert len(registered_engines()) == 1

    def test_different_databases_get_different_engines(self, tmp_path: Path) -> None:
        a = _engine_of(create_sql_entity_strategy(_config(str(tmp_path / "a.db")), "requests"))
        b = _engine_of(create_sql_entity_strategy(_config(str(tmp_path / "b.db")), "requests"))

        assert a is not b
        assert len(registered_engines()) == 2

    def test_two_spellings_of_one_file_share_an_engine(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        direct = str(tmp_path / "orb.db")
        indirect = str(tmp_path / "sub" / ".." / "orb.db")

        a = _engine_of(create_sql_entity_strategy(_config(direct), "requests"))
        b = _engine_of(create_sql_entity_strategy(_config(indirect), "machines"))

        assert a is b

    def test_in_memory_database_is_one_shared_engine(self) -> None:
        a = _engine_of(create_sql_entity_strategy(_config(":memory:"), "requests"))
        uow = create_sql_unit_of_work(_manager_for(":memory:"))

        assert uow.engine is a

    def test_explicit_connection_string_consumers_share_an_engine(self, tmp_path: Path) -> None:
        url = f"sqlite:///{tmp_path / 'explicit.db'}"
        config = {"connection_string": url}

        a = _engine_of(create_sql_entity_strategy(config, "requests"))
        b = _engine_of(create_sql_entity_strategy(config, "machines"))
        uow = create_sql_unit_of_work(config)

        assert a is b is uow.engine

    def test_connection_manager_given_a_url_uses_the_shared_engine(self, tmp_path: Path) -> None:
        url = f"sqlite:///{tmp_path / 'managed.db'}"

        first = SQLConnectionManager({"connection_string": url})
        second = SQLConnectionManager({"connection_string": url})

        assert first.engine is second.engine

    def test_private_in_memory_databases_are_not_shared_by_url(self) -> None:
        first = SQLConnectionManager({"connection_string": "sqlite:///:memory:"})
        second = SQLConnectionManager({"connection_string": "sqlite:///:memory:"})

        assert first.engine is not second.engine
        assert len(registered_engines()) == 0


@pytest.mark.unit
class TestEngineLifetime:
    def test_strategy_cleanup_does_not_dispose_the_shared_engine(self, tmp_path: Path) -> None:
        name = str(tmp_path / "orb.db")
        first = create_sql_entity_strategy(_config(name), "requests")
        second = create_sql_entity_strategy(_config(name), "machines")

        first.cleanup()

        with _engine_of(second).connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar_one() == 1
        assert registered_engines() == [_engine_of(second)]

    def test_dispose_engines_releases_every_pool_and_later_use_builds_new_ones(
        self, tmp_path: Path
    ) -> None:
        name = str(tmp_path / "orb.db")
        before = _engine_of(create_sql_entity_strategy(_config(name), "requests"))
        disposed: list[object] = []
        original = before.dispose
        before.dispose = lambda *a, **k: (disposed.append(before), original(*a, **k))[1]  # type: ignore[method-assign]

        dispose_engines()
        after = _engine_of(create_sql_entity_strategy(_config(name), "requests"))

        assert disposed == [before]
        assert after is not before
        assert registered_engines() == [after]

    def test_application_shutdown_disposes_the_shared_engines(self, tmp_path: Path) -> None:
        from orb.bootstrap import Application

        create_sql_entity_strategy(_config(str(tmp_path / "orb.db")), "requests")
        app = Application.__new__(Application)
        app.logger = MagicMock()  # type: ignore[attr-defined]
        app._initialized = True  # type: ignore[attr-defined]

        app.shutdown()

        assert registered_engines() == []

    def test_one_failing_pool_does_not_stop_the_others_being_disposed(self) -> None:
        good = create_engine("sqlite://")
        bad = MagicMock()
        bad.dispose.side_effect = RuntimeError("boom")
        get_or_create_engine("sqlite:///a.db", lambda _u: bad)
        get_or_create_engine("sqlite:///b.db", lambda _u: good)
        disposed = MagicMock(wraps=good.dispose)
        good.dispose = disposed  # type: ignore[method-assign]

        dispose_engines()

        disposed.assert_called_once()
        assert registered_engines() == []

    def test_keys_never_appear_in_log_output(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level("DEBUG")
        url = "postgresql://orb:s3cr3t-pw@db.example/orb"

        get_or_create_engine(url, lambda _u: MagicMock())

        assert "s3cr3t-pw" not in caplog.text
        assert "s3cr3t-pw" in engine_key(url)


@pytest.mark.unit
@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
class TestForkSafety:
    def test_child_does_not_inherit_the_parents_engines(self, tmp_path: Path) -> None:
        name = str(tmp_path / "orb.db")
        parent_engine = _engine_of(create_sql_entity_strategy(_config(name), "requests"))
        with parent_engine.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS t (v INTEGER)"))
            conn.commit()
        read_fd, write_fd = os.pipe()

        pid = os.fork()
        if pid == 0:  # pragma: no cover  # runs in the child
            code = 1
            try:
                os.close(read_fd)
                inherited = len(registered_engines())
                child_engine = _engine_of(create_sql_entity_strategy(_config(name), "requests"))
                with child_engine.begin() as conn:
                    conn.execute(text("INSERT INTO t VALUES (2)"))
                ok = inherited == 0 and child_engine is not parent_engine
                os.write(write_fd, b"ok" if ok else b"bad")
                code = 0
            finally:
                os._exit(code)

        os.close(write_fd)
        outcome = os.read(read_fd, 8)
        os.close(read_fd)
        _, status = os.waitpid(pid, 0)

        assert outcome == b"ok"
        assert os.WEXITSTATUS(status) == 0
        # The parent's own engine and pool are untouched by the child
        assert registered_engines() == [parent_engine]
        with parent_engine.begin() as conn:
            conn.execute(text("INSERT INTO t VALUES (1)"))
            assert conn.execute(text("SELECT COUNT(*) FROM t")).scalar_one() == 2

    def test_fork_hooks_are_installed(self) -> None:
        assert engine_registry._lock is not None
        assert sys.platform != "win32"


@pytest.mark.unit
class TestIsolation:
    def test_reset_helper_empties_the_registry(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.sql.registration import reset_shared_memory_engines

        create_sql_entity_strategy(_config(str(tmp_path / "orb.db")), "requests")
        create_sql_entity_strategy(_config(":memory:"), "requests")
        assert len(registered_engines()) == 2

        reset_shared_memory_engines()

        assert registered_engines() == []
