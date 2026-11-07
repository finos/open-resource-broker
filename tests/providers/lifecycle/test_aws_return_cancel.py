"""AWS return, cancel and blast-radius scenarios."""

from __future__ import annotations

import pytest

from tests.providers.lifecycle import flows
from tests.providers.lifecycle.cells import AWS_CELLS, AWS_EC2_FLEET_INSTANT, AWS_RUN_INSTANCES_SPOT
from tests.providers.lifecycle.gaps import (
    INSTANT_FLEET_PARTIAL_RETURN,
    SPOT_RUN_INSTANCES,
    skip_cancel_aborts,
)
from tests.providers.lifecycle.oracle import InvariantViolation

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


async def test_cancel_flips_request_to_cancelled(life):
    await flows.cancel_flips_status(life)


@skip_cancel_aborts
async def test_cancel_aborts_every_resource(life):
    await flows.cancel_aborts_resources(life)


@skip_cancel_aborts
async def test_cancel_of_a_settled_request_keeps_its_status_and_aborts(life):
    await flows.cancel_settled_keeps_status(life)


@skip_cancel_aborts
async def test_failed_abort_leaves_the_request_unchanged_and_repeatable(life):
    await flows.failed_abort_leaves_request(life)


async def test_return_of_an_unknown_machine_kills_nothing(life):
    await flows.return_unknown_machine(life)


async def test_return_of_a_vanished_machine_completes(life):
    await flows.return_vanished_machine(life)


async def test_return_spanning_two_requests(life):
    await flows.return_spanning_two_requests(life)


async def test_return_completes_only_after_machines_terminate(life):
    await flows.return_lag(life)


async def test_return_does_not_touch_untargeted_machines(life, request):
    if life.cell == AWS_EC2_FLEET_INSTANT:
        request.applymarker(
            pytest.mark.xfail(
                strict=True, raises=InvariantViolation, reason=INSTANT_FLEET_PARTIAL_RETURN
            )
        )
    await flows.blast_radius(life)


async def test_return_is_idempotent(life):
    await flows.return_lifecycle(life)
