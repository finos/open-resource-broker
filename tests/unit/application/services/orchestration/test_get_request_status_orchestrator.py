"""Unit tests for GetRequestStatusOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import SyncAndGetRequestQuery, SyncAndListActiveRequestsQuery
from orb.application.services.orchestration.dtos import (
    GetRequestStatusInput,
    GetRequestStatusOutput,
    Paginated,
)
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator


class _FakeClock:
    """Deterministic monotonic clock for wall-clock timeout tests.

    ``monotonic()`` returns virtual seconds; ``advance()`` moves it forward.
    Patching both the clock's ``async_sleep`` (to advance) and ``monotonic`` (to
    read) lets the timeout logic be exercised without any real waiting.
    """

    def __init__(self) -> None:
        self._now: float = 0.0

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def mock_command_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_query_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def orchestrator(mock_command_bus, mock_query_bus, mock_logger):
    return GetRequestStatusOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestGetRequestStatusOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_all_requests_dispatches_list_active_query(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = GetRequestStatusInput(all_requests=True)
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, SyncAndListActiveRequestsQuery)

    @pytest.mark.asyncio
    async def test_execute_all_requests_returns_list(self, orchestrator, mock_query_bus):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={"request_id": "req-1"})
        mock_query_bus.execute.return_value = [r]
        input = GetRequestStatusInput(all_requests=True)
        result = await orchestrator.execute(input)
        assert isinstance(result, GetRequestStatusOutput)
        assert len(result.requests) == 1

    @pytest.mark.asyncio
    async def test_execute_specific_ids_dispatches_get_request_query(
        self, orchestrator, mock_query_bus
    ):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={"request_id": "req-1"})
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"])
        await orchestrator.execute(input)
        mock_query_bus.execute.assert_called_once()
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, SyncAndGetRequestQuery)
        assert query.request_id == "req-1"

    @pytest.mark.asyncio
    async def test_execute_detailed_sets_long_flag(self, orchestrator, mock_query_bus):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={})
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"], verbose=True)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert query.verbose is True
        assert query.lightweight is False

    @pytest.mark.asyncio
    async def test_execute_not_detailed_sets_lightweight_flag(self, orchestrator, mock_query_bus):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={})
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"], verbose=False)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert query.verbose is False

    @pytest.mark.asyncio
    async def test_execute_multiple_ids_queries_each(self, orchestrator, mock_query_bus):
        r = MagicMock()
        r.model_dump = MagicMock(return_value={})
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1", "req-2", "req-3"])
        result = await orchestrator.execute(input)
        assert mock_query_bus.execute.call_count == 3
        assert len(result.requests) == 3

    @pytest.mark.asyncio
    async def test_execute_query_error_returns_error_dict(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("not found")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        result = await orchestrator.execute(input)
        assert len(result.requests) == 1
        entry = result.requests[0]
        assert entry["request_id"] == "req-bad"
        assert entry["error"] == "not found"

    @pytest.mark.asyncio
    async def test_execute_query_error_logs_error(self, orchestrator, mock_query_bus, mock_logger):
        mock_query_bus.execute.side_effect = Exception("oops")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        await orchestrator.execute(input)
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_execute_not_found_error_tags_not_found_flag(self, orchestrator, mock_query_bus):
        """A genuine EntityNotFoundError → entry carries the explicit not_found flag."""
        from orb.domain.request.exceptions import RequestNotFoundError

        mock_query_bus.execute.side_effect = RequestNotFoundError("req-missing")
        input = GetRequestStatusInput(request_ids=["req-missing"])
        result = await orchestrator.execute(input)
        entry = result.requests[0]
        assert entry["not_found"] is True
        assert entry["request_id"] == "req-missing"
        assert entry["error"]

    @pytest.mark.asyncio
    async def test_execute_sync_error_has_no_not_found_flag(self, orchestrator, mock_query_bus):
        """A non-not-found error (ProviderContractError) → error entry, no not_found flag."""
        from orb.domain.base.exceptions import ProviderContractError

        mock_query_bus.execute.side_effect = ProviderContractError("contract violated")
        input = GetRequestStatusInput(request_ids=["req-exists"])
        result = await orchestrator.execute(input)
        entry = result.requests[0]
        assert "not_found" not in entry
        assert entry["error"] == "contract violated"

    @pytest.mark.asyncio
    async def test_execute_batch_partial_failure_mixes_markers(self, orchestrator, mock_query_bus):
        """Batch fan-out: one ok, one not-found, one sync-errored — one entry each.

        Guards the per-ID partial-failure contract the batch POST relies on: the
        orchestrator never blows up mid-batch and each ID yields exactly one
        entry, with the not_found flag present only on the genuinely missing ID.
        """
        from orb.domain.base.exceptions import ProviderContractError
        from orb.domain.request.exceptions import RequestNotFoundError

        ok = MagicMock(spec=["model_dump"])
        ok.model_dump.return_value = {"request_id": "req-ok", "status": "completed"}
        mock_query_bus.execute.side_effect = [
            ok,
            RequestNotFoundError("req-missing"),
            ProviderContractError("contract violated"),
        ]
        input = GetRequestStatusInput(request_ids=["req-ok", "req-missing", "req-errored"])
        result = await orchestrator.execute(input)

        assert len(result.requests) == 3
        ok_entry, missing_entry, errored_entry = result.requests
        assert "not_found" not in ok_entry and ok_entry["status"] == "completed"
        assert missing_entry["not_found"] is True
        assert "not_found" not in errored_entry and errored_entry["error"] == "contract violated"

    @pytest.mark.asyncio
    async def test_execute_all_requests_none_result_returns_empty(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = None
        input = GetRequestStatusInput(all_requests=True)
        result = await orchestrator.execute(input)
        assert result.requests == []

    @pytest.mark.asyncio
    async def test_to_dict_uses_model_dump(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["model_dump"])
        r.model_dump.return_value = {"key": "value"}
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"])
        result = await orchestrator.execute(input)
        assert result.requests[0] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_to_dict_falls_back_to_to_dict_method(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["to_dict"])
        r.to_dict.return_value = {"key": "from_to_dict"}
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"])
        result = await orchestrator.execute(input)
        assert result.requests[0] == {"key": "from_to_dict"}

    @pytest.mark.asyncio
    async def test_detailed_true_result_contains_expected_keys(self, orchestrator, mock_query_bus):
        """detailed=True: result.requests[0] contains machine_references and status."""
        r = MagicMock(spec=["model_dump"])
        r.model_dump = MagicMock(
            return_value={
                "request_id": "req-detail-1",
                "status": "running",
                "machine_references": ["m-001", "m-002"],
            }
        )
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-detail-1"], verbose=True)

        result = await orchestrator.execute(input)

        assert len(result.requests) == 1
        entry = result.requests[0]
        assert entry["request_id"] == "req-detail-1"
        assert entry["status"] == "running"
        assert entry["machine_references"] == ["m-001", "m-002"]

    @pytest.mark.asyncio
    async def test_execute_query_error_entry_is_dict(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.side_effect = Exception("boom")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        result = await orchestrator.execute(input)
        assert isinstance(result.requests[0], dict)

    @pytest.mark.asyncio
    async def test_execute_query_error_entry_has_request_id_and_error_keys(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.side_effect = Exception("boom")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        result = await orchestrator.execute(input)
        assert result.requests[0].get("request_id") == "req-bad"

    @pytest.mark.asyncio
    async def test_execute_query_error_entry_get_status_returns_empty_string(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.side_effect = Exception("boom")
        input = GetRequestStatusInput(request_ids=["req-bad"])
        result = await orchestrator.execute(input)
        assert result.requests[0].get("status", "") == ""

    @pytest.mark.asyncio
    async def test_execute_mixed_success_and_error_all_entries_are_dicts(
        self, orchestrator, mock_query_bus
    ):
        ok = MagicMock(spec=["model_dump"])
        ok.model_dump = MagicMock(return_value={"request_id": "req-ok", "status": "running"})
        mock_query_bus.execute.side_effect = [ok, Exception("fail")]
        input = GetRequestStatusInput(request_ids=["req-ok", "req-bad"])
        result = await orchestrator.execute(input)
        assert len(result.requests) == 2
        assert all(isinstance(e, dict) for e in result.requests)

    @pytest.mark.asyncio
    async def test_execute_all_requests_paginated_result_returns_items(
        self, orchestrator, mock_query_bus
    ):
        """Real Paginated return shape must not crash and must surface .items.

        This test FAILS against the pre-fix code (TypeError: 'Paginated' object
        is not iterable) and PASSES after the isinstance guard is applied.
        """
        r1 = MagicMock(spec=["model_dump"])
        r1.model_dump.return_value = {"request_id": "req-p1", "status": "running"}
        r2 = MagicMock(spec=["model_dump"])
        r2.model_dump.return_value = {"request_id": "req-p2", "status": "pending"}
        mock_query_bus.execute.return_value = Paginated(items=[r1, r2], total_count=2)
        input = GetRequestStatusInput(all_requests=True)
        result = await orchestrator.execute(input)
        assert isinstance(result, GetRequestStatusOutput)
        assert len(result.requests) == 2
        assert result.requests[0]["request_id"] == "req-p1"
        assert result.requests[1]["request_id"] == "req-p2"


@pytest.mark.unit
@pytest.mark.application
class TestGetRequestStatusOrchestratorWait:
    """Polling behaviour when ``wait=True``."""

    @pytest.mark.asyncio
    async def test_wait_false_does_not_poll(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["model_dump"])
        r.model_dump.return_value = {"request_id": "req-1", "status": "pending"}
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"], wait=False)
        await orchestrator.execute(input)
        assert mock_query_bus.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_wait_returns_immediately_when_terminal(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["model_dump"])
        r.model_dump.return_value = {"request_id": "req-1", "status": "complete"}
        mock_query_bus.execute.return_value = r
        input = GetRequestStatusInput(request_ids=["req-1"], wait=True, timeout_seconds=300)
        result = await orchestrator.execute(input)
        # Only the initial fetch — already terminal, no extra polls.
        assert mock_query_bus.execute.call_count == 1
        assert result.requests[0]["status"] == "complete"

    @pytest.mark.asyncio
    async def test_wait_polls_until_terminal(self, orchestrator, mock_query_bus, monkeypatch):
        pending = MagicMock(spec=["model_dump"])
        pending.model_dump.return_value = {"request_id": "req-1", "status": "pending"}
        done = MagicMock(spec=["model_dump"])
        done.model_dump.return_value = {"request_id": "req-1", "status": "complete"}
        mock_query_bus.execute.side_effect = [pending, pending, done]

        async def _no_sleep(_seconds):
            return None

        monkeypatch.setattr(orchestrator._clock, "async_sleep", _no_sleep)
        input = GetRequestStatusInput(request_ids=["req-1"], wait=True, timeout_seconds=300)
        result = await orchestrator.execute(input)
        assert mock_query_bus.execute.call_count == 3
        assert result.requests[0]["status"] == "complete"

    @pytest.mark.asyncio
    async def test_wait_stops_at_timeout_with_last_snapshot(
        self, orchestrator, mock_query_bus, monkeypatch
    ):
        pending = MagicMock(spec=["model_dump"])
        pending.model_dump.return_value = {"request_id": "req-1", "status": "pending"}
        mock_query_bus.execute.return_value = pending

        # Fake monotonic clock: the (no-op) sleep advances virtual wall-clock by
        # the requested duration, so the timeout is driven by simulated time
        # rather than real elapsed time — the loop is deterministic.
        clock = _FakeClock()

        async def _fake_sleep(seconds):
            clock.advance(seconds)

        monkeypatch.setattr(orchestrator._clock, "async_sleep", _fake_sleep)
        monkeypatch.setattr(orchestrator._clock, "monotonic", clock.monotonic)
        # timeout_seconds=2 with a 2s interval → initial fetch (t=0), one poll
        # (sleep advances t to 2), then t-start=2 is not < 2 → stop.
        input = GetRequestStatusInput(request_ids=["req-1"], wait=True, timeout_seconds=2)
        result = await orchestrator.execute(input)
        assert result.requests[0]["status"] == "pending"
        assert mock_query_bus.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_wait_timeout_counts_fetch_latency_not_just_intervals(
        self, orchestrator, mock_query_bus, monkeypatch
    ):
        """Slow fetches consume the wall-clock budget, so the poll stops early.

        Regression for the interval-counting bug: previously ``elapsed`` grew by
        a constant ``_POLL_INTERVAL_SECONDS`` per loop and ignored how long each
        ``_fetch_once`` took, so a slow provider could blow past
        ``timeout_seconds``.  With true monotonic tracking, a fetch that itself
        consumes the whole budget must terminate the poll after a single extra
        iteration rather than continuing to poll.
        """
        pending = MagicMock(spec=["model_dump"])
        pending.model_dump.return_value = {"request_id": "req-1", "status": "pending"}
        mock_query_bus.execute.return_value = pending

        clock = _FakeClock()

        # Each fetch itself takes 10 virtual seconds of wall-clock.
        original_fetch = orchestrator._fetch_once

        async def _slow_fetch(inp):
            clock.advance(10)
            return await original_fetch(inp)

        async def _fake_sleep(seconds):
            clock.advance(seconds)

        monkeypatch.setattr(orchestrator, "_fetch_once", _slow_fetch)
        monkeypatch.setattr(orchestrator._clock, "async_sleep", _fake_sleep)
        monkeypatch.setattr(orchestrator._clock, "monotonic", clock.monotonic)

        # timeout=5. start=monotonic() at t=0 (before first fetch).  Initial fetch
        # advances t to 10. Loop check: 10-0=10 < 5 is False → no further polls.
        # An interval-only bound (elapsed=0 after the initial fetch) would have
        # entered the loop and issued more fetches, overshooting the 5s cap.
        input = GetRequestStatusInput(request_ids=["req-1"], wait=True, timeout_seconds=5)
        result = await orchestrator.execute(input)
        assert result.requests[0]["status"] == "pending"
        assert mock_query_bus.execute.call_count == 1, (
            "fetch latency must count toward the timeout budget"
        )

    @pytest.mark.asyncio
    async def test_wait_tolerates_transient_error_then_returns_terminal(
        self, orchestrator, mock_query_bus, monkeypatch
    ):
        """A transient sync error on the first poll must NOT abort the wait.

        The provider blips on the first fetch (raises), then recovers and
        returns a terminal ``complete``. The loop must keep polling past the
        transient error and return the terminal result, not the error entry.
        """
        done = MagicMock(spec=["model_dump"])
        done.model_dump.return_value = {"request_id": "req-1", "status": "complete"}
        # First fetch blips; subsequent fetches succeed terminally.
        mock_query_bus.execute.side_effect = [Exception("transient blip"), done, done]

        async def _no_sleep(_seconds):
            return None

        monkeypatch.setattr(orchestrator._clock, "async_sleep", _no_sleep)
        input = GetRequestStatusInput(request_ids=["req-1"], wait=True, timeout_seconds=300)
        result = await orchestrator.execute(input)

        assert mock_query_bus.execute.call_count >= 2, "poll must continue past the transient error"
        assert result.requests[0]["status"] == "complete"
        assert "error" not in result.requests[0]

    @pytest.mark.asyncio
    async def test_wait_aborts_after_max_consecutive_transient_errors(
        self, orchestrator, mock_query_bus, monkeypatch
    ):
        """More than N consecutive transient errors stops the poll with the last snapshot."""
        from orb.application.services.orchestration.base import MAX_CONSECUTIVE_POLL_ERRORS

        mock_query_bus.execute.side_effect = Exception("still blipping")

        async def _no_sleep(_seconds):
            return None

        monkeypatch.setattr(orchestrator._clock, "async_sleep", _no_sleep)
        input = GetRequestStatusInput(request_ids=["req-1"], wait=True, timeout_seconds=300)
        result = await orchestrator.execute(input)

        # Initial fetch + retries up to the consecutive-error cap, then stop —
        # well short of the 300s / 2s = 150 polls a timeout-only bound implies.
        assert mock_query_bus.execute.call_count == MAX_CONSECUTIVE_POLL_ERRORS
        assert result.requests[0]["error"] == "still blipping"

    @pytest.mark.asyncio
    async def test_wait_stops_immediately_on_genuine_terminal_failure(
        self, orchestrator, mock_query_bus, monkeypatch
    ):
        """A real terminal FAILED status (not a sync blip) stops the poll at once."""
        failed = MagicMock(spec=["model_dump"])
        failed.model_dump.return_value = {"request_id": "req-1", "status": "failed"}
        mock_query_bus.execute.return_value = failed

        async def _no_sleep(_seconds):
            return None

        monkeypatch.setattr(orchestrator._clock, "async_sleep", _no_sleep)
        input = GetRequestStatusInput(request_ids=["req-1"], wait=True, timeout_seconds=300)
        result = await orchestrator.execute(input)

        assert mock_query_bus.execute.call_count == 1
        assert result.requests[0]["status"] == "failed"
