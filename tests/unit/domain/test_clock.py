"""The clock seam defaults to real time and can be replaced by injection."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from orb.application.services.orchestration.acquire_machines import AcquireMachinesOrchestrator
from orb.application.services.orchestration.get_request_status import (
    GetRequestStatusOrchestrator,
)
from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
from orb.domain.base.clock import Clock, SystemClock
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine


class _Stub(SystemClock):
    def now(self) -> datetime:
        return datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_system_clock_reads_real_time():
    clock = SystemClock()
    before = datetime.now(timezone.utc)
    now = clock.now()
    after = datetime.now(timezone.utc)
    assert before <= now <= after
    assert now.tzinfo is not None
    assert abs(clock.time() - time.time()) < 1.0
    assert abs(clock.monotonic() - time.monotonic()) < 1.0
    assert isinstance(clock, Clock)


def test_system_clock_sleeps_for_real():
    clock = SystemClock()
    start = time.monotonic()
    clock.sleep(0.02)
    assert time.monotonic() - start >= 0.015
    start = time.monotonic()
    asyncio.run(clock.async_sleep(0.02))
    assert time.monotonic() - start >= 0.015


def test_state_machine_defaults_to_system_clock():
    machine = FulfilmentStateMachine(grace_period_seconds=60)
    before = datetime.now(timezone.utc)
    assert before <= machine.now() <= datetime.now(timezone.utc) + timedelta(seconds=1)


def test_state_machine_uses_injected_clock():
    machine = FulfilmentStateMachine(grace_period_seconds=60, clock=_Stub())
    assert machine.now().year == 2030


@pytest.mark.parametrize(
    "orchestrator_cls",
    [AcquireMachinesOrchestrator, ReturnMachinesOrchestrator, GetRequestStatusOrchestrator],
)
def test_orchestrators_default_to_system_clock_and_accept_injection(orchestrator_cls):
    default = orchestrator_cls(command_bus=MagicMock(), query_bus=MagicMock(), logger=MagicMock())
    assert type(default._clock) is SystemClock

    injected = _Stub()
    custom = orchestrator_cls(
        command_bus=MagicMock(), query_bus=MagicMock(), logger=MagicMock(), clock=injected
    )
    assert custom._clock is injected
