"""Unit tests for orchestrator-backed request and machine list methods on ORBClient."""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.exceptions import SDKError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _initialized_sdk() -> ORBClient:
    sdk = ORBClient(config={"provider_type": "aws"})
    sdk._initialized = True
    sdk._container = MagicMock()
    return sdk


def _mock_container(sdk: ORBClient, orchestrator_class, orchestrator, scheduler=None):
    """Wire container.get() for one orchestrator plus the shared response formatter,
    and container.get_optional() for scheduler.

    The SDK resolves ``ResponseFormattingService`` from the container to render each
    operation's body, so the fake container returns a real formatter over the given
    (mock) scheduler. Renders therefore delegate through the scheduler exactly as in
    production, keeping response shapes and scheduler-call assertions faithful.
    """
    from orb.interface.response_formatting_service import ResponseFormattingService

    def _get(cls):
        if cls is orchestrator_class:
            return orchestrator
        if cls is ResponseFormattingService:
            return ResponseFormattingService(scheduler if scheduler is not None else MagicMock())
        raise KeyError(cls)

    assert sdk._container is not None
    container: Any = sdk._container
    container.get.side_effect = _get
    container.get_optional.return_value = scheduler


# ---------------------------------------------------------------------------
# list_requests
# ---------------------------------------------------------------------------


class TestListRequests:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_requests_forwards_offset(self):
        from orb.application.services.orchestration.dtos import (
            ListRequestsInput,
            ListRequestsOutput,
        )
        from orb.application.services.orchestration.list_requests import (
            ListRequestsOrchestrator,
        )

        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(return_value=ListRequestsOutput(requests=[]))

        sdk = _initialized_sdk()
        _mock_container(sdk, ListRequestsOrchestrator, mock_orch)

        await sdk.list_requests(offset=5)

        mock_orch.execute.assert_called_once()
        call_input: ListRequestsInput = mock_orch.execute.call_args[0][0]
        assert call_input.offset == 5

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_requests_default_offset_is_zero(self):
        from orb.application.services.orchestration.dtos import (
            ListRequestsInput,
            ListRequestsOutput,
        )
        from orb.application.services.orchestration.list_requests import (
            ListRequestsOrchestrator,
        )

        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(return_value=ListRequestsOutput(requests=[]))

        sdk = _initialized_sdk()
        _mock_container(sdk, ListRequestsOrchestrator, mock_orch)

        await sdk.list_requests()

        call_input: ListRequestsInput = mock_orch.execute.call_args[0][0]
        assert call_input.offset == 0

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_requests_forwards_offset_and_limit(self):
        from orb.application.services.orchestration.dtos import (
            ListRequestsInput,
            ListRequestsOutput,
        )
        from orb.application.services.orchestration.list_requests import (
            ListRequestsOrchestrator,
        )

        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(return_value=ListRequestsOutput(requests=[]))

        sdk = _initialized_sdk()
        _mock_container(sdk, ListRequestsOrchestrator, mock_orch)

        await sdk.list_requests(offset=5, limit=10)

        call_input: ListRequestsInput = mock_orch.execute.call_args[0][0]
        assert call_input.offset == 5
        assert call_input.limit == 10

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_requests_not_initialized_raises(self):
        sdk = ORBClient(config={"provider_type": "aws"})
        with pytest.raises(SDKError):
            await sdk.list_requests()


# ---------------------------------------------------------------------------
# list_machines
# ---------------------------------------------------------------------------


class TestListMachines:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_machines_forwards_offset(self):
        from orb.application.services.orchestration.dtos import (
            ListMachinesInput,
            ListMachinesOutput,
        )
        from orb.application.services.orchestration.list_machines import (
            ListMachinesOrchestrator,
        )

        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(return_value=ListMachinesOutput(machines=[]))

        sdk = _initialized_sdk()
        _mock_container(sdk, ListMachinesOrchestrator, mock_orch)

        await sdk.list_machines(offset=10)

        call_input: ListMachinesInput = mock_orch.execute.call_args[0][0]
        assert call_input.offset == 10

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_machines_forwards_limit(self):
        from orb.application.services.orchestration.dtos import (
            ListMachinesInput,
            ListMachinesOutput,
        )
        from orb.application.services.orchestration.list_machines import (
            ListMachinesOrchestrator,
        )

        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(return_value=ListMachinesOutput(machines=[]))

        sdk = _initialized_sdk()
        _mock_container(sdk, ListMachinesOrchestrator, mock_orch)

        await sdk.list_machines(limit=25)

        call_input: ListMachinesInput = mock_orch.execute.call_args[0][0]
        assert call_input.limit == 25

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_machines_default_offset_and_limit(self):
        from orb.application.services.orchestration.dtos import (
            ListMachinesInput,
            ListMachinesOutput,
        )
        from orb.application.services.orchestration.list_machines import (
            ListMachinesOrchestrator,
        )

        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(return_value=ListMachinesOutput(machines=[]))

        sdk = _initialized_sdk()
        _mock_container(sdk, ListMachinesOrchestrator, mock_orch)

        await sdk.list_machines()

        call_input: ListMachinesInput = mock_orch.execute.call_args[0][0]
        assert call_input.offset == 0
        assert call_input.limit == 100

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_machines_not_initialized_raises(self):
        sdk = ORBClient(config={"provider_type": "aws"})
        with pytest.raises(SDKError):
            await sdk.list_machines()


# ---------------------------------------------------------------------------
# Query-bus-backed methods (no orchestrator)
# ---------------------------------------------------------------------------


class TestGetRequestSummary:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_dispatches_query_and_returns_dict(self):
        from orb.application.dto.queries import GetRequestSummaryQuery
        from orb.application.request.dto import RequestSummaryDTO

        dto = RequestSummaryDTO(
            request_id="req-1",
            status="running",
            total_machines=2,
            machine_statuses={"running": 2},
            created_at=datetime.now(timezone.utc),
        )
        sdk = _initialized_sdk()
        sdk._query_bus = MagicMock()
        sdk._query_bus.execute = AsyncMock(return_value=dto)

        result = await sdk.get_request_summary(request_id="req-1")

        query = sdk._query_bus.execute.call_args[0][0]
        assert isinstance(query, GetRequestSummaryQuery)
        assert query.request_id == "req-1"
        assert result["request_id"] == "req-1"
        assert result["total_machines"] == 2

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_not_found_raises(self):
        from orb.domain.base.exceptions import EntityNotFoundError
        from orb.sdk.exceptions import NotFoundError

        sdk = _initialized_sdk()
        sdk._query_bus = MagicMock()
        sdk._query_bus.execute = AsyncMock(side_effect=EntityNotFoundError("Request", "missing"))

        with pytest.raises(NotFoundError):
            await sdk.get_request_summary(request_id="missing")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_not_initialized_raises(self):
        sdk = ORBClient(config={"provider_type": "aws"})
        with pytest.raises(SDKError):
            await sdk.get_request_summary(request_id="req-1")


class TestGetActiveMachineCount:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_dispatches_query_and_returns_int(self):
        from orb.application.dto.queries import GetActiveMachineCountQuery

        sdk = _initialized_sdk()
        sdk._query_bus = MagicMock()
        sdk._query_bus.execute = AsyncMock(return_value=7)

        result = await sdk.get_active_machine_count()

        query = sdk._query_bus.execute.call_args[0][0]
        assert isinstance(query, GetActiveMachineCountQuery)
        assert result == 7

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_not_initialized_raises(self):
        sdk = ORBClient(config={"provider_type": "aws"})
        with pytest.raises(SDKError):
            await sdk.get_active_machine_count()


class TestGetRequestMetrics:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_dispatches_query_and_forwards_kwargs(self):
        from orb.application.request.queries import GetRequestMetricsQuery

        payload = {"group_by": "template", "metrics": {}}
        sdk = _initialized_sdk()
        sdk._query_bus = MagicMock()
        sdk._query_bus.execute = AsyncMock(return_value=payload)

        result = await sdk.get_request_metrics(
            start_date="2026-01-01", end_date="2026-02-01", group_by="template"
        )

        query = sdk._query_bus.execute.call_args[0][0]
        assert isinstance(query, GetRequestMetricsQuery)
        assert query.start_date == "2026-01-01"
        assert query.end_date == "2026-02-01"
        assert query.group_by == "template"
        assert result is payload

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_default_group_by(self):
        from orb.application.request.queries import GetRequestMetricsQuery

        sdk = _initialized_sdk()
        sdk._query_bus = MagicMock()
        sdk._query_bus.execute = AsyncMock(return_value={})

        await sdk.get_request_metrics()

        query = sdk._query_bus.execute.call_args[0][0]
        assert isinstance(query, GetRequestMetricsQuery)
        assert query.group_by == "status"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_not_initialized_raises(self):
        sdk = ORBClient(config={"provider_type": "aws"})
        with pytest.raises(SDKError):
            await sdk.get_request_metrics()
