"""Unit tests for ListRequestsOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import SyncAndListActiveRequestsQuery
from orb.application.request.queries import ListRequestsQuery
from orb.application.services.orchestration.dtos import (
    ListRequestsInput,
    ListRequestsOutput,
    Paginated,
)
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator


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
    return ListRequestsOrchestrator(
        command_bus=mock_command_bus,
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


@pytest.mark.unit
@pytest.mark.application
class TestListRequestsOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_sync_false_dispatches_list_requests_query(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=False)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, ListRequestsQuery)

    @pytest.mark.asyncio
    async def test_execute_sync_true_dispatches_list_active_requests_query(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=True)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, SyncAndListActiveRequestsQuery)

    @pytest.mark.asyncio
    async def test_execute_sync_true_passes_limit_and_all_resources(
        self, orchestrator, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=True, limit=25)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert query.limit == 25
        assert query.all_resources is True

    @pytest.mark.asyncio
    async def test_execute_sync_false_passes_status_and_limit(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=False, status="pending", limit=10)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert query.status == "pending"
        assert query.limit == 10

    @pytest.mark.asyncio
    async def test_execute_returns_list_requests_output(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput()
        result = await orchestrator.execute(input)
        assert isinstance(result, ListRequestsOutput)
        assert hasattr(result, "requests")

    @pytest.mark.asyncio
    async def test_execute_maps_results_to_dicts(self, orchestrator, mock_query_bus):
        r = MagicMock(spec=["to_dict"])
        r.to_dict = MagicMock(return_value={"request_id": "req-1"})
        mock_query_bus.execute.return_value = [r]
        input = ListRequestsInput()
        result = await orchestrator.execute(input)
        assert result.requests == [{"request_id": "req-1"}]

    @pytest.mark.asyncio
    async def test_execute_none_result_returns_empty_list(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = None
        input = ListRequestsInput()
        result = await orchestrator.execute(input)
        assert result.requests == []

    @pytest.mark.asyncio
    async def test_execute_does_not_call_command_bus(
        self, orchestrator, mock_command_bus, mock_query_bus
    ):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput()
        await orchestrator.execute(input)
        mock_command_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_true_with_status_forwards_status(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=True, status="pending")
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, SyncAndListActiveRequestsQuery)
        assert query.status == "pending"

    @pytest.mark.asyncio
    async def test_sync_true_with_no_status_does_not_filter(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        input = ListRequestsInput(sync=True, status=None)
        await orchestrator.execute(input)
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, SyncAndListActiveRequestsQuery)
        assert query.status is None

    @pytest.mark.asyncio
    async def test_execute_paginated_result_returns_items(self, orchestrator, mock_query_bus):
        """Regression guard: Paginated return shape surfaces .items without crashing."""
        r = MagicMock(spec=["to_dict"])
        r.to_dict.return_value = {"request_id": "req-1"}
        mock_query_bus.execute.return_value = Paginated(items=[r], total_count=1)
        result = await orchestrator.execute(ListRequestsInput())
        assert isinstance(result, ListRequestsOutput)
        assert result.requests == [{"request_id": "req-1"}]
        assert result.count == 1


def _page(next_position=None):
    from orb.application.services.orchestration.page_cursor import KeysetPaginated

    return KeysetPaginated(items=[], total_count=0, next_position=next_position)


@pytest.mark.unit
@pytest.mark.application
class TestListRequestsDateRange:
    @pytest.mark.asyncio
    async def test_bounds_become_aware_utc_datetimes_on_the_query(
        self, orchestrator, mock_query_bus
    ):
        from datetime import datetime, timezone

        mock_query_bus.execute.return_value = []
        await orchestrator.execute(
            ListRequestsInput(start_time="2026-11-01", end_time="2026-11-02T10:00:00+02:00")
        )
        query = mock_query_bus.execute.call_args[0][0]
        assert query.start_time == datetime(2026, 11, 1, tzinfo=timezone.utc)
        assert query.end_time == datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
        assert query.end_time.utcoffset().total_seconds() == 0

    @pytest.mark.asyncio
    async def test_naive_timestamp_is_utc(self, orchestrator, mock_query_bus):
        from datetime import datetime, timezone

        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListRequestsInput(start_time="2026-11-01T09:30:00"))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.start_time == datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_date_only_end_is_midnight_utc(self, orchestrator, mock_query_bus):
        from datetime import datetime, timezone

        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListRequestsInput(end_time="2026-11-26"))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.end_time == datetime(2026, 11, 26, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("blank", ["", "   "])
    async def test_blank_bound_means_unset(self, orchestrator, mock_query_bus, blank):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListRequestsInput(start_time=blank, end_time=blank))
        query = mock_query_bus.execute.call_args[0][0]
        assert query.start_time is None
        assert query.end_time is None

    @pytest.mark.asyncio
    async def test_blank_bound_allowed_with_sync(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListRequestsInput(sync=True, start_time=""))
        assert isinstance(mock_query_bus.execute.call_args[0][0], SyncAndListActiveRequestsQuery)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["start_time", "end_time"])
    @pytest.mark.parametrize("bad", ["yesterday", "2026-13-45", "26/11/2026"])
    async def test_malformed_bound_is_validation_error_without_echo(
        self, orchestrator, mock_query_bus, field, bad
    ):
        from orb.domain.base.exceptions import ValidationError

        with pytest.raises(ValidationError) as exc:
            await orchestrator.execute(ListRequestsInput(**{field: bad}))
        assert field in str(exc.value)
        assert "YYYY-MM-DD" in str(exc.value)
        assert bad not in str(exc.value)
        mock_query_bus.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_after_end_rejected(self, orchestrator):
        from orb.domain.base.exceptions import ValidationError

        with pytest.raises(ValidationError, match="start_time must not be after end_time"):
            await orchestrator.execute(
                ListRequestsInput(start_time="2026-11-02", end_time="2026-11-01")
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["start_time", "end_time"])
    async def test_sync_with_range_rejected(self, orchestrator, field):
        from orb.domain.base.exceptions import ValidationError

        with pytest.raises(ValidationError, match="not supported with sync=true"):
            await orchestrator.execute(ListRequestsInput(sync=True, **{field: "2026-11-01"}))

    @pytest.mark.asyncio
    async def test_sync_with_other_filters_unchanged(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = []
        await orchestrator.execute(ListRequestsInput(sync=True, status="pending", template_id="t"))
        query = mock_query_bus.execute.call_args[0][0]
        assert isinstance(query, SyncAndListActiveRequestsQuery)
        assert query.status == "pending"
        assert query.template_id == "t"

    async def _cursor(self, orchestrator, mock_query_bus, **inputs):
        mock_query_bus.execute.return_value = _page(("2026-11-01", "r1"))
        out = await orchestrator.execute(ListRequestsInput(sort="created_at", limit=1, **inputs))
        assert out.next_cursor
        return out.next_cursor

    @pytest.mark.asyncio
    async def test_cursor_is_bound_to_the_range(self, orchestrator, mock_query_bus):
        from orb.domain.base.exceptions import ValidationError

        cursor = await self._cursor(orchestrator, mock_query_bus, start_time="2026-11-01")
        mock_query_bus.execute.return_value = _page()
        with pytest.raises(ValidationError):
            await orchestrator.execute(
                ListRequestsInput(sort="created_at", cursor=cursor, start_time="2026-11-02")
            )
        with pytest.raises(ValidationError):
            await orchestrator.execute(ListRequestsInput(sort="created_at", cursor=cursor))

    @pytest.mark.asyncio
    async def test_equivalent_instants_share_a_cursor(self, orchestrator, mock_query_bus):
        cursor = await self._cursor(
            orchestrator, mock_query_bus, start_time="2026-11-01T10:00:00Z", end_time="2026-11-02"
        )
        mock_query_bus.execute.return_value = _page()
        await orchestrator.execute(
            ListRequestsInput(
                sort="created_at",
                cursor=cursor,
                start_time="2026-11-01T12:00:00+02:00",
                end_time="2026-11-02T00:00:00+00:00",
            )
        )

    @pytest.mark.asyncio
    async def test_cursor_without_range_unchanged(self, orchestrator, mock_query_bus):
        # A cursor minted before the range existed must stay valid
        cursor = await self._cursor(orchestrator, mock_query_bus)
        mock_query_bus.execute.return_value = _page()
        await orchestrator.execute(ListRequestsInput(sort="created_at", cursor=cursor))

    def test_fingerprint_without_range_is_stable(self):
        from orb.application.services.orchestration.page_cursor import query_fingerprint

        # Golden value: listings without a range must hash as they did before the range existed
        fp = query_fingerprint(
            "requests",
            status=None,
            q=None,
            filter_expressions=[],
            sync=False,
            sort="-created_at",
            start_time=None,
            end_time=None,
        )
        assert fp == "FGHj5CkXZrA"
