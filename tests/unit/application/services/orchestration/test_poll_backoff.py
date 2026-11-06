"""Wait loops back off after the first half minute and keep a fixed wall-clock cap."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.base import next_poll_interval
from orb.application.services.orchestration.dtos import (
    AcquireMachinesInput,
    GetRequestStatusInput,
    ReturnMachinesInput,
)
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator

_MAX_FETCHES_FOR_10_MINUTE_WAIT = 150


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(0, 2), (1.9, 2), (29.9, 2), (30, 5), (31, 5), (600, 5)],
)
def test_next_poll_interval_table(elapsed, expected):
    assert next_poll_interval(elapsed) == expected


class _CountingQueryBus:
    """Never reaches a terminal state; optionally turns terminal on the Nth read."""

    def __init__(self, terminal_on: int | None = None) -> None:
        self.calls = 0
        self._terminal_on = terminal_on

    async def execute(self, query):
        self.calls += 1
        done = self._terminal_on is not None and self.calls >= self._terminal_on
        status = "completed" if done else "in_progress"
        return SimpleNamespace(
            status=status,
            machine_references=[],
            to_dict=lambda: {"request_id": "req-1", "status": status},
        )


def _command_bus(**attrs):
    bus = MagicMock()

    async def execute(command):
        for key, value in attrs.items():
            setattr(command, key, value)

    bus.execute = AsyncMock(side_effect=execute)
    return bus


def _acquire(query_bus):
    return AcquireMachinesOrchestrator(
        _command_bus(created_request_id="req-1"), query_bus, MagicMock()
    )


def _return(query_bus):
    return ReturnMachinesOrchestrator(
        _command_bus(created_request_ids=["req-1"], skipped_machines=[]), query_bus, MagicMock()
    )


def _status(query_bus):
    return GetRequestStatusOrchestrator(MagicMock(), query_bus, MagicMock())


async def _run_acquire(orch, timeout):
    return await orch.execute(
        AcquireMachinesInput(template_id="t", requested_count=1, wait=True, timeout_seconds=timeout)
    )


async def _run_return(orch, timeout):
    return await orch.execute(
        ReturnMachinesInput(machine_ids=["i-1"], wait=True, timeout_seconds=timeout)
    )


async def _run_status(orch, timeout):
    return await orch.execute(
        GetRequestStatusInput(request_ids=["req-1"], wait=True, timeout_seconds=timeout)
    )


_RUNNERS = [
    pytest.param(_acquire, _run_acquire, id="acquire"),
    pytest.param(_return, _run_return, id="return"),
    pytest.param(_status, _run_status, id="status"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("build", "run"), _RUNNERS)
async def test_ten_minute_wait_does_not_poll_every_two_seconds(build, run, fake_clock):
    query_bus = _CountingQueryBus()

    await run(build(query_bus), 600)

    assert query_bus.calls < _MAX_FETCHES_FOR_10_MINUTE_WAIT
    assert fake_clock.sleeps[0] == 2
    assert fake_clock.sleeps[-1] <= 5
    assert fake_clock.now == pytest.approx(600)


@pytest.mark.asyncio
@pytest.mark.parametrize(("build", "run"), _RUNNERS)
async def test_first_fetch_is_immediate_and_terminal_second_poll_sleeps_once(
    build, run, fake_clock
):
    query_bus = _CountingQueryBus(terminal_on=2)

    await run(build(query_bus), 600)

    assert query_bus.calls == 2
    assert fake_clock.sleeps == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize(("build", "run"), _RUNNERS)
async def test_wait_never_sleeps_past_the_timeout(build, run, fake_clock):
    await run(build(_CountingQueryBus()), 32)

    assert fake_clock.now == pytest.approx(32)
