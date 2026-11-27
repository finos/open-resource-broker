"""BackgroundTasks keeps fire-and-forget tasks referenced until they finish."""

from __future__ import annotations

import asyncio
import gc
import threading
import weakref
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.utilities.background_tasks import BackgroundTasks, drain_all_sync


@pytest.mark.asyncio
async def test_task_is_tracked_while_running_and_released_when_done() -> None:
    tasks = BackgroundTasks(MagicMock())
    gate = asyncio.Event()

    async def _work() -> str:
        await gate.wait()
        return "done"

    task = tasks.spawn(asyncio.get_running_loop(), _work(), name="t")
    ref = weakref.ref(task)
    del task
    await asyncio.sleep(0)
    gc.collect()
    assert ref() is not None
    assert len(tasks) == 1

    gate.set()
    for _ in range(5):
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

    assert len(tasks) == 0
    logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_logger_failure_in_done_callback_does_not_escape() -> None:
    logger = MagicMock()
    logger.warning.side_effect = RuntimeError("logger down")
    tasks = BackgroundTasks(logger)

    async def _boom() -> None:
        raise ValueError("x")

    task = tasks.spawn(asyncio.get_running_loop(), _boom(), name="boom")
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert len(tasks) == 0


@pytest.mark.asyncio
async def test_drain_waits_for_completion() -> None:
    logger = MagicMock()
    tasks = BackgroundTasks(logger)
    done: list[str] = []

    async def _work() -> None:
        await asyncio.sleep(0.02)
        done.append("x")

    tasks.spawn(asyncio.get_running_loop(), _work(), name="w")
    await tasks.drain(timeout=2.0)
    assert done == ["x"]
    assert len(tasks) == 0
    logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_drain_timeout_cancels_and_logs_once_with_names() -> None:
    logger = MagicMock()
    tasks = BackgroundTasks(logger)

    async def _forever() -> None:
        await asyncio.Event().wait()

    loop = asyncio.get_running_loop()
    first = tasks.spawn(loop, _forever(), name="k8s-delete-pod-a")
    second = tasks.spawn(loop, _forever(), name="k8s-delete-pod-b")
    await tasks.drain(timeout=0.01)
    await asyncio.sleep(0)

    assert first.cancelled() and second.cancelled()
    assert len(tasks) == 0
    logger.warning.assert_called_once()
    args = logger.warning.call_args.args
    rendered = args[0] % args[1:]
    assert "2 background" in rendered
    assert "k8s-delete-pod-a" in rendered and "k8s-delete-pod-b" in rendered


def test_drain_all_sync_without_running_loop_finishes_work() -> None:
    logger = MagicMock()
    tasks = BackgroundTasks(logger)
    loop = asyncio.new_event_loop()
    done: list[str] = []

    async def _work() -> None:
        await asyncio.sleep(0.02)
        done.append("x")

    try:
        tasks.spawn(loop, _work(), name="w")
        drain_all_sync([tasks], logger, timeout=2.0)
        assert done == ["x"]
        assert len(tasks) == 0
    finally:
        loop.close()


def test_drain_all_sync_from_foreign_thread_finishes_work() -> None:
    logger = MagicMock()
    tasks = BackgroundTasks(logger)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    done: list[str] = []

    async def _work() -> None:
        await asyncio.sleep(0.02)
        done.append("x")

    try:
        asyncio.run_coroutine_threadsafe(_spawn(tasks, _work()), loop).result(2.0)
        drain_all_sync([tasks], logger, timeout=2.0)
        assert done == ["x"]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()


async def _spawn(tasks: BackgroundTasks, coro) -> None:
    tasks.spawn(asyncio.get_running_loop(), coro, name="w")


@pytest.mark.asyncio
async def test_drain_all_sync_on_loop_thread_neither_blocks_nor_cancels() -> None:
    logger = MagicMock()
    tasks = BackgroundTasks(logger)
    gate = asyncio.Event()

    async def _work() -> None:
        await gate.wait()

    task = tasks.spawn(asyncio.get_running_loop(), _work(), name="w")
    drain_all_sync([tasks], logger, timeout=0.01)
    await asyncio.sleep(0)

    assert not task.cancelled() and not task.done()
    logger.warning.assert_called_once()
    gate.set()
    await task
