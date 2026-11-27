"""BackgroundTasks keeps fire-and-forget tasks referenced until they finish."""

from __future__ import annotations

import asyncio
import gc
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.utilities.background_tasks import BackgroundTasks


@pytest.mark.asyncio
async def test_task_is_tracked_while_running_and_released_when_done() -> None:
    tasks = BackgroundTasks(MagicMock())
    gate = asyncio.Event()

    async def _work() -> str:
        await gate.wait()
        return "done"

    task = tasks.spawn(asyncio.get_running_loop(), _work(), name="t")
    assert len(tasks) == 1
    gc.collect()
    assert len(tasks) == 1

    gate.set()
    assert await task == "done"
    await asyncio.sleep(0)
    assert len(tasks) == 0


@pytest.mark.asyncio
async def test_failure_is_logged_with_exception_class_only() -> None:
    logger = MagicMock()
    tasks = BackgroundTasks(logger)

    async def _boom() -> None:
        raise ValueError("secret-detail")

    task = tasks.spawn(asyncio.get_running_loop(), _boom(), name="boom")
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert len(tasks) == 0
    logger.warning.assert_called_once()
    rendered = logger.warning.call_args.args[0] % logger.warning.call_args.args[1:]
    assert "ValueError" in rendered
    assert "secret-detail" not in rendered


@pytest.mark.asyncio
async def test_cancel_all_cancels_and_releases_without_warning() -> None:
    logger = MagicMock()
    tasks = BackgroundTasks(logger)

    async def _forever() -> None:
        await asyncio.Event().wait()

    task = tasks.spawn(asyncio.get_running_loop(), _forever(), name="forever")
    tasks.cancel_all()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert task.cancelled()
    assert len(tasks) == 0
    logger.warning.assert_not_called()
