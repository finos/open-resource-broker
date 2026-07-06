"""Unit tests for :class:`K8sNodeWatcher`.

The watcher wraps ``kubernetes.watch.Watch().stream(...)``; the tests
inject a stub ``Watch`` via the ``watch_factory`` constructor parameter
so no apiserver is required.  Covers:

* event translation (ADDED / MODIFIED / DELETED) into cache mutations
* label extraction: instance-type, zone, capacity-type (stable and beta labels)
* condition extraction and ``ready`` derivation
* capacity / allocatable resource string extraction
* 410 Gone resets resource_version and continues
* exponential backoff between retries on non-410 errors
* :meth:`stop` signals the thread and joins cleanly
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.providers.k8s.watch.node_state_cache import K8sNodeStateCache
from orb.providers.k8s.watch.node_watcher import K8sNodeWatcher

# ---------------------------------------------------------------------------
# Stub node builder
# ---------------------------------------------------------------------------


def _node(
    *,
    name: str = "node-a",
    instance_type: str | None = "m5.xlarge",
    zone: str | None = "us-east-1a",
    capacity_type: str | None = "on-demand",
    cpu_capacity: str | None = "4",
    memory_capacity: str | None = "16Gi",
    cpu_allocatable: str | None = "3800m",
    memory_allocatable: str | None = "14Gi",
    ready: bool = True,
    use_beta_labels: bool = False,
) -> SimpleNamespace:
    """Build a fake ``V1Node`` that mimics the kubernetes SDK object shape."""
    labels: dict[str, str] = {}
    if instance_type is not None:
        key = (
            "beta.kubernetes.io/instance-type"
            if use_beta_labels
            else "node.kubernetes.io/instance-type"
        )
        labels[key] = instance_type
    if zone is not None:
        key = (
            "failure-domain.beta.kubernetes.io/zone"
            if use_beta_labels
            else "topology.kubernetes.io/zone"
        )
        labels[key] = zone
    if capacity_type is not None:
        labels["karpenter.sh/capacity-type"] = capacity_type

    conditions: list[SimpleNamespace] = [
        SimpleNamespace(
            type="Ready",
            status="True" if ready else "False",
            reason=None,
            last_transition_time=None,
        )
    ]

    # capacity and allocatable are dicts in the real SDK (already a dict or
    # an object; we test both shapes via different tests — here use dict).
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels=labels),
        status=SimpleNamespace(
            capacity={"cpu": cpu_capacity, "memory": memory_capacity},
            allocatable={"cpu": cpu_allocatable, "memory": memory_allocatable},
            conditions=conditions,
        ),
    )


# ---------------------------------------------------------------------------
# Stub Watch factory helpers
# ---------------------------------------------------------------------------


class _StubWatch:
    """Minimal stand-in for ``kubernetes.watch.Watch``.

    The watcher constructs a fresh ``Watch`` for every reconnect, so
    tests supply a *factory* that returns whatever sequence of stubs
    they want.
    """

    def __init__(self, events: Iterator[Any], *, raise_after: Exception | None = None) -> None:
        self._events = events
        self._raise_after = raise_after
        self._stopped = False
        self.resource_version: str | None = None
        self.last_kwargs: dict[str, Any] = {}
        self.last_func: Any = None

    def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:  # type: ignore[override]
        self.last_func = func
        self.last_kwargs = kwargs
        yield from self._events
        if self._raise_after is not None:
            raise self._raise_after

    def stop(self) -> None:
        self._stopped = True


def _single_session_factory(events: list[Any]) -> Any:
    """Return a factory that yields a single stub watch then an empty stream."""
    call_count = 0

    def factory() -> _StubWatch:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _StubWatch(iter(events))
        # Second and subsequent sessions: stop immediately.
        return _StubWatch(iter([]))

    return factory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: str, node: Any) -> dict[str, Any]:
    return {"type": event_type, "object": node}


def _make_watcher(
    cache: K8sNodeStateCache,
    watch_factory: Any,
    *,
    base_backoff: float = 0.001,
) -> K8sNodeWatcher:
    """Build a K8sNodeWatcher with a mock kubernetes client."""
    client = MagicMock()
    client.core_v1.list_node = MagicMock()
    return K8sNodeWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        watch_factory=watch_factory,
        base_backoff_seconds=base_backoff,
        max_backoff_seconds=0.05,
    )


def _run_sync(watcher: K8sNodeWatcher) -> None:
    """Run one session synchronously on the calling thread for determinism.

    Calls :meth:`K8sNodeWatcher._run_one_session` directly so there is
    no thread boundary and no need to join.  The stop event is set
    *after* the session so a second call to ``_run`` would exit cleanly.
    """
    try:
        watcher._run_one_session(None)
    except Exception:
        pass
    watcher._stop_event.set()


# ---------------------------------------------------------------------------
# Event translation
# ---------------------------------------------------------------------------


def test_added_event_upserts_node_state() -> None:
    cache = K8sNodeStateCache()
    node = _node(name="node-a", instance_type="m5.xlarge", zone="us-east-1a")
    factory = _single_session_factory([_make_event("ADDED", node)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    state = cache.get("node-a")
    assert state is not None
    assert state.instance_type == "m5.xlarge"
    assert state.zone == "us-east-1a"
    assert state.capacity_type == "on-demand"
    assert state.ready is True


def test_modified_event_updates_existing_entry() -> None:
    cache = K8sNodeStateCache()
    # Seed the cache with initial state
    node_v1 = _node(name="node-b", instance_type="m5.xlarge")
    node_v2 = _node(name="node-b", instance_type="c5.2xlarge")
    factory = _single_session_factory(
        [_make_event("ADDED", node_v1), _make_event("MODIFIED", node_v2)]
    )
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    state = cache.get("node-b")
    assert state is not None
    assert state.instance_type == "c5.2xlarge"


def test_deleted_event_removes_node_from_cache() -> None:
    cache = K8sNodeStateCache()
    # Seed, then delete.
    node = _node(name="node-c")
    factory = _single_session_factory([_make_event("ADDED", node), _make_event("DELETED", node)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    assert cache.get("node-c") is None


def test_beta_labels_are_used_as_fallback() -> None:
    """When stable labels are absent, beta equivalents fill in the gaps."""
    cache = K8sNodeStateCache()
    node = _node(name="node-d", instance_type="t3.medium", zone="eu-west-1b", use_beta_labels=True)
    factory = _single_session_factory([_make_event("ADDED", node)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    state = cache.get("node-d")
    assert state is not None
    assert state.instance_type == "t3.medium"
    assert state.zone == "eu-west-1b"


def test_capacity_and_allocatable_are_extracted() -> None:
    cache = K8sNodeStateCache()
    node = _node(
        name="node-e",
        cpu_capacity="8",
        memory_capacity="32Gi",
        cpu_allocatable="7500m",
        memory_allocatable="30Gi",
    )
    factory = _single_session_factory([_make_event("ADDED", node)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    state = cache.get("node-e")
    assert state is not None
    assert state.cpu_capacity == "8"
    assert state.memory_capacity == "32Gi"
    assert state.cpu_allocatable == "7500m"
    assert state.memory_allocatable == "30Gi"


def test_not_ready_node_is_reflected() -> None:
    cache = K8sNodeStateCache()
    node = _node(name="node-f", ready=False)
    factory = _single_session_factory([_make_event("ADDED", node)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    state = cache.get("node-f")
    assert state is not None
    assert state.ready is False


def test_node_with_no_labels_has_none_fields() -> None:
    """Nodes with no relevant labels must produce None for optional fields."""
    cache = K8sNodeStateCache()
    node = SimpleNamespace(
        metadata=SimpleNamespace(name="bare-node", labels={}),
        status=SimpleNamespace(
            capacity=None,
            allocatable=None,
            conditions=[],
        ),
    )
    factory = _single_session_factory([_make_event("ADDED", node)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    state = cache.get("bare-node")
    assert state is not None
    assert state.instance_type is None
    assert state.zone is None
    assert state.capacity_type is None
    assert state.cpu_capacity is None
    assert state.memory_capacity is None
    assert state.ready is False


# ---------------------------------------------------------------------------
# 410 Gone reconnect
# ---------------------------------------------------------------------------


def test_410_resets_resource_version_and_continues() -> None:
    """A 410 ApiException must reset resource_version and retry cleanly."""
    cache = K8sNodeStateCache()
    api_410 = ApiException(status=410, reason="Gone")

    call_count = 0

    def factory() -> _StubWatch:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First session: raise 410 after yielding nothing.
            return _StubWatch(iter([]), raise_after=api_410)
        # Second session: yield one event then allow stop.
        node = _node(name="node-reconnect")
        return _StubWatch(iter([_make_event("ADDED", node)]))

    watcher = _make_watcher(cache, factory)

    # Session 1 raises 410 -> _ResourceTooOld should be raised.
    from orb.providers.k8s.watch.node_watcher import _ResourceTooOld

    try:
        watcher._run_one_session(resource_version="some-rv")
        pytest.fail("Expected _ResourceTooOld")
    except _ResourceTooOld:
        pass  # Expected — outer loop resets rv to None

    # Session 2 with rv=None; stop_event must NOT be set so the stream runs.
    assert not watcher._stop_event.is_set()
    watcher._run_one_session(resource_version=None)

    state = cache.get("node-reconnect")
    assert state is not None


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


@pytest.mark.timeout(5)
def test_stop_signals_thread_to_exit() -> None:
    """Calling stop() must set the stop event and join the thread."""
    cache = K8sNodeStateCache()

    stop_evt = threading.Event()

    def slow_factory() -> _StubWatch:
        # Block until stop is called.
        class _BlockingStream:
            def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
                stop_evt.wait(timeout=4.0)
                return iter([])

            def stop(self) -> None:
                stop_evt.set()

        w = _BlockingStream()
        return w  # type: ignore[return-value]

    client = MagicMock()
    client.core_v1.list_node = MagicMock()
    watcher = K8sNodeWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        watch_factory=slow_factory,
        base_backoff_seconds=0.001,
        max_backoff_seconds=0.01,
    )

    watcher.start()
    assert watcher.is_running()

    watcher.stop(timeout=3.0)
    assert not watcher.is_running()


@pytest.mark.timeout(5)
def test_start_is_idempotent() -> None:
    """Calling start() while already running is a no-op."""
    cache = K8sNodeStateCache()
    call_count = 0

    def blocking_factory() -> _StubWatch:
        nonlocal call_count
        call_count += 1

        class _BlockingStream:
            def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
                time.sleep(0.2)
                return iter([])

            def stop(self) -> None:
                pass

        return _BlockingStream()  # type: ignore[return-value]

    client = MagicMock()
    client.core_v1.list_node = MagicMock()
    watcher = K8sNodeWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        watch_factory=blocking_factory,
        base_backoff_seconds=0.001,
        max_backoff_seconds=0.01,
    )

    watcher.start()
    thread_id = watcher._thread.ident if watcher._thread else None
    watcher.start()  # Should be a no-op
    assert watcher._thread is not None
    assert watcher._thread.ident == thread_id

    watcher.stop(timeout=2.0)


# ---------------------------------------------------------------------------
# Backoff calculation
# ---------------------------------------------------------------------------


def test_backoff_doubles_per_attempt() -> None:
    cache = K8sNodeStateCache()
    client = MagicMock()
    watcher = K8sNodeWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        base_backoff_seconds=1.0,
        max_backoff_seconds=32.0,
    )

    assert watcher._backoff_for_attempt(1) == 1.0
    assert watcher._backoff_for_attempt(2) == 2.0
    assert watcher._backoff_for_attempt(3) == 4.0
    assert watcher._backoff_for_attempt(4) == 8.0
    assert watcher._backoff_for_attempt(6) == 32.0  # Capped
    assert watcher._backoff_for_attempt(10) == 32.0  # Still capped
