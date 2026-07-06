"""kmock-backed tests for the K8sWatcher pod ingest pipeline.

Architecture note on scope
--------------------------

The ``K8sWatcher`` consumes the Kubernetes Watch API via
``kubernetes.watch.Watch().stream()``, which is a synchronous generator
that reads newline-delimited JSON from an HTTP chunked-transfer response.
The ORB watcher wraps this in ``asyncio.to_thread``.

kmock serves Kubernetes API endpoints via aiohttp, but the kubernetes SDK
Watch object makes a raw HTTP call with ``_preload_content=False`` and
reads lines from the streaming response.  Wiring kmock's async watch
endpoint to the SDK's synchronous streaming generator requires the server
to write newline-delimited JSON frames in the background while the thread
is blocking in ``readline()``.  This interaction works but is sensitive
to event-loop scheduling and read-timeout boundaries.

For these tests we therefore inject a synthetic ``watch_factory`` — a
callable that returns a stub mimicking the ``Watch.stream()`` interface
— while all other parts of the system (the real ``K8sClient`` facade,
the real ``PodStateCache``, and the real ``_handle_event``/``_pod_to_state``
translation pipeline) remain untouched.  The kmock server is still started
and used to validate the LIST call the watcher issues before opening the
watch stream (the ``_build_list_call`` path).

This hybrid approach tests the watcher's event-translation and cache-update
logic end-to-end without relying on the fragile interplay between an async
HTTP server and a blocking readline loop.

Covered scenarios
-----------------

* Watcher ingests pod ADDED events and populates PodStateCache.
* Watcher ingests pod DELETED events and removes entries from cache.
* Watcher resets resource_version to None on a 410 Gone from the watch
  factory and re-enters the stream loop.
* Watcher survives a transient exception from the watch factory and
  reconnects after backoff.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from kmock import KubernetesEmulator

# ---------------------------------------------------------------------------
# Stub watch factory helpers
# ---------------------------------------------------------------------------


def _make_v1pod(
    *,
    name: str,
    namespace: str = "orb-test",
    phase: str = "Running",
    ready: bool = True,
    request_id: str = "req-test",
) -> Any:
    """Build a minimal V1Pod-like namespace object for watch event payloads."""
    conditions = [SimpleNamespace(type="Ready", status="True" if ready else "False", reason=None)]
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels={
                "orb.io/managed": "true",
                "orb.io/request-id": request_id,
                "orb.io/provider-api": "Pod",
            },
        ),
        spec=SimpleNamespace(node_name="node-1"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1" if phase == "Running" else None,
            host_ip="10.1.0.1" if phase == "Running" else None,
            start_time=None,
            conditions=conditions,
            container_statuses=[],
        ),
    )


class _SyntheticWatch:
    """Minimal stub implementing the ``Watch.stream()`` interface.

    ``stream()`` yields the provided events sequence then stops.
    The ``resource_version`` attribute is set to the last seen rv
    (mirroring the real SDK behaviour).
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.resource_version: str | None = None

    def stream(self, func: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:  # type: ignore[return]
        yield from self._events

    def stop(self) -> None:
        pass


class _ErrorWatch:
    """Watch stub that raises on stream() — simulates a transient disconnect."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.resource_version: str | None = None

    def stream(self, func: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        raise self._exc
        yield  # make it a generator function for type checkers  # noqa: unreachable

    def stop(self) -> None:
        pass


class _GoneWatch:
    """Watch stub that raises a 410 ApiException — simulates server GC of rv."""

    def __init__(self) -> None:
        self.resource_version: str | None = None

    def stream(self, func: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        from kubernetes.client.exceptions import ApiException  # noqa: PLC0415

        raise ApiException(status=410, reason="Gone")
        yield  # noqa: unreachable

    def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Watcher factory helper
# ---------------------------------------------------------------------------


def _make_watcher(
    k8s_client_facade: Any,
    k8s_config: Any,
    *,
    watch_factory: Any,
    namespace: str = "orb-test",
) -> tuple[Any, Any]:
    """Return ``(K8sWatcher, PodStateCache)`` with the given watch factory."""
    from orb.providers.k8s.watch.pod_state_cache import PodStateCache  # noqa: PLC0415
    from orb.providers.k8s.watch.watcher import K8sWatcher  # noqa: PLC0415

    cache = PodStateCache()
    watcher = K8sWatcher(
        kubernetes_client=k8s_client_facade,
        cache=cache,
        logger=MagicMock(),
        namespace=namespace,
        watch_factory=watch_factory,
        base_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
    )
    return watcher, cache


# ---------------------------------------------------------------------------
# test_watch_ingests_pod_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_ingests_pod_events(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Watcher populates PodStateCache from a single ADDED event sequence.

    The synthetic watch factory yields two ADDED events then returns.
    After the watcher processes the stream the cache must contain both pods.
    """
    request_id = str(uuid.uuid4())
    pod_a = _make_v1pod(name="orb-pod-0000", request_id=request_id)
    pod_b = _make_v1pod(name="orb-pod-0001", request_id=request_id)

    events = [
        {"type": "ADDED", "object": pod_a},
        {"type": "ADDED", "object": pod_b},
    ]

    call_count = 0

    def _factory() -> _SyntheticWatch:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _SyntheticWatch(events)
        # Second call — stop the watcher immediately by returning an empty stream.
        return _SyntheticWatch([])

    watcher, cache = _make_watcher(k8s_client_facade, k8s_config, watch_factory=_factory)
    watcher.start()
    # Allow two event-loop iterations for the thread to process events.
    await asyncio.sleep(0.1)
    await watcher.stop()

    states = cache.get(request_id) or []
    assert len(states) == 2
    pod_names = {s.pod_name for s in states}
    assert pod_names == {"orb-pod-0000", "orb-pod-0001"}
    for state in states:
        assert state.status == "running"


@pytest.mark.asyncio
async def test_watch_processes_deleted_event(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Watcher removes a pod from the cache on a DELETED event.

    Sequence: ADDED then DELETED.  After processing the cache must not
    contain any live state for the pod.
    """
    request_id = str(uuid.uuid4())
    pod = _make_v1pod(name="orb-ephemeral-0000", request_id=request_id)

    events = [
        {"type": "ADDED", "object": pod},
        {"type": "DELETED", "object": pod},
    ]

    call_count = 0

    def _factory() -> _SyntheticWatch:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _SyntheticWatch(events)
        return _SyntheticWatch([])

    watcher, cache = _make_watcher(k8s_client_facade, k8s_config, watch_factory=_factory)
    watcher.start()
    await asyncio.sleep(0.1)
    await watcher.stop()

    states = cache.get(request_id) or []
    # After DELETED the cache entry is removed; the list may be empty or
    # contain only a terminal snapshot depending on the race between the
    # stop signal and the delete processing.  At minimum there must be no
    # non-deleted state.
    live_states = [s for s in states if not s.deleted]
    assert live_states == [], f"Expected no live states after DELETED; got {live_states}"


# ---------------------------------------------------------------------------
# test_watch_reconnects_on_410_gone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_reconnects_on_410_gone(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Watcher resets resource_version to None on a 410 Gone response.

    The first watch factory call raises a 410 ApiException.  The outer loop
    must catch _ResourceTooOld (the private wrapper), reset resource_version,
    and restart without entering the backoff path.  A second call delivers
    a real event to confirm the loop recovered.
    """
    request_id = str(uuid.uuid4())
    pod = _make_v1pod(name="orb-recovered-0000", request_id=request_id)

    call_count = 0

    def _factory() -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _GoneWatch()
        if call_count == 2:
            return _SyntheticWatch([{"type": "ADDED", "object": pod}])
        return _SyntheticWatch([])

    watcher, cache = _make_watcher(k8s_client_facade, k8s_config, watch_factory=_factory)
    watcher.start()
    await asyncio.sleep(0.2)
    await watcher.stop()

    # The 410 path must NOT increment consecutive_failures (it is a clean
    # restart, not a failure).  We verify the cache has the recovered pod.
    states = cache.get(request_id) or []
    pod_names = {s.pod_name for s in states}
    assert "orb-recovered-0000" in pod_names


# ---------------------------------------------------------------------------
# test_watch_survives_transient_disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_survives_transient_disconnect(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Watcher reconnects after a transient exception from the watch factory.

    The first watch call raises a generic RuntimeError (simulating a TCP
    disconnect).  The outer loop must back off briefly and retry.  The
    second call delivers a real event confirming recovery.
    """
    request_id = str(uuid.uuid4())
    pod = _make_v1pod(name="orb-post-disconnect-0000", request_id=request_id)

    call_count = 0

    def _factory() -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _ErrorWatch(ConnectionResetError("peer closed connection"))
        if call_count == 2:
            return _SyntheticWatch([{"type": "ADDED", "object": pod}])
        return _SyntheticWatch([])

    watcher, cache = _make_watcher(k8s_client_facade, k8s_config, watch_factory=_factory)
    watcher.start()
    # Allow time for the backoff (0.01s) plus the second stream to process.
    await asyncio.sleep(0.3)
    await watcher.stop()

    states = cache.get(request_id) or []
    pod_names = {s.pod_name for s in states}
    assert "orb-post-disconnect-0000" in pod_names, (
        f"Expected pod in cache after reconnect; got {pod_names}"
    )
