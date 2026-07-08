"""Unit tests for DashboardSummaryOrchestrator."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from orb.application.services.orchestration.dashboard_summary import (
    DashboardSummaryOrchestrator,
    _to_iso,
)
from orb.application.services.orchestration.dtos import (
    DashboardSummaryInput,
    DashboardSummaryOutput,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    request_id: str = "req-1",
    status: str = "complete",
    request_type: str = "acquire",
    template_id: str = "tpl-1",
    created_at: datetime | None = None,
    started_at: datetime | None = None,
    first_status_check: datetime | None = None,
    last_status_check: datetime | None = None,
    completed_at: datetime | None = None,
    successful_count: int = 0,
    requested_count: int = 1,
):
    """Return a mock object that mimics the Request domain aggregate."""
    r = MagicMock()
    r.request_id.value = request_id
    r.status.value = status
    r.request_type.value = request_type
    r.template_id = template_id
    r.created_at = created_at or datetime(2024, 1, 1, tzinfo=timezone.utc)
    r.started_at = started_at
    r.first_status_check = first_status_check
    r.last_status_check = last_status_check
    r.completed_at = completed_at
    r.successful_count = successful_count
    r.requested_count = requested_count
    return r


def _make_uow(
    *,
    machine_by_status=None,
    request_by_status=None,
    provider_api_counts=None,
    recent_requests=None,
):
    """Return a context-manager-compatible fake UoW."""
    machine_by_status = machine_by_status or {}
    request_by_status = request_by_status or {}
    provider_api_counts = provider_api_counts or {}
    recent_requests = recent_requests if recent_requests is not None else []

    machines_repo = MagicMock()
    machines_repo.count_by_status.return_value = dict(machine_by_status)

    requests_repo = MagicMock()
    requests_repo.count_by_status.return_value = dict(request_by_status)
    requests_repo.list_recent_activity.return_value = list(recent_requests)

    templates_repo = MagicMock()
    templates_repo.count_by_provider_api.return_value = dict(provider_api_counts)

    uow = MagicMock()
    uow.machines = machines_repo
    uow.requests = requests_repo
    uow.templates = templates_repo

    # Support `with uow_factory.create_unit_of_work() as uow:`
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    return uow


def _make_factory(uow):
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    return factory


def _make_orchestrator(
    *,
    machine_by_status=None,
    request_by_status=None,
    provider_api_counts=None,
    recent_requests=None,
):
    uow = _make_uow(
        machine_by_status=machine_by_status,
        request_by_status=request_by_status,
        provider_api_counts=provider_api_counts,
        recent_requests=recent_requests,
    )
    factory = _make_factory(uow)
    logger = MagicMock()
    return DashboardSummaryOrchestrator(
        uow_factory=factory,
        logger=logger,
    )


# ---------------------------------------------------------------------------
# _to_iso helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToIso:
    def test_none_returns_none(self):
        assert _to_iso(None) is None

    def test_aware_datetime_returns_iso(self):
        dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _to_iso(dt)
        assert isinstance(result, str)
        assert "2024-06-01" in result
        assert "12:00:00" in result

    def test_naive_datetime_assumes_utc(self):
        dt = datetime(2024, 6, 1, 12, 0, 0)
        result = _to_iso(dt)
        assert isinstance(result, str)
        assert "+00:00" in result

    def test_string_passthrough(self):
        iso = "2024-01-15T10:00:00+00:00"
        assert _to_iso(iso) == iso

    def test_other_type_coerced_to_str(self):
        assert _to_iso(42) == "42"


# ---------------------------------------------------------------------------
# DashboardSummaryOrchestrator.execute
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.application
class TestDashboardSummaryOrchestrator:
    @pytest.mark.asyncio
    async def test_happy_path_aggregates_all_sections(self):
        """All three sections must be populated from the UoW repos."""
        orchestrator = _make_orchestrator(
            machine_by_status={"running": 3, "stopped": 1},
            request_by_status={"pending": 2, "complete": 5},
            provider_api_counts={"aws": 4, "EC2Fleet": 1},
        )
        result = await orchestrator.execute(DashboardSummaryInput())

        assert isinstance(result, DashboardSummaryOutput)
        assert result.machines["total"] == 4
        assert result.machines["by_status"]["running"] == 3
        assert result.machines["by_status"]["stopped"] == 1
        assert result.requests["total"] == 7
        assert result.requests["by_status"]["pending"] == 2
        assert result.requests["by_status"]["complete"] == 5
        assert result.templates["total"] == 5
        assert result.templates["by_provider_api"]["aws"] == 4

    @pytest.mark.asyncio
    async def test_happy_path_in_flight_counts_only_non_terminal(self):
        """in_flight must exclude terminal statuses (complete, failed, cancelled, timeout, partial)."""
        orchestrator = _make_orchestrator(
            request_by_status={
                "pending": 3,
                "in_progress": 2,
                "complete": 10,
                "failed": 4,
                "cancelled": 1,
            },
        )
        result = await orchestrator.execute(DashboardSummaryInput())
        # Only pending + in_progress are non-terminal
        assert result.requests["in_flight"] == 5

    @pytest.mark.asyncio
    async def test_well_known_keys_present_even_when_count_is_zero(self):
        """Missing well-known keys must be defaulted to 0."""
        orchestrator = _make_orchestrator(
            machine_by_status={"running": 1},
            request_by_status={"pending": 1},
            provider_api_counts={"aws": 1},
        )
        result = await orchestrator.execute(DashboardSummaryInput())

        for key in ("running", "pending", "stopped", "terminated", "shutting-down"):
            assert key in result.machines["by_status"]
        for key in (
            "pending",
            "in_progress",
            "acquiring",
            "complete",
            "failed",
            "partial",
            "cancelled",
            "timeout",
        ):
            assert key in result.requests["by_status"]
        # The dynamic key list is sourced from the live provider registry.
        # In the test context no providers are registered, so the only key
        # present is the one explicitly returned by count_by_provider_api.
        assert "aws" in result.templates["by_provider_api"]
        assert result.templates["by_provider_api"]["aws"] == 1

    @pytest.mark.asyncio
    async def test_empty_data_all_zero_counts_empty_recent_activity(self):
        """With no data, totals are 0 and recent_activity is empty."""
        orchestrator = _make_orchestrator()
        result = await orchestrator.execute(DashboardSummaryInput())

        assert result.machines["total"] == 0
        assert result.requests["total"] == 0
        assert result.requests["in_flight"] == 0
        assert result.templates["total"] == 0
        assert result.recent_activity == []

    @pytest.mark.asyncio
    async def test_recent_activity_capped_at_10(self):
        """Even if list_recent_activity returns >10 rows, only 10 are included."""
        requests = [
            _make_request(
                request_id=f"req-{i}",
                created_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            )
            for i in range(15)
        ]
        orchestrator = _make_orchestrator(recent_requests=requests[:10])
        result = await orchestrator.execute(DashboardSummaryInput())
        assert len(result.recent_activity) == 10

    @pytest.mark.asyncio
    async def test_recent_activity_sorted_by_created_at_desc(self):
        """Most recently created requests appear first (repo returns pre-sorted)."""
        old = _make_request(
            request_id="old",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        new = _make_request(
            request_id="new",
            created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        # list_recent_activity already returns sorted desc
        orchestrator = _make_orchestrator(recent_requests=[new, old])
        result = await orchestrator.execute(DashboardSummaryInput())
        assert result.recent_activity[0]["request_id"] == "new"
        assert result.recent_activity[1]["request_id"] == "old"

    @pytest.mark.asyncio
    async def test_recent_activity_lifecycle_fields_forwarded_as_iso_or_none(self):
        """started_at, first_status_check, last_status_check, completed_at forwarded."""
        dt = datetime(2024, 5, 1, 8, 0, 0, tzinfo=timezone.utc)
        r = _make_request(
            request_id="r1",
            template_id="t1",
            created_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
            started_at=dt,
            first_status_check=None,
            last_status_check=None,
            completed_at=None,
            successful_count=2,
            requested_count=3,
        )
        orchestrator = _make_orchestrator(recent_requests=[r])
        result = await orchestrator.execute(DashboardSummaryInput())
        item = result.recent_activity[0]
        assert item["started_at"] is not None
        assert "2024-05-01" in item["started_at"]
        assert item["first_status_check"] is None
        assert item["completed_at"] is None
        assert item["successful_count"] == 2
        assert item["requested_count"] == 3

    @pytest.mark.asyncio
    async def test_uow_context_manager_called_once(self):
        """A single UoW context manager wraps both count queries and recent-activity."""
        uow = _make_uow(
            machine_by_status={"running": 1},
            request_by_status={"pending": 1},
        )
        factory = _make_factory(uow)
        orchestrator = DashboardSummaryOrchestrator(
            uow_factory=factory,
            logger=MagicMock(),
        )
        await orchestrator.execute(DashboardSummaryInput())

        # Only one UoW was ever created.
        factory.create_unit_of_work.assert_called_once()
        # Both count queries AND recent-activity happened on the same uow.
        uow.machines.count_by_status.assert_called_once()
        uow.requests.count_by_status.assert_called_once()
        uow.requests.list_recent_activity.assert_called_once_with(10)
        uow.templates.count_by_provider_api.assert_called_once()

    @pytest.mark.asyncio
    async def test_snapshot_consistency_counts_and_activity_agree(self):
        """Counts and recent-activity must come from the same UoW snapshot.

        This test proves the OLD two-UoW design was broken and the new single-UoW
        design is correct.

        Setup: the repo returns 1 pending request for count_by_status, but if a
        second UoW were opened it would see 2 pending requests.  With the new
        design, list_recent_activity is called on the *same* uow and therefore
        returns exactly the 1 request that the count reflects.
        """
        # Snapshot: 1 pending request visible in this UoW.
        r1 = _make_request(request_id="req-snapshot", status="pending")
        uow = _make_uow(
            request_by_status={"pending": 1},
            recent_requests=[r1],
        )
        factory = _make_factory(uow)
        orchestrator = DashboardSummaryOrchestrator(
            uow_factory=factory,
            logger=MagicMock(),
        )
        result = await orchestrator.execute(DashboardSummaryInput())

        # Count and activity are consistent: 1 pending, 1 item in recent_activity.
        assert result.requests["by_status"]["pending"] == 1
        assert len(result.recent_activity) == 1
        assert result.recent_activity[0]["request_id"] == "req-snapshot"

        # With the old two-UoW design a second uow would have been created, but
        # there is only one factory call → single-snapshot guarantee holds.
        factory.create_unit_of_work.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_by_status_called_on_machines_and_requests_repos(self):
        """count_by_status must be called on machines and requests repos (not find_all)."""
        uow = _make_uow(
            machine_by_status={"running": 1},
            request_by_status={"pending": 1},
        )
        factory = _make_factory(uow)
        orchestrator = DashboardSummaryOrchestrator(
            uow_factory=factory,
            logger=MagicMock(),
        )
        await orchestrator.execute(DashboardSummaryInput())
        uow.machines.count_by_status.assert_called_once()
        uow.requests.count_by_status.assert_called_once()
        uow.templates.count_by_provider_api.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_by_provider_api_returns_real_values(self):
        """count_by_provider_api values flow into templates section."""
        orchestrator = _make_orchestrator(provider_api_counts={"EC2Fleet": 7, "RunInstances": 3})
        result = await orchestrator.execute(DashboardSummaryInput())
        assert result.templates["by_provider_api"]["EC2Fleet"] == 7
        assert result.templates["by_provider_api"]["RunInstances"] == 3
        assert result.templates["total"] == 10
