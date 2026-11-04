"""Every SQL entry point reads and writes the typed requests / machines / templates tables."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from orb.application.commands.machine_handlers import UpdateMachineStatusHandler
from orb.application.commands.request_lifecycle_handlers import CompleteRequestHandler
from orb.application.dto.commands import CompleteRequestCommand
from orb.application.machine.commands import UpdateMachineStatusCommand
from orb.application.services.storage_registry_service import StorageRegistryService
from orb.domain.base.exceptions import ConcurrencyError, ConfigurationError
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus, RequestType
from orb.infrastructure.storage.registry import StorageRegistry, get_storage_registry
from orb.infrastructure.storage.sql.registration import (
    create_sql_entity_strategy,
    create_sql_strategy,
    create_sql_unit_of_work,
    register_sql_storage,
)
from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork
from orb.infrastructure.utilities.factories.repository_factory import RepositoryFactory


def _app_config(name: str) -> dict:
    return {"storage": {"strategy": "sql", "sql_strategy": {"type": "sqlite", "name": name}}}


def _factory(name: str) -> RepositoryFactory:
    register_sql_storage()
    config_manager = MagicMock()
    config_manager.get_storage_strategy.return_value = "sql"
    config_manager.app_config.model_dump.return_value = _app_config(name)
    return RepositoryFactory(config_manager=config_manager, logger=MagicMock(), event_bus=None)


def _new_request() -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tmpl-1",
        machine_count=1,
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
    )


def _machine(request_id: str, machine_id: str = "i-0abc") -> Machine:
    return Machine(
        machine_id=machine_id,
        template_id="tmpl-1",
        request_id=request_id,
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
        instance_type="t3.medium",
        image_id="ami-1",
        status=MachineStatus.PENDING,
    )


def _tables(db: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{db}")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _count(db: Path, table: str) -> int:
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608
    finally:
        engine.dispose()


def _handler_args():
    config = MagicMock()
    config.get_request_config.return_value = {}
    return {
        "logger": MagicMock(),
        "event_publisher": MagicMock(),
        "error_handler": None,
    }, config


@pytest.fixture
def isolated_registry():
    """A registry of its own: the real one is a process-wide singleton."""
    import uuid

    name = f"_IsolatedStorageRegistry{uuid.uuid4().hex}"
    cls = type(name, (StorageRegistry,), {})
    registry = cls()
    yield registry
    StorageRegistry._instances.pop(name, None)


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "state" / "orb.db"


class TestRegistryEntityFactory:
    def test_entity_factory_is_used_only_when_registered(self, isolated_registry):
        registry = isolated_registry
        plain = MagicMock(return_value="plain")
        typed = MagicMock(return_value="typed")
        registry.register_storage("plain", plain, MagicMock())
        registry.register_storage("typed", plain, MagicMock(), entity_strategy_factory=typed)

        assert registry.create_strategy("plain", {"c": 1}, entity="machines") == "plain"
        plain.assert_called_once_with({"c": 1})
        assert registry.create_strategy("typed", {"c": 1}, entity="machines") == "typed"
        typed.assert_called_once_with({"c": 1}, "machines")

    def test_without_an_entity_the_plain_factory_is_used(self, isolated_registry):
        registry = isolated_registry
        plain = MagicMock(return_value="plain")
        typed = MagicMock(return_value="typed")
        registry.register_storage("typed", plain, MagicMock(), entity_strategy_factory=typed)

        assert registry.create_strategy("typed", {}) == "plain"
        typed.assert_not_called()

    def test_a_kwargs_factory_is_never_handed_an_entity(self, isolated_registry):
        registry = isolated_registry
        seen = {}

        def third_party(config, **kwargs):
            seen["args"] = (config, kwargs)
            return "strategy"

        registry.register_storage("other", third_party, MagicMock())
        registry.create_strategy("other", {"c": 1}, entity="requests")

        assert seen["args"] == ({"c": 1}, {})

    def test_json_and_dynamodb_registrations_have_no_entity_factory(self, isolated_registry):
        from orb.infrastructure.storage.json.registration import register_json_storage
        from orb.providers.aws.storage.registration import register_dynamodb_storage

        register_json_storage()
        register_dynamodb_storage(isolated_registry)

        json_registration = get_storage_registry()._get_type_registration("json")
        dynamodb_registration = isolated_registry._get_type_registration("dynamodb")
        assert json_registration.get_factory("entity_strategy_factory") is None
        assert dynamodb_registration.get_factory("entity_strategy_factory") is None

    def test_sql_registration_provides_one(self):
        register_sql_storage()
        registration = get_storage_registry()._get_type_registration("sql")

        assert registration.get_factory("entity_strategy_factory") is create_sql_entity_strategy


class TestEntityTables:
    @pytest.mark.parametrize("entity", ["requests", "machines", "templates"])
    def test_each_entity_gets_its_typed_table(self, db, entity):
        strategy = create_sql_entity_strategy(_app_config(str(db)), entity)

        assert strategy.table_name == entity
        assert strategy.supports_versioned_save is True
        assert "version" in strategy.columns

    def test_no_entity_binds_to_requests_for_probes(self, db):
        assert create_sql_strategy(_app_config(str(db))).table_name == "requests"

    def test_unknown_entity_is_a_configuration_error(self, db):
        with pytest.raises(ConfigurationError, match="Unknown SQL entity"):
            create_sql_entity_strategy(_app_config(str(db)), "widgets")

    def test_explicit_connection_string_uses_the_typed_table(self, db):
        db.parent.mkdir(parents=True)
        strategy = create_sql_entity_strategy({"connection_string": f"sqlite:///{db}"}, "machines")

        assert strategy.table_name == "machines"

    def test_repository_creating_paths_always_name_an_entity(self, db):
        factory = _factory(str(db))
        registry = MagicMock()
        factory._storage_registry = registry

        for create in (
            factory.create_request_repository,
            factory.create_machine_repository,
        ):
            create()

        assert [c.kwargs.get("entity") for c in registry.create_strategy.call_args_list] == [
            "requests",
            "machines",
        ]

    def test_storage_strategy_factory_names_the_entity(self):
        from orb.infrastructure.storage.factory import StorageStrategyFactory

        registry = MagicMock()
        with patch(
            "orb.infrastructure.storage.factory.get_storage_registry", return_value=registry
        ):
            factory = StorageStrategyFactory()
            cfg = {"storage": {"strategy": "sql"}}
            factory.create_request_storage_strategy(cfg)
            factory.create_machine_storage_strategy(cfg)
            factory.create_template_storage_strategy(cfg)

        assert [c.kwargs["entity"] for c in registry.create_strategy.call_args_list] == [
            "requests",
            "machines",
            "templates",
        ]

    def test_di_repositories_use_the_typed_tables(self, db):
        factory = _factory(str(db))

        requests = factory.create_request_repository()
        machines = factory.create_machine_repository()

        assert requests.storage_strategy.table_name == "requests"
        assert machines.storage_strategy.table_name == "machines"


class TestNoGenericStorage:
    def test_nothing_creates_or_writes_generic_storage(self, db):
        factory = _factory(str(db))
        requests = factory.create_request_repository()
        machines = factory.create_machine_repository()
        probe = get_storage_registry().create_strategy("sql", _app_config(str(db)))
        service = StorageRegistryService(
            get_storage_registry(), MagicMock(), config=_app_config(str(db))
        )
        service.get_storage_health("sql")
        uow = SQLUnitOfWork(create_engine(f"sqlite:///{db}"))

        request = _new_request()
        requests.save(request)
        machines.save(_machine(str(request.request_id)))
        uow.requests.save(request.start_processing())
        probe.is_healthy()

        tables = _tables(db)
        assert "generic_storage" not in tables
        assert {"requests", "machines", "templates"} <= tables


class TestCrossPathVisibility:
    def test_request_written_by_the_unit_of_work_is_completed_through_the_di_repository(self, db):
        factory = _factory(str(db))
        di_requests = factory.create_request_repository()
        uow = SQLUnitOfWork(create_engine(f"sqlite:///{db}"))
        request = _new_request()
        uow.requests.save(request)
        started = request.start_processing()
        uow.requests.save(started)
        handler_args, config = _handler_args()
        handler = CompleteRequestHandler(
            request_repository=di_requests,
            state_machine=FulfilmentStateMachine(grace_period_seconds=60),
            config_port=config,
            **handler_args,
        )

        asyncio.run(
            handler.execute_command(CompleteRequestCommand(request_id=str(request.request_id)))
        )

        seen = SQLUnitOfWork(create_engine(f"sqlite:///{db}")).requests.find_by_id(
            str(request.request_id)
        )
        assert seen is not None and seen.status == RequestStatus.COMPLETED
        assert _count(db, "requests") == 1

    def test_machine_written_through_the_di_repository_is_seen_by_the_unit_of_work(self, db):
        factory = _factory(str(db))
        di_machines = factory.create_machine_repository()
        request = _new_request()
        di_machines.save(_machine(str(request.request_id)))

        uow = SQLUnitOfWork(create_engine(f"sqlite:///{db}"))
        assert uow.machines.get_by_id("i-0abc") is not None

        handler_args, _ = _handler_args()
        handler = UpdateMachineStatusHandler(machine_repository=di_machines, **handler_args)
        asyncio.run(
            handler.handle(UpdateMachineStatusCommand(machine_id="i-0abc", status="running"))
        )

        seen = SQLUnitOfWork(create_engine(f"sqlite:///{db}")).machines.get_by_id("i-0abc")
        assert seen is not None and seen.status == MachineStatus.RUNNING
        assert _count(db, "machines") == 1

    def test_machine_written_by_the_unit_of_work_is_read_through_the_di_repository(self, db):
        db.parent.mkdir(parents=True)
        uow = SQLUnitOfWork(create_engine(f"sqlite:///{db}"))
        uow.machines.save(_machine("req-1"))

        assert _factory(str(db)).create_machine_repository().get_by_id("i-0abc") is not None

    def test_di_repositories_conflict_with_other_writers(self, db):
        factory = _factory(str(db))
        di_requests = factory.create_request_repository()
        uow = SQLUnitOfWork(create_engine(f"sqlite:///{db}"))
        request = _new_request()
        uow.requests.save(request)
        mine = di_requests.find_by_id(str(request.request_id))
        theirs = uow.requests.find_by_id(str(request.request_id))
        assert mine is not None and theirs is not None
        uow.requests.save(theirs.start_processing())

        with pytest.raises(ConcurrencyError):
            di_requests.save(mine.start_processing())


class TestMachineHandlerRace:
    def test_a_real_race_surfaces_as_a_clean_concurrency_error(self, db):
        factory = _factory(str(db))
        di_machines = factory.create_machine_repository()
        di_machines.save(_machine("req-1"))
        other = SQLUnitOfWork(create_engine(f"sqlite:///{db}"))
        real_find = di_machines.find_by_id

        def find_then_interfere(machine_id):
            loaded = real_find(machine_id)
            moved = other.machines.get_by_id("i-0abc")
            other.machines.save(moved.update_status(MachineStatus.TERMINATED))  # type: ignore[union-attr]
            return loaded

        di_machines.find_by_id = find_then_interfere  # type: ignore[method-assign]
        handler_args, _ = _handler_args()
        handler = UpdateMachineStatusHandler(machine_repository=di_machines, **handler_args)

        with pytest.raises(ConcurrencyError) as exc_info:
            asyncio.run(
                handler.handle(UpdateMachineStatusCommand(machine_id="i-0abc", status="running"))
            )

        error = exc_info.value
        assert type(error) is ConcurrencyError  # not wrapped into a generic storage error
        assert "i-0abc" in str(error)
        assert "Traceback" not in str(error)
        # the winning write is intact
        stored = SQLUnitOfWork(create_engine(f"sqlite:///{db}")).machines.get_by_id("i-0abc")
        assert stored is not None and stored.status == MachineStatus.TERMINATED


class TestSharedMemoryDatabase:
    CONFIG = _app_config(":memory:")

    def test_unit_of_works_share_one_database(self):
        first = create_sql_unit_of_work(self._manager())
        second = create_sql_unit_of_work(self._manager())
        first.requests.save(_new_request())

        assert len(second.requests.find_all()) == 1

    def test_unit_of_work_and_di_repositories_share_one_database(self):
        factory = _factory(":memory:")
        di_requests = factory.create_request_repository()
        di_machines = factory.create_machine_repository()
        uow = create_sql_unit_of_work(self._manager())
        request = _new_request()
        uow.requests.save(request)
        di_machines.save(_machine(str(request.request_id)))

        assert di_requests.find_by_id(str(request.request_id)) is not None
        assert uow.machines.get_by_id("i-0abc") is not None

    def test_the_shared_engine_is_not_owned_by_a_strategy(self):
        strategy = create_sql_entity_strategy(self.CONFIG, "requests")

        assert strategy.connection_manager._owns_engine is False
        engine = strategy.connection_manager.engine
        with patch.object(engine, "dispose") as dispose:
            strategy.connection_manager.cleanup()
        dispose.assert_not_called()

    def test_reset_starts_from_an_empty_database(self):
        from orb.infrastructure.storage.sql.registration import reset_shared_memory_engines

        uow = create_sql_unit_of_work(self._manager())
        uow.requests.save(_new_request())
        reset_shared_memory_engines()

        assert create_sql_unit_of_work(self._manager()).requests.find_all() == []

    @staticmethod
    def _manager():
        from orb.config.manager import ConfigurationManager
        from orb.config.schemas.storage_schema import SqlStrategyConfig, StorageConfig

        manager = MagicMock(spec=ConfigurationManager)
        manager.get_typed.return_value = StorageConfig(
            strategy="sql",
            sql_strategy=SqlStrategyConfig(type="sqlite", name=":memory:"),  # type: ignore[arg-type]
        )
        return manager


class TestSchemaCreation:
    def test_concurrent_first_start_on_a_fresh_database(self, db):
        db.parent.mkdir(parents=True)
        errors: list[Exception] = []
        barrier = threading.Barrier(4)

        def start():
            try:
                barrier.wait()
                SQLUnitOfWork(create_engine(f"sqlite:///{db}"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=start) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert {"requests", "machines", "templates"} <= _tables(db)

    @staticmethod
    def _racing_create(failures, error):
        """Table.create that fails ``failures`` times, then really creates."""
        from sqlalchemy import Table

        real = Table.create
        calls = {"n": 0}

        def create(self, bind=None, checkfirst=False):
            calls["n"] += 1
            if calls["n"] <= failures:
                raise error
            return real(self, bind, checkfirst=checkfirst)

        return create, calls

    def test_an_already_exists_error_is_tolerated_and_retried(self, db):
        from sqlalchemy import Table

        from orb.infrastructure.storage.sql.models import Base

        strategy = create_sql_entity_strategy(_app_config(str(db)), "requests")
        create, calls = self._racing_create(
            2, OperationalError("CREATE TABLE", {}, Exception("table requests already exists"))
        )

        with patch.object(Table, "create", create):
            strategy._create_schema(strategy.connection_manager.engine, Base.metadata)

        assert calls["n"] >= 3

    def test_a_postgres_type_collision_is_tolerated_too(self, db):
        from sqlalchemy import Table
        from sqlalchemy.exc import IntegrityError

        from orb.infrastructure.storage.sql.models import Base

        strategy = create_sql_entity_strategy(_app_config(str(db)), "requests")
        collision = IntegrityError(
            "CREATE TABLE",
            {},
            Exception('duplicate key value violates unique constraint "pg_type_typname_nsp_index"'),
        )
        create, calls = self._racing_create(1, collision)

        with patch.object(Table, "create", create):
            strategy._create_schema(strategy.connection_manager.engine, Base.metadata)

        assert calls["n"] >= 2

    def test_retries_are_bounded(self, db):
        from sqlalchemy import Table

        from orb.infrastructure.storage.sql.models import Base

        strategy = create_sql_entity_strategy(_app_config(str(db)), "requests")
        create, calls = self._racing_create(
            100, OperationalError("CREATE TABLE", {}, Exception("table x already exists"))
        )

        with patch.object(Table, "create", create), patch("time.sleep"):
            with pytest.raises(OperationalError):
                strategy._create_schema(strategy.connection_manager.engine, Base.metadata)

        assert calls["n"] == strategy._SCHEMA_ATTEMPTS

    def test_other_ddl_errors_still_raise(self, db):
        from sqlalchemy import Table

        from orb.infrastructure.storage.sql.models import Base

        strategy = create_sql_entity_strategy(_app_config(str(db)), "requests")
        create, calls = self._racing_create(
            100, OperationalError("CREATE TABLE", {}, Exception("disk I/O error"))
        )

        with patch.object(Table, "create", create):
            with pytest.raises(OperationalError):
                strategy._create_schema(strategy.connection_manager.engine, Base.metadata)

        assert calls["n"] == 1

    @pytest.mark.parametrize(
        ("pgcode", "errno", "expected"),
        [("42P07", None, True), (None, 1050, True), (None, 1061, True), ("42501", None, False)],
    )
    def test_dialect_specific_already_exists_codes(self, pgcode, errno, expected):
        from orb.infrastructure.storage.sql.strategy import _is_already_exists

        orig = Exception(errno or "boom")
        orig.pgcode = pgcode  # type: ignore[attr-defined]
        assert _is_already_exists(OperationalError("stmt", {}, orig)) is expected

    @pytest.fixture
    def keep_logging_config(self):
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

    def test_alembic_head_and_create_all_produce_the_same_columns(
        self, tmp_path, monkeypatch, keep_logging_config
    ):
        import os

        import alembic.command
        import alembic.config

        import orb

        migrated = tmp_path / "migrated.db"
        created = tmp_path / "created.db"
        ini = os.path.join(
            os.path.dirname(os.path.abspath(orb.__file__)),
            "infrastructure",
            "storage",
            "sql",
            "migrations",
            "alembic.ini",
        )
        monkeypatch.setenv("ORB_SQL_URL", f"sqlite:///{migrated}")
        alembic.command.upgrade(alembic.config.Config(ini), "head")
        create_sql_entity_strategy(_app_config(str(created)), "requests")

        def layout(path: Path) -> dict[str, dict[str, tuple]]:
            engine = create_engine(f"sqlite:///{path}")
            try:
                inspector = inspect(engine)
                tables = set(inspector.get_table_names()) - {"alembic_version"}
                return {
                    table: {
                        col["name"]: (str(col["type"]).upper(), bool(col["nullable"]))
                        for col in inspector.get_columns(table)
                    }
                    | {"__pk__": tuple(inspector.get_pk_constraint(table)["constrained_columns"])}  # type: ignore[dict-item]
                    for table in tables
                }
            finally:
                engine.dispose()

        migrated_layout, created_layout = layout(migrated), layout(created)

        assert set(migrated_layout) == set(created_layout) == {"requests", "machines", "templates"}
        assert migrated_layout == created_layout


class TestAurora:
    def test_aurora_registration_has_an_entity_factory(self, isolated_registry):
        from orb.providers.aws.storage.registration import (
            create_aurora_entity_strategy,
            register_aurora_storage,
        )

        registry = isolated_registry
        register_aurora_storage(registry)

        registration = registry._get_type_registration("aurora")
        assert registration.get_factory("entity_strategy_factory") is create_aurora_entity_strategy

    def test_connection_string_builds_the_typed_table(self, db):
        from orb.providers.aws.storage.registration import create_aurora_entity_strategy

        db.parent.mkdir(parents=True)
        strategy = create_aurora_entity_strategy(
            {"connection_string": f"sqlite:///{db}"}, "machines"
        )

        assert strategy.table_name == "machines"

    @pytest.mark.parametrize("config", [{}, {"storage": {}}, object()])
    def test_no_derivable_connection_is_an_explicit_error(self, config):
        from orb.infrastructure.storage.exceptions import StorageError
        from orb.providers.aws.storage.registration import (
            create_aurora_entity_strategy,
            create_aurora_strategy,
            create_aurora_unit_of_work,
        )

        for build in (
            lambda c: create_aurora_entity_strategy(c, "requests"),
            create_aurora_strategy,
            create_aurora_unit_of_work,
        ):
            with pytest.raises(StorageError) as exc_info:
                build(config)
            # StorageError keeps its detail in error_code
            assert "no connection could be derived" in exc_info.value.error_code
