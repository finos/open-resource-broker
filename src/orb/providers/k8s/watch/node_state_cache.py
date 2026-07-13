"""Thread-safe in-memory cache of Kubernetes node state, keyed by node name.

Populated by :class:`~orb.providers.k8s.watch.node_watcher.K8sNodeWatcher`
and consumed by
:class:`~orb.providers.k8s.handlers.base_handler.K8sHandlerBase` when
``node_watch_enabled=True`` is set in
:class:`~orb.providers.k8s.configuration.config.K8sProviderConfig`.

The cache stores the minimum information the handlers need to enrich
per-instance ``provider_data`` with node-level metadata:

* ``instance_type``      — EC2 / cloud instance type; read from the
  ``node.kubernetes.io/instance-type`` label (with
  ``beta.kubernetes.io/instance-type`` as a fallback for older clusters).
* ``zone``               — availability zone; read from
  ``topology.kubernetes.io/zone`` (with
  ``failure-domain.beta.kubernetes.io/zone`` as a fallback).
* ``region``             — cloud region; read from
  ``topology.kubernetes.io/region`` (with
  ``failure-domain.beta.kubernetes.io/region`` as a fallback).
* ``capacity_type``      — canonical capacity type resolved cloud-agnostically
  from provider-specific labels; stored as ``"spot"``, ``"ondemand"``, or
  ``None``.
* ``cpu_capacity``       — value of ``node.status.capacity.cpu`` as
  reported by the kubelet (e.g. ``"32"``).
* ``memory_capacity``    — value of ``node.status.capacity.memory``
  (e.g. ``"128Gi"``).
* ``cpu_allocatable``    — value of ``node.status.allocatable.cpu``.
* ``memory_allocatable`` — value of ``node.status.allocatable.memory``.
* ``conditions``         — list of condition dicts extracted from
  ``node.status.conditions``; each dict carries ``type``, ``status``,
  ``reason``, and ``lastTransitionTime``.
* ``ready``              — ``True`` when the ``Ready`` condition is
  present and its ``status`` is ``"True"``.
* ``last_updated``       — :func:`datetime.datetime.utcnow` timestamp of
  the last upsert; used for diagnostics and staleness checks.

The cache uses a :class:`threading.RLock` because the node watcher
runs on a worker thread while status-read callers run in the asyncio
event loop.  Node lookups are O(1) on name.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class K8sNodeState:
    """Immutable snapshot of a single Kubernetes node's state.

    Frozen so cache readers can hand the snapshot to handlers without
    worrying about background mutation.

    Attributes:
        name: Kubernetes node name (``node.metadata.name``).
        instance_type: Cloud provider instance type, derived from the
            ``node.kubernetes.io/instance-type`` label (or its
            ``beta.kubernetes.io/instance-type`` legacy alias).
        zone: Availability zone from the
            ``topology.kubernetes.io/zone`` label (or the legacy
            ``failure-domain.beta.kubernetes.io/zone`` alias).
        region: Cloud region from the ``topology.kubernetes.io/region``
            label (or the legacy ``failure-domain.beta.kubernetes.io/region``
            alias).  ``None`` when the label is absent or on-prem.
        capacity_type: Canonical capacity type resolved cloud-agnostically;
            one of ``"spot"``, ``"ondemand"``, or ``None`` when not
            determinable (e.g. on-prem or unknown labels).
        cpu_capacity: Raw ``node.status.capacity.cpu`` string from
            the kubelet (e.g. ``"32"``).
        memory_capacity: Raw ``node.status.capacity.memory`` string
            (e.g. ``"128932196Ki"``).
        cpu_allocatable: Raw ``node.status.allocatable.cpu`` string;
            reflects resources not reserved by system daemons.
        memory_allocatable: Raw ``node.status.allocatable.memory``
            string.
        conditions: List of condition dicts extracted from
            ``node.status.conditions``.  Each dict contains the keys
            ``type``, ``status``, ``reason``, and
            ``lastTransitionTime``.  Empty list when conditions are
            not present in the watch payload.
        ready: Convenience bool derived from the ``Ready`` condition.
            ``True`` iff the ``Ready`` condition has ``status="True"``.
        last_updated: UTC timestamp of the last cache upsert.
    """

    name: str
    instance_type: Optional[str] = None
    zone: Optional[str] = None
    region: Optional[str] = None
    capacity_type: Optional[str] = None
    cpu_capacity: Optional[str] = None
    memory_capacity: Optional[str] = None
    cpu_allocatable: Optional[str] = None
    memory_allocatable: Optional[str] = None
    conditions: list[dict] = field(default_factory=list)
    ready: bool = False
    last_updated: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )


class K8sNodeStateCache:
    """Thread-safe cache of :class:`K8sNodeState` keyed by node name.

    Designed for the "many concurrent reads, occasional writes" pattern
    the node watcher produces: every ADDED/MODIFIED/DELETED event
    triggers a single :meth:`upsert` or :meth:`delete`, while
    ``check_hosts_status`` callers invoke :meth:`get` and never block
    writes for long.

    The cache is intentionally simple — cluster-scoped nodes share a
    single flat namespace, so the secondary per-request index used by
    :class:`~orb.providers.k8s.watch.pod_state_cache.PodStateCache`
    is not needed here.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[str, K8sNodeState] = {}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def upsert(self, state: K8sNodeState) -> None:
        """Insert or replace the cached entry for ``state.name``.

        ``state.last_updated`` is overwritten with the current UTC
        clock so the cache controls the timestamp regardless of what
        the watcher passes in — defensive against test fixtures
        forgetting to stamp the field.
        """
        stamped = K8sNodeState(
            name=state.name,
            instance_type=state.instance_type,
            zone=state.zone,
            region=state.region,
            capacity_type=state.capacity_type,
            cpu_capacity=state.cpu_capacity,
            memory_capacity=state.memory_capacity,
            cpu_allocatable=state.cpu_allocatable,
            memory_allocatable=state.memory_allocatable,
            conditions=list(state.conditions),
            ready=state.ready,
            last_updated=datetime.now(tz=timezone.utc),
        )
        with self._lock:
            self._states[stamped.name] = stamped

    def delete(self, name: str) -> None:
        """Remove the entry for ``name``; no-op if missing."""
        with self._lock:
            self._states.pop(name, None)

    def clear(self) -> None:
        """Drop every cached entry.  Used on watcher restart."""
        with self._lock:
            self._states.clear()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[K8sNodeState]:
        """Return the snapshot for ``name``, or ``None`` if not cached."""
        with self._lock:
            return self._states.get(name)

    def all(self) -> list[K8sNodeState]:
        """Return a snapshot of every cached state."""
        with self._lock:
            return list(self._states.values())

    def size(self) -> int:
        """Return the number of cached entries."""
        with self._lock:
            return len(self._states)


__all__ = ["K8sNodeState", "K8sNodeStateCache"]
