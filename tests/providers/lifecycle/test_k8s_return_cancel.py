"""Kubernetes return, cancel and blast-radius scenarios."""

from __future__ import annotations

import pytest

from tests.providers.lifecycle import flows
from tests.providers.lifecycle.cells import K8S_CELLS

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize("life", K8S_CELLS, indirect=True, ids=lambda c: c.name),
]


async def test_cancel_flips_request_to_cancelled(life):
    await flows.cancel_flips_status(life)


async def test_cancel_aborts_every_resource(life):
    await flows.cancel_aborts_resources(life)


async def test_cancel_of_a_settled_request_keeps_its_status_and_aborts(life):
    await flows.cancel_settled_keeps_status(life)


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


async def test_return_does_not_touch_untargeted_machines(life):
    await flows.blast_radius(life)


async def test_return_is_idempotent(life):
    await flows.return_lifecycle(life)
