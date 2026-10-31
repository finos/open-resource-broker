"""Optimistic locking for aggregates stored in SQL.

An aggregate remembers the stored version it was loaded at or last saved at. The
SQL save only writes while the row is still at that version, so lifecycles with
several in-memory mutations between saves work while concurrent writers conflict.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text

from orb.application.commands.request_lifecycle_handlers import UpdateRequestStatusHandler
from orb.application.dto.commands import UpdateRequestStatusCommand
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus, RequestType
from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'orb.db'}")
    yield engine
    engine.dispose()


@pytest.fixture
def uow(engine):
    return SQLUnitOfWork(engine)


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


def _stored(engine, table: str, key: str, value: str) -> dict:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(f"SELECT * FROM {table} WHERE {key} = :v"),  # noqa: S608
                {"v": value},
            )
            .mappings()
            .one()
        )
    return dict(row)


class TestHolder:
    def test_every_mutated_copy_shares_the_holder(self):
        request = _new_request()

        assert request.start_processing().persisted_revision is request.persisted_revision
        assert request.add_resource_id("fleet-1").persisted_revision is request.persisted_revision

    def test_machine_mutators_keep_the_holder(self):
        machine = _machine("req-1")

        assert (
            machine.update_status(MachineStatus.RUNNING).persisted_revision
            is machine.persisted_revision
        )

    def test_json_round_trip_gives_a_fresh_holder(self):
        request = _new_request()
        request.persisted_revision.mark_loaded(3)

        dumped = request.model_dump(mode="json")
        rebuilt = Request.model_validate(dumped)

        assert dumped["persisted_revision"] is None
        assert rebuilt.persisted_revision.value is None

    def test_state_equality_ignores_the_holder(self):
        request = _new_request()
        other = Request.model_validate(request.model_dump())
        other.persisted_revision.mark_loaded(9)

        assert request.model_dump() == other.model_dump()

    def test_deep_copies_detach(self):
        request = _new_request()

        assert copy.deepcopy(request).persisted_revision is not request.persisted_revision

    def test_holder_is_not_in_repr_or_stored_payloads(self, uow):
        request = _new_request()
        machine = _machine("req-1")

        assert "persisted_revision" not in repr(request)
        assert "persisted_revision" not in uow.requests.serializer.to_dict(request)
        assert "persisted_revision" not in uow.machines.serializer.to_dict(machine)


class TestRepositoryBaseline:
    def test_new_aggregate_has_no_baseline_until_saved(self, uow):
        request = _new_request()
        assert request.persisted_revision.value is None

        uow.requests.save(request)

        assert request.persisted_revision.value == 0

    def test_loaded_aggregate_carries_the_stored_version(self, uow):
        request = _new_request()
        uow.requests.save(request)
        started = request.start_processing()
        uow.requests.save(started)

        loaded = uow.requests.find_by_id(str(request.request_id))

        assert loaded is not None and loaded.persisted_revision.value == started.version
        assert loaded.persisted_revision is not started.persisted_revision

    def test_loaded_machine_carries_the_stored_version(self, uow):
        machine = _machine("req-1")
        uow.machines.save(machine)
        uow.machines.save(machine.update_status(MachineStatus.RUNNING))

        loaded = uow.machines.get_by_id("i-0abc")

        assert loaded is not None and loaded.persisted_revision.value == 1

    def test_failed_save_leaves_the_baseline_alone(self, uow):
        request = _new_request()
        uow.requests.save(request)
        winner = uow.requests.find_by_id(str(request.request_id))
        assert winner is not None
        uow.requests.save(winner.start_processing())

        stale = request.start_processing()
        with pytest.raises(ConcurrencyError):
            uow.requests.save(stale)

        assert stale.persisted_revision.value == 0


class TestLifecycleOnOneLoad:
    def test_checkpoint_copy_then_final_copy_keeps_the_stored_resource_ids(self, uow, engine):
        """The provisioning flow saves a checkpoint copy, then the handler's own copy."""
        request = _new_request()
        uow.requests.save(request)

        checkpoint = request.add_resource_id("fleet-1").start_processing()
        uow.requests.save(checkpoint)

        final = request.add_resource_id("fleet-1").start_processing().complete("done")
        uow.requests.save(final)

        row = _stored(engine, "requests", "request_id", str(request.request_id))
        assert "fleet-1" in str(row["resource_ids"])
        assert row["status"] == RequestStatus.COMPLETED.value
        assert row["version"] > checkpoint.version - 1
        assert uow.requests.find_by_id(str(request.request_id)).status == RequestStatus.COMPLETED  # type: ignore[union-attr]

    def test_versions_only_move_forward(self, uow, engine):
        request = _new_request()
        seen = []
        uow.requests.save(request)
        seen.append(_stored(engine, "requests", "request_id", str(request.request_id))["version"])
        for step in (request.add_resource_id("f"), request.start_processing()):
            uow.requests.save(step)
            seen.append(
                _stored(engine, "requests", "request_id", str(request.request_id))["version"]
            )

        assert seen == sorted(set(seen))

    def test_machines_follow_the_same_rules(self, uow, engine):
        machine = _machine("req-1")
        uow.machines.save(machine)
        running = machine.update_status(MachineStatus.RUNNING)
        uow.machines.save(running)
        uow.machines.save(running.update_status(MachineStatus.TERMINATED))

        assert _stored(engine, "machines", "machine_id", "i-0abc")["status"] == "terminated"

    def test_the_same_machine_discovered_twice_is_an_upsert(self, uow, engine):
        """Fresh aggregates (no baseline) never conflict with each other."""
        uow.machines.save_batch([_machine("req-1")])
        uow.machines.save_batch([_machine("req-1")])
        uow.machines.save(_machine("req-1"))

        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM machines")).scalar_one() == 1

    def test_machine_batch_is_all_or_nothing(self, uow, engine):
        first, second = _machine("req-1", "i-1"), _machine("req-1", "i-2")
        uow.machines.save_batch([first, second])
        other = SQLUnitOfWork(engine)
        moved = other.machines.get_by_id("i-2")
        assert moved is not None
        other.machines.save(moved.update_status(MachineStatus.RUNNING))

        with pytest.raises(ConcurrencyError):
            uow.machines.save_batch(
                [
                    first.update_status(MachineStatus.RUNNING),
                    second.update_status(MachineStatus.RUNNING),
                ]
            )

        assert _stored(engine, "machines", "machine_id", "i-1")["status"] == "pending"


class TestConcurrentWriters:
    def test_two_loads_of_one_request_conflict(self, uow, engine):
        request = _new_request()
        uow.requests.save(request)
        request_id = str(request.request_id)
        mine = uow.requests.find_by_id(request_id)
        theirs = SQLUnitOfWork(engine).requests.find_by_id(request_id)
        assert mine is not None and theirs is not None

        SQLUnitOfWork(engine).requests.save(theirs.start_processing())

        with pytest.raises(ConcurrencyError):
            uow.requests.save(mine.start_processing())

    def test_delete_after_load_conflicts(self, uow, engine):
        request = _new_request()
        uow.requests.save(request)
        loaded = uow.requests.find_by_id(str(request.request_id))
        assert loaded is not None
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM requests"))

        with pytest.raises(ConcurrencyError):
            uow.requests.save(loaded.start_processing())

    def test_retry_loop_reloads_and_succeeds_after_an_interleaved_write(self, engine):
        """UpdateRequestStatusHandler reloads on ConcurrencyError and converges."""
        setup = SQLUnitOfWork(engine)
        request = _new_request()
        setup.requests.save(request)
        request_id = str(request.request_id)

        interferer = SQLUnitOfWork(engine)
        calls = {"n": 0}

        class _Factory:
            def create_unit_of_work(self):
                unit = SQLUnitOfWork(engine)
                real_find = unit.requests.find_by_id

                def find_then_interfere(rid):
                    loaded = real_find(rid)
                    calls["n"] += 1
                    if calls["n"] == 1:
                        # Another writer lands between this unit's read and write
                        other = interferer.requests.find_by_id(request_id)
                        interferer.requests.save(other.add_resource_id("fleet-9"))  # type: ignore[union-attr]
                    return loaded

                unit.requests.find_by_id = find_then_interfere  # type: ignore[method-assign]
                return unit

        config = MagicMock()
        config.get_request_config.return_value = {}
        handler = UpdateRequestStatusHandler(
            uow_factory=_Factory(),  # type: ignore[arg-type]
            request_repository=MagicMock(),
            logger=MagicMock(),
            event_publisher=MagicMock(),
            error_handler=MagicMock(),
            state_machine=FulfilmentStateMachine(grace_period_seconds=60),
            config_port=config,
        )

        import asyncio

        asyncio.run(
            handler.execute_command(
                UpdateRequestStatusCommand(
                    request_id=request_id, status=RequestStatus.IN_PROGRESS, message="go"
                )
            )
        )

        row = _stored(engine, "requests", "request_id", request_id)
        assert calls["n"] == 2  # one conflict, one successful retry
        assert row["status"] == RequestStatus.IN_PROGRESS.value
        assert "fleet-9" in str(row["resource_ids"])  # the interleaved write survived


class TestProductionWiring:
    def test_di_built_repositories_use_the_versioned_save(self):
        """RepositoryFactory (what the container registers) yields SQL strategies with the capability."""
        from orb.infrastructure.storage.sql.registration import register_sql_storage
        from orb.infrastructure.utilities.factories.repository_factory import RepositoryFactory

        register_sql_storage()
        config_manager = MagicMock()
        config_manager.get_storage_strategy.return_value = "sql"
        config_manager.app_config.model_dump.return_value = {
            "storage": {"strategy": "sql", "sql_strategy": {"type": "sqlite", "name": ":memory:"}}
        }
        factory = RepositoryFactory(
            config_manager=config_manager, logger=MagicMock(), event_bus=None
        )

        for repository in (
            factory.create_request_repository(),
            factory.create_machine_repository(),
        ):
            assert repository.storage_strategy.supports_versioned_save is True

    def test_unit_of_work_repositories_use_the_versioned_save(self, uow):
        assert uow.requests.storage_strategy.supports_versioned_save is True
        assert uow.machines.storage_strategy.supports_versioned_save is True

    def test_repositories_call_save_versioned_with_the_baseline(self, uow):
        strategy = uow.requests.storage_strategy
        calls = []
        real = strategy.save_versioned

        def spy(entity_id, data, expected):
            calls.append(expected)
            return real(entity_id, data, expected)

        strategy.save_versioned = spy  # type: ignore[method-assign]
        request = _new_request()
        uow.requests.save(request)
        uow.requests.save(request.start_processing())

        assert calls == [None, 0]

    def test_other_strategies_keep_the_plain_save(self):
        from orb.infrastructure.storage.repositories.request_repository import (
            RequestRepositoryImpl,
        )

        strategy = MagicMock()
        repository = RequestRepositoryImpl(strategy)

        repository.save(_new_request())

        strategy.save.assert_called_once()
        strategy.save_versioned.assert_not_called()


class TestStoredVersionsNeverDecrease:
    def test_unconditional_save_with_a_lower_counter_moves_the_row_forward(self, uow, engine):
        machine = _machine("req-1")
        uow.machines.save(machine)
        current = machine
        for status in (MachineStatus.RUNNING, MachineStatus.TERMINATED):
            current = current.update_status(status)
            uow.machines.save(current)
        stored = _stored(engine, "machines", "machine_id", "i-0abc")["version"]
        assert stored >= 2

        # Rediscovered from the provider: a new aggregate, local version 0, no baseline
        fresh = _machine("req-1")
        written = uow.machines.storage_strategy.save_versioned(
            "i-0abc", uow.machines.serializer.to_dict(fresh), None
        )

        assert written == stored + 1
        assert _stored(engine, "machines", "machine_id", "i-0abc")["version"] == stored + 1

    def test_a_loader_from_before_the_bump_conflicts(self, uow, engine):
        machine = _machine("req-1")
        uow.machines.save(machine)
        older = SQLUnitOfWork(engine).machines.get_by_id("i-0abc")
        assert older is not None

        uow.machines.save(_machine("req-1"))  # no baseline, bumps the stored version

        with pytest.raises(ConcurrencyError):
            SQLUnitOfWork(engine).machines.save(older.update_status(MachineStatus.RUNNING))

    def test_a_loader_after_the_bump_saves_fine(self, uow, engine):
        uow.machines.save(_machine("req-1"))
        uow.machines.save(_machine("req-1"))
        loaded = uow.machines.get_by_id("i-0abc")
        assert loaded is not None

        uow.machines.save(loaded.update_status(MachineStatus.RUNNING))

        assert _stored(engine, "machines", "machine_id", "i-0abc")["status"] == "running"


class TestDuplicateKeyDetection:
    @pytest.mark.parametrize(
        "message", ["UNIQUE constraint failed: machines.machine_id", "PRIMARY KEY must be unique"]
    )
    def test_unique_violations_are_retried(self, message):
        from sqlalchemy.exc import IntegrityError

        from orb.infrastructure.storage.sql.strategy import _is_duplicate_key

        assert _is_duplicate_key(IntegrityError("stmt", {}, Exception(message)))

    @pytest.mark.parametrize(
        "message",
        ["NOT NULL constraint failed: machines.template_id", "FOREIGN KEY constraint failed"],
    )
    def test_other_integrity_errors_are_not(self, message):
        from sqlalchemy.exc import IntegrityError

        from orb.infrastructure.storage.sql.strategy import _is_duplicate_key

        assert not _is_duplicate_key(IntegrityError("stmt", {}, Exception(message)))

    def test_a_not_null_failure_is_not_retried(self, uow):
        from sqlalchemy.exc import IntegrityError

        strategy = uow.machines.storage_strategy
        attempts = {"n": 0}

        def boom(*_args, **_kwargs):
            attempts["n"] += 1
            raise IntegrityError("stmt", {}, Exception("NOT NULL constraint failed: x"))

        strategy._write_one = boom  # type: ignore[method-assign]

        with pytest.raises(Exception):
            strategy.save_versioned("i-0abc", {"machine_id": "i-0abc"}, None)

        assert attempts["n"] == 1

    def test_a_lost_insert_race_is_retried_as_an_update(self, uow, engine):
        from sqlalchemy.exc import IntegrityError

        strategy = uow.machines.storage_strategy
        real = strategy._write_one
        attempts = {"n": 0}

        def flaky(session, entity_id, data, expected):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise IntegrityError(
                    "stmt", {}, Exception("UNIQUE constraint failed: machines.machine_id")
                )
            return real(session, entity_id, data, expected)

        strategy._write_one = flaky  # type: ignore[method-assign]
        uow.machines.save(_machine("req-1"))

        assert attempts["n"] == 2
        assert _stored(engine, "machines", "machine_id", "i-0abc")["status"] == "pending"
