"""Kubernetes watch-based event ingestion.

Phase C deliverables:

* :class:`~orb.providers.k8s.watch.pod_state_cache.PodStateCache`
  — in-process pod-state cache keyed by ``(request_id, pod_name)``.
* :class:`~orb.providers.k8s.watch.watcher.K8sWatcher`
  — asyncio task wrapping ``kubernetes.watch.Watch().stream()`` with
  410-Gone handling and exponential backoff.
* :class:`~orb.providers.k8s.watch.multi_namespace.MultiNamespaceWatcher`
  — fans the watcher out across the configured namespaces (or runs a
  single cluster-scoped watcher when ``namespaces=["*"]``).

All three live under this subpackage so that the kubernetes SDK import
surface remains confined to ``src/orb/providers/k8s/`` (enforced
by the architecture test).
"""

from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache
from orb.providers.k8s.watch.watcher import K8sWatcher

__all__ = [
    "K8sWatcher",
    "MultiNamespaceWatcher",
    "PodState",
    "PodStateCache",
]
