"""One canonical table renders identically through every in-process surface.

Requests are driven into each state through the real lifecycle, then rendered
through the status operation the way the interfaces do. The HostFactory
formatter backs the CLI, REST, SDK and MCP renderers, so the CLI renderer is
compared against the shared one and the canonical row is checked once. The wire
statuses and machine results come from the repository's own wire-format baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orb.application.services.orchestration.dtos import GetRequestStatusInput
from orb.application.services.orchestration.get_request_status import (
    GetRequestStatusOrchestrator,
)
from orb.interface.catalog import OPERATION_CATALOG, Interface
from orb.interface.response_formatting_service import ResponseFormattingService
from tests.providers.lifecycle import flows
from tests.providers.lifecycle.cells import AWS_EC2_FLEET_INSTANT, AWS_RUN_INSTANCES, K8S_DEPLOYMENT
from tests.providers.lifecycle.oracle import exercise, require

_BASELINE = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "wire_format_baseline.json").read_text()
)


def _wire(kind: str, *parts: str) -> str:
    return _BASELINE[":".join([kind, *parts])]


# state -> (wire request status, machine status the cloud holds, wire machine result)
CANONICAL = {
    "pending": (
        _wire("request_status", "in_progress"),
        "pending",
        _wire("machine_result", "acquire", "pending"),
    ),
    "complete": (
        _wire("request_status", "complete"),
        "running",
        _wire("machine_result", "acquire", "running"),
    ),
    "partial": (
        _wire("request_status", "partial"),
        "running",
        _wire("machine_result", "acquire", "running"),
    ),
    "return-in-progress": (
        _wire("request_status", "in_progress"),
        "shutting-down",
        _wire("machine_result", "return", "shutting-down"),
    ),
    "return-complete": (
        _wire("request_status", "complete"),
        "terminated",
        _wire("machine_result", "return", "terminated"),
    ),
}

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize(
        "life",
        [AWS_RUN_INSTANCES, AWS_EC2_FLEET_INSTANT, K8S_DEPLOYMENT],
        indirect=True,
        ids=lambda c: c.name,
    ),
]


async def _request_in_state(life, state: str) -> str:
    if state == "pending":
        life.cloud.hold_pending()
        return (await life.acquire(2)).request_id
    if state == "complete":
        return (await flows.acquire_complete(life, 2)).request_id
    if state == "partial":
        life.cloud.limit_capacity(1)
        handle = await life.acquire(2)
        life.scenario.deadline_passed = True
        life.advance_clock(flows.PAST_DEADLINE_SECONDS)
        await life.sync(handle, check=False)
        return handle.request_id
    handle = await flows.acquire_complete(life, 2)
    if state == "return-in-progress":
        life.cloud.lag_termination()
    ids = [m.machine_id for m in life.cloud.machines(handle.request_id)]
    return_ids = await life.return_(ids)
    if state == "return-complete":
        life.cloud.advance()
    return return_ids[0]


@pytest.mark.parametrize("state", sorted(CANONICAL))
async def test_every_surface_renders_the_canonical_row(life, state):
    request_id = await _request_in_state(life, state)
    wire_status, _, wire_result = CANONICAL[state]
    orchestrator = life.container.get(GetRequestStatusOrchestrator)
    output = await orchestrator.execute(
        GetRequestStatusInput(request_ids=[request_id], verbose=True)
    )
    formatter = life.container.get(ResponseFormattingService)
    entry = OPERATION_CATALOG["get_request_status"]
    default = entry.renderer_for(Interface.REST)(formatter, output).data
    cli = entry.renderer_for(Interface.CLI)(formatter, output).data
    exercise("interface_consistency")
    require(
        default == cli,
        "interface_consistency",
        "the CLI renderer drifted from the shared formatter",
    )
    row = default["requests"][0]
    require(
        row["status"] == wire_status,
        "interface_consistency",
        f"{state}: status {row['status']!r}, want {wire_status!r}",
    )
    results = {
        m["result"] for m in row["machines"] if m["machineId"] not in life.scenario.resource_ids
    }
    require(
        results == {wire_result},
        "interface_consistency",
        f"{state}: machine results {results}, want {wire_result!r}",
    )
