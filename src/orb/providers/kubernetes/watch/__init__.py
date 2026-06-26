"""Kubernetes watch-based event ingestion.

Phase C deliverables:

* :class:`~orb.providers.kubernetes.watch.pod_state_cache.PodStateCache`
  — in-process pod-state cache keyed by ``(request_id, pod_name)``.
* :class:`~orb.providers.kubernetes.watch.watcher.KubernetesWatcher`
  — asyncio task wrapping ``kubernetes.watch.Watch().stream()`` with
  410-Gone handling and exponential backoff.
* :class:`~orb.providers.kubernetes.watch.multi_namespace.MultiNamespaceWatcher`
  — fans the watcher out across the configured namespaces (or runs a
  single cluster-scoped watcher when ``namespaces=["*"]``).

All three live under this subpackage so that the kubernetes SDK import
surface remains confined to ``src/orb/providers/kubernetes/`` (enforced
by the architecture test).
"""

from orb.providers.kubernetes.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.kubernetes.watch.pod_state_cache import PodState, PodStateCache
from orb.providers.kubernetes.watch.watcher import KubernetesWatcher

__all__ = [
    "KubernetesWatcher",
    "MultiNamespaceWatcher",
    "PodState",
    "PodStateCache",
]
