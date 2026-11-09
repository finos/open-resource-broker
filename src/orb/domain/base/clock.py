"""Time sources for code whose behaviour depends on the passage of time.

``SystemClock`` is the production implementation and simply forwards to the
standard library. Components that stamp deadlines, measure elapsed time or poll
accept a ``Clock`` and default to ``SystemClock``, so a different clock can be
substituted without patching module-level time functions.

Pure domain — standard library only.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of wall-clock time, monotonic time and sleeping."""

    def now(self) -> datetime:  # type: ignore[return]
        """Timezone-aware current UTC time."""
        pass

    def time(self) -> float:  # type: ignore[return]
        """Seconds since the epoch."""
        pass

    def monotonic(self) -> float:  # type: ignore[return]
        """Monotonic seconds, for measuring elapsed time."""
        pass

    def sleep(self, seconds: float) -> None:
        """Block the calling thread for ``seconds``."""

    async def async_sleep(self, seconds: float) -> None:
        """Suspend the calling coroutine for ``seconds``."""


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    async def async_sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
