"""ListReclaimedMachinesOrchestrator maps reclaimed machines to poll items."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from orb.application.services.orchestration.dtos import ListReclaimedMachinesInput
from orb.application.services.orchestration.list_reclaimed_machines import (
    ListReclaimedMachinesOrchestrator,
)


def test_spot_machines_get_the_short_grace_period():
    query_bus = MagicMock()
    query_bus.execute = AsyncMock(
        return_value=[
            SimpleNamespace(name="h1", machine_id="i-1", price_type="spot"),
            SimpleNamespace(name="h2", machine_id="i-2", price_type="ondemand"),
        ]
    )
    orchestrator = ListReclaimedMachinesOrchestrator(MagicMock(), query_bus, MagicMock())

    output = asyncio.run(orchestrator.execute(ListReclaimedMachinesInput(identifiers=["i-1"])))

    assert output.machines == [
        {"name": "h1", "machine_id": "i-1", "grace_period": 120},
        {"name": "h2", "machine_id": "i-2", "grace_period": 300},
    ]
    assert query_bus.execute.call_args[0][0].identifiers == ["i-1"]
