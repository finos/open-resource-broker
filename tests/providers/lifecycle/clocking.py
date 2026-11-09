"""Fake time for the lifecycle suite.

``FakeClock`` is real time plus an offset that grows with ``advance`` and with
every routed ``sleep``. The event loop is never frozen: wall and monotonic time
move together, so throttles and polls expire inside a test while nothing hangs.

``SLEEP_SITES`` lists the modules below the application layer that import
``time`` or ``asyncio`` and sleep on their own. Their ``time``/``asyncio``
attribute is replaced with a delegating stand-in whose ``sleep`` advances the
fake clock; the standard library modules themselves are left alone.
"""

from __future__ import annotations

import asyncio as _asyncio
import time as _time
from datetime import datetime, timedelta
from typing import Any

from orb.domain.base.clock import SystemClock

# Captured at import, before any guard replaces them.
REAL_SLEEP = _time.sleep
REAL_ASYNC_SLEEP = _asyncio.sleep

# Modules that call time.sleep / asyncio.sleep directly. Key: module path,
# value: the stdlib module names the module imports and sleeps through.
SLEEP_SITES: dict[str, tuple[str, ...]] = {
    "orb.infrastructure.resilience.retry_decorator": ("time",),
    "orb.providers.aws.infrastructure.handlers.base_handler": ("asyncio",),
    "orb.providers.aws.utilities.terminate_instances": ("time",),
    "orb.providers.aws.infrastructure.handlers.run_instances.handler": ("time",),
    "orb.providers.k8s.infrastructure.handlers.base_handler": ("time",),
    "orb.application.base.handlers": ("asyncio",),
    "orb.application.commands.request_creation_handlers": ("asyncio",),
}


class FakeClock(SystemClock):
    """Real time shifted by an offset the test controls."""

    def __init__(self) -> None:
        self.offset = 0.0

    def reset(self) -> None:
        self.offset = 0.0

    def advance(self, seconds: float) -> None:
        self.offset += seconds

    def now(self) -> datetime:
        return super().now() + timedelta(seconds=self.offset)

    def time(self) -> float:
        return super().time() + self.offset

    def monotonic(self) -> float:
        return super().monotonic() + self.offset

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    async def async_sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await REAL_ASYNC_SLEEP(0)


class _TimeStandIn:
    """Delegates to ``time`` except for ``sleep``."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock

    def sleep(self, seconds: float) -> None:
        self._clock.sleep(seconds)

    def __getattr__(self, name: str) -> Any:
        return getattr(_time, name)


class _AsyncioStandIn:
    """Delegates to ``asyncio`` except for ``sleep``."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock

    async def sleep(self, seconds: float, result: Any = None) -> Any:
        await self._clock.async_sleep(seconds)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(_asyncio, name)


def route_sleeps(monkeypatch: Any, clock: FakeClock) -> None:
    """Replace the ``time``/``asyncio`` attribute of every sleeping module."""
    import importlib

    for module_path, names in SLEEP_SITES.items():
        module = importlib.import_module(module_path)
        for name in names:
            stand_in = _TimeStandIn(clock) if name == "time" else _AsyncioStandIn(clock)
            monkeypatch.setattr(module, name, stand_in)


class RealSleepGuard:
    """Records any real sleep longer than ``LIMIT`` seconds."""

    LIMIT = 0.05

    def __init__(self) -> None:
        self.violations: list[tuple[str, float]] = []

    def install(self, monkeypatch: Any) -> None:
        guard = self

        def sleep(seconds: float) -> None:
            if seconds > guard.LIMIT:
                guard.violations.append(("time.sleep", seconds))
            REAL_SLEEP(seconds)

        async def async_sleep(seconds: float, result: Any = None) -> Any:
            if seconds > guard.LIMIT:
                guard.violations.append(("asyncio.sleep", seconds))
            return await REAL_ASYNC_SLEEP(seconds, result)

        monkeypatch.setattr(_time, "sleep", sleep)
        monkeypatch.setattr(_asyncio, "sleep", async_sleep)


# Modules that stamp deadlines, read "now" for status writes or run wait loops.
# None of them may call datetime.now / time.* / asyncio.sleep directly (default
# argument values excepted): they take their time from the injected clock.
CLOCK_SEAM_MODULES: tuple[str, ...] = (
    "orb/application/services/request_status_service.py",
    "orb/application/services/request_status_management_service.py",
    "orb/application/queries/request_query_handlers.py",
    "orb/application/services/provisioning_orchestration_service.py",
    "orb/application/commands/request_lifecycle_handlers.py",
    "orb/application/services/machine_sync_service.py",
    "orb/application/services/orchestration/acquire_machines.py",
    "orb/application/services/orchestration/return_machines.py",
    "orb/application/services/orchestration/get_request_status.py",
)

# Direct sleeps in code the suite can reach that are not routed, with the reason.
UNROUTED_SLEEPS: dict[str, str] = {
    "orb/application/events/base/event_handler.py": "event handler retry; only runs when a handler fails",
    "orb/providers/aws/infrastructure/handlers/microvm/handler.py": "microvm is not a covered api",
    "orb/providers/aws/storage/components/dynamodb_transaction_manager.py": (
        "the suite stores requests in JSON; the DynamoDB backoff is injectable and unused here"
    ),
}
