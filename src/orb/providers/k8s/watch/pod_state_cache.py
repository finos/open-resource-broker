"""Thread-safe in-memory cache of pod state, keyed by ``(request_id, pod_name)``.

Populated by :class:`~orb.providers.k8s.watch.watcher.K8sWatcher`
and consumed by
:class:`~orb.providers.k8s.handlers.pod_handler.K8sPodHandler`
when the watcher task is alive.  When the cache returns ``None`` (cold
start, watcher dead, or stale entries) the handler falls back to a
scoped ``list_namespaced_pod`` call.

The cache stores the minimum information the Pod handler needs to compute
fulfilment without re-deriving fields from a ``V1Pod`` on every read:

* ``status``           — ORB status string (``"running"`` / ``"starting"`` /
  ``"pending"`` / ``"failed"``).  Produced from ``status.phase`` plus
  the readiness condition by the watcher's event-translator.
* ``phase``            — raw kubernetes ``status.phase`` for debugging.
* ``ready``            — whether the Ready condition is True.
* ``pod_ip`` / ``host_ip`` / ``node_name`` — convenient flattened
  attributes used by the per-instance status dict.
* ``status_reason``    — terminated/waiting container reason or
  ``PodScheduled=False`` reason.
* ``namespace``        — namespace the pod belongs to (so multi-namespace
  callers can recover it without re-listing).
* ``labels``           — frozen snapshot of the labels.
* ``deleted``          — ``True`` when the pod was observed via a
  ``DELETED`` watch event; the entry is retained briefly so concurrent
  status reads see the terminal state, then removed by
  :meth:`PodStateCache.delete`.
* ``last_updated``     — monotonic timestamp (seconds) of the last
  upsert; consumed by :meth:`PodStateCache.mark_stale`.
* ``disrupted_reason`` / ``disrupted_message`` — set when the pod
  carries a ``DisruptionTarget=True`` condition (Karpenter preemption).
  ``None`` when the pod is not being preempted.
* ``restart_count``    — sum of ``restartCount`` across all containers;
  non-zero values indicate a container restart loop and are surfaced in
  the per-instance ``provider_data`` so operators can detect
  ``CrashLoopBackOff`` before the pending timeout fires.

The cache uses a coarse :class:`threading.RLock` because the watcher
runs on a worker thread (via :func:`asyncio.to_thread`) while readers
run in the asyncio event loop.  Per-request reads are O(1) thanks to a
secondary index from ``request_id`` to its pod-name set.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class PodState:
    """Immutable snapshot of a single pod's state.

    Frozen so cache readers can hand the snapshot to handlers without
    worrying about background mutation.  The fields mirror what the Pod
    handler currently extracts from a ``V1Pod`` in
    :meth:`K8sPodHandler._instance_dict_for_pod`.
    """

    request_id: str
    pod_name: str
    namespace: str
    status: str  # ORB status string: running / starting / pending / failed
    phase: Optional[str] = None  # Raw kubernetes ``status.phase``
    ready: bool = False
    pod_ip: Optional[str] = None
    host_ip: Optional[str] = None
    node_name: Optional[str] = None
    status_reason: Optional[str] = None
    start_time: Optional[str] = None
    labels: dict[str, str] = field(default_factory=dict)
    deleted: bool = False
    last_updated: float = 0.0
    # Karpenter / cluster-autoscaler preemption signal.  Set when the pod
    # carries a ``DisruptionTarget=True`` condition; ``None`` otherwise.
    disrupted_reason: Optional[str] = None
    disrupted_message: Optional[str] = None
    # Sum of restartCount across all containers.  Non-zero indicates the
    # pod is in a restart loop (e.g. CrashLoopBackOff).
    restart_count: int = 0


class PodStateCache:
    """Thread-safe cache of :class:`PodState` keyed by ``(request_id, pod_name)``.

    Designed for the "many concurrent reads, occasional writes" pattern
    a watch task produces: every ADDED/MODIFIED/DELETED event triggers
    a single :meth:`upsert`, while ``check_hosts_status`` calls invoke
    :meth:`get` and never block writes for long.

    The secondary index ``self._by_request`` lets :meth:`get` return all
    pods for a request without scanning the whole dict — this keeps the
    cache O(N_pods_for_request) rather than O(N_total_pods).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str], PodState] = {}
        # Index: request_id -> set of pod_names.  Kept in sync with
        # ``_states`` so :meth:`get` is O(1) in the number of requests.
        self._by_request: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def upsert(self, state: PodState) -> None:
        """Insert or replace the cached entry for ``state``.

        ``state.last_updated`` is overwritten with the current monotonic
        clock so the cache controls staleness regardless of what the
        watcher passes in (defensive against test fixtures forgetting
        to stamp the field).
        """
        stamped = PodState(
            request_id=state.request_id,
            pod_name=state.pod_name,
            namespace=state.namespace,
            status=state.status,
            phase=state.phase,
            ready=state.ready,
            pod_ip=state.pod_ip,
            host_ip=state.host_ip,
            node_name=state.node_name,
            status_reason=state.status_reason,
            start_time=state.start_time,
            labels=dict(state.labels),
            deleted=state.deleted,
            last_updated=time.monotonic(),
            disrupted_reason=state.disrupted_reason,
            disrupted_message=state.disrupted_message,
            restart_count=state.restart_count,
        )
        key = (stamped.request_id, stamped.pod_name)
        with self._lock:
            self._states[key] = stamped
            self._by_request.setdefault(stamped.request_id, set()).add(stamped.pod_name)

    def delete(self, request_id: str, pod_name: str) -> None:
        """Remove the entry for ``(request_id, pod_name)``; no-op if missing."""
        key = (request_id, pod_name)
        with self._lock:
            self._states.pop(key, None)
            bucket = self._by_request.get(request_id)
            if bucket is not None:
                bucket.discard(pod_name)
                if not bucket:
                    self._by_request.pop(request_id, None)

    def clear(self) -> None:
        """Drop every cached entry.  Used on watcher restart."""
        with self._lock:
            self._states.clear()
            self._by_request.clear()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, request_id: str) -> Optional[list[PodState]]:
        """Return the snapshots for ``request_id``, or ``None`` if uncached.

        A return of ``None`` means "the watcher has not seen any pods
        for this request yet — caller should fall back to an on-demand
        list".  An empty list means "the watcher has seen this request
        and there are currently no pods" (a real terminal state).

        The distinction matters: cold-start cache lookups would
        otherwise be indistinguishable from a successful list that
        returned zero pods.
        """
        with self._lock:
            bucket = self._by_request.get(request_id)
            if bucket is None:
                return None
            return [self._states[(request_id, name)] for name in bucket]

    def all_states(self) -> list[PodState]:
        """Return a snapshot of every cached state (debug / metrics use only)."""
        with self._lock:
            return list(self._states.values())

    def size(self) -> int:
        """Return the number of cached entries."""
        with self._lock:
            return len(self._states)

    # ------------------------------------------------------------------
    # Staleness
    # ------------------------------------------------------------------

    def mark_stale(self, request_id: str, threshold: float) -> list[PodState]:
        """Drop and return entries for ``request_id`` older than ``threshold`` seconds.

        Used by the strategy when the watcher task has been dead long
        enough that cached entries can no longer be trusted.  Returns
        the dropped snapshots so callers can log them at debug.

        ``threshold`` is a duration in seconds; entries whose
        ``last_updated`` is older than ``now - threshold`` are removed.
        """
        cutoff = time.monotonic() - max(threshold, 0.0)
        dropped: list[PodState] = []
        with self._lock:
            bucket = self._by_request.get(request_id)
            if bucket is None:
                return dropped
            for name in list(bucket):
                key = (request_id, name)
                state = self._states.get(key)
                if state is None:
                    bucket.discard(name)
                    continue
                if state.last_updated < cutoff:
                    dropped.append(state)
                    self._states.pop(key, None)
                    bucket.discard(name)
            if not bucket:
                self._by_request.pop(request_id, None)
        return dropped


__all__ = ["PodState", "PodStateCache"]
