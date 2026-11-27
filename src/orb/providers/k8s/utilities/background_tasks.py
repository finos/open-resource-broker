"""Strong references for fire-and-forget asyncio tasks.

The event loop keeps only weak references to tasks, so a task nobody
holds can be garbage-collected before it finishes.  :class:`BackgroundTasks`
holds each task until it completes and logs failures.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from orb.domain.base.ports import LoggingPort


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
            self._logger.warning(
                "Background task %s failed: %s", task.get_name(), type(exc).__name__
            )

    def cancel_all(self) -> None:
        """Cancel outstanding tasks; each is released by its done-callback."""
        for task in list(self._tasks):
            task.cancel()
