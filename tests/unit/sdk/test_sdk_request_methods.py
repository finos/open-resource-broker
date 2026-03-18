"""Unit tests for orchestrator-backed request and machine list methods on ORBClient."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.exceptions import SDKError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _initialized_sdk() -> ORBClient:
    sdk = ORBClient(config={"provider": "aws"})
    sdk._initialized = True
    sdk._container = MagicMock()
    return sdk


def _mock_container(sdk: ORBClient, orchestrator_class, orchestrator, scheduler=None):
    """Wire container.get() for one orchestrator and container.get_optional() for scheduler."""

    def _get(cls):
        if cls is orchestrator_class:
            return orchestrator
        raise KeyError(cls)

    sdk._container.get.side_effect = _get
    sdk._container.get_optional.return_value = scheduler


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
        sdk = ORBClient(config={"provider": "aws"})
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
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError):
            await sdk.list_machines()
