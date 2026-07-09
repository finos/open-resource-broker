"""Unit tests for specialized query handlers (B2-B4).

Tests cover:
- B2: GetActiveMachineCountHandler — refactored to count_by_status aggregation
- B3: GetRequestSummaryHandler — all-status grouping
- B4: GetRequestMetricsHandler — time-windowed metrics via get_metrics_by_date_range
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.application.dto.queries import (
    GetActiveMachineCountQuery,
    GetRequestSummaryQuery,
)
from orb.application.queries.specialized_handlers import (
    _ACTIVE_MACHINE_STATUSES,
    GetActiveMachineCountHandler,
    GetRequestMetricsHandler,
    GetRequestSummaryHandler,
)
from orb.application.request.queries import GetRequestMetricsQuery
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.machine.value_objects import MachineStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_uow_factory(
    *,
    machine_by_status: dict[str, int] | None = None,
    request: Any = None,
    machines: list[Any] | None = None,
    request_metrics: dict[str, int] | None = None,
) -> MagicMock:
    uow = MagicMock()
    uow.machines.count_by_status.return_value = machine_by_status or {}
    uow.requests.get_by_id.return_value = request
    uow.machines.find_by_request_id.return_value = machines or []
    uow.requests.get_metrics_by_date_range.return_value = request_metrics or {}
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    return factory


def _make_handler_deps(factory):
    return factory, MagicMock(), MagicMock()


def _make_machine_mock(status_value: str) -> MagicMock:
    m = MagicMock()
    m.status = MagicMock()
    m.status.value = status_value
    return m


def _make_request_mock(request_id: str = "req-1", status_value: str = "complete") -> MagicMock:
    r = MagicMock()
    r.request_id = request_id
    r.status = MagicMock()
    r.status.value = status_value
    r.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return r


# ---------------------------------------------------------------------------
# B2 — GetActiveMachineCountHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetActiveMachineCountHandler:
    async def test_sums_active_status_buckets(self):
        factory = _make_uow_factory(
            machine_by_status={"running": 5, "pending": 3, "launching": 2, "terminated": 10}
        )
        handler = GetActiveMachineCountHandler(*_make_handler_deps(factory))
        result = await handler.execute_query(GetActiveMachineCountQuery())
        assert result == 10  # 5 + 3 + 2

    async def test_no_active_machines_returns_zero(self):
        factory = _make_uow_factory(machine_by_status={"terminated": 7})
        handler = GetActiveMachineCountHandler(*_make_handler_deps(factory))
        assert await handler.execute_query(GetActiveMachineCountQuery()) == 0

    async def test_empty_status_map_returns_zero(self):
        factory = _make_uow_factory(machine_by_status={})
        handler = GetActiveMachineCountHandler(*_make_handler_deps(factory))
        assert await handler.execute_query(GetActiveMachineCountQuery()) == 0

    async def test_uses_count_by_status_not_find_by_statuses(self):
        factory = _make_uow_factory(machine_by_status={"running": 1})
        handler = GetActiveMachineCountHandler(*_make_handler_deps(factory))
        await handler.execute_query(GetActiveMachineCountQuery())
        uow = factory.create_unit_of_work.return_value
        uow.machines.count_by_status.assert_called_once()
        uow.machines.find_by_statuses.assert_not_called()

    async def test_active_statuses_constant_contains_expected_values(self):
        assert MachineStatus.RUNNING.value in _ACTIVE_MACHINE_STATUSES
        assert MachineStatus.PENDING.value in _ACTIVE_MACHINE_STATUSES
        assert MachineStatus.LAUNCHING.value in _ACTIVE_MACHINE_STATUSES
        assert MachineStatus.TERMINATED.value not in _ACTIVE_MACHINE_STATUSES

    async def test_single_uow_context_created(self):
        factory = _make_uow_factory(machine_by_status={"running": 3})
        handler = GetActiveMachineCountHandler(*_make_handler_deps(factory))
        await handler.execute_query(GetActiveMachineCountQuery())
        factory.create_unit_of_work.assert_called_once()


# ---------------------------------------------------------------------------
# B3 — GetRequestSummaryHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetRequestSummaryHandler:
    async def test_groups_all_statuses(self):
        factory = _make_uow_factory(
            request=_make_request_mock("req-1", "complete"),
            machines=[
                _make_machine_mock("running"),
                _make_machine_mock("running"),
                _make_machine_mock("failed"),
                _make_machine_mock("launching"),
            ],
        )
        handler = GetRequestSummaryHandler(*_make_handler_deps(factory))
        result = await handler.execute_query(GetRequestSummaryQuery(request_id="req-1"))
        assert result.machine_statuses == {"running": 2, "failed": 1, "launching": 1}
        assert result.total_machines == 4

    async def test_empty_machine_list(self):
        factory = _make_uow_factory(request=_make_request_mock("req-2"), machines=[])
        handler = GetRequestSummaryHandler(*_make_handler_deps(factory))
        result = await handler.execute_query(GetRequestSummaryQuery(request_id="req-2"))
        assert result.total_machines == 0
        assert result.machine_statuses == {}

    async def test_not_found_raises_entity_not_found(self):
        factory = _make_uow_factory(request=None)
        handler = GetRequestSummaryHandler(*_make_handler_deps(factory))
        with pytest.raises(EntityNotFoundError):
            await handler.execute_query(GetRequestSummaryQuery(request_id="missing"))

    async def test_request_id_and_status_forwarded(self):
        factory = _make_uow_factory(
            request=_make_request_mock("req-abc", "in_progress"),
            machines=[_make_machine_mock("running")],
        )
        handler = GetRequestSummaryHandler(*_make_handler_deps(factory))
        result = await handler.execute_query(GetRequestSummaryQuery(request_id="req-abc"))
        assert result.request_id == "req-abc"
        assert result.status == "in_progress"

    async def test_single_uow_context_created(self):
        factory = _make_uow_factory(request=_make_request_mock("req-x"), machines=[])
        handler = GetRequestSummaryHandler(*_make_handler_deps(factory))
        await handler.execute_query(GetRequestSummaryQuery(request_id="req-x"))
        factory.create_unit_of_work.assert_called_once()


# ---------------------------------------------------------------------------
# B4 — GetRequestMetricsHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetRequestMetricsHandler:
    async def test_returns_metrics_from_repo(self):
        expected = {"total": 100, "completed": 70, "failed": 30, "in_progress": 0, "pending": 0}
        factory = _make_uow_factory(request_metrics=expected)
        handler = GetRequestMetricsHandler(*_make_handler_deps(factory))
        result = await handler.execute_query(
            GetRequestMetricsQuery(
                start_date="2024-01-01T00:00:00+00:00",
                end_date="2024-12-31T23:59:59+00:00",
            )
        )
        assert result["metrics"] == expected
        assert result["group_by"] == "status"

    async def test_no_dates_uses_epoch_to_now(self):
        factory = _make_uow_factory(request_metrics={"total": 0})
        handler = GetRequestMetricsHandler(*_make_handler_deps(factory))
        await handler.execute_query(GetRequestMetricsQuery())
        uow = factory.create_unit_of_work.return_value
        start_dt, end_dt = uow.requests.get_metrics_by_date_range.call_args[0]
        assert start_dt == datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert end_dt > start_dt

    async def test_invalid_date_string_falls_back_gracefully(self):
        factory = _make_uow_factory(request_metrics={"total": 5})
        handler = GetRequestMetricsHandler(*_make_handler_deps(factory))
        result = await handler.execute_query(
            GetRequestMetricsQuery(start_date="not-a-date", end_date="also-not-a-date")
        )
        assert result["metrics"] == {"total": 5}

    async def test_group_by_forwarded_in_response(self):
        factory = _make_uow_factory(request_metrics={})
        handler = GetRequestMetricsHandler(*_make_handler_deps(factory))
        result = await handler.execute_query(GetRequestMetricsQuery(group_by="template_id"))
        assert result["group_by"] == "template_id"

    async def test_start_end_dates_in_response(self):
        factory = _make_uow_factory(request_metrics={})
        handler = GetRequestMetricsHandler(*_make_handler_deps(factory))
        result = await handler.execute_query(
            GetRequestMetricsQuery(start_date="2024-06-01T00:00:00+00:00")
        )
        assert "start_date" in result
        assert "end_date" in result
        assert "2024-06-01" in result["start_date"]

    async def test_single_uow_context_created(self):
        factory = _make_uow_factory(request_metrics={})
        handler = GetRequestMetricsHandler(*_make_handler_deps(factory))
        await handler.execute_query(GetRequestMetricsQuery())
        factory.create_unit_of_work.assert_called_once()
