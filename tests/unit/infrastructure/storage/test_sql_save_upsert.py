"""Saving the same aggregate twice on SQL storage updates the row.

Requests, machines and templates are keyed by ``request_id`` / ``machine_id`` /
``template_id``. The strategy has to look the row up by that key; looking it up
by a generic ``id`` column made every second save of an aggregate an INSERT that
failed with a UNIQUE constraint error, so a request could not move past creation.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus, RequestType
from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork


@pytest.fixture
def uow(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'orb.db'}")
    yield SQLUnitOfWork(engine)
    engine.dispose()


def _count(uow, table: str) -> int:
    with uow.engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608


def _new_request() -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tmpl-1",
        machine_count=2,
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
    )


def _machine(request_id: str) -> Machine:
    return Machine(
        machine_id="i-0abc",
        template_id="tmpl-1",
        request_id=request_id,
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
        instance_type="t3.medium",
        image_id="ami-1",
        status=MachineStatus.PENDING,
    )


@pytest.mark.parametrize(
    ("table", "key"),
    [("requests", "request_id"), ("machines", "machine_id"), ("templates", "template_id")],
)
def test_strategy_keys_orm_tables_by_their_primary_key(uow, table, key):
    strategy = {
        "requests": uow._request_strategy,
        "machines": uow._machine_strategy,
        "templates": uow._template_strategy,
    }[table]

    assert strategy._get_id_column() == key
    assert strategy.serializer.id_column == key


def test_request_create_then_status_updates_then_return(uow):
    request = _new_request()
    uow.requests.save(request)
    request_id = str(request.request_id)

    started = request.start_processing()
    uow.requests.save(started)
    completed = started.complete("done")
    uow.requests.save(completed)

    assert _count(uow, "requests") == 1
    stored = uow.requests.find_by_id(request_id)
    assert stored is not None and stored.status == RequestStatus.COMPLETED

    # The return flow adds a second request and updates it the same way
    ret = Request.create_return_request(
        machine_ids=["i-0abc"],
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
    )
    uow.requests.save(ret)
    uow.requests.save(ret.start_processing())

    assert _count(uow, "requests") == 2


def test_machine_save_then_status_update_and_return(uow):
    request = _new_request()
    uow.requests.save(request)
    machine = _machine(str(request.request_id))
    uow.machines.save(machine)

    running = machine.update_status(MachineStatus.RUNNING)
    uow.machines.save(running)
    terminated = running.update_status(MachineStatus.TERMINATED)
    uow.machines.save(terminated)

    assert _count(uow, "machines") == 1
    stored = uow.machines.get_by_id("i-0abc")
    assert stored is not None and stored.status == MachineStatus.TERMINATED


def test_saving_one_aggregate_twice_does_not_duplicate_the_row(uow):
    request = _new_request()
    uow.requests.save(request)
    uow.requests.save(request)

    assert _count(uow, "requests") == 1


def test_template_save_twice_updates_the_row(uow):
    strategy = uow._template_strategy
    base = {"template_id": "t-1", "provider_api": "RunInstances", "image_id": "ami-1"}

    strategy.save("t-1", dict(base))
    strategy.save("t-1", {**base, "image_id": "ami-2"})

    assert _count(uow, "templates") == 1
    assert strategy.find_by_id("t-1")["image_id"] == "ami-2"  # type: ignore[index]
