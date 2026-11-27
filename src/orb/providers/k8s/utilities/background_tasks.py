"""Strong references for fire-and-forget asyncio tasks.

The event loop keeps only weak references to tasks, so a task nobody
holds can be garbage-collected before it finishes.  :class:`BackgroundTasks`
holds each task until it completes and logs failures.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine, Iterable
from typing import Any

from orb.domain.base.ports import LoggingPort

# No existing k8s config timeout fits shutdown; matches the watch-stop timeout.
BACKGROUND_DRAIN_TIMEOUT_SECONDS = 10.0

# Extra time a foreign-thread caller waits beyond the drain timeout, so the
# drain's own cancel-and-log step can finish before the wait gives up.
FOREIGN_THREAD_WAIT_MARGIN_SECONDS = 5.0


class BackgroundTasks:
    """Owner-scoped set of running background tasks."""

    def __init__(self, logger: LoggingPort) -> None:
        self._logger = logger
        self._tasks: set[asyncio.Task[Any]] = set()

    def __len__(self) -> int:
        return len(self._tasks)

    def spawn(
        self,
        loop: asyncio.AbstractEventLoop,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str,
    ) -> asyncio.Task[Any]:
        """Start ``coro`` on ``loop`` and keep it referenced until it finishes."""
        task = loop.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # A logger fault must not escape a loop callback; the logger itself
            # failed, so there is nothing left to log to.
            with contextlib.suppress(Exception):
                self._logger.warning(
                    "Background task %s failed: %s", task.get_name(), type(exc).__name__
                )

    def cancel_all(self) -> None:
        """Cancel outstanding tasks; each is released by its done-callback."""
        for task in list(self._tasks):
            task.cancel()

    async def drain(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for outstanding tasks, then cancel the rest.

        Logs one warning with the count and task names when the timeout
        elapses.
        """
        tasks = set(self._tasks)
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        if not pending:
            return
        self._logger.warning(
            "%d background task(s) did not finish within %.1fs and were cancelled: %s",
            len(pending),
            timeout,
            ", ".join(sorted(t.get_name() for t in pending)),
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def drain_all_sync(
    trackers: Iterable[BackgroundTasks], logger: LoggingPort, *, timeout: float
) -> None:
    """Drain ``trackers`` from synchronous cleanup code.

    * No loop running in this thread, owning loop idle: drive the drain on it.
    * No loop running in this thread, owning loop running elsewhere: schedule
      the drain there and block up to ``timeout`` plus a small margin.
    * Called on the owning loop's thread: blocking would deadlock, so nothing
      is awaited or cancelled; a warning reports the pending count and the
      tasks finish on their own.
    """
    trackers = [t for t in trackers if len(t)]
    if not trackers:
        return
    loop = next(iter(trackers[0]._tasks)).get_loop()

    async def _drain() -> None:
        await asyncio.gather(*(t.drain(timeout) for t in trackers))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning(
            "%d background deletion(s) still pending during cleanup on the event-loop "
            "thread; they are left to finish on their own.",
            sum(len(t) for t in trackers),
        )
        return

    if loop.is_closed():
        return
    if loop.is_running():
        asyncio.run_coroutine_threadsafe(_drain(), loop).result(
            timeout + FOREIGN_THREAD_WAIT_MARGIN_SECONDS
        )
    else:
        loop.run_until_complete(_drain())
