"""Kubernetes API auth loaders.

These modules are thin wrappers around ``kubernetes.config.load_*`` calls
used to bootstrap the Kubernetes API client.  They are not ORB
:class:`~orb.infrastructure.adapters.ports.auth.AuthPort` strategies (which
authenticate inbound HTTP requests to ORB's REST surface) — the
ORB-side ``AuthRegistry`` entries for the kubernetes provider are
registered in :mod:`orb.providers.k8s.registration`.
"""

from orb.providers.k8s.auth.in_cluster import (
    is_in_cluster,
    load_in_cluster_config,
)
from orb.providers.k8s.auth.kubeconfig import load_kubeconfig

__all__: list[str] = [
    "is_in_cluster",
    "load_in_cluster_config",
    "load_kubeconfig",
]
