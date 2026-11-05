"""Saving, reloading and saving one aggregate again inside a single unit of work.

A reload inside the unit of work sees the transaction's own staged write, so the
second save is based on the version that write will store. Every backend must
treat it as a continuation of the same change, not as a conflict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orb.application.commands.request_creation_handlers import CreateReturnRequestHandler
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus

moto = pytest.importorskip("moto")


@pytest.fixture(params=["json", "sql", "dynamodb"])
def make_uow(request, tmp_path: Path):
    """A factory of fresh units of work over one shared store."""
    name = request.param
    if name == "json":
        from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

        yield lambda: JSONUnitOfWork(data_dir=str(tmp_path), backup_enabled=False)
    elif name == "sql":
        from sqlalchemy import create_engine

        from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork

        engine = create_engine(f"sqlite:///{tmp_path / 'orb.db'}")
        yield lambda: SQLUnitOfWork(engine)
        engine.dispose()
    else:
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
        from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork

        with moto.mock_aws():
            yield lambda: DynamoDBUnitOfWork(
                aws_client=None, logger=LoggingAdapter("test.reload"), region="us-east-1"
            )


def _machine(machine_id: str, return_request_id: str | None = None) -> Machine:
    return Machine(
        machine_id=machine_id,
        template_id="tmpl-1",
        request_id="req-00000000-0000-4000-8000-000000000001",
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
        instance_type="t3.medium",
        image_id="ami-1",
        status=MachineStatus.RUNNING,
        return_request_id=return_request_id,
        created_at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    )


def _return_request(machine_id: str) -> Request:
    return Request.create_return_request(
        machine_ids=[machine_id],
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
    )


def test_save_reload_save_commits(make_uow):
    with make_uow() as uow:
        uow.machines.save(_machine("i-1"))

    with make_uow() as uow:
        machine = uow.machines.get_by_id(MachineId(value="i-1"))
        uow.machines.save(machine.model_copy(update={"status": MachineStatus.STOPPING}))
        reloaded = uow.machines.get_by_id(MachineId(value="i-1"))
        uow.machines.save(reloaded.model_copy(update={"status": MachineStatus.STOPPED}))

    with make_uow() as uow:
        stored = uow.machines.get_by_id(MachineId(value="i-1"))
    assert stored is not None
    assert stored.status == MachineStatus.STOPPED


def test_two_writers_from_one_baseline_still_conflict(make_uow):
    """A stale copy saved after a fresh save of the same record is still refused."""
    from orb.domain.base.exceptions import ConcurrencyError

    with make_uow() as uow:
        uow.machines.save(_machine("i-1"))

    with make_uow() as setup:
        stale = setup.machines.get_by_id(MachineId(value="i-1"))

    with make_uow() as uow:
        fresh = uow.machines.get_by_id(MachineId(value="i-1"))
        uow.machines.save(fresh.model_copy(update={"status": MachineStatus.STOPPING}))
    with pytest.raises(ConcurrencyError):
        with make_uow() as uow:
            uow.machines.save(stale.model_copy(update={"status": MachineStatus.STOPPED}))


def test_force_return_supersedes_a_stuck_return_request(make_uow):
    stuck = _return_request("i-1")
    with make_uow() as uow:
        uow.requests.save(stuck)
        uow.machines.save(_machine("i-1", return_request_id=str(stuck.request_id)))

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = lambda: make_uow()
    handler = CreateReturnRequestHandler(
        uow_factory=factory,
        logger=MagicMock(),
        event_publisher=MagicMock(),
        error_handler=MagicMock(),
        query_bus=MagicMock(),
        provider_selection_port=MagicMock(),
        command_bus=MagicMock(),
        domain_config=MagicMock(),
        deprovisioning_orchestrator=MagicMock(),
    )
    new_request = _return_request("i-1")

    handler._cancel_validate_and_persist(["i-1"], new_request, force_return=True)

    with make_uow() as uow:
        machine = uow.machines.get_by_id(MachineId(value="i-1"))
        old = uow.requests.get_by_id(stuck.request_id)
        new = uow.requests.get_by_id(new_request.request_id)
    assert machine is not None
    assert machine.return_request_id == str(new_request.request_id)
    assert old is not None and old.status == RequestStatus.CANCELLED
    assert new is not None
