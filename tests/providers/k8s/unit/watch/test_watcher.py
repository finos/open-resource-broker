"""Unit tests for :class:`K8sWatcher`.

The watcher wraps ``kubernetes.watch.Watch().stream(...)``; the tests
inject a stub ``Watch`` via the ``watch_factory`` constructor parameter
so no apiserver is required.  Covers:

* event translation (ADDED / MODIFIED / DELETED) into cache mutations
* 410 Gone resets the resource_version and continues
* exponential backoff between retries on non-410 errors
* :meth:`stop` cancels the loop and the inner Watch
* cluster-scoped mode picks ``list_pod_for_all_namespaces``
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.providers.k8s.watch.pod_state_cache import PodStateCache
from orb.providers.k8s.watch.watcher import K8sWatcher


def _pod(
    *,
    name: str,
    phase: str = "Running",
    ready: bool = True,
    request_id: str = "req-1",
    namespace: str = "ns",
    label_prefix: str = "orb.io",
) -> SimpleNamespace:
    conditions: list[SimpleNamespace] = []
    if ready:
        conditions.append(SimpleNamespace(type="Ready", status="True", reason=None))
    else:
        conditions.append(SimpleNamespace(type="Ready", status="False", reason=None))
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels={
                f"{label_prefix}/managed": "true",
                f"{label_prefix}/request-id": request_id,
                f"{label_prefix}/machine-id": name,
            },
        ),
        spec=SimpleNamespace(node_name="node-a"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1",
            host_ip="10.1.0.1",
            start_time=None,
            conditions=conditions,
            container_statuses=[],
        ),
    )


class _StubWatch:
    """Minimal stand-in for ``kubernetes.watch.Watch``.

    The watcher constructs a fresh ``Watch`` for every reconnect, so
    tests should pass a *factory* that returns whatever sequence of
    stub watches they want — see ``_stub_watch_factory`` below.
    """

    def __init__(self, events: Iterator[Any], *, raise_after: Exception | None = None) -> None:
        self._events = events
        self._raise_after = raise_after
        self._stopped = False
        self.resource_version: str | None = None
        self.last_kwargs: dict[str, Any] = {}
        self.last_func: Any = None

    def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
        self.last_func = func
        self.last_kwargs = kwargs
        for ev in self._events:
            if self._stopped:
                return
            yield ev
        if self._raise_after is not None:
            raise self._raise_after

    def stop(self) -> None:
        self._stopped = True


def _make_kubernetes_client_mock() -> MagicMock:
    """Return a mock with .core_v1.list_namespaced_pod and list_pod_for_all_namespaces."""
    client = MagicMock()
    client.core_v1.list_namespaced_pod = MagicMock(name="list_namespaced_pod")
    client.core_v1.list_pod_for_all_namespaces = MagicMock(name="list_pod_for_all_namespaces")
    return client


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_added_event_populates_cache() -> None:
    cache = PodStateCache()
    client = _make_kubernetes_client_mock()
    events = [
        {"type": "ADDED", "object": _pod(name="orb-0001", phase="Running", ready=True)},
    ]
    # Factory returns one stub; on subsequent reconnects we return an
    # immediately-empty stream so the loop idles until ``stop`` is
    # called.
    stubs = iter([_StubWatch(iter(events))])

    def factory() -> _StubWatch:
        try:
            return next(stubs)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    # Poll briefly for the event to land in the cache.
    for _ in range(50):
        if cache.size() > 0:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    states = cache.get("req-1")
    assert states is not None
    assert len(states) == 1
    state = states[0]
    assert state.pod_name == "orb-0001"
    assert state.status == "running"
    assert state.phase == "Running"
    assert state.namespace == "ns"


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_deleted_event_evicts_cache_entry() -> None:
    cache = PodStateCache()
    client = _make_kubernetes_client_mock()
    events = [
        {"type": "ADDED", "object": _pod(name="orb-0001")},
        {"type": "DELETED", "object": _pod(name="orb-0001")},
    ]
    stubs = iter([_StubWatch(iter(events))])

    def factory() -> _StubWatch:
        try:
            return next(stubs)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    for _ in range(50):
        if watcher.last_event_at > 0 and cache.size() == 0:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    assert cache.get("req-1") is None


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_410_gone_relists_and_resumes_from_new_resource_version() -> None:
    """A 410 ApiException triggers a fresh LIST; the watch resumes from the LIST rv.

    rv=0 or rv=None would allow the apiserver to skip events between the
    stale rv and its cache start, leaving the pod cache out of sync.  The
    correct recovery is a full LIST (consistent snapshot) followed by a
    watch resumed from the resourceVersion the LIST returned.

    After the LIST, pods that were in the cache before the 410 but are
    absent from the LIST snapshot are evicted (fix: resync must evict
    deleted pods, not just upsert live ones).  ``orb-0001`` was observed
    by the first watch session but is NOT in the relist snapshot — it
    must be evicted.  Only ``orb-relist`` (from the LIST) and ``orb-0002``
    (from the second watch session) survive.
    """
    cache = PodStateCache()
    client = _make_kubernetes_client_mock()

    relist_pod = _pod(name="orb-relist")
    relist_response = MagicMock()
    relist_response.metadata = MagicMock(resource_version="12345")
    relist_response.items = [relist_pod]
    client.core_v1.list_namespaced_pod.return_value = relist_response

    first = _StubWatch(
        iter([{"type": "ADDED", "object": _pod(name="orb-0001")}]),
        raise_after=ApiException(status=410, reason="Gone"),
    )
    second = _StubWatch(iter([{"type": "ADDED", "object": _pod(name="orb-0002")}]))
    stubs = iter([first, second])

    def factory() -> _StubWatch:
        try:
            return next(stubs)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    # Wait for the second watch session to produce its event (orb-0002).
    for _ in range(200):
        states = cache.get("req-1")
        if states is not None and any(s.pod_name == "orb-0002" for s in states):
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    states = cache.get("req-1")
    assert states is not None
    pod_names = {s.pod_name for s in states}
    # orb-relist: added by the 410-recovery LIST snapshot.
    # orb-0002:   added by the second watch session after the LIST.
    # orb-0001:   was in the cache from the first watch session but is absent
    #             from the LIST snapshot — must have been evicted by resync.
    assert "orb-relist" in pod_names, "LIST snapshot pod must be in cache"
    assert "orb-0002" in pod_names, "post-LIST watch event pod must be in cache"
    assert "orb-0001" not in pod_names, (
        "Pod absent from the LIST snapshot must be evicted by resync "
        "(it was deleted between watch session 1 and the relist)"
    )
    # The re-list must have been called with label_selector but WITHOUT
    # a stale resource_version.
    assert client.core_v1.list_namespaced_pod.called
    relist_kwargs = client.core_v1.list_namespaced_pod.call_args.kwargs
    assert "label_selector" in relist_kwargs
    assert relist_kwargs.get("resource_version") in (None, "")
    # Second watch session must resume from the rv the LIST returned,
    # NOT rv="0" (would skip events) and NOT rv=None (would rewind).
    assert second.last_kwargs.get("resource_version") == "12345"
    # consecutive_failures must NOT have ticked up for a 410.
    assert watcher.last_error is None


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_non_410_error_triggers_exponential_backoff() -> None:
    """Non-410 errors increment the failure counter and back off."""
    cache = PodStateCache()
    client = _make_kubernetes_client_mock()

    # First two sessions raise; third yields one event so we can confirm
    # recovery and inspect the backoff trace.
    first = _StubWatch(iter([]), raise_after=RuntimeError("boom1"))
    second = _StubWatch(iter([]), raise_after=RuntimeError("boom2"))
    third = _StubWatch(iter([{"type": "ADDED", "object": _pod(name="orb-0001")}]))
    stubs = iter([first, second, third])

    def factory() -> _StubWatch:
        try:
            return next(stubs)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
        base_backoff_seconds=0.01,
        max_backoff_seconds=0.04,
    )

    # Validate the backoff formula independently of timing.
    assert watcher._backoff_for_attempt(1) == pytest.approx(0.01)
    assert watcher._backoff_for_attempt(2) == pytest.approx(0.02)
    assert watcher._backoff_for_attempt(3) == pytest.approx(0.04)
    assert watcher._backoff_for_attempt(4) == pytest.approx(0.04)  # capped

    watcher.start()
    for _ in range(200):
        if cache.size() >= 1:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    states = cache.get("req-1")
    assert states is not None
    assert {s.pod_name for s in states} == {"orb-0001"}
    # After successful recovery the failure counter is reset.
    assert watcher.last_error is None


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_cluster_scoped_uses_list_pod_for_all_namespaces() -> None:
    """When ``namespace=None`` the watcher calls the cluster-scoped API."""
    cache = PodStateCache()
    client = _make_kubernetes_client_mock()
    stub = _StubWatch(iter([{"type": "ADDED", "object": _pod(name="orb-0001")}]))
    stubs = iter([stub])

    def factory() -> _StubWatch:
        try:
            return next(stubs)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace=None,
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    for _ in range(50):
        if stub.last_func is not None:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    assert stub.last_func is client.core_v1.list_pod_for_all_namespaces
    assert "namespace" not in stub.last_kwargs


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_stop_terminates_loop_promptly() -> None:
    """``stop`` must close the inner Watch and let the task settle."""
    cache = PodStateCache()
    client = _make_kubernetes_client_mock()

    # Long-running stub that yields nothing — emulates a quiet stream.
    stub = _StubWatch(iter([]))
    stubs = iter([stub])

    def factory() -> _StubWatch:
        try:
            return next(stubs)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    await asyncio.sleep(0.05)
    await watcher.stop()
    assert not watcher.is_running()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_pod_missing_request_id_label_is_skipped() -> None:
    """Pods missing the request-id label must not be cached."""
    cache = PodStateCache()
    client = _make_kubernetes_client_mock()
    pod = _pod(name="orb-stray")
    # Strip the request-id label.
    pod.metadata.labels.pop("orb.io/request-id")
    events = [{"type": "ADDED", "object": pod}]
    stubs = iter([_StubWatch(iter(events))])

    def factory() -> _StubWatch:
        try:
            return next(stubs)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    for _ in range(50):
        if watcher.last_event_at > 0:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    assert cache.size() == 0


def test_is_resource_version_too_old_detects_410() -> None:
    """Static helper must classify 410 vs other ApiException correctly."""
    assert K8sWatcher._is_resource_version_too_old(ApiException(status=410))
    assert not K8sWatcher._is_resource_version_too_old(ApiException(status=500))
    assert not K8sWatcher._is_resource_version_too_old(RuntimeError("oops"))


# ---------------------------------------------------------------------------
# threading.Event stop signal — correctness in worker thread
# ---------------------------------------------------------------------------


def test_stop_thread_event_is_threading_event() -> None:
    """_stop_thread_event must be a threading.Event, not an asyncio.Event.

    asyncio.Event is bound to the event loop and must not be called from
    worker threads spawned by asyncio.to_thread.  threading.Event is the
    correct primitive for cross-thread signalling.
    """
    client = _make_kubernetes_client_mock()
    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=PodStateCache(),
        logger=MagicMock(),
        namespace="ns",
    )
    assert isinstance(watcher._stop_thread_event, threading.Event), (
        "_stop_thread_event must be threading.Event for thread-safe cross-thread stop signalling"
    )


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_stop_sets_threading_event() -> None:
    """Calling stop() must set _stop_thread_event so the worker thread can observe it."""
    client = _make_kubernetes_client_mock()

    # A stub that pauses until _stop_thread_event is observed set.
    signal_received: list[bool] = []

    class _BlockingWatch:
        resource_version: str | None = None

        def stream(self, func: Any, **kwargs: Any) -> Any:
            # Spin until the threading event fires — this simulates the worker
            # thread polling the stop event while consuming a long-lived stream.
            for _ in range(2000):
                import time

                time.sleep(0.001)
                # We cannot reference watcher here yet, so we read via closure.
                if stop_event_ref[0].is_set():
                    signal_received.append(True)
                    return
            return

        def stop(self) -> None:
            pass

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=PodStateCache(),
        logger=MagicMock(),
        namespace="ns",
        watch_factory=_BlockingWatch,
        watch_timeout_seconds=1,
    )
    stop_event_ref: list[Any] = [watcher._stop_thread_event]

    watcher.start()
    await asyncio.sleep(0.05)
    await watcher.stop()

    assert watcher._stop_thread_event.is_set(), (
        "stop() must set the threading.Event so the worker thread exits"
    )


# ---------------------------------------------------------------------------
# T01 — resource_version="0" fast LIST fallback on 410-Gone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_410_gone_never_resumes_with_rv_zero() -> None:
    """After a 410-Gone the watcher must NEVER pass rv='0' to the next watch.

    rv='0' allows the apiserver to serve events from an arbitrary point
    in its cache and skip mutations that happened between the stale
    resource_version and the cache start.  The correct recovery is a
    fresh LIST followed by a watch resumed from the LIST's rv — never
    rv='0'.  This test locks in the invariant.
    """
    cache = PodStateCache()
    client = _make_kubernetes_client_mock()

    relist_response = MagicMock()
    relist_response.metadata = MagicMock(resource_version="99")
    relist_response.items = []
    client.core_v1.list_namespaced_pod.return_value = relist_response

    first = _StubWatch(
        iter([]),
        raise_after=ApiException(status=410, reason="Gone"),
    )
    second = _StubWatch(iter([{"type": "ADDED", "object": _pod(name="orb-post-410")}]))
    stubs = iter([first, second])

    def factory() -> _StubWatch:
        try:
            return next(stubs)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="ns",
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    for _ in range(100):
        if cache.size() >= 1:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    assert second.last_kwargs.get("resource_version") != "0", (
        "410-Gone handler must NOT resume watch with rv='0' — that would "
        "silently drop events between the stale rv and the apiserver cache start"
    )
    assert second.last_kwargs.get("resource_version") == "99"
