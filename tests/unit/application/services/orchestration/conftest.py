"""Shared fixtures for orchestrator wait-loop tests."""

from __future__ import annotations

import time

import pytest


class FakeClock:
    """Virtual clock: ``asyncio.sleep`` advances it, ``time.monotonic`` reads it."""

    def __init__(self) -> None:
        self.now: float = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr("asyncio.sleep", clock.sleep)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    return clock
