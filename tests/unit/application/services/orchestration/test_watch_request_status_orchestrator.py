"""Unit tests for WatchRequestStatusOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import (
    GetRequestQuery,
    GetTemplateQuery,
    ListMachinesQuery,
)
from orb.application.services.orchestration.dtos import (
    WatchRequestStatusInput,
    WatchRequestStatusOutput,
)
from orb.application.services.orchestration.watch_request_status import (
    WatchRequestStatusOrchestrator,
)


@pytest.fixture
def mock_query_bus():
    """Bus that dispatches by query type. Set bus._request, bus._machines,
    bus._template per-test to control responses. Falls back to defaults."""
    bus = MagicMock()
    bus._request = None
    bus._machines: list = []
    bus._template = None
    bus._template_raises = None

    async def _execute(query):
        if isinstance(query, GetRequestQuery):
            return bus._request if bus._request is not None else _make_request_dto()
        if isinstance(query, ListMachinesQuery):
            return list(bus._machines or [])
        if isinstance(query, GetTemplateQuery):
            if bus._template_raises is not None:
                raise bus._template_raises
            return bus._template
        raise AssertionError(f"unexpected query: {type(query).__name__}")

    bus.execute = AsyncMock(side_effect=_execute)
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


_REF_COUNTER = {"n": 0}


def _make_machine_ref(instance_type="t3.large", price_type="ondemand", machine_id=None):
    """Build a machine_reference (HF-shaped, no vcpus/az)."""
    ref = MagicMock()
    if machine_id is None:
        _REF_COUNTER["n"] += 1
        machine_id = f"m-{_REF_COUNTER['n']}"
    ref.machine_id = machine_id
    ref.instance_type = instance_type
    ref.price_type = price_type
    return ref


def _make_machine_dto(machine_id, vcpus=2, az="eu-west-1a"):
    """Build a MachineDTO mock with provider_data populated."""
    dto = MagicMock()
    dto.machine_id = machine_id
    dto.provider_data = {"vcpus": vcpus, "availability_zone": az}
    return dto


def _wire(bus, *, refs=None, machines=None, template=None, template_raises=None):
    """Wire request_dto/machine list/template onto the mock bus."""
    bus._request = _make_request_dto(machine_references=refs or [])
    bus._machines = machines or []
    bus._template = template
    bus._template_raises = template_raises


@pytest.mark.unit
@pytest.mark.application
class TestWatchRequestStatusOrchestrator:
    @pytest.mark.asyncio
    async def test_dispatches_get_request_query_with_skip_cache(self, orchestrator, mock_query_bus):
        _wire(mock_query_bus)
        await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        request_queries = [
            c[0][0]
            for c in mock_query_bus.execute.call_args_list
            if isinstance(c[0][0], GetRequestQuery)
        ]
        assert len(request_queries) == 1
        assert request_queries[0].skip_cache is True
        assert request_queries[0].lightweight is False

    @pytest.mark.asyncio
    async def test_dispatches_list_machines_query_lightweight(self, orchestrator, mock_query_bus):
        _wire(mock_query_bus)
        await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        list_queries = [
            c[0][0]
            for c in mock_query_bus.execute.call_args_list
            if isinstance(c[0][0], ListMachinesQuery)
        ]
        assert len(list_queries) == 1
        assert list_queries[0].request_id == "req-123"
        assert list_queries[0].lightweight is True

    @pytest.mark.asyncio
    async def test_returns_watch_output(self, orchestrator, mock_query_bus):
        _wire(mock_query_bus)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert isinstance(result, WatchRequestStatusOutput)
        assert result.request_id == "req-123"

    @pytest.mark.asyncio
    async def test_terminal_status_detected(self, orchestrator, mock_query_bus):
        mock_query_bus._request = _make_request_dto(status="complete")
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.terminal is True

    @pytest.mark.asyncio
    async def test_active_status_not_terminal(self, orchestrator, mock_query_bus):
        mock_query_bus._request = _make_request_dto(status="in_progress")
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.terminal is False

    @pytest.mark.asyncio
    async def test_vcpus_summed_from_provider_data(self, orchestrator, mock_query_bus):
        refs = [_make_machine_ref(machine_id="m-a"), _make_machine_ref(machine_id="m-b")]
        machines = [
            _make_machine_dto("m-a", vcpus=2),
            _make_machine_dto("m-b", vcpus=4),
        ]
        _wire(mock_query_bus, refs=refs, machines=machines)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.fulfilled_vcpus == 6
        assert result.fulfilled_count == 2

    @pytest.mark.asyncio
    async def test_od_spot_split(self, orchestrator, mock_query_bus):
        refs = [
            _make_machine_ref(machine_id="m1", price_type="ondemand"),
            _make_machine_ref(machine_id="m2", price_type="spot"),
            _make_machine_ref(machine_id="m3", price_type="spot"),
        ]
        machines = [
            _make_machine_dto("m1", vcpus=2),
            _make_machine_dto("m2", vcpus=4),
            _make_machine_dto("m3", vcpus=2),
        ]
        _wire(mock_query_bus, refs=refs, machines=machines)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.od_vcpus == 2
        assert result.spot_vcpus == 6
        assert result.od_machines == 1
        assert result.spot_machines == 2

    @pytest.mark.asyncio
    async def test_az_stats_grouped(self, orchestrator, mock_query_bus):
        refs = [
            _make_machine_ref(machine_id="m1", price_type="ondemand"),
            _make_machine_ref(machine_id="m2", price_type="spot"),
            _make_machine_ref(machine_id="m3", price_type="spot"),
        ]
        machines = [
            _make_machine_dto("m1", vcpus=2, az="eu-west-1a"),
            _make_machine_dto("m2", vcpus=4, az="eu-west-1b"),
            _make_machine_dto("m3", vcpus=2, az="eu-west-1a"),
        ]
        _wire(mock_query_bus, refs=refs, machines=machines)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert "eu-west-1a" in result.az_stats
        assert "eu-west-1b" in result.az_stats
        assert result.az_stats["eu-west-1a"]["od_vcpus"] == 2
        assert result.az_stats["eu-west-1a"]["spot_vcpus"] == 2
        assert result.az_stats["eu-west-1b"]["spot_vcpus"] == 4

    @pytest.mark.asyncio
    async def test_az_stats_machine_counts(self, orchestrator, mock_query_bus):
        refs = [
            _make_machine_ref(machine_id="m1", price_type="ondemand"),
            _make_machine_ref(machine_id="m2", price_type="spot"),
            _make_machine_ref(machine_id="m3", price_type="ondemand"),
        ]
        machines = [
            _make_machine_dto("m1", az="eu-west-1a"),
            _make_machine_dto("m2", az="eu-west-1a"),
            _make_machine_dto("m3", az="eu-west-1b"),
        ]
        _wire(mock_query_bus, refs=refs, machines=machines)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.az_stats["eu-west-1a"]["od_machines"] == 1
        assert result.az_stats["eu-west-1a"]["spot_machines"] == 1
        assert result.az_stats["eu-west-1b"]["od_machines"] == 1
        assert result.az_stats["eu-west-1b"]["spot_machines"] == 0

    @pytest.mark.asyncio
    async def test_az_unknown_when_provider_data_missing(self, orchestrator, mock_query_bus):
        # Machine reference exists but no MachineDTO matches (race window).
        refs = [_make_machine_ref(machine_id="m-orphan")]
        _wire(mock_query_bus, refs=refs, machines=[])
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert "unknown" in result.az_stats
        assert result.fulfilled_vcpus == 0

    @pytest.mark.asyncio
    async def test_weighted_capacity_from_template(self, orchestrator, mock_query_bus):
        refs = [
            _make_machine_ref(machine_id="m1", instance_type="t3.large", price_type="ondemand"),
            _make_machine_ref(machine_id="m2", instance_type="t3.medium", price_type="spot"),
        ]
        machines = [
            _make_machine_dto("m1", vcpus=2),
            _make_machine_dto("m2", vcpus=2),
        ]
        template = MagicMock()
        template.machine_types = {"t3.large": 2, "t3.medium": 1}
        _wire(mock_query_bus, refs=refs, machines=machines, template=template)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.weighted is True
        assert result.fulfilled_capacity == 3  # 2 + 1
        assert result.od_capacity == 2
        assert result.spot_capacity == 1

    @pytest.mark.asyncio
    async def test_template_cache_reused(self, orchestrator, mock_query_bus):
        refs = [_make_machine_ref(machine_id="m1")]
        machines = [_make_machine_dto("m1")]
        template = MagicMock()
        template.machine_types = {"t3.large": 2}
        _wire(mock_query_bus, refs=refs, machines=machines, template=template)
        await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
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
        refs = [_make_machine_ref(machine_id="m1")]
        machines = [_make_machine_dto("m1")]
        _wire(
            mock_query_bus,
            refs=refs,
            machines=machines,
            template_raises=Exception("template not found"),
        )
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.weighted is False
        assert result.fulfilled_capacity == 0
        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_vcpus_fallback_zero_when_provider_data_missing_vcpus(
        self, orchestrator, mock_query_bus
    ):
        refs = [_make_machine_ref(machine_id="m1")]
        dto = MagicMock()
        dto.machine_id = "m1"
        dto.provider_data = {"availability_zone": "eu-west-1a"}  # no vcpus key
        _wire(mock_query_bus, refs=refs, machines=[dto])
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.fulfilled_vcpus == 0

    @pytest.mark.asyncio
    async def test_created_at_passed_through(self, orchestrator, mock_query_bus):
        _wire(mock_query_bus)
        result = await orchestrator.execute(WatchRequestStatusInput(request_id="req-123"))
        assert result.created_at == "2026-04-20T00:00:00Z"
