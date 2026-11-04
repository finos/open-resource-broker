"""Which database file the SQL storage uses.

The unit of work and the dependency-injected repositories (create_sql_strategy)
open the same configured database. Tests run from a scratch working directory
and assert that no stray ``database.db`` or ``data.db`` appears in it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from alembic.config import Config
from sqlalchemy.engine import make_url

from orb.config.manager import ConfigurationManager
from orb.config.schemas.storage_schema import SqlStrategyConfig, StorageConfig
from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.storage.components.sql_connection_manager import (
    SQLConnectionManager,
    build_sql_url,
)
from orb.infrastructure.storage.sql.registration import (
    _build_connection_string,
    create_sql_strategy,
    create_sql_unit_of_work,
)

MACHINE = {
    "machine_id": "m-1",
    "instance_type": "t3.medium",
    "provider_api": "RunInstances",
    "provider_name": "aws-default",
    "template_id": "tmpl-1",
    "image_id": "ami-1",
}


_REQUEST_ROW = {
    "request_id": "req-1",
    "request_type": "acquire",
    "status": "pending",
    "template_id": "tmpl-1",
    "provider_type": "aws",
    "provider_name": "aws-default",
}


def _config_manager(**sql: object) -> MagicMock:
    manager = MagicMock(spec=ConfigurationManager)
    manager.get_typed.return_value = StorageConfig(
        strategy="sql",
        sql_strategy=SqlStrategyConfig(**sql),  # type: ignore[arg-type]
    )
    return manager


def _uow(**sql: object):
    return create_sql_unit_of_work(_config_manager(**sql))


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def _db_files(directory: Path) -> set[str]:
    return {p.relative_to(directory).as_posix() for p in directory.rglob("*.db")}


def _app_config(name: str) -> dict:
    """The shape the repository factory passes: the dumped application config."""
    return {"storage": {"strategy": "sql", "sql_strategy": {"type": "sqlite", "name": name}}}


class TestOneDatabaseForBothPaths:
    def test_unit_of_work_and_repositories_open_the_same_file(self, tmp_path):
        target = tmp_path / "state" / "orb.db"

        uow = _uow(type="sqlite", name=str(target))
        strategy = create_sql_strategy(_app_config(str(target)))
        strategy.save("req-1", _REQUEST_ROW)

        assert Path(str(uow.engine.url.database)) == target
        assert Path(str(strategy.connection_manager.engine.url.database)) == target
        assert _db_files(tmp_path / "state") == {"orb.db"}
        assert strategy.find_by_id("req-1") is not None

    def test_dumped_config_is_read_not_defaulted(self, tmp_path):
        target = tmp_path / "configured.db"

        strategy = create_sql_strategy(_app_config(str(target)))

        assert target.exists()
        assert not (Path.cwd() / "data.db").exists()
        assert strategy.connection_manager.engine is not None

    def test_bare_sql_strategy_mapping_is_also_read(self, tmp_path):
        target = tmp_path / "bare.db"

        create_sql_strategy({"sql_strategy": {"type": "sqlite", "name": str(target)}})

        assert target.exists()

    def test_config_object_with_sql_strategy_is_read(self, tmp_path):
        target = tmp_path / "obj.db"
        config = StorageConfig(
            strategy="sql",
            sql_strategy=SqlStrategyConfig(type="sqlite", name=str(target)),  # type: ignore[arg-type]
        )

        create_sql_strategy(config)

        assert target.exists()

    def test_explicit_url_is_used_as_given(self, tmp_path):
        target = tmp_path / "url.db"

        strategy = create_sql_strategy({"connection_string": f"sqlite:///{target}"})
        strategy.save("req-1", _REQUEST_ROW)

        assert target.exists()
        assert not (Path.cwd() / "data.db").exists()

    def test_no_stray_files_in_the_working_directory(self, tmp_path, monkeypatch):
        target = tmp_path / "elsewhere" / "orb.db"
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        _uow(type="sqlite", name=str(target))
        create_sql_strategy(_app_config(str(target)))

        assert _db_files(cwd) == set()
        assert not (cwd / "database.db").exists() and not (cwd / "data.db").exists()


class TestEveryCallerShapeResolvesTheConfiguredFile:
    """Each production caller of create_sql_strategy names the configured file or fails.

    None may open a ``database.db`` / ``data.db`` in the working directory.
    """

    def test_configuration_port_object_reads_storage_config(self, tmp_path, monkeypatch):
        # The health-probe path passes the ConfigurationPort, not a dict
        target = tmp_path / "state" / "orb.db"
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        port = _config_manager(type="sqlite", name=str(target))

        strategy = create_sql_strategy(port)

        port.get_typed.assert_called_once_with(StorageConfig)
        assert Path(str(strategy.connection_manager.engine.url.database)) == target
        assert _db_files(cwd) == set()

    def test_configuration_port_resolves_same_file_as_repositories(self, tmp_path):
        target = tmp_path / "shared.db"

        via_port = create_sql_strategy(_config_manager(type="sqlite", name=str(target)))
        via_dict = create_sql_strategy(_app_config(str(target)))

        assert (
            via_port.connection_manager.engine.url.database
            == via_dict.connection_manager.engine.url.database
        )
        assert _db_files(tmp_path) == {"shared.db"}

    def test_application_config_object_reads_its_storage_section(self, tmp_path):
        target = tmp_path / "app.db"
        app = SimpleNamespace(
            storage=StorageConfig(
                strategy="sql",
                sql_strategy=SqlStrategyConfig(type="sqlite", name=str(target)),  # type: ignore[arg-type]
            )
        )

        strategy = create_sql_strategy(app)

        assert Path(str(strategy.connection_manager.engine.url.database)) == target

    @pytest.mark.parametrize("shape", [{}, {"storage": {}}, {"storage": {"strategy": "sql"}}])
    def test_config_without_a_database_raises_and_creates_nothing(self, tmp_path, shape):
        with pytest.raises(ConfigurationError, match="sql_strategy"):
            create_sql_strategy(shape)

        assert list(Path.cwd().iterdir()) == []
        assert _db_files(tmp_path) == set()

    def test_health_probe_passes_the_real_configuration(self, tmp_path):
        from orb.application.services.storage_registry_service import StorageRegistryService

        registry = MagicMock()
        port = object()

        StorageRegistryService(registry, MagicMock(), config=port).get_storage_health("sql")

        registry.create_strategy.assert_called_once_with("sql", port)


class TestNameResolution:
    def test_absolute_path_is_used_as_given_and_parent_is_created(self, tmp_path):
        target = tmp_path / "a" / "b" / "orb.db"

        uow = _uow(type="sqlite", name=str(target))

        assert target.exists()
        assert Path(str(uow.engine.url.database)) == target

    def test_in_memory_database_is_shared_by_all_strategies(self, caplog):
        with caplog.at_level(logging.WARNING):
            uow = _uow(type="sqlite", name=":memory:")

        for strategy in (uow._machine_strategy, uow._request_strategy, uow._template_strategy):
            assert strategy.connection_manager.engine is uow.engine
            for table in ("machines", "requests", "templates"):
                assert strategy.connection_manager.table_exists(table)
        uow._machine_strategy.save("m-1", dict(MACHINE))
        assert uow._request_strategy.connection_manager.execute_query(
            "SELECT machine_id FROM machines"
        ) == [("m-1",)]
        assert _db_files(Path.cwd()) == set()
        assert _warnings(caplog) == []

    def test_file_uri_is_opened_as_a_uri(self, tmp_path):
        target = tmp_path / "uri.db"

        uow = _uow(type="sqlite", name=f"file:{target}")
        uow._machine_strategy.save("m-1", dict(MACHINE))

        assert target.exists()
        assert not any(p.name.startswith("file:") for p in Path.cwd().iterdir())

    def test_url_backed_dict_config_for_the_unit_of_work(self, tmp_path):
        target = tmp_path / "dict.db"

        uow = create_sql_unit_of_work({"connection_string": f"sqlite:///{target}"})

        assert Path(str(uow.engine.url.database)) == target


class TestNoImplicitDatabaseDefaults:
    def test_unit_of_work_without_url_or_manager_raises(self, tmp_path):
        for config in ({}, {"type": "sqlite"}, object()):
            with pytest.raises(ConfigurationError):
                create_sql_unit_of_work(config)

        assert list(Path.cwd().iterdir()) == []

    def test_connection_manager_without_a_name_raises(self, tmp_path):
        with pytest.raises(Exception) as exc_info:
            SQLConnectionManager({"type": "sqlite"})

        assert "database name" in str(exc_info.value)
        assert list(Path.cwd().iterdir()) == []


class TestLoaderResolvesRelativeNames:
    def _apply(self, sql_strategy: dict) -> dict:
        from orb.config.loader import ConfigurationLoader

        config: dict = {"storage": {"sql_strategy": dict(sql_strategy)}}
        svc = MagicMock()
        svc.resolve_directory.side_effect = lambda kind: "/work" if kind == "work" else ""
        with patch.object(ConfigurationLoader, "_get_path_resolution_service", return_value=svc):
            ConfigurationLoader._process_scheduler_directories(config, None)
        return config["storage"]["sql_strategy"]

    def test_default_name_lands_under_the_scheduler_data_directory(self):
        assert self._apply({"type": "sqlite", "name": "database.db"})["name"] == (
            "/work/data/database.db"
        )

    def test_missing_name_gets_the_default(self):
        assert self._apply({"type": "sqlite"})["name"] == "/work/data/database.db"

    def test_custom_relative_name_is_kept_under_the_data_directory(self):
        assert self._apply({"type": "sqlite", "name": "sub/x.db"})["name"] == "/work/data/sub/x.db"

    def _fail_resolution(self, config: dict) -> None:
        from orb.config.loader import ConfigurationLoader

        with patch.object(
            ConfigurationLoader,
            "_get_path_resolution_service",
            side_effect=RuntimeError("no scheduler"),
        ):
            ConfigurationLoader._process_scheduler_directories(config, None)

    def test_failed_resolution_with_sql_storage_raises(self):
        config = {"storage": {"strategy": "sql", "sql_strategy": {"type": "sqlite"}}}

        with pytest.raises(ConfigurationError, match="scheduler directory"):
            self._fail_resolution(config)

    def test_empty_resolution_with_sql_storage_raises(self):
        from orb.config.loader import ConfigurationLoader

        svc = MagicMock()
        svc.resolve_directory.return_value = ""
        for sql_strategy in ({"type": "sqlite"}, {"type": "sqlite", "name": "x.db"}):
            config = {"storage": {"strategy": "sql", "sql_strategy": sql_strategy}}
            with patch.object(
                ConfigurationLoader, "_get_path_resolution_service", return_value=svc
            ):
                with pytest.raises(ConfigurationError, match="empty directory"):
                    ConfigurationLoader._process_scheduler_directories(config, None)

    def test_empty_resolution_keeps_absolute_names_and_other_storage(self):
        from orb.config.loader import ConfigurationLoader

        svc = MagicMock()
        svc.resolve_directory.return_value = ""
        configs = (
            {"storage": {"strategy": "sql", "sql_strategy": {"type": "sqlite", "name": "/x.db"}}},
            {"storage": {"strategy": "json"}},
        )
        for config in configs:
            with patch.object(
                ConfigurationLoader, "_get_path_resolution_service", return_value=svc
            ):
                ConfigurationLoader._process_scheduler_directories(config, None)

    def test_failed_resolution_keeps_absolute_names_and_other_storage(self):
        sql = {"storage": {"strategy": "sql", "sql_strategy": {"type": "sqlite", "name": "/x.db"}}}
        json_only = {"storage": {"strategy": "json", "sql_strategy": {"type": "sqlite"}}}
        postgres = {"storage": {"strategy": "sql", "sql_strategy": {"type": "postgresql"}}}

        for config in (sql, json_only, postgres):
            self._fail_resolution(config)

    @pytest.mark.parametrize("name", ["/srv/orb/x.db", ":memory:", "file:/srv/x.db?mode=ro"])
    def test_absolute_memory_and_uri_names_are_left_alone(self, name):
        assert self._apply({"type": "sqlite", "name": name})["name"] == name

    def test_other_engines_keep_their_database_name(self):
        assert self._apply({"type": "postgresql", "name": "orb"})["name"] == "orb"


class TestMissingDatabaseWarning:
    MESSAGE = "creating a new empty database"

    def test_warns_once_with_the_full_path_when_the_file_is_created(self, tmp_path, caplog):
        target = tmp_path / "new" / "orb.db"

        with caplog.at_level(logging.WARNING):
            _uow(type="sqlite", name=str(target))
            _uow(type="sqlite", name=str(target))

        warnings = [w for w in _warnings(caplog) if self.MESSAGE in w]
        assert len(warnings) == 1
        assert str(target.resolve()) in warnings[0]
        assert "storage.sql_strategy.name" in warnings[0]

    def test_no_warning_when_the_file_exists(self, tmp_path, caplog):
        target = tmp_path / "orb.db"
        _uow(type="sqlite", name=str(target))
        caplog.clear()

        with caplog.at_level(logging.WARNING):
            _uow(type="sqlite", name=str(target))
            create_sql_strategy(_app_config(str(target)))

        assert [w for w in _warnings(caplog) if self.MESSAGE in w] == []

    def test_no_warning_for_memory_databases(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            _uow(type="sqlite", name=":memory:")
            _uow(type="sqlite", name="file::memory:?cache=shared")
            _uow(type="sqlite", name="file:memdb1?mode=memory&cache=shared")
            create_sql_unit_of_work({"connection_string": f"sqlite:///{tmp_path / 'c.db'}"})

        assert [w for w in _warnings(caplog) if self.MESSAGE in w] == []

    @pytest.mark.parametrize("query", ["", "?mode=rwc"])
    def test_file_uri_warns_with_the_resolved_path_when_missing(self, tmp_path, caplog, query):
        target = tmp_path / "uri" / "u.db"

        with caplog.at_level(logging.WARNING):
            _uow(type="sqlite", name=f"file:{target}{query}")
            _uow(type="sqlite", name=f"file:{target}{query}")

        warnings = [w for w in _warnings(caplog) if self.MESSAGE in w]
        assert len(warnings) == 1
        assert str(target.resolve()) in warnings[0]

    def test_repositories_warn_too_when_they_create_the_file(self, tmp_path, caplog):
        target = tmp_path / "repo.db"

        with caplog.at_level(logging.WARNING):
            create_sql_strategy(_app_config(str(target)))

        assert len([w for w in _warnings(caplog) if self.MESSAGE in w]) == 1


class TestSqlUrlBuilding:
    @pytest.mark.parametrize("password", ["p@ss", "p:ss", "p/ss", "p%rd", "a@b:c/d%e", None])
    @pytest.mark.parametrize("db_type", ["postgresql", "mysql"])
    def test_password_survives_the_connection_string_round_trip(self, db_type, password):
        cfg = SimpleNamespace(
            type=db_type,
            name="orb",
            host="db.example",
            port=5432,
            username="orb",
            password=password,
        )

        parsed = make_url(_build_connection_string(cfg))

        assert parsed.password == password
        assert parsed.host == "db.example"
        assert parsed.drivername == ("postgresql" if db_type == "postgresql" else "mysql+pymysql")

    @pytest.mark.parametrize("password", ["p@ss", "p/ss", "p%rd", "a@b:c/d%e"])
    def test_migration_config_round_trips_the_escaped_url(self, password):
        rendered = build_sql_url("postgresql", "orb", "h", 5432, "u", password).render_as_string(
            hide_password=False
        )

        config = Config()
        config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))

        assert make_url(config.get_main_option("sqlalchemy.url")).password == password

    def test_password_is_masked_when_the_url_is_displayed(self):
        url = build_sql_url("postgresql", "orb", "h", 5432, "u", "s3cr3t/pw")

        assert "s3cr3t" not in str(url)
        assert "s3cr3t" not in repr(url)

    def test_discrete_postgres_fields_reach_create_engine_as_a_url_object(self):
        seen: list[object] = []

        def fake_create_engine(url, **_kwargs):
            seen.append(url)
            return MagicMock()

        with patch(
            "orb.infrastructure.storage.components.sql_connection_manager.create_engine",
            side_effect=fake_create_engine,
        ):
            SQLConnectionManager(
                {
                    "type": "postgresql",
                    "name": "orb",
                    "host": "h",
                    "port": 5432,
                    "username": "u",
                    "password": "p@ss/w%rd",
                }
            )

        assert seen and not isinstance(seen[0], str)
        assert seen[0].password == "p@ss/w%rd"  # type: ignore[attr-defined]

    def test_supplied_engine_is_not_disposed_by_the_manager(self):
        engine = MagicMock()
        manager = SQLConnectionManager({"type": "sqlite", "engine": engine})

        manager.cleanup()

        engine.dispose.assert_not_called()

    def test_manager_disposes_an_engine_it_is_told_it_owns(self):
        engine = MagicMock()
        manager = SQLConnectionManager({"type": "sqlite", "engine": engine, "owns_engine": True})

        manager.cleanup()

        engine.dispose.assert_called_once()

    def test_engine_created_for_a_strategy_is_disposed_on_cleanup(self, tmp_path):
        strategy = create_sql_strategy(_app_config(str(tmp_path / "owned.db")))
        engine = strategy.connection_manager.engine

        with patch.object(engine, "dispose", wraps=engine.dispose) as dispose:
            strategy.connection_manager.cleanup()

        dispose.assert_called_once()

    def test_engine_shared_with_the_unit_of_work_is_not_disposed_by_a_strategy(self):
        engine = MagicMock()
        manager = SQLConnectionManager({"type": "sqlite", "engine": engine, "owns_engine": False})

        manager.cleanup()

        engine.dispose.assert_not_called()
