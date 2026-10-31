"""The command handlers' retry-on-conflict loop converges on every storage backend.

A competing writer lands between a handler's read and its write. The handler must
see the conflict, reload fresh state (not a cached copy) and apply its change on
top of the winner's.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orb.application.commands.request_lifecycle_handlers import (
    CancelRequestHandler,
    UpdateRequestStatusHandler,
)
from orb.application.dto.commands import CancelRequestCommand, UpdateRequestStatusCommand
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus, RequestType

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
                aws_client=None, logger=LoggingAdapter("test.retry"), region="us-east-1"
            )


def _new_request() -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tmpl-1",
        machine_count=1,
        provider_type="aws",
        provider_name="aws-default",
        provider_api="RunInstances",
    )


def _saved_request(make_uow) -> str:
    request = _new_request()
    with make_uow() as uow:
        uow.requests.save(request)
    return str(request.request_id)


class _InterferingFactory:
    """Hands out units of work; the first read of each is followed by a rival write."""

    def __init__(self, make_uow, request_id: str, rivals: int = 1) -> None:
        self._make_uow = make_uow
        self._request_id = request_id
        self._rivals = rivals
        self.reads = 0

    def create_unit_of_work(self):
        unit = self._make_uow()
        real_find = unit.requests.find_by_id

        def find_then_interfere(request_id):
            loaded = real_find(request_id)
            self.reads += 1
            if self.reads <= self._rivals:
                with self._make_uow() as rival:
                    other = rival.requests.find_by_id(self._request_id)
                    rival.requests.save(other.add_resource_id(f"fleet-{self.reads}"))  # type: ignore[union-attr]
            return loaded

        unit.requests.find_by_id = find_then_interfere  # type: ignore[method-assign]
        return unit


def _handler(cls, factory):
    config = MagicMock()
    config.get_request_config.return_value = {}
    kwargs = {"request_repository": MagicMock()} if cls is UpdateRequestStatusHandler else {}
    return cls(
        uow_factory=factory,
        logger=MagicMock(),
        event_publisher=MagicMock(),
        error_handler=MagicMock(),
        state_machine=FulfilmentStateMachine(grace_period_seconds=60),
        config_port=config,
        **kwargs,
    )


def test_status_update_reloads_after_an_interleaved_write_and_converges(make_uow):
    request_id = _saved_request(make_uow)
    factory = _InterferingFactory(make_uow, request_id)
    handler = _handler(UpdateRequestStatusHandler, factory)

    asyncio.run(
        handler.execute_command(
            UpdateRequestStatusCommand(
                request_id=request_id, status=RequestStatus.IN_PROGRESS, message="go"
            )
        )
    )

    with make_uow() as uow:
        stored = uow.requests.find_by_id(request_id)
    assert stored is not None
    assert factory.reads == 2  # one conflict, one successful retry on fresh state
    assert stored.status == RequestStatus.IN_PROGRESS
    assert "fleet-1" in stored.resource_ids  # the rival's write survived
    assert stored.version == 2  # the rival's write plus the handler's


def test_status_update_gives_up_with_the_conflict_when_rivals_keep_winning(make_uow):
    request_id = _saved_request(make_uow)
    factory = _InterferingFactory(make_uow, request_id, rivals=100)
    handler = _handler(UpdateRequestStatusHandler, factory)

    with pytest.raises(ConcurrencyError):
        asyncio.run(
            handler.execute_command(
                UpdateRequestStatusCommand(
                    request_id=request_id, status=RequestStatus.IN_PROGRESS, message="go"
                )
            )
        )

    with make_uow() as uow:
        stored = uow.requests.find_by_id(request_id)
    assert stored is not None and stored.status != RequestStatus.IN_PROGRESS


def test_two_loaders_of_one_request_cannot_both_save(make_uow):
    request_id = _saved_request(make_uow)
    first, second = make_uow(), make_uow()
    mine = first.requests.find_by_id(request_id)
    theirs = second.requests.find_by_id(request_id)
    assert mine is not None and theirs is not None

    with second:
        second.requests.save(theirs.add_resource_id("theirs"))
    with pytest.raises(ConcurrencyError):
        with first:
            first.requests.save(mine.add_resource_id("mine"))

    with make_uow() as uow:
        stored = uow.requests.find_by_id(request_id)
    assert stored is not None and stored.resource_ids == ["theirs"]


def test_cancel_reloads_after_an_interleaved_write_and_converges(make_uow):
    request_id = _saved_request(make_uow)
    factory = _InterferingFactory(make_uow, request_id)
    handler = _handler(CancelRequestHandler, factory)
    command = CancelRequestCommand(request_id=request_id, reason="no longer needed")

    asyncio.run(handler.execute_command(command))

    with make_uow() as uow:
        stored = uow.requests.find_by_id(request_id)
    assert stored is not None
    assert factory.reads == 2
    assert command.cancelled is True
    assert stored.status == RequestStatus.CANCELLED
    assert "fleet-1" in stored.resource_ids
