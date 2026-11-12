"""SyncAndListReclaimedMachinesHandler: which of the named machines did the provider reclaim."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from orb.application.dto.queries import ListReclaimedMachinesQuery
from orb.application.queries.request_query_handlers import SyncAndListReclaimedMachinesHandler
from orb.application.services.machine_sync_service import FailedProviderFetch
from orb.domain.machine.machine_status import MachineStatus


def _machine(
    mid,
    status,
    request_id="req-11111111-1111-1111-1111-111111111111",
    return_request_id=None,
    name=None,
):
    return SimpleNamespace(
        machine_id=SimpleNamespace(value=mid),
        name=name or f"host-{mid}",
        status=status,
        request_id=request_id,
        return_request_id=return_request_id,
    )


def _handler(stored, provider_state):
    """``provider_state`` maps machine id to the status the provider reports."""
    uow = MagicMock()
    uow.machines.find_by_ids.side_effect = lambda ids: [
        m for m in stored if m.machine_id.value in ids
    ]
    uow.machines.get_all.return_value = stored
    uow.requests.get_by_id.side_effect = lambda rid: SimpleNamespace(request_id=rid)
    uow.__enter__ = lambda s: s
    uow.__exit__ = MagicMock(return_value=False)
    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.return_value = uow

    sync = MagicMock()

    async def _refresh(request, machines):
        return [
            SimpleNamespace(**{**vars(m), "status": provider_state[m.machine_id.value]})
            for m in machines
            if m.machine_id.value in provider_state
        ], {}

    async def _sync(request, db_machines, provider_machines):
        return provider_machines, {}

    sync.refresh_instance_status = AsyncMock(side_effect=_refresh)
    sync.sync_machines_with_provider = AsyncMock(side_effect=_sync)
    logger = MagicMock()
    handler = SyncAndListReclaimedMachinesHandler(
        uow_factory=uow_factory,
        logger=logger,
        error_handler=MagicMock(),
        machine_sync_service=sync,
    )
    return handler, sync, logger


def _ids(result):
    return sorted(m.machine_id for m in result)


def _patch_dto(monkeypatch):
    from orb.application.queries import request_query_handlers as mod

    monkeypatch.setattr(
        mod.MachineDTO,
        "from_domain",
        classmethod(lambda cls, m: SimpleNamespace(machine_id=m.machine_id.value, name=m.name)),
    )


def test_refreshed_provider_state_decides_not_stored_state(monkeypatch):
    _patch_dto(monkeypatch)
    running, ended, interrupted, stopped, returned = (
        MachineStatus.RUNNING,
        MachineStatus.TERMINATED,
        MachineStatus.SHUTTING_DOWN,
        MachineStatus.STOPPED,
        MachineStatus.TERMINATED,
    )
    stored = [
        _machine("A", MachineStatus.RUNNING),
        _machine("B", MachineStatus.RUNNING),
        _machine("C", MachineStatus.STOPPED),
        _machine("D", MachineStatus.RUNNING),
        _machine("E", MachineStatus.TERMINATED, return_request_id="ret-1"),
    ]
    handler, sync, _ = _handler(
        stored, {"A": running, "B": ended, "C": stopped, "D": interrupted, "E": returned}
    )

    result = asyncio.run(
        handler.execute_query(ListReclaimedMachinesQuery(identifiers=["A", "B", "C", "D", "E"]))
    )

    assert _ids(result) == ["B", "D"]
    sync.refresh_instance_status.assert_awaited_once()
    sync.sync_machines_with_provider.assert_awaited_once()


def test_one_provider_read_per_parent_request(monkeypatch):
    _patch_dto(monkeypatch)
    stored = [
        _machine("A", MachineStatus.RUNNING, request_id="req-11111111-1111-1111-1111-111111111111"),
        _machine("B", MachineStatus.RUNNING, request_id="req-11111111-1111-1111-1111-111111111111"),
        _machine("C", MachineStatus.RUNNING, request_id="req-22222222-2222-2222-2222-222222222222"),
    ]
    handler, sync, _ = _handler(
        stored, {m.machine_id.value: MachineStatus.TERMINATED for m in stored}
    )

    result = asyncio.run(
        handler.execute_query(ListReclaimedMachinesQuery(identifiers=["A", "B", "C"]))
    )

    assert _ids(result) == ["A", "B", "C"]
    assert sync.refresh_instance_status.await_count == 2


def test_provider_failure_reports_nothing_and_warns(monkeypatch):
    _patch_dto(monkeypatch)
    stored = [_machine("A", MachineStatus.RUNNING)]
    handler, sync, logger = _handler(stored, {})
    sync.refresh_instance_status = AsyncMock(
        return_value=(FailedProviderFetch(stored), {"provider_fetch_failed": True})
    )

    result = asyncio.run(handler.execute_query(ListReclaimedMachinesQuery(identifiers=["A"])))

    assert result == []
    sync.sync_machines_with_provider.assert_not_awaited()
    logger.warning.assert_called_once()


def test_matches_by_name_when_the_id_is_unknown(monkeypatch):
    _patch_dto(monkeypatch)
    stored = [_machine("A", MachineStatus.RUNNING, name="node-a")]
    handler, _, _ = _handler(stored, {"A": MachineStatus.TERMINATED})

    result = asyncio.run(handler.execute_query(ListReclaimedMachinesQuery(identifiers=["node-a"])))

    assert _ids(result) == ["A"]


def test_no_identifiers_reads_nothing(monkeypatch):
    _patch_dto(monkeypatch)
    handler, sync, _ = _handler([_machine("A", MachineStatus.RUNNING)], {})

    assert asyncio.run(handler.execute_query(ListReclaimedMachinesQuery())) == []
    sync.refresh_instance_status.assert_not_awaited()
