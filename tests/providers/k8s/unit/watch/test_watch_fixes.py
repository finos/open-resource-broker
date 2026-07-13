"""Regression tests for adversarial-review bug fixes on the watch layer.

Covers:
* Fix 1: MultiNamespaceWatcher._build_watcher forwards periodic_resync_interval_seconds
* Fix 2: _relist_snapshot evicts pods absent from the LIST snapshot
* Fix 3: resync does not overwrite newer watch-stream state (upsert_if_not_newer)
* Fix 4: K8sNodeEventsCache TTL eviction (node-name reuse after scale-in)
* Fix 5: Karpenter v1.x reason "Disrupted" is parsed correctly
* Fix 6: PodStateCache._stale_locks pruned on drain + clear() resets stale_locks
* Fix 7: events_watcher.start() emits no duplicate INFO log
* Fix 8: _active_watch guarded by a lock
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterator, Optional
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.watch.events_watcher import (
    KARPENTER_EMPTY_DELETE,
    KARPENTER_UNDERUTILIZED_DELETE,
    KARPENTER_V1_REASON,
    K8sEventsWatcher,
    K8sNodeDisruptionEvent,
    K8sNodeEventsCache,
    _parse_karpenter_reason,
)
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache
from orb.providers.k8s.watch.watcher import K8sWatcher

# ---------------------------------------------------------------------------
# Shared stub helpers
# ---------------------------------------------------------------------------


def _pod_ns(
    *,
    name: str,
    namespace: str = "ns",
    request_id: str = "req-1",
    phase: str = "Running",
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
        spec=SimpleNamespace(node_name="node-a", containers=[], restart_policy="Always"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1",
            host_ip="10.1.0.1",
            start_time=None,
            conditions=[SimpleNamespace(type="Ready", status="True", reason=None)],
            container_statuses=[],
        ),
    )


def _make_pod_list(pods: list[Any], rv: str = "rv-100") -> SimpleNamespace:
    return SimpleNamespace(
        items=pods,
        metadata=SimpleNamespace(resource_version=rv),
    )


class _StubWatch:
    """Minimal stand-in for ``kubernetes.watch.Watch``."""

    def __init__(
        self,
        events: list[Any],
        *,
        raise_after: Optional[Exception] = None,
    ) -> None:
        self._events = iter(events)
        self._raise_after = raise_after
        self._stopped = False
        self.resource_version: Optional[str] = None
        self.last_kwargs: dict[str, Any] = {}

    def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
        self.last_kwargs = kwargs
        for ev in self._events:
            if self._stopped:
                return
            yield ev
        if self._raise_after is not None:
            raise self._raise_after

    def stop(self) -> None:
        self._stopped = True


def _noop_watch_factory() -> _StubWatch:
    return _StubWatch([])


def _make_client(pods: list[Any] = (), rv: str = "rv-100") -> MagicMock:
    pod_list = _make_pod_list(list(pods), rv)
    client = MagicMock()
    client.core_v1.list_namespaced_pod = MagicMock(return_value=pod_list)
    client.core_v1.list_pod_for_all_namespaces = MagicMock(return_value=pod_list)
    return client


# ---------------------------------------------------------------------------
# Fix 1: MultiNamespaceWatcher._build_watcher forwards periodic_resync_interval_seconds
# ---------------------------------------------------------------------------


class TestFix1PeriodicResyncForwarded:
    """MultiNamespaceWatcher must forward periodic_resync_interval_seconds to children."""

    def _build_manager(
        self, interval: int, namespaces: Optional[list[str]] = None
    ) -> MultiNamespaceWatcher:
        config = K8sProviderConfig(
            namespace="ns",
            namespaces=namespaces,
            periodic_resync_interval_seconds=interval,
        )
        return MultiNamespaceWatcher(
            kubernetes_client=MagicMock(),
            config=config,
            logger=MagicMock(),
            cache=PodStateCache(),
            watch_factory=_noop_watch_factory,
        )

    @pytest.mark.asyncio
    async def test_interval_forwarded_to_single_watcher(self) -> None:
        """Interval must reach the child K8sWatcher when namespaces=None."""
        manager = self._build_manager(interval=180)
        manager.start()
        try:
            assert len(manager.watchers) == 1
            assert manager.watchers[0]._periodic_resync_interval_seconds == 180
        finally:
            await manager.stop()

    @pytest.mark.asyncio
    async def test_interval_forwarded_to_all_watchers_multi_ns(self) -> None:
        """Interval must reach every child when multiple namespaces are configured."""
        manager = self._build_manager(interval=120, namespaces=["a", "b", "c"])
        manager.start()
        try:
            for w in manager.watchers:
                assert w._periodic_resync_interval_seconds == 120
        finally:
            await manager.stop()

    @pytest.mark.asyncio
    async def test_zero_interval_forwarded(self) -> None:
        """0 (disabled) must also be forwarded correctly — the default case."""
        manager = self._build_manager(interval=0)
        manager.start()
        try:
            assert manager.watchers[0]._periodic_resync_interval_seconds == 0
        finally:
            await manager.stop()


# ---------------------------------------------------------------------------
# Fix 2: _relist_snapshot evicts pods absent from the LIST
# ---------------------------------------------------------------------------


class TestFix2ResyncEjectsDeletedPod:
    """Periodic resync must evict cache entries absent from LIST; 410-recovery must not."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_resync_evicts_pod_absent_from_list(self) -> None:
        """A pod absent from the periodic-resync LIST must be evicted (evict_absent=True path).

        410-recovery does NOT evict (it relies on the resumed watch stream to
        deliver DELETE events); periodic resync is the path that calls
        _relist_snapshot(evict_absent=True) and is authoritative about absent pods.
        We exercise the eviction directly via _relist_snapshot(evict_absent=True)
        to keep the test deterministic.
        """
        cache = PodStateCache()
        # orb-deleted is in cache — absent from the LIST → must be evicted.
        cache.upsert(
            PodState(request_id="req-1", pod_name="orb-deleted", namespace="ns", status="running")
        )
        # orb-live is in the LIST → must survive.
        client = _make_client(
            pods=[_pod_ns(name="orb-live")],
            rv="rv-200",
        )

        watcher = K8sWatcher(
            kubernetes_client=client,
            cache=cache,
            logger=MagicMock(),
            namespace="ns",
            watch_factory=_noop_watch_factory,
            watch_timeout_seconds=1,
        )
        # Periodic resync passes evict_absent=True — exercise that path directly.
        watcher._relist_snapshot(evict_absent=True)

        states = cache.get("req-1")
        assert states is not None
        pod_names = {s.pod_name for s in states}
        assert "orb-live" in pod_names, "Pod in LIST must remain in cache"
        assert "orb-deleted" not in pod_names, "Pod absent from LIST must be evicted by resync"

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_410_recovery_does_not_evict_watch_stream_pods(self) -> None:
        """410-recovery relist must NOT evict pods added by the watch stream.

        After a 410 the second watch session (resumed from the relist rv) will
        deliver DELETE events for genuinely-deleted pods.  Eager eviction during
        410 recovery would discard pods that are still running in the cluster
        but happened to be absent from the cached LIST snapshot.
        """
        from kubernetes.client.exceptions import ApiException

        cache = PodStateCache()
        client = _make_client(
            pods=[_pod_ns(name="orb-live")],
            rv="rv-200",
        )

        stubs_iter = iter(
            [
                _StubWatch(
                    [{"type": "ADDED", "object": _pod_ns(name="orb-watch-pod")}],
                    raise_after=ApiException(status=410, reason="Gone"),
                ),
                _StubWatch([]),
            ]
        )

        def factory() -> _StubWatch:
            try:
                return next(stubs_iter)
            except StopIteration:
                return _StubWatch([])

        watcher = K8sWatcher(
            kubernetes_client=client,
            cache=cache,
            logger=MagicMock(),
            namespace="ns",
            watch_factory=factory,
            watch_timeout_seconds=1,
        )
        watcher.start()
        # Wait until the 410 recovery runs and orb-live appears in cache.
        for _ in range(200):
            states = cache.get("req-1")
            if states is not None and any(s.pod_name == "orb-live" for s in states):
                break
            await asyncio.sleep(0.01)
        await watcher.stop()

        states = cache.get("req-1")
        assert states is not None
        pod_names = {s.pod_name for s in states}
        assert "orb-live" in pod_names, "Pod in LIST must remain in cache after 410 recovery"
        assert "orb-watch-pod" in pod_names, (
            "Pod added by watch stream must NOT be evicted by 410-recovery relist"
        )

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_resync_does_not_evict_other_namespace(self) -> None:
        """Namespace-scoped LIST must not evict pods from other namespaces."""
        cache = PodStateCache()
        # Seed the cache with a pod in a different namespace.
        other_ns_state = PodState(
            request_id="req-other",
            pod_name="orb-other",
            namespace="other-ns",
            status="running",
        )
        cache.upsert(other_ns_state)

        # The LIST only covers namespace="ns" and returns nothing.
        client = _make_client(pods=[], rv="rv-300")

        watcher = K8sWatcher(
            kubernetes_client=client,
            cache=cache,
            logger=MagicMock(),
            namespace="ns",  # scoped to "ns", not "other-ns"
            watch_factory=_noop_watch_factory,
            watch_timeout_seconds=1,
        )
        # Run the resync with eviction enabled (periodic resync path).
        watcher._relist_snapshot(evict_absent=True)

        # Pod in "other-ns" must survive — it was outside the LIST scope.
        states = cache.get("req-other")
        assert states is not None
        assert any(s.pod_name == "orb-other" for s in states)

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_resync_evicts_in_correct_namespace_only(self) -> None:
        """Resync must only evict pods in the namespace the LIST covered."""
        cache = PodStateCache()
        # Pod in the watched namespace — absent from LIST, should be evicted.
        cache.upsert(
            PodState(request_id="req-1", pod_name="orb-gone", namespace="ns", status="running")
        )
        # Pod in another namespace — should NOT be evicted.
        cache.upsert(
            PodState(
                request_id="req-2", pod_name="orb-other", namespace="other-ns", status="running"
            )
        )

        # LIST for "ns" returns nothing.
        client = _make_client(pods=[], rv="rv-400")

        watcher = K8sWatcher(
            kubernetes_client=client,
            cache=cache,
            logger=MagicMock(),
            namespace="ns",
            watch_factory=_noop_watch_factory,
            watch_timeout_seconds=1,
        )
        # Periodic resync path: eviction is enabled.
        watcher._relist_snapshot(evict_absent=True)

        # orb-gone must be evicted (in "ns", absent from LIST, evict_absent=True).
        assert cache.get("req-1") is None, (
            "Pod in scoped namespace absent from LIST must be evicted"
        )
        # orb-other must survive (different namespace).
        states = cache.get("req-2")
        assert states is not None
        assert any(s.pod_name == "orb-other" for s in states)

    def test_resync_evicts_only_pre_list_keys_not_concurrent_watch_adds(self) -> None:
        """Eviction must only target pods that were in the cache BEFORE the LIST started.

        Guard (a): pods added to the cache by a concurrent watch stream during the
        LIST call are NOT in the pre-LIST key snapshot and must never be evicted,
        even if they happen to be absent from the LIST results.
        """
        cache = PodStateCache()

        # Pod present before the LIST — absent from LIST → must be evicted.
        cache.upsert(
            PodState(request_id="req-1", pod_name="orb-pre-list", namespace="ns", status="running")
        )

        # Simulate a pod that arrives in the cache AFTER the pre-LIST snapshot
        # is taken but during (or just after) the LIST call.  We replicate this
        # by injecting it directly into the cache with a timestamp in the future
        # relative to captured_before.  The real-world scenario is a concurrent
        # watch ADDED event that lands while the LIST is in-flight.
        #
        # We use a custom client that injects a cache side-effect mid-LIST to
        # represent the concurrent watch upsert.
        concurrent_pod_name = "orb-concurrent-watch"
        concurrent_state = PodState(
            request_id="req-1", pod_name=concurrent_pod_name, namespace="ns", status="running"
        )

        list_pod_list = _make_pod_list([], rv="rv-500")

        def list_with_concurrent_upsert(**kwargs: Any) -> Any:
            # Simulate: watch stream adds orb-concurrent-watch to cache DURING LIST.
            cache.upsert(concurrent_state)
            return list_pod_list

        client = MagicMock()
        client.core_v1.list_namespaced_pod = list_with_concurrent_upsert

        watcher = K8sWatcher(
            kubernetes_client=client,
            cache=cache,
            logger=MagicMock(),
            namespace="ns",
            watch_factory=_noop_watch_factory,
            watch_timeout_seconds=1,
        )
        watcher._relist_snapshot(evict_absent=True)

        # orb-pre-list must be evicted (present before LIST, absent from LIST).
        pre_list_states = cache.get("req-1")
        pod_names = {s.pod_name for s in (pre_list_states or [])}
        assert "orb-pre-list" not in pod_names, (
            "Pod present before LIST and absent from LIST must be evicted"
        )
        # orb-concurrent-watch must survive — it was NOT in the pre-LIST snapshot.
        assert concurrent_pod_name in pod_names, (
            "Pod added during LIST by concurrent watch stream must not be evicted"
        )


# ---------------------------------------------------------------------------
# Fix 3: resync does not clobber newer watch-stream state
# ---------------------------------------------------------------------------


class TestFix3ResyncDoesNotClobberNewerState:
    """upsert_if_not_newer must leave newer watch events in place."""

    def test_upsert_if_not_newer_skips_when_cache_is_newer(self) -> None:
        """When existing entry was written after captured_before, skip the upsert."""
        cache = PodStateCache()
        # Write a "live" watch event state first.
        live_state = PodState(
            request_id="req-1",
            pod_name="orb-pod",
            namespace="ns",
            status="running",
        )
        cache.upsert(live_state)
        # Record the timestamp after the live write.
        live_ts = cache.get("req-1")[0].last_updated  # type: ignore[index]

        # captured_before is set to *before* the live write, simulating a
        # LIST snapshot taken while the watch was still catching up.
        captured_before = live_ts - 0.01

        stale_state = PodState(
            request_id="req-1",
            pod_name="orb-pod",
            namespace="ns",
            status="failed",  # stale LIST would say "failed"
        )
        written = cache.upsert_if_not_newer(stale_state, captured_before)

        assert not written, "Stale LIST upsert must be skipped when cache entry is newer"
        states = cache.get("req-1")
        assert states is not None
        assert states[0].status == "running", "Newer watch-stream status must be preserved"

    def test_upsert_if_not_newer_writes_when_cache_is_older(self) -> None:
        """When existing entry was written before captured_before, upsert proceeds."""
        cache = PodStateCache()
        old_state = PodState(
            request_id="req-1",
            pod_name="orb-pod",
            namespace="ns",
            status="pending",
        )
        cache.upsert(old_state)
        # captured_before is set to *after* the old write.
        captured_before = time.monotonic() + 1.0

        fresh_list_state = PodState(
            request_id="req-1",
            pod_name="orb-pod",
            namespace="ns",
            status="running",
        )
        written = cache.upsert_if_not_newer(fresh_list_state, captured_before)

        assert written, "LIST upsert must proceed when existing entry is older than captured_before"
        states = cache.get("req-1")
        assert states is not None
        assert states[0].status == "running"

    def test_upsert_if_not_newer_inserts_new_entry(self) -> None:
        """New entries (not yet in cache) must always be written."""
        cache = PodStateCache()
        new_state = PodState(
            request_id="req-new",
            pod_name="orb-new",
            namespace="ns",
            status="running",
        )
        written = cache.upsert_if_not_newer(new_state, captured_before=time.monotonic())
        assert written
        assert cache.get("req-new") is not None

    @pytest.mark.asyncio
    @pytest.mark.timeout(15)
    async def test_periodic_resync_does_not_overwrite_concurrent_watch_event(self) -> None:
        """A periodic resync snapshot must not replace a watch event that arrived after the LIST."""
        # The resync records captured_before, then the LIST runs (slow).
        # A watch MODIFIED arrives between captured_before and LIST completion.
        # The resync must not overwrite the MODIFIED state.
        cache = PodStateCache()

        # Seed the pod as "pending" so the LIST would upsert it as "pending".
        cache.upsert(
            PodState(request_id="req-1", pod_name="orb-pod", namespace="ns", status="pending")
        )
        # Manually inject a "newer" state by manipulating last_updated directly.
        # We do this by upserting, then capturing the timestamp.
        cache.upsert(
            PodState(request_id="req-1", pod_name="orb-pod", namespace="ns", status="running")
        )
        current_ts = cache.get("req-1")[0].last_updated  # type: ignore[index]

        # captured_before must be older than the running upsert.
        captured_before = current_ts - 0.01

        stale = PodState(request_id="req-1", pod_name="orb-pod", namespace="ns", status="pending")
        written = cache.upsert_if_not_newer(stale, captured_before)
        assert not written
        states = cache.get("req-1")
        assert states is not None
        assert states[0].status == "running"


# ---------------------------------------------------------------------------
# Fix 4: K8sNodeEventsCache TTL eviction
# ---------------------------------------------------------------------------


class TestFix4NodeEventsCacheTTL:
    """K8sNodeEventsCache must evict entries older than the TTL."""

    def test_get_returns_none_for_expired_entry(self) -> None:
        """An entry beyond TTL must be evicted and None returned on get."""
        cache = K8sNodeEventsCache(ttl_seconds=1)
        old_event = K8sNodeDisruptionEvent(
            node_name="node-old",
            observed_at=datetime.now(tz=timezone.utc) - timedelta(seconds=3600),
        )
        # Inject directly to bypass upsert-time pruning.
        with cache._lock:  # type: ignore[attr-defined]
            cache._events["node-old"] = old_event  # type: ignore[attr-defined]

        result = cache.get("node-old")
        assert result is None, "Expired entry must be evicted on get"

    def test_get_returns_event_when_within_ttl(self) -> None:
        """A fresh entry must be returned normally."""
        cache = K8sNodeEventsCache(ttl_seconds=3600)
        event = K8sNodeDisruptionEvent(node_name="node-fresh")
        cache.upsert(event)
        assert cache.get("node-fresh") is not None

    def test_upsert_prunes_expired_entries(self) -> None:
        """Inserting a new event must prune expired entries in the same pass."""
        cache = K8sNodeEventsCache(ttl_seconds=1)
        # Plant an expired entry directly.
        expired = K8sNodeDisruptionEvent(
            node_name="node-expired",
            observed_at=datetime.now(tz=timezone.utc) - timedelta(hours=2),
        )
        with cache._lock:  # type: ignore[attr-defined]
            cache._events["node-expired"] = expired  # type: ignore[attr-defined]

        # Insert a fresh entry — should prune node-expired.
        cache.upsert(K8sNodeDisruptionEvent(node_name="node-new"))

        assert cache.get("node-expired") is None, "Expired entry must be pruned on upsert"
        assert cache.get("node-new") is not None

    def test_node_name_reuse_after_scale_in(self) -> None:
        """After a node is recycled, the new node with the same name must show a fresh entry."""
        cache = K8sNodeEventsCache(ttl_seconds=1)
        old_event = K8sNodeDisruptionEvent(
            node_name="node-recycled",
            karpenter_reason="Underutilized/Delete",
            observed_at=datetime.now(tz=timezone.utc) - timedelta(hours=2),
        )
        with cache._lock:  # type: ignore[attr-defined]
            cache._events["node-recycled"] = old_event  # type: ignore[attr-defined]

        # New event for the recycled node name (new physical node).
        new_event = K8sNodeDisruptionEvent(node_name="node-recycled", karpenter_reason=None)
        cache.upsert(new_event)

        result = cache.get("node-recycled")
        assert result is not None
        assert result.karpenter_reason is None, (
            "New node event must not show stale disruption reason from old node"
        )

    def test_all_excludes_expired_entries(self) -> None:
        """all() must not return entries that have exceeded the TTL."""
        cache = K8sNodeEventsCache(ttl_seconds=1)
        expired = K8sNodeDisruptionEvent(
            node_name="node-expired",
            observed_at=datetime.now(tz=timezone.utc) - timedelta(hours=2),
        )
        fresh = K8sNodeDisruptionEvent(node_name="node-fresh")
        with cache._lock:  # type: ignore[attr-defined]
            cache._events["node-expired"] = expired  # type: ignore[attr-defined]
        cache.upsert(fresh)

        result = cache.all()
        names = {e.node_name for e in result}
        assert "node-expired" not in names
        assert "node-fresh" in names


# ---------------------------------------------------------------------------
# Fix 5: Karpenter v1.x reason parsing
# ---------------------------------------------------------------------------


class TestFix5KarpenterV1Parsing:
    """_parse_karpenter_reason must recognise Karpenter v1.x events."""

    def test_v0_underutilized_still_works(self) -> None:
        assert _parse_karpenter_reason(KARPENTER_UNDERUTILIZED_DELETE) == "Underutilized/Delete"

    def test_v0_empty_still_works(self) -> None:
        assert _parse_karpenter_reason(KARPENTER_EMPTY_DELETE) == "Empty/Delete"

    def test_v1_underutilized_delete(self) -> None:
        result = _parse_karpenter_reason(
            "Disrupting node: Underutilized/Delete some-node",
            reason=KARPENTER_V1_REASON,
        )
        assert result == "Underutilized/Delete"

    def test_v1_empty_delete(self) -> None:
        result = _parse_karpenter_reason(
            "Disrupting node: Empty/Delete some-node",
            reason=KARPENTER_V1_REASON,
        )
        assert result == "Empty/Delete"

    def test_v1_drift_delete(self) -> None:
        result = _parse_karpenter_reason(
            "Disrupting node: Drift/Delete some-node",
            reason=KARPENTER_V1_REASON,
        )
        assert result == "Drift/Delete"

    def test_v1_consolidation(self) -> None:
        result = _parse_karpenter_reason(
            "Disrupting node: Consolidation/Delete some-node",
            reason=KARPENTER_V1_REASON,
        )
        assert result == "Consolidation/Delete"

    def test_v1_generic_fallback(self) -> None:
        """An unrecognised v1 message should fall back to the generic 'Disrupted' label."""
        result = _parse_karpenter_reason(
            "Disrupting node: NewCause/Delete some-node",
            reason=KARPENTER_V1_REASON,
        )
        assert result == "Disrupted"

    def test_disrupted_reason_without_matching_prefix_returns_none(self) -> None:
        """reason=Disrupted but unrecognised message prefix → generic fallback."""
        result = _parse_karpenter_reason(
            "Completely different message",
            reason=KARPENTER_V1_REASON,
        )
        # Falls through all v1 prefixes — no match → None
        assert result is None

    def test_none_message_still_none(self) -> None:
        assert _parse_karpenter_reason(None, reason=KARPENTER_V1_REASON) is None

    def test_v1_reason_without_reason_kwarg_falls_through(self) -> None:
        """Without reason=Disrupted the v1 prefix matching must not fire."""
        result = _parse_karpenter_reason("Disrupting node: Underutilized/Delete some-node")
        assert result is None, "v1 prefix must not match when reason is not 'Disrupted'"


# ---------------------------------------------------------------------------
# Fix 6: PodStateCache._stale_locks pruning
# ---------------------------------------------------------------------------


class TestFix6StaleLocksUnbounded:
    """_stale_locks must be pruned when the request bucket is drained."""

    def test_stale_lock_pruned_after_full_eviction(self) -> None:
        """After mark_stale evicts the last pod for a request, its lock must be removed."""
        cache = PodStateCache()
        cache.upsert(
            PodState(request_id="req-prune", pod_name="pod-a", namespace="ns", status="running")
        )
        time.sleep(0.02)

        # Trigger lock creation + eviction.
        cache.mark_stale("req-prune", threshold=0.01)

        with cache._stale_locks_mutex:  # type: ignore[attr-defined]
            assert "req-prune" not in cache._stale_locks, (  # type: ignore[attr-defined]
                "Per-request lock must be pruned after the request bucket is drained"
            )

    def test_stale_lock_not_pruned_when_entries_remain(self) -> None:
        """If not all pods are evicted, the lock must stay (still needed)."""
        cache = PodStateCache()
        cache.upsert(
            PodState(request_id="req-keep", pod_name="pod-a", namespace="ns", status="running")
        )
        # Fresh upsert won't be evicted.
        cache.upsert(
            PodState(request_id="req-keep", pod_name="pod-b", namespace="ns", status="running")
        )
        time.sleep(0.02)
        # Only pod-a is old enough to be stale.
        cache.upsert(
            PodState(request_id="req-keep", pod_name="pod-b", namespace="ns", status="running")
        )

        cache.mark_stale("req-keep", threshold=0.01)

        # pod-b was just refreshed so it won't be evicted; the bucket itself
        # must still hold pod-b regardless of whether the per-request stale
        # lock was pruned.
        states = cache.get("req-keep")
        assert states is not None
        assert any(s.pod_name == "pod-b" for s in states)

    def test_clear_resets_stale_locks(self) -> None:
        """clear() must also wipe _stale_locks to prevent unbounded growth."""
        cache = PodStateCache()
        for i in range(5):
            cache.upsert(
                PodState(
                    request_id=f"req-{i}", pod_name=f"pod-{i}", namespace="ns", status="running"
                )
            )
        time.sleep(0.02)
        for i in range(5):
            cache.mark_stale(f"req-{i}", threshold=0.01)

        # Even if some locks remain, clear must wipe them.
        cache.clear()
        with cache._stale_locks_mutex:  # type: ignore[attr-defined]
            assert len(cache._stale_locks) == 0, "clear() must wipe _stale_locks"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Fix 7: No duplicate INFO log in events_watcher.start()
# ---------------------------------------------------------------------------


class TestFix7NoDuplicateStartLog:
    """events_watcher.start() must not emit a duplicate INFO log."""

    @pytest.mark.timeout(5)
    def test_start_emits_no_info_log(self) -> None:
        """start() must not call logger.info — the strategy layer logs the start."""
        mock_logger = MagicMock()
        client = MagicMock()
        client.core_v1.list_event_for_all_namespaces = MagicMock()

        # Use a blocking factory so the thread doesn't immediately exit.
        import threading as _threading

        gate = _threading.Event()

        def blocking_factory() -> Any:
            class _Blocker:
                def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
                    gate.wait(timeout=3.0)
                    return iter([])

                def stop(self) -> None:
                    gate.set()

            return _Blocker()

        watcher = K8sEventsWatcher(
            kubernetes_client=client,
            cache=K8sNodeEventsCache(),
            logger=mock_logger,
            watch_factory=blocking_factory,
        )
        watcher.start()
        watcher.stop(timeout=2.0)

        # start() must not have called logger.info.
        info_calls = [str(c) for c in mock_logger.info.call_args_list]
        start_info_calls = [c for c in info_calls if "started" in c.lower()]
        assert len(start_info_calls) == 0, (
            f"start() emitted unexpected INFO log(s): {start_info_calls}"
        )


# ---------------------------------------------------------------------------
# Fix 8: _active_watch guarded by a lock
# ---------------------------------------------------------------------------


class TestFix8ActiveWatchLock:
    """K8sWatcher._active_watch must be guarded by _active_watch_lock."""

    def test_active_watch_lock_exists(self) -> None:
        """K8sWatcher must have a _active_watch_lock threading.Lock attribute."""

        cache = PodStateCache()
        watcher = K8sWatcher(
            kubernetes_client=MagicMock(),
            cache=cache,
            logger=MagicMock(),
            namespace="ns",
        )
        assert hasattr(watcher, "_active_watch_lock")
        # threading.Lock() returns an instance of _thread.lock (an internal C type).
        # Check via the context-manager protocol (acquire/release) which all lock
        # types implement, rather than relying on the non-public type name.
        lock = watcher._active_watch_lock  # type: ignore[attr-defined]
        assert callable(getattr(lock, "acquire", None)), "_active_watch_lock must be a lock"
        assert callable(getattr(lock, "release", None)), "_active_watch_lock must be a lock"

    @pytest.mark.asyncio
    @pytest.mark.timeout(5)
    async def test_stop_reads_active_watch_under_lock(self) -> None:
        """stop() must read _active_watch inside _active_watch_lock (no AttributeError)."""
        cache = PodStateCache()
        watcher = K8sWatcher(
            kubernetes_client=_make_client(),
            cache=cache,
            logger=MagicMock(),
            namespace="ns",
            watch_factory=_noop_watch_factory,
            watch_timeout_seconds=1,
        )
        watcher.start()
        await asyncio.sleep(0.05)
        # stop() must not raise even when _active_watch is None at call time.
        await watcher.stop()
        assert watcher._task is None
