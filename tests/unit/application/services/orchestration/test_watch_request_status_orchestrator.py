"""Unit tests for WatchRequestStatusOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import GetRequestQuery, GetTemplateQuery
from orb.application.services.orchestration.dtos import (
    WatchRequestStatusInput,
    WatchRequestStatusOutput,
)
from orb.application.services.orchestration.watch_request_status import (
    WatchRequestStatusOrchestrator,
)


@pytest.fixture
def mock_query_bus():
    bus = MagicMock()
    bus.execute = AsyncMock()
    return bus


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def orchestrator(mock_query_bus, mock_logger):
    return WatchRequestStatusOrchestrator(
        query_bus=mock_query_bus,
        logger=mock_logger,
    )


def _make_request_dto(**overrides):
    """Build a mock RequestDTO with sensible defaults."""
    defaults = {
        "status": "in_progress",
        "requested_count": 10,
        "template_id": "tmpl-1",
        "created_at": MagicMock(isoformat=MagicMock(return_value="2026-04-20T00:00:00Z")),
        "machine_references": [],
        "machine_ids": [],
    }
    defaults.update(overrides)
    dto = MagicMock()
    for k, v in defaults.items():
        setattr(dto, k, v)
    return dto


def _make_machine_ref(instance_type="t3.large", price_type="ondemand", vcpus=2, az="eu-west-1a"):
    ref = MagicMock()
    ref.instance_type = instance_type
    ref.price_type = price_type
    ref.vcpus = vcpus
    ref.availability_zone = az
    return ref


@pytest.mark.unit
@pytest.mark.application
class TestWatchRequestStatusOrchestrator:
    @pytest.mark.asyncio
    async def test_dispatches_get_request_query_with_skip_cache(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_request_dto()
        await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        query = mock_query_bus.execute.call_args_list[0][0][0]
        assert isinstance(query, GetRequestQuery)
        assert query.skip_cache is True
        assert query.lightweight is False

    @pytest.mark.asyncio
    async def test_returns_watch_output(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_request_dto()
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert isinstance(result, WatchRequestStatusOutput)
        assert result.request_id == "req-123"

    @pytest.mark.asyncio
    async def test_terminal_status_detected(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_request_dto(status="complete")
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.terminal is True

    @pytest.mark.asyncio
    async def test_active_status_not_terminal(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_request_dto(status="in_progress")
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.terminal is False

    @pytest.mark.asyncio
    async def test_vcpus_summed_from_machine_refs(self, orchestrator, mock_query_bus):
        refs = [
            _make_machine_ref(vcpus=2),
            _make_machine_ref(vcpus=4),
        ]
        mock_query_bus.execute.return_value = _make_request_dto(machine_references=refs)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.fulfilled_vcpus == 6
        assert result.fulfilled_count == 2

    @pytest.mark.asyncio
    async def test_od_spot_split(self, orchestrator, mock_query_bus):
        refs = [
            _make_machine_ref(vcpus=2, price_type="ondemand"),
            _make_machine_ref(vcpus=4, price_type="spot"),
            _make_machine_ref(vcpus=2, price_type="spot"),
        ]
        mock_query_bus.execute.return_value = _make_request_dto(machine_references=refs)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.od_vcpus == 2
        assert result.spot_vcpus == 6
        assert result.od_machines == 1
        assert result.spot_machines == 2

    @pytest.mark.asyncio
    async def test_az_stats_grouped(self, orchestrator, mock_query_bus):
        refs = [
            _make_machine_ref(vcpus=2, az="eu-west-1a", price_type="ondemand"),
            _make_machine_ref(vcpus=4, az="eu-west-1b", price_type="spot"),
            _make_machine_ref(vcpus=2, az="eu-west-1a", price_type="spot"),
        ]
        mock_query_bus.execute.return_value = _make_request_dto(machine_references=refs)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert "eu-west-1a" in result.az_stats
        assert "eu-west-1b" in result.az_stats
        assert result.az_stats["eu-west-1a"]["od_vcpus"] == 2
        assert result.az_stats["eu-west-1a"]["spot_vcpus"] == 2
        assert result.az_stats["eu-west-1b"]["spot_vcpus"] == 4

    @pytest.mark.asyncio
    async def test_az_stats_machine_counts(self, orchestrator, mock_query_bus):
        refs = [
            _make_machine_ref(az="eu-west-1a", price_type="ondemand"),
            _make_machine_ref(az="eu-west-1a", price_type="spot"),
            _make_machine_ref(az="eu-west-1b", price_type="ondemand"),
        ]
        mock_query_bus.execute.return_value = _make_request_dto(machine_references=refs)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.az_stats["eu-west-1a"]["od_machines"] == 1
        assert result.az_stats["eu-west-1a"]["spot_machines"] == 1
        assert result.az_stats["eu-west-1b"]["od_machines"] == 1
        assert result.az_stats["eu-west-1b"]["spot_machines"] == 0

    @pytest.mark.asyncio
    async def test_weighted_capacity_from_template(self, orchestrator, mock_query_bus):
        refs = [
            _make_machine_ref(instance_type="t3.large", vcpus=2, price_type="ondemand"),
            _make_machine_ref(instance_type="t3.medium", vcpus=2, price_type="spot"),
        ]
        # First call: GetRequestQuery, second call: GetTemplateQuery
        template = MagicMock()
        template.machine_types = {"t3.large": 2, "t3.medium": 1}
        mock_query_bus.execute.side_effect = [
            _make_request_dto(machine_references=refs),
            template,
        ]
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.weighted is True
        assert result.fulfilled_capacity == 3  # 2 + 1
        assert result.od_capacity == 2
        assert result.spot_capacity == 1

    @pytest.mark.asyncio
    async def test_template_cache_reused(self, orchestrator, mock_query_bus):
        refs = [_make_machine_ref()]
        template = MagicMock()
        template.machine_types = {"t3.large": 2}
        mock_query_bus.execute.side_effect = [
            _make_request_dto(machine_references=refs),
            template,
            _make_request_dto(machine_references=refs),
        ]
        await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        # Template query should only be called once (cached)
        template_calls = [
            c
            for c in mock_query_bus.execute.call_args_list
            if isinstance(c[0][0], GetTemplateQuery)
        ]
        assert len(template_calls) == 1

    @pytest.mark.asyncio
    async def test_template_load_failure_sets_weighted_false(
        self, orchestrator, mock_query_bus, mock_logger
    ):
        refs = [_make_machine_ref()]
        mock_query_bus.execute.side_effect = [
            _make_request_dto(machine_references=refs),
            Exception("template not found"),
        ]
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.weighted is False
        assert result.fulfilled_capacity == 0
        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_vcpus_fallback_zero_when_none(self, orchestrator, mock_query_bus):
        ref = _make_machine_ref(vcpus=None)
        mock_query_bus.execute.return_value = _make_request_dto(machine_references=[ref])
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.fulfilled_vcpus == 0

    @pytest.mark.asyncio
    async def test_created_at_passed_through(self, orchestrator, mock_query_bus):
        mock_query_bus.execute.return_value = _make_request_dto()
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.created_at == "2026-04-20T00:00:00Z"
