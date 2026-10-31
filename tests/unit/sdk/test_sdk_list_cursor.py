"""The Python SDK list methods forward `cursor` to the orchestrators."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.orchestration.dtos import (
    ListMachinesOutput,
    ListRequestsOutput,
    ListReturnRequestsOutput,
)
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.application.services.orchestration.list_return_requests import (
    ListReturnRequestsOrchestrator,
)
from tests.unit.sdk.test_sdk_request_methods import _initialized_sdk, _mock_container


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "orchestrator_cls", "output"),
    [
        ("list_requests", ListRequestsOrchestrator, ListRequestsOutput(requests=[])),
        ("list_machines", ListMachinesOrchestrator, ListMachinesOutput(machines=[])),
        (
            "list_return_requests",
            ListReturnRequestsOrchestrator,
            ListReturnRequestsOutput(requests=[]),
        ),
    ],
)
async def test_cursor_is_forwarded(method, orchestrator_cls, output) -> None:  # noqa: ANN001
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(return_value=output)
    sdk = _initialized_sdk()
    _mock_container(sdk, orchestrator_cls, orchestrator)

    await getattr(sdk, method)(cursor="tok")

    assert orchestrator.execute.call_args[0][0].cursor == "tok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cursor_defaults_to_none() -> None:
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(return_value=ListRequestsOutput(requests=[]))
    sdk = _initialized_sdk()
    _mock_container(sdk, ListRequestsOrchestrator, orchestrator)

    await sdk.list_requests()

    assert orchestrator.execute.call_args[0][0].cursor is None
