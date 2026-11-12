"""SDK calls follow the configured scheduler unless a call names another format."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.orchestration.dtos import ListMachinesOutput
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory
from orb.infrastructure.scheduler.registry import get_scheduler_registry
from orb.interface.response_formatting_service import ResponseFormattingService
from orb.sdk.client import ORBClient
from tests.utilities.reset_singletons import reset_all_singletons


def _scheduler(marker: str) -> MagicMock:
    scheduler = MagicMock()
    scheduler.get_scheduler_type.return_value = marker
    scheduler.format_machine_status_response.return_value = {"marker": marker}
    return scheduler


@pytest.fixture
def client():
    configured, other = _scheduler("default"), _scheduler("sdk-probe")
    orchestrator = AsyncMock(spec=ListMachinesOrchestrator)
    orchestrator.execute.return_value = ListMachinesOutput(machines=[], count=0, total_count=0)
    config = MagicMock()
    config.get_scheduler_strategy.return_value = "default"
    factory = MagicMock()
    factory.create_strategy.return_value = other
    services = {
        ListMachinesOrchestrator: orchestrator,
        ResponseFormattingService: ResponseFormattingService(configured),
        ConfigurationPort: config,
        SchedulerStrategyFactory: factory,
    }
    container = MagicMock()
    container.get.side_effect = lambda cls: services[cls]

    sdk = ORBClient.__new__(ORBClient)
    sdk._initialized = True
    sdk._container = container
    sdk._config = MagicMock(provider_name=None, provider_type=None)
    get_scheduler_registry().register(
        "sdk-probe", lambda cfg: other, lambda c: None, strategy_class=MagicMock
    )
    yield sdk
    reset_all_singletons()


@pytest.mark.asyncio
async def test_default_follows_the_configured_scheduler(client):
    assert (await client.list_machines())["marker"] == "default"


@pytest.mark.asyncio
async def test_explicit_format_renders_with_that_format(client):
    assert (await client.list_machines(format="sdk-probe"))["marker"] == "sdk-probe"


@pytest.mark.asyncio
async def test_unknown_format_is_rejected(client):
    from orb.domain.request.exceptions import RequestValidationError

    with pytest.raises(RequestValidationError):
        await client.list_machines(format="nope")
