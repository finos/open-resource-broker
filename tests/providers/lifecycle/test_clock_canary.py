"""Every status path moves with the fake clock.

A status path still reading the real clock leaves a request non-terminal after
the deadline, or stamps a deadline that is already past, and fails here.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from orb.application.dto.queries import SyncAndGetRequestQuery
from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.dtos import AcquireMachinesInput
from orb.domain.request.request_types import RequestStatus
from tests.providers.lifecycle.cells import AWS_RUN_INSTANCES
from tests.providers.lifecycle.flows import PAST_DEADLINE_SECONDS
from tests.providers.lifecycle.oracle import NON_TERMINAL, TERMINAL, precondition, require

TIMEOUT_SECONDS = 20

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize("life", [AWS_RUN_INSTANCES], indirect=True, ids=lambda c: c.name),
]


@pytest.fixture
def request_timeout() -> int:
    return TIMEOUT_SECONDS


async def _pending_request(life):
    life.cloud.hold_pending()
    handle = await life.acquire(2)
    precondition(life.status(handle) in NON_TERMINAL, "request settled at creation")
    life.scenario.deadline_passed = True
    return handle


async def test_sweep_fires_through_sync(life):
    handle = await _pending_request(life)
    life.advance_clock(PAST_DEADLINE_SECONDS)
    await life.sync(handle, check=False)
    require(life.status(handle) in TERMINAL, "L4", "sync did not sweep an expired request")


async def test_sweep_fires_through_the_query_read(life):
    handle = await _pending_request(life)
    life.advance_clock(PAST_DEADLINE_SECONDS)
    await life.query_bus.execute(
        SyncAndGetRequestQuery(request_id=handle.request_id, lightweight=True)
    )
    require(
        life.status(handle) in TERMINAL, "L4", "the query read did not sweep an expired request"
    )


async def test_sweep_fires_through_a_waiting_acquire(life):
    life.cloud.hold_pending()
    orchestrator = life.container.get(AcquireMachinesOrchestrator)
    output = await orchestrator.execute(
        AcquireMachinesInput(
            template_id=life.cell.template_id,
            requested_count=2,
            wait=True,
            timeout_seconds=10 * TIMEOUT_SECONDS,
        )
    )
    require(
        RequestStatus(output.status) in TERMINAL or output.status in ("complete", "timeout"),
        "L4",
        f"a waiting acquire returned {output.status!r} past its deadline",
    )
    # The wait ended because the fake clock reached the deadline, not by exhausting
    # its own timeout: the loop sleeps in two second steps.
    assert life.clock.offset >= TIMEOUT_SECONDS


async def test_deadline_is_stamped_from_the_fake_clock(life):
    life.advance_clock(PAST_DEADLINE_SECONDS)
    life.cloud.hold_pending()
    handle = await life.acquire(2)
    view = life.view(handle)
    with life.uow_factory.create_unit_of_work() as uow:
        request = uow.requests.find_by_request_id(handle.request_id)
    now = life.clock.now()
    precondition(request.deadline_at is not None, "no deadline stamped")
    require(request.deadline_at > now, "L4", "the new request is already past its deadline")
    drift = abs((request.deadline_at - (now + timedelta(seconds=TIMEOUT_SECONDS))).total_seconds())
    require(drift < 60, "L4", f"deadline_at is {drift:.0f}s away from fake now + timeout")
    assert view.status in NON_TERMINAL
