"""Integration test for the asyncio pod watcher lifecycle.

Drives :class:`K8sWatcher` end-to-end with a stub ``Watch`` so
no apiserver is required.  Covers:

* steady-state — synthetic ADDED / MODIFIED / DELETED events propagate
  into the shared :class:`PodStateCache`;
* 410 Gone reconnection — the watcher drops its in-flight
  ``resource_version`` and re-LISTs from the latest; the cache continues
  to receive events from the new session;
* graceful shutdown — :meth:`stop` cancels the loop, closes the inner
  ``Watch`` and the task settles within the timeout budget.
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


def _synthetic_pod(
    *,
    name: str,
    request_id: str,
    phase: str = "Running",
    ready: bool = True,
    namespace: str = "orb-it",
) -> SimpleNamespace:
    conditions = [
        SimpleNamespace(
            type="Ready",
            status="True" if ready else "False",
            reason=None,
        )
    ]
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels={
                "orb.io/managed": "true",
                "orb.io/request-id": request_id,
                "orb.io/machine-id": name,
            },
        ),
        spec=SimpleNamespace(node_name="node-a"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.2",
            host_ip="10.1.0.2",
            start_time=None,
            conditions=conditions,
            container_statuses=[],
        ),
    )


class _StubWatch:
    """Minimal ``kubernetes.watch.Watch`` stand-in.

    The watcher constructs a fresh ``Watch`` on every reconnect, so
    tests pass a factory that hands out stubs in sequence.  Each stub
    yields a fixed event list then optionally raises an exception to
    simulate either a clean end-of-stream or an error condition (e.g.
    ``410 Gone``).
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


def _client_mock() -> MagicMock:
    client = MagicMock()
    client.core_v1.list_namespaced_pod = MagicMock(name="list_namespaced_pod")
    client.core_v1.list_pod_for_all_namespaces = MagicMock(name="list_pod_for_all_namespaces")
    return client


async def _await_predicate(
    predicate: Any,
    *,
    timeout: float = 2.0,
    interval: float = 0.01,
) -> bool:
    """Poll ``predicate`` until it returns truthy or the timeout elapses."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_watch_steady_state_event_translation() -> None:
    """ADDED + MODIFIED + DELETED events propagate into the cache."""
    cache = PodStateCache()
    client = _client_mock()
    request_id = "req-77777777-7777-7777-7777-777777777777"

    events = [
        {"type": "ADDED", "object": _synthetic_pod(name="orb-a-0001", request_id=request_id)},
        {
            "type": "MODIFIED",
            "object": _synthetic_pod(
                name="orb-a-0001", request_id=request_id, phase="Running", ready=False
            ),
        },
        {"type": "ADDED", "object": _synthetic_pod(name="orb-a-0002", request_id=request_id)},
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
        namespace="orb-it",
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    assert await _await_predicate(lambda: cache.size() >= 2)

    # Second pod ADDED brought the total to 2; first pod was modified to
    # status "starting" (Running but not Ready).
    states = cache.get(request_id)
    assert states is not None
    assert {s.pod_name for s in states} == {"orb-a-0001", "orb-a-0002"}
    by_name = {s.pod_name: s for s in states}
    assert by_name["orb-a-0001"].status == "starting"
    assert by_name["orb-a-0002"].status == "running"

    # Now feed a DELETED event for one pod and confirm eviction.
    new_events = [
        {"type": "DELETED", "object": _synthetic_pod(name="orb-a-0001", request_id=request_id)},
    ]
    follow_up = _StubWatch(iter(new_events))
    # Inject the next stub by reusing the factory slot.  The watcher
    # naturally reconnects after the stream above finished, so we patch
    # the factory closure via a fresh container.
    stubs_followup = iter([follow_up])

    def factory_followup() -> _StubWatch:
        try:
            return next(stubs_followup)
        except StopIteration:
            return _StubWatch(iter([]))

    watcher._watch_factory = factory_followup  # type: ignore[attr-defined]
    assert await _await_predicate(
        lambda: (cache.get(request_id) or []) and len(cache.get(request_id) or []) == 1
    )
    await watcher.stop()
    remaining = cache.get(request_id)
    assert remaining is not None and len(remaining) == 1
    assert remaining[0].pod_name == "orb-a-0002"


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_watch_410_gone_reconnects_without_resource_version() -> None:
    """A 410 mid-stream resets the resource_version and continues."""
    cache = PodStateCache()
    client = _client_mock()
    request_id = "req-88888888-8888-8888-8888-888888888888"

    first = _StubWatch(
        iter(
            [
                {
                    "type": "ADDED",
                    "object": _synthetic_pod(name="orb-b-0001", request_id=request_id),
                }
            ]
        ),
        raise_after=ApiException(status=410, reason="Gone"),
    )
    second = _StubWatch(
        iter(
            [
                {
                    "type": "ADDED",
                    "object": _synthetic_pod(name="orb-b-0002", request_id=request_id),
                }
            ]
        )
    )
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
        namespace="orb-it",
        watch_factory=factory,
        watch_timeout_seconds=1,
        base_backoff_seconds=0.01,
    )
    watcher.start()
    assert await _await_predicate(
        lambda: (cache.get(request_id) or []) and len(cache.get(request_id) or []) >= 2
    )
    await watcher.stop()

    # Second session must NOT have carried a resource_version because
    # the 410 handler drops it before reconnecting.
    assert "resource_version" not in second.last_kwargs
    # The 410 must not increment the consecutive-failure counter.
    assert watcher.last_error is None


class _BlockingStubWatch(_StubWatch):
    """Stub that blocks the stream generator until ``stop`` is called.

    Mirrors the production ``kubernetes.watch.Watch`` behaviour where
    the stream remains open until the apiserver returns or the SDK's
    ``stop`` is called from another thread.  This lets the outer watcher
    loop sit inside ``_run_one_session`` so :meth:`stop` actually has an
    ``active_watch`` to close.
    """

    def __init__(self, first_event: Any) -> None:
        super().__init__(iter([]))
        self._first_event = first_event
        self._stop_event = threading.Event()

    def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
        self.last_func = func
        self.last_kwargs = kwargs
        # Yield the first event so cache reads observe steady-state,
        # then block until ``stop`` releases the event.
        yield self._first_event
        # ``stream`` is a generator; wait for the stop signal.
        self._stop_event.wait(timeout=15.0)

    def stop(self) -> None:
        super().stop()
        self._stop_event.set()


@pytest.mark.asyncio
@pytest.mark.timeout(20)
async def test_watch_graceful_shutdown_closes_inner_watch() -> None:
    """``stop`` flips the cancellation flag and the inner Watch's stop is invoked."""
    cache = PodStateCache()
    client = _client_mock()
    stub = _BlockingStubWatch(
        first_event={
            "type": "ADDED",
            "object": _synthetic_pod(
                name="orb-c-0001",
                request_id="req-99999999-9999-9999-9999-999999999999",
            ),
        }
    )

    def factory() -> _BlockingStubWatch:
        return stub

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="orb-it",
        watch_factory=factory,
        watch_timeout_seconds=1,
    )
    watcher.start()
    # Let the event flow into the cache.
    assert await _await_predicate(lambda: cache.size() >= 1, timeout=5.0)
    # ``stop`` should resolve quickly — well within the 5s internal
    # watchdog and our 20s outer timeout.
    await asyncio.wait_for(watcher.stop(), timeout=10.0)
    assert not watcher.is_running()
    # The inner Watch saw the stop request.
    assert stub._stopped is True
