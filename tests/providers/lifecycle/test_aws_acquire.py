"""AWS acquire scenarios: completion, delayed pending, deadline, capacity, dry run."""

from __future__ import annotations

import pytest

from tests.providers.lifecycle import flows
from tests.providers.lifecycle.cells import AWS_CELLS, AWS_RUN_INSTANCES_SPOT
from tests.providers.lifecycle.gaps import (
    DEADLINE_COUNTS_LAUNCHED,
    SPOT_RUN_INSTANCES,
    skip_abandoned_pending,
    skip_deadline_release,
    skip_terminal_rows_stale,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize(
        "life",
        [
            pytest.param(
                c,
                id=c.name,
                marks=pytest.mark.skip(reason=SPOT_RUN_INSTANCES)
                if c == AWS_RUN_INSTANCES_SPOT
                else [],
            )
            for c in AWS_CELLS
        ],
        indirect=True,
    ),
]


async def test_complete_matches_cloud(life):
    await flows.acquire_complete(life)


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
    await flows.deadline_status(life, running)


@skip_deadline_release
async def test_deadline_releases_stragglers(life):
    await flows.deadline_releases_stragglers(life)


@skip_abandoned_pending
async def test_abandoned_pending_fails_after_window(life):
    await flows.abandoned_pending(life)


async def test_capacity_shortfall_ends_partial(life):
    await flows.capacity_shortfall(life)


async def test_exhausted_capacity_fails_at_create(life):
    await flows.exhausted_capacity_at_create(life)


async def test_failed_provider_read_does_not_regress_stored_state(life):
    await flows.stale_read_guard(life)


async def test_post_terminal_drift_keeps_request_terminal(life):
    await flows.post_terminal_drift(life)


@skip_terminal_rows_stale
async def test_post_terminal_loss_reaches_stored_rows(life):
    await flows.post_terminal_loss_in_rows(life)


async def test_dry_run_creates_nothing(life):
    await flows.dry_run(life)
