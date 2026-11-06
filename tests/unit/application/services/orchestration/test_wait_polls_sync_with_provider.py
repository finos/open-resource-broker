"""Waiting on a request must poll with a provider sync.

A lightweight read never refreshes the request from the provider, so a request
that is still in progress would never settle and the wait would run to its
timeout.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.queries import SyncAndGetRequestQuery
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.cancel_request import CancelRequestOrchestrator
from orb.application.services.orchestration.dtos import (
    AcquireMachinesInput,
    ReturnMachinesInput,
)
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator


class _ProviderBackedQueryBus:
    """Reports in progress for a lightweight read and complete after a sync."""

    def __init__(self, synced_status: str = "completed") -> None:
        self.queries: list[SyncAndGetRequestQuery] = []
        self._synced_status = synced_status

    async def execute(self, query: SyncAndGetRequestQuery) -> SimpleNamespace:
        self.queries.append(query)
        status = "in_progress" if query.lightweight else self._synced_status
        return SimpleNamespace(status=status, machine_references=[])


def _command_bus(**attrs):
    bus = MagicMock()

    async def execute(command):
        for key, value in attrs.items():
            setattr(command, key, value)

    bus.execute = AsyncMock(side_effect=execute)
    return bus


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("asyncio.sleep", new=AsyncMock()) as sleep:
        yield sleep


@pytest.mark.unit
@pytest.mark.application
class TestAcquireWait:
    @pytest.mark.asyncio
    async def test_wait_polls_with_provider_sync_and_returns_final_status(self):
        query_bus = _ProviderBackedQueryBus()
        orchestrator = AcquireMachinesOrchestrator(
            _command_bus(created_request_id="req-1"), query_bus, MagicMock()
        )

        result = await orchestrator.execute(
            AcquireMachinesInput(template_id="t", requested_count=1, wait=True, timeout_seconds=10)
        )

        assert result.status == "completed"
        assert all(q.lightweight is False and q.skip_cache is True for q in query_bus.queries)

    @pytest.mark.asyncio
    async def test_no_wait_reads_stored_status_without_polling_or_sleeping(self, _no_sleep):
        query_bus = _ProviderBackedQueryBus()
        orchestrator = AcquireMachinesOrchestrator(
            _command_bus(created_request_id="req-1"), query_bus, MagicMock()
        )

        result = await orchestrator.execute(
            AcquireMachinesInput(template_id="t", requested_count=1)
        )

        assert result.request_id == "req-1"
        assert result.status == "in_progress"
        assert all(query.lightweight for query in query_bus.queries)
        _no_sleep.assert_not_called()


@pytest.mark.unit
@pytest.mark.application
class TestReturnWait:
    @pytest.mark.asyncio
    async def test_wait_polls_with_provider_sync_and_returns_final_status(self):
        query_bus = _ProviderBackedQueryBus()
        orchestrator = ReturnMachinesOrchestrator(
            _command_bus(created_request_ids=["ret-1"], unrecorded_failure_request_ids=[]),
            query_bus,
            MagicMock(),
        )

        result = await orchestrator.execute(
            ReturnMachinesInput(machine_ids=["i-1"], wait=True, timeout_seconds=10)
        )

        assert result.status == "completed"
        assert all(q.lightweight is False and q.skip_cache is True for q in query_bus.queries)

    @pytest.mark.asyncio
    async def test_no_wait_returns_without_polling_or_sleeping(self, _no_sleep):
        query_bus = _ProviderBackedQueryBus()
        orchestrator = ReturnMachinesOrchestrator(
            _command_bus(created_request_ids=["ret-1"], unrecorded_failure_request_ids=[]),
            query_bus,
            MagicMock(),
        )

        await orchestrator.execute(ReturnMachinesInput(machine_ids=["i-1"]))

        assert query_bus.queries == []
        _no_sleep.assert_not_called()


@pytest.mark.unit
@pytest.mark.application
class TestCancelReadsReturnRequests:
    @pytest.mark.asyncio
    async def test_return_request_outcome_is_read_with_provider_sync(self):
        query_bus = _ProviderBackedQueryBus(synced_status="failed")
        orchestrator = CancelRequestOrchestrator(MagicMock(), query_bus, MagicMock(), MagicMock())
        return_result = SimpleNamespace(request_ids=["ret-1"], request_id="ret-1")

        status = await orchestrator._worst_return_request_status(return_result, "pending")

        assert status == "failed"
        assert all(q.lightweight is False and q.skip_cache is True for q in query_bus.queries)
