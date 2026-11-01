"""ConfirmMachinesTerminatedHandler: audit append, idempotency, concurrency."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from orb.application.commands.request_confirm_terminated_handler import (
    ConfirmMachinesTerminatedHandler,
)
from orb.application.dto.commands import ConfirmMachinesTerminatedCommand
from orb.domain.base.exceptions import ConcurrencyError, EntityNotFoundError, ValidationError
from orb.domain.request.aggregate import Request
from orb.domain.request.termination_confirmation import CONFIRMED_TERMINATED_KEY


class _Store:
    """Versioned single-request store; save() rejects a stale version like the real repository."""

    def __init__(self, request: Request | None):
        self.request = request
        self.saves = 0
        self.before_save = None  # hook to simulate a concurrent writer
        self.repeat_hook = False

    def factory(self):
        store = self
        uow = MagicMock()
        uow.requests.find_by_id.side_effect = lambda _id: store.request

        def save(updated):
            if store.before_save:
                hook = store.before_save
                if not store.repeat_hook:
                    store.before_save = None
                hook()
            if updated.version != store.request.version + 1:
                raise ConcurrencyError("stale version")
            store.request = updated
            store.saves += 1
            return []

        uow.requests.save.side_effect = save

        @contextmanager
        def create():
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = create
        return factory


def _request():
    return Request.create_return_request(
        machine_ids=["i-1", "i-2", "i-3"],
        provider_type="oci",
        provider_name="oci-main",
        provider_api="OCICompute",
    )


def _handler(store):
    config = MagicMock()
    config.get_request_config.return_value = {"concurrency_max_retries": 3}
    return ConfirmMachinesTerminatedHandler(
        uow_factory=store.factory(),
        logger=MagicMock(),
        event_publisher=MagicMock(),
        error_handler=MagicMock(),
        config_port=config,
    )


def _command(request, ids, reason="verified in console", operator="alice", role="operator"):
    return ConfirmMachinesTerminatedCommand(
        request_id=str(request.request_id),
        machine_ids=ids,
        reason=reason,
        operator=operator,
        role=role,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_appends_one_record_per_machine_with_principal():
    store = _Store(_request())
    command = _command(store.request, ["i-1", "i-2"])
    await _handler(store).execute_command(command)

    records = store.request.metadata[CONFIRMED_TERMINATED_KEY]
    assert [r["machine_id"] for r in records] == ["i-1", "i-2"]
    assert {r["operator"] for r in records} == {"alice"}
    assert {r["role"] for r in records} == {"operator"}
    assert records[0]["reason"] == "verified in console"
    assert records[0]["at"]
    assert command.newly_confirmed == ["i-1", "i-2"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeat_confirmation_is_a_noop_with_existing_record():
    store = _Store(_request())
    handler = _handler(store)
    await handler.execute_command(_command(store.request, ["i-1"], operator="alice"))
    version = store.request.version

    command = _command(store.request, ["i-1"], operator="mallory", reason="other")
    await handler.execute_command(command)

    assert store.request.version == version
    assert store.saves == 1
    assert command.newly_confirmed == []
    assert command.already_confirmed == ["i-1"]
    assert command.records[0]["operator"] == "alice"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_confirmation_is_not_lost():
    store = _Store(_request())
    handler = _handler(store)

    def concurrent_writer():
        store.request = store.request.update_metadata(
            {CONFIRMED_TERMINATED_KEY: [{"machine_id": "i-3", "operator": "bob"}]}
        )

    store.before_save = concurrent_writer
    await handler.execute_command(_command(store.request, ["i-1"]))

    ids = [r["machine_id"] for r in store.request.metadata[CONFIRMED_TERMINATED_KEY]]
    assert ids == ["i-3", "i-1"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persistent_conflict_surfaces_after_retries():
    store = _Store(_request())
    store.repeat_hook = True
    store.before_save = lambda: setattr(
        store, "request", store.request.update_metadata({"touch": True})
    )
    with pytest.raises(ConcurrencyError):
        await _handler(store).execute_command(_command(store.request, ["i-1"]))
    assert CONFIRMED_TERMINATED_KEY not in store.request.metadata


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validation_failure_writes_nothing():
    store = _Store(_request())
    with pytest.raises(ValidationError, match="i-9"):
        await _handler(store).execute_command(_command(store.request, ["i-1", "i-9"]))
    assert store.saves == 0
    assert CONFIRMED_TERMINATED_KEY not in store.request.metadata


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_request_raises_not_found():
    store = _Store(None)
    command = ConfirmMachinesTerminatedCommand(
        request_id="ret-x", machine_ids=["i-1"], reason="r", operator="a", role="operator"
    )
    with pytest.raises(EntityNotFoundError):
        await _handler(store).execute_command(command)
