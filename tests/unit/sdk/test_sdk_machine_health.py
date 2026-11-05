"""Unit tests for the query-bus-backed get_machine_health method on ORBClient."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.exceptions import NotFoundError, SDKError


def _initialized_sdk() -> ORBClient:
    sdk = ORBClient(config={"provider_type": "aws"})
    sdk._initialized = True
    sdk._container = MagicMock()
    return sdk


class TestGetMachineHealth:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_dispatches_query_and_returns_dict(self):
        from orb.application.dto.queries import GetMachineHealthQuery
        from orb.application.machine.dto import MachineHealthDTO

        dto = MachineHealthDTO(
            machine_id="m-1",
            overall_status="running",
            system_status="ok",
            instance_status="ok",
        )
        sdk = _initialized_sdk()
        sdk._query_bus = MagicMock()
        sdk._query_bus.execute = AsyncMock(return_value=dto)

        result = await sdk.get_machine_health(machine_id="m-1", refresh=True)

        query = sdk._query_bus.execute.call_args[0][0]
        assert isinstance(query, GetMachineHealthQuery)
        assert query.machine_id == "m-1"
        assert query.refresh is True
        assert result["machine_id"] == "m-1"
        assert result["system_status"] == "ok"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_refresh_defaults_to_false(self):
        from orb.application.dto.queries import GetMachineHealthQuery
        from orb.application.machine.dto import MachineHealthDTO

        dto = MachineHealthDTO(machine_id="m-1", overall_status="running")
        sdk = _initialized_sdk()
        sdk._query_bus = MagicMock()
        sdk._query_bus.execute = AsyncMock(return_value=dto)

        await sdk.get_machine_health(machine_id="m-1")

        query = sdk._query_bus.execute.call_args[0][0]
        assert isinstance(query, GetMachineHealthQuery)
        assert query.refresh is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_not_found_raises(self):
        from orb.domain.base.exceptions import EntityNotFoundError

        sdk = _initialized_sdk()
        sdk._query_bus = MagicMock()
        sdk._query_bus.execute = AsyncMock(side_effect=EntityNotFoundError("Machine", "missing"))

        with pytest.raises(NotFoundError):
            await sdk.get_machine_health(machine_id="missing")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_not_initialized_raises(self):
        sdk = ORBClient(config={"provider_type": "aws"})
        with pytest.raises(SDKError):
            await sdk.get_machine_health(machine_id="m-1")
