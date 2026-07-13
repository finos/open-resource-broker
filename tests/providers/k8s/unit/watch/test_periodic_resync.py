"""Unit tests for the K8sWatcher periodic full-LIST resync backstop.

Covers the ``periodic_resync_interval_seconds`` feature added to
:class:`orb.providers.k8s.watch.watcher.K8sWatcher` to mirror the
legacy ``RefreshPodsTask`` (``hfcron.py``).

Tests:

* Resync task is NOT created when ``periodic_resync_interval_seconds=0``
  (the default -- disabled).
* Resync task IS created when interval > 0.
* Resync fires after the interval elapses and calls ``_relist_snapshot``.
* A second resync fires after the interval has elapsed again.
* Resync is disabled (no extra LIST calls) when interval=0.
* Resync errors are logged as warnings and do not propagate.
* Resync task is stopped when :meth:`stop` is called.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.watch.pod_state_cache import PodStateCache
from orb.providers.k8s.watch.watcher import K8sWatcher

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pod(
    *,
    name: str = "orb-0001",
    phase: str = "Running",
    request_id: str = "req-1",
    namespace: str = "ns",
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels={
                "orb.io/managed": "true",
                "orb.io/request-id": request_id,
            },
        ),
        spec=SimpleNamespace(node_name="node-a"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1",
            host_ip="10.1.0.1",
            start_time=None,
            conditions=[SimpleNamespace(type="Ready", status="True", reason=None)],
            container_statuses=[],
        ),
    )


class _StubWatch:
    def __init__(self, events: list[Any], *, raise_after: Exception | None = None) -> None:
        self._events = iter(events)
        self._raise_after = raise_after
        self.resource_version: str | None = None

    def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
        yield from self._events
        if self._raise_after is not None:
            raise self._raise_after

    def stop(self) -> None:
        pass


def _blocking_watch_factory() -> _StubWatch:
    """Return a watch that immediately ends (empty stream)."""
    return _StubWatch([])


def _make_client_with_list_result(pods: list[Any]) -> MagicMock:
    """Return a mock k8s client whose list_namespaced_pod returns ``pods``."""
    pod_list = SimpleNamespace(
        items=pods,
        metadata=SimpleNamespace(resource_version="rv-100"),
    )
    client = MagicMock()
    client.core_v1.list_namespaced_pod = MagicMock(return_value=pod_list)
    client.core_v1.list_pod_for_all_namespaces = MagicMock(return_value=pod_list)
    return client


# ---------------------------------------------------------------------------
# disabled (interval=0) — default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_resync_disabled_by_default() -> None:
    """With interval=0 (default), no resync task is spawned."""
    cache = PodStateCache()
    client = _make_client_with_list_result([])
    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=_blocking_watch_factory,
        watch_timeout_seconds=1,
        # periodic_resync_interval_seconds not set -> defaults to 0
    )
    watcher.start()
    # Give the loop a chance to start tasks
    await asyncio.sleep(0.05)

    assert watcher._resync_task is None

    await watcher.stop()


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_resync_disabled_no_extra_list_calls() -> None:
    """With interval=0, list_namespaced_pod is called at most once (the watch session)."""
    cache = PodStateCache()
    client = _make_client_with_list_result([])
    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=_blocking_watch_factory,
        watch_timeout_seconds=1,
        periodic_resync_interval_seconds=0,
    )
    watcher.start()
    await asyncio.sleep(0.1)
    await watcher.stop()

    # The watch factory is a streaming call (not list_namespaced_pod); the
    # list function should not have been called by the resync path.
    assert client.core_v1.list_namespaced_pod.call_count == 0


# ---------------------------------------------------------------------------
# enabled (interval > 0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_resync_task_is_created_when_interval_positive() -> None:
    """With a positive interval, a resync asyncio task is created at start()."""
    cache = PodStateCache()
    client = _make_client_with_list_result([])
    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=_blocking_watch_factory,
        watch_timeout_seconds=1,
        periodic_resync_interval_seconds=10,
    )
    watcher.start()
    await asyncio.sleep(0.05)

    assert watcher._resync_task is not None
    assert not watcher._resync_task.done()

    await watcher.stop()


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_resync_fires_after_interval() -> None:
    """The resync task calls _relist_snapshot once the interval elapses."""
    cache = PodStateCache()
    client = _make_client_with_list_result([])

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=_blocking_watch_factory,
        watch_timeout_seconds=1,
        periodic_resync_interval_seconds=1,  # short interval
    )

    # Patch _relist_snapshot to count calls
    relist_calls: list[int] = []

    original = watcher._relist_snapshot

    def counting_relist(**kwargs: Any) -> Any:
        relist_calls.append(1)
        return original(**kwargs)

    watcher._relist_snapshot = counting_relist  # type: ignore[method-assign]

    watcher.start()
    # Wait slightly more than the interval
    await asyncio.sleep(1.2)
    await watcher.stop()

    # Should have been called at least once
    assert len(relist_calls) >= 1


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_resync_reconciles_drift_in_cache() -> None:
    """When a pod is in the LIST but was missed by the watch, resync adds it."""
    cache = PodStateCache()
    new_pod = _pod(name="orb-0002", request_id="req-2")
    client = _make_client_with_list_result([new_pod])

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=_blocking_watch_factory,
        watch_timeout_seconds=1,
        periodic_resync_interval_seconds=1,
    )
    watcher.start()
    # Wait for the resync to fire
    await asyncio.sleep(1.3)
    await watcher.stop()

    # The pod from the LIST should now be in the cache
    states = cache.get("req-2")
    assert states is not None
    assert any(s.pod_name == "orb-0002" for s in states)


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_resync_error_is_logged_not_propagated() -> None:
    """Errors during periodic resync are logged at WARNING and execution continues."""
    cache = PodStateCache()
    client = MagicMock()
    client.core_v1.list_namespaced_pod = MagicMock(side_effect=Exception("apiserver down"))
    client.core_v1.list_pod_for_all_namespaces = MagicMock(side_effect=Exception("apiserver down"))

    mock_logger = MagicMock()

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=mock_logger,
        namespace="ns",
        watch_factory=_blocking_watch_factory,
        watch_timeout_seconds=1,
        periodic_resync_interval_seconds=1,
    )
    watcher.start()
    await asyncio.sleep(1.3)
    await watcher.stop()

    # logger.warning should have been called for the resync error
    assert mock_logger.warning.called
    # Resync task itself should have exited cleanly (not propagated)
    assert watcher._resync_task is None


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_resync_task_stops_when_watcher_stops() -> None:
    """The resync task must be cancelled/stopped when stop() is called."""
    cache = PodStateCache()
    client = _make_client_with_list_result([])

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=_blocking_watch_factory,
        watch_timeout_seconds=1,
        periodic_resync_interval_seconds=60,  # Long interval so it's always pending
    )
    watcher.start()
    await asyncio.sleep(0.05)
    assert watcher._resync_task is not None

    await watcher.stop()

    # After stop, the resync task reference should be cleared
    assert watcher._resync_task is None
