"""Kubernetes acquire scenarios: completion, delayed pending, deadline, drift, dry run."""

from __future__ import annotations

import pytest

from orb.domain.request.value_objects import RequestStatus
from tests.providers.lifecycle import flows
from tests.providers.lifecycle.cells import K8S_CELLS, K8S_DEPLOYMENT
from tests.providers.lifecycle.gaps import (
    DEPLOYMENT_LATE_PODS_NOT_RELEASED,
    STALE_READ_GUARD,
    skip_terminal_rows_stale,
)
from tests.providers.lifecycle.oracle import KnownGap, expect_violation

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize("life", K8S_CELLS, indirect=True, ids=lambda c: c.name),
]


async def test_complete_matches_cloud(life):
    await flows.acquire_complete(life)


async def test_complete_request_lists_only_machines(life):
    await flows.complete_lists_only_machines(life)


async def test_delayed_pending_stays_non_terminal_until_started(life):
    await flows.delayed_pending(life)


@pytest.mark.parametrize("running", [0, 1, 3], ids=["none-running", "some-running", "all-running"])
async def test_deadline_outcome_follows_running_count(life, running):
    await flows.deadline_status(life, running)


async def test_deadline_releases_stragglers(life):
    await flows.deadline_releases_stragglers(life)


async def test_abandoned_pending_fails_after_window(life):
    await flows.abandoned_pending(life)


async def test_deployment_pods_created_after_the_deadline_are_released(life, request):
    if life.cell != K8S_DEPLOYMENT:
        pytest.skip("only a Deployment can hold a resource id with no machine ids")
    request.applymarker(
        pytest.mark.xfail(strict=True, raises=KnownGap, reason=DEPLOYMENT_LATE_PODS_NOT_RELEASED)
    )
    with expect_violation("deadline_outcome"):
        await flows.quota_blocked_release(life)


async def test_controller_request_records_its_pods_not_the_controller(life):
    if life.cell != K8S_DEPLOYMENT:
        pytest.skip("only a controller workload has a resource that is not a machine")
    handle = await life.acquire(2)
    with life.uow_factory.create_unit_of_work() as uow:
        stored = uow.requests.find_by_request_id(handle.request_id)
    assert [str(r) for r in stored.resource_ids]
    assert life.view(handle).machine_ids == []

    await life.sync(handle)

    pods = {m.machine_id for m in life.cloud.machines(handle.request_id)}
    view = life.view(handle)
    assert len(pods) == 2
    assert set(view.machine_ids) == pods
    assert set(view.rows) == pods
    assert {m["machineId"] for m in view.hf_entry["machines"]} == pods


async def test_controller_timeout_without_pods_owes_no_release(life):
    if life.cell != K8S_DEPLOYMENT:
        pytest.skip("only a controller workload has a resource that is not a machine")
    life.cloud.block_pod_creation()
    handle = await life.acquire(2)
    calls_before = len(life.cloud.call_log)
    life.advance_clock(flows.PAST_DEADLINE_SECONDS)

    await life.sync(handle, check=False)

    view = life.view(handle)
    assert view.status == RequestStatus.TIMEOUT
    assert "deadline_release" not in view.metadata
    assert view.machine_ids == []
    released = [
        c
        for c in life.cloud.call_log[calls_before:]
        if c.operation.split()[0] in ("DELETE", "PATCH")
    ]
    assert released == []


@pytest.mark.skip(reason=STALE_READ_GUARD)
async def test_failed_provider_read_does_not_regress_stored_state(life):
    await flows.stale_read_guard(life)


async def test_post_terminal_drift_keeps_request_terminal(life):
    await flows.post_terminal_drift(life)


@skip_terminal_rows_stale
async def test_post_terminal_loss_reaches_stored_rows(life):
    await flows.post_terminal_loss_in_rows(life)


async def test_dry_run_creates_nothing(life):
    await flows.dry_run(life)
