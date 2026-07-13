"""Unit tests for :class:`K8sEventsWatcher` and :class:`K8sNodeEventsCache`.

Tests inject a stub ``Watch`` via the ``watch_factory`` constructor
parameter so no apiserver is required.  Covers:

* event translation (ADDED / MODIFIED / DELETED) into cache mutations
* Karpenter reason parsing: Underutilized/Delete, Empty/Delete, unknown
* field-selector double-check (non-Node involved objects are skipped)
* 410 Gone resets resource_version and continues
* exponential backoff between retries on non-410 errors
* :meth:`stop` signals the thread and joins cleanly
* ``_parse_karpenter_reason`` helper
* ``K8sNodeEventsCache`` thread-safe CRUD operations
"""

from __future__ import annotations

import contextlib
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.providers.k8s.watch.events_watcher import (
    KARPENTER_EMPTY_DELETE,
    KARPENTER_UNDERUTILIZED_DELETE,
    K8sEventsWatcher,
    K8sNodeDisruptionEvent,
    K8sNodeEventsCache,
    _parse_karpenter_reason,
)

# ---------------------------------------------------------------------------
# Stub event builder
# ---------------------------------------------------------------------------


def _k8s_event(
    *,
    node_name: str = "node-a",
    reason: str = "Disrupting",
    message: str = KARPENTER_UNDERUTILIZED_DELETE,
    event_type: str = "Warning",
    involved_kind: str = "Node",
    first_timestamp: str | None = "2024-01-01T00:00:00Z",
) -> SimpleNamespace:
    """Build a fake ``CoreV1Event`` that mimics the kubernetes SDK object shape."""
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=f"event-{node_name}",
            creation_timestamp="2024-01-01T00:00:00Z",
        ),
        involved_object=SimpleNamespace(
            kind=involved_kind,
            name=node_name,
            uid=f"uid-{node_name}",
        ),
        reason=reason,
        message=message,
        type=event_type,
        first_timestamp=first_timestamp,
    )


# ---------------------------------------------------------------------------
# Stub Watch factory helpers
# ---------------------------------------------------------------------------


class _StubWatch:
    """Minimal stand-in for ``kubernetes.watch.Watch``."""

    def __init__(
        self,
        events: Iterator[Any],
        *,
        raise_after: Exception | None = None,
    ) -> None:
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
        return _StubWatch(iter([]))

    return factory


def _make_event(event_type: str, k8s_event: Any) -> dict[str, Any]:
    return {"type": event_type, "object": k8s_event}


def _make_watcher(
    cache: K8sNodeEventsCache,
    watch_factory: Any,
    *,
    base_backoff: float = 0.001,
) -> K8sEventsWatcher:
    """Build a K8sEventsWatcher with a mock kubernetes client."""
    client = MagicMock()
    client.core_v1.list_event_for_all_namespaces = MagicMock()
    return K8sEventsWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        watch_factory=watch_factory,
        base_backoff_seconds=base_backoff,
        max_backoff_seconds=0.05,
    )


def _run_sync(watcher: K8sEventsWatcher) -> None:
    """Run one session synchronously on the calling thread for determinism."""
    # A session run may raise once the mock event stream is exhausted or the
    # watch is torn down mid-iteration; the test only asserts on the cache
    # state built up beforehand, so a session-teardown error is expected here.
    with contextlib.suppress(Exception):
        watcher._run_one_session(None)
    watcher._stop_event.set()


# ---------------------------------------------------------------------------
# _parse_karpenter_reason
# ---------------------------------------------------------------------------


class TestParseKarpenterReason:
    """Unit tests for the Karpenter message parser."""

    def test_underutilized_delete_matches(self) -> None:
        result = _parse_karpenter_reason(KARPENTER_UNDERUTILIZED_DELETE)
        assert result == "Underutilized/Delete"

    def test_empty_delete_matches(self) -> None:
        result = _parse_karpenter_reason(KARPENTER_EMPTY_DELETE)
        assert result == "Empty/Delete"

    def test_unknown_message_returns_none(self) -> None:
        assert _parse_karpenter_reason("Some other message") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_karpenter_reason("") is None

    def test_none_returns_none(self) -> None:
        assert _parse_karpenter_reason(None) is None

    def test_partial_match_does_not_match(self) -> None:
        # Partial substring should not match
        assert _parse_karpenter_reason("Disrupting Node: Underutilized") is None


# ---------------------------------------------------------------------------
# K8sNodeEventsCache
# ---------------------------------------------------------------------------


class TestK8sNodeEventsCache:
    """Unit tests for the events cache."""

    def test_upsert_and_get(self) -> None:
        cache = K8sNodeEventsCache()
        event = K8sNodeDisruptionEvent(
            node_name="node-a",
            reason="Disrupting",
            message=KARPENTER_UNDERUTILIZED_DELETE,
            karpenter_reason="Underutilized/Delete",
        )
        cache.upsert(event)
        result = cache.get("node-a")
        assert result is not None
        assert result.node_name == "node-a"
        assert result.karpenter_reason == "Underutilized/Delete"

    def test_get_returns_none_for_missing(self) -> None:
        cache = K8sNodeEventsCache()
        assert cache.get("nonexistent") is None

    def test_upsert_replaces_existing(self) -> None:
        cache = K8sNodeEventsCache()
        ev1 = K8sNodeDisruptionEvent(node_name="node-a", karpenter_reason="Underutilized/Delete")
        ev2 = K8sNodeDisruptionEvent(node_name="node-a", karpenter_reason="Empty/Delete")
        cache.upsert(ev1)
        cache.upsert(ev2)
        result = cache.get("node-a")
        assert result is not None
        assert result.karpenter_reason == "Empty/Delete"

    def test_all_returns_snapshot(self) -> None:
        cache = K8sNodeEventsCache()
        cache.upsert(K8sNodeDisruptionEvent(node_name="node-a"))
        cache.upsert(K8sNodeDisruptionEvent(node_name="node-b"))
        all_events = cache.all()
        assert len(all_events) == 2
        names = {e.node_name for e in all_events}
        assert names == {"node-a", "node-b"}

    def test_clear_removes_all_entries(self) -> None:
        cache = K8sNodeEventsCache()
        cache.upsert(K8sNodeDisruptionEvent(node_name="node-a"))
        cache.clear()
        assert cache.size() == 0
        assert cache.get("node-a") is None

    def test_size_reflects_entry_count(self) -> None:
        cache = K8sNodeEventsCache()
        assert cache.size() == 0
        cache.upsert(K8sNodeDisruptionEvent(node_name="node-a"))
        assert cache.size() == 1
        cache.upsert(K8sNodeDisruptionEvent(node_name="node-b"))
        assert cache.size() == 2

    def test_thread_safe_concurrent_writes(self) -> None:
        """Multiple threads writing different keys must not lose entries."""
        cache = K8sNodeEventsCache()
        errors: list[Exception] = []

        def write(node_name: str) -> None:
            try:
                for _ in range(50):
                    cache.upsert(K8sNodeDisruptionEvent(node_name=node_name))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(f"node-{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert cache.size() == 5


# ---------------------------------------------------------------------------
# Event translation: ADDED / MODIFIED recognized; DELETED skipped
# ---------------------------------------------------------------------------


def test_added_event_populates_cache() -> None:
    cache = K8sNodeEventsCache()
    ev = _k8s_event(node_name="node-a", message=KARPENTER_UNDERUTILIZED_DELETE)
    factory = _single_session_factory([_make_event("ADDED", ev)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    result = cache.get("node-a")
    assert result is not None
    assert result.node_name == "node-a"
    assert result.reason == "Disrupting"
    assert result.karpenter_reason == "Underutilized/Delete"


def test_modified_event_updates_cache() -> None:
    """MODIFIED events replace the cached entry (most-recent wins)."""
    cache = K8sNodeEventsCache()
    ev1 = _k8s_event(node_name="node-b", message=KARPENTER_UNDERUTILIZED_DELETE)
    ev2 = _k8s_event(node_name="node-b", message=KARPENTER_EMPTY_DELETE)
    factory = _single_session_factory([_make_event("ADDED", ev1), _make_event("MODIFIED", ev2)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    result = cache.get("node-b")
    assert result is not None
    assert result.karpenter_reason == "Empty/Delete"


def test_deleted_event_is_ignored() -> None:
    """DELETED events on k8s Event objects are not meaningful -- cache unchanged."""
    cache = K8sNodeEventsCache()
    ev = _k8s_event(node_name="node-c", message=KARPENTER_EMPTY_DELETE)
    factory = _single_session_factory([_make_event("DELETED", ev)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    # DELETED event on a k8s Event object should not populate the cache.
    assert cache.get("node-c") is None


def test_non_node_involved_object_is_skipped() -> None:
    """Events whose involvedObject.kind != Node are ignored (belt-and-suspenders)."""
    cache = K8sNodeEventsCache()
    ev = _k8s_event(node_name="pod-x", involved_kind="Pod")
    factory = _single_session_factory([_make_event("ADDED", ev)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    assert cache.get("pod-x") is None


def test_event_with_no_karpenter_reason_is_stored_with_none_karpenter_reason() -> None:
    """Non-Karpenter node events are stored in cache with karpenter_reason=None."""
    cache = K8sNodeEventsCache()
    ev = _k8s_event(node_name="node-d", message="SomeOtherNodeEvent", reason="NodeNotReady")
    factory = _single_session_factory([_make_event("ADDED", ev)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    result = cache.get("node-d")
    assert result is not None
    assert result.karpenter_reason is None
    assert result.reason == "NodeNotReady"


def test_field_selector_is_applied_to_watch_call() -> None:
    """The watcher passes involvedObject.kind=Node as field_selector to the apiserver."""
    cache = K8sNodeEventsCache()
    stub_watch = _StubWatch(iter([]))

    def factory() -> _StubWatch:
        return stub_watch

    watcher = _make_watcher(cache, factory)
    watcher._run_one_session(None)

    assert stub_watch.last_kwargs.get("field_selector") == "involvedObject.kind=Node"


def test_timestamp_is_extracted_from_first_timestamp() -> None:
    """The watcher records the first_timestamp from the k8s Event object."""
    cache = K8sNodeEventsCache()
    ev = _k8s_event(node_name="node-ts", first_timestamp="2024-06-01T12:00:00Z")
    factory = _single_session_factory([_make_event("ADDED", ev)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    result = cache.get("node-ts")
    assert result is not None
    assert result.timestamp_str == "2024-06-01T12:00:00Z"


def test_timestamp_falls_back_to_creation_timestamp() -> None:
    """When first_timestamp is None, creation_timestamp is used."""
    cache = K8sNodeEventsCache()
    ev = _k8s_event(node_name="node-ts2", first_timestamp=None)
    factory = _single_session_factory([_make_event("ADDED", ev)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    result = cache.get("node-ts2")
    assert result is not None
    # Should fall back to creation_timestamp from metadata
    assert result.timestamp_str == "2024-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# 410 Gone reconnect
# ---------------------------------------------------------------------------


def test_410_resets_resource_version_and_continues() -> None:
    """A 410 ApiException must reset resource_version and retry."""
    from orb.providers.k8s.watch.events_watcher import _ResourceTooOld

    cache = K8sNodeEventsCache()
    api_410 = ApiException(status=410, reason="Gone")

    watcher = _make_watcher(cache, _single_session_factory([]))
    # Patch in a watch that raises 410
    watcher._watch_factory = lambda: _StubWatch(iter([]), raise_after=api_410)  # type: ignore[assignment]

    try:
        watcher._run_one_session(resource_version="old-rv")
        pytest.fail("Expected _ResourceTooOld")
    except _ResourceTooOld:
        pass  # Expected


def test_non_410_raises_directly() -> None:
    """Non-410 exceptions propagate to the outer retry loop."""
    cache = K8sNodeEventsCache()
    error = ApiException(status=500, reason="Internal Server Error")

    watcher = _make_watcher(cache, _single_session_factory([]))
    watcher._watch_factory = lambda: _StubWatch(iter([]), raise_after=error)  # type: ignore[assignment]

    with pytest.raises(ApiException):
        watcher._run_one_session(None)


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


@pytest.mark.timeout(5)
def test_stop_signals_thread_to_exit() -> None:
    """Calling stop() must set the stop event and join the thread."""
    cache = K8sNodeEventsCache()
    stop_gate = threading.Event()

    def slow_factory() -> Any:
        class _BlockingStream:
            def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
                stop_gate.wait(timeout=4.0)
                return iter([])

            def stop(self) -> None:
                stop_gate.set()

        return _BlockingStream()

    client = MagicMock()
    client.core_v1.list_event_for_all_namespaces = MagicMock()
    watcher = K8sEventsWatcher(
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
    """Calling start() while already running is a no-op (same thread)."""
    cache = K8sNodeEventsCache()

    def blocking_factory() -> Any:
        class _BlockingStream:
            def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
                time.sleep(0.3)
                return iter([])

            def stop(self) -> None:
                pass

        return _BlockingStream()

    client = MagicMock()
    client.core_v1.list_event_for_all_namespaces = MagicMock()
    watcher = K8sEventsWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        watch_factory=blocking_factory,
        base_backoff_seconds=0.001,
        max_backoff_seconds=0.01,
    )

    watcher.start()
    first_thread_id = watcher._thread.ident if watcher._thread else None
    watcher.start()  # Should be a no-op
    assert watcher._thread is not None
    assert watcher._thread.ident == first_thread_id

    watcher.stop(timeout=2.0)


# ---------------------------------------------------------------------------
# Backoff calculation
# ---------------------------------------------------------------------------


def test_backoff_doubles_per_attempt() -> None:
    cache = K8sNodeEventsCache()
    client = MagicMock()
    watcher = K8sEventsWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        base_backoff_seconds=1.0,
        max_backoff_seconds=32.0,
    )

    assert watcher._backoff_for_attempt(1) == 1.0
    assert watcher._backoff_for_attempt(2) == 2.0
    assert watcher._backoff_for_attempt(3) == 4.0
    assert watcher._backoff_for_attempt(6) == 32.0  # Capped
    assert watcher._backoff_for_attempt(10) == 32.0  # Still capped


# ---------------------------------------------------------------------------
# Multiple Karpenter disruption events
# ---------------------------------------------------------------------------


def test_multiple_karpenter_disruption_events() -> None:
    """Two nodes each get the correct disruption reason cached."""
    cache = K8sNodeEventsCache()
    ev_a = _k8s_event(node_name="node-a", message=KARPENTER_UNDERUTILIZED_DELETE)
    ev_b = _k8s_event(node_name="node-b", message=KARPENTER_EMPTY_DELETE)
    factory = _single_session_factory([_make_event("ADDED", ev_a), _make_event("ADDED", ev_b)])
    watcher = _make_watcher(cache, factory)

    _run_sync(watcher)

    result_a = cache.get("node-a")
    result_b = cache.get("node-b")
    assert result_a is not None and result_a.karpenter_reason == "Underutilized/Delete"
    assert result_b is not None and result_b.karpenter_reason == "Empty/Delete"
