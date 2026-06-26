"""Kubernetes reconciliation and garbage collection.

Components:

* :class:`~orb.providers.k8s.reconciliation.startup_reconciler.StartupReconciler`
  — runs once at provider :meth:`initialize`, before the watch task is
  started.  Lists every managed pod with the configured label selector,
  cross-references the request id labels against the request ids ORB
  knows about, populates the :class:`PodStateCache` so the first
  ``check_hosts_status`` after a restart hits a warm cache, and surfaces
  any pods ORB does not know about as "orphans".
* :class:`~orb.providers.k8s.reconciliation.orphan_gc.OrphanGarbageCollector`
  — long-running asyncio task that polls the cluster at
  ``orphan_gc_interval_seconds`` intervals and either logs the orphans
  it finds (default) or deletes them when ``auto_cleanup_orphans`` is on.
* :mod:`~orb.providers.k8s.reconciliation.timeout_gc` — pure
  helper functions that handlers call from their ``check_hosts_status``
  to detect pods stuck in ``Pending`` past
  ``K8sProviderConfig.pod_timeout_seconds`` and rewrite the
  matching per-instance dicts to ``status="terminated"`` with the
  unschedulable reason copied from ``pod.status.conditions`` into
  ``provider_data.unschedulable_reason``.  No automatic deletion — the
  operator may want to inspect the stuck pod first.

The three modules live alongside the existing ``watch/`` subpackage so
that the ``kubernetes`` SDK import surface stays confined to
``src/orb/providers/k8s/``.
"""

from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector
from orb.providers.k8s.reconciliation.startup_reconciler import (
    ReconciliationReport,
    StartupReconciler,
)
from orb.providers.k8s.reconciliation.timeout_gc import (
    apply_pod_timeout,
    is_pod_timed_out,
)

__all__ = [
    "OrphanGarbageCollector",
    "ReconciliationReport",
    "StartupReconciler",
    "apply_pod_timeout",
    "is_pod_timed_out",
]
