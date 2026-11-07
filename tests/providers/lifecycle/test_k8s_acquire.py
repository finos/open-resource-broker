"""Kubernetes acquire scenarios: completion, delayed pending, deadline, drift, dry run."""

from __future__ import annotations

import pytest

from tests.providers.lifecycle import flows
from tests.providers.lifecycle.cells import K8S_CELLS, K8S_DEPLOYMENT, K8S_POD
from tests.providers.lifecycle.gaps import (
    DEADLINE_COUNTS_LAUNCHED,
    DEPLOYMENT_LISTS_ITSELF,
    STALE_READ_GUARD,
    ZERO_ID_RELEASE,
    skip_abandoned_pending,
    skip_deadline_release,
    skip_terminal_rows_stale,
)
from tests.providers.lifecycle.oracle import InvariantViolation

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize("life", K8S_CELLS, indirect=True, ids=lambda c: c.name),
]


async def test_complete_matches_cloud(life):
    await flows.acquire_complete(life)


async def test_complete_request_lists_only_machines(life, request):
    if life.cell == K8S_DEPLOYMENT:
        request.applymarker(
            pytest.mark.xfail(
                strict=True, raises=InvariantViolation, reason=DEPLOYMENT_LISTS_ITSELF
            )
        )
    await flows.complete_lists_only_machines(life)


async def test_delayed_pending_stays_non_terminal_until_started(life):
    await flows.delayed_pending(life)


@pytest.mark.parametrize(
    "running",
    [
        pytest.param(0, id="none-running", marks=pytest.mark.skip(reason=DEADLINE_COUNTS_LAUNCHED)),
        pytest.param(1, id="some-running", marks=pytest.mark.skip(reason=DEADLINE_COUNTS_LAUNCHED)),
        pytest.param(3, id="all-running"),
    ],
)
async def test_deadline_outcome_follows_running_count(life, running):
    if running == 3 and life.cell == K8S_DEPLOYMENT:
        pytest.skip(DEADLINE_COUNTS_LAUNCHED)
    await flows.deadline_status(life, running)


@skip_deadline_release
async def test_deadline_releases_stragglers(life):
    await flows.deadline_releases_stragglers(life)


@skip_abandoned_pending
async def test_abandoned_pending_fails_after_window(life):
    await flows.abandoned_pending(life)


@pytest.mark.skip(reason=DEADLINE_COUNTS_LAUNCHED)
async def test_deployment_pods_created_after_the_deadline_are_released(life, request):
    if life.cell != K8S_DEPLOYMENT:
        pytest.skip("only a Deployment can hold a resource id with no machine ids")
    request.applymarker(
        pytest.mark.xfail(strict=True, raises=InvariantViolation, reason=ZERO_ID_RELEASE)
    )
    await flows.quota_blocked_release(life)


async def test_failed_provider_read_does_not_regress_stored_state(life):
    if life.cell == K8S_POD:
        pytest.skip(STALE_READ_GUARD)
    await flows.stale_read_guard(life)


async def test_post_terminal_drift_keeps_request_terminal(life):
    await flows.post_terminal_drift(life)


@skip_terminal_rows_stale
async def test_post_terminal_loss_reaches_stored_rows(life):
    await flows.post_terminal_loss_in_rows(life)


async def test_dry_run_creates_nothing(life):
    await flows.dry_run(life)
