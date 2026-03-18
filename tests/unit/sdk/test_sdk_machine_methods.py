"""Unit tests for orchestrator-backed machine methods on ORBClient."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.exceptions import NotFoundError, SDKError

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
# get_machine
# ---------------------------------------------------------------------------


class TestGetMachine:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_raises_not_found_when_machine_is_none(self):
        from orb.application.services.orchestration.dtos import GetMachineOutput
        from orb.application.services.orchestration.get_machine import GetMachineOrchestrator

        mock_orch = MagicMock()
        mock_orch.execute = AsyncMock(return_value=GetMachineOutput(machine=None))

        sdk = _initialized_sdk()
        _mock_container(sdk, GetMachineOrchestrator, mock_orch)

        with pytest.raises(NotFoundError) as exc_info:
            await sdk.get_machine("missing-machine-id")

        assert exc_info.value.entity_type == "Machine"
        assert exc_info.value.entity_id == "missing-machine-id"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_raises_sdk_error_when_not_initialized(self):
        sdk = ORBClient(config={"provider": "aws"})
        with pytest.raises(SDKError):
            await sdk.get_machine("m1")
