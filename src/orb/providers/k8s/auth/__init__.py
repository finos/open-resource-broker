"""Kubernetes API auth loaders and inbound HTTP auth strategies.

API client bootstrap loaders
-----------------------------
These modules are thin wrappers around ``kubernetes.config.load_*`` calls
used to bootstrap the Kubernetes API client.  They are not ORB
:class:`~orb.infrastructure.adapters.ports.auth.AuthPort` strategies (which
authenticate inbound HTTP requests to ORB's REST surface) — the
ORB-side ``AuthRegistry`` entries for the kubernetes provider are
registered in :mod:`orb.providers.k8s.registration`.

Inbound HTTP auth strategy
---------------------------
:class:`KubeAuthStrategy` implements the ``AuthPort`` interface and validates
caller Kubernetes ServiceAccount JWTs via the ``authentication.k8s.io/v1
TokenReview`` API.  It is registered in the ``AuthRegistry`` by
:func:`orb.providers.k8s.registration.register_k8s_auth_strategies` when
``inbound_auth_enabled=True`` in :class:`K8sProviderConfig`.
"""

from orb.providers.k8s.auth.in_cluster import (
    is_in_cluster,
    load_in_cluster_config,
)
from orb.providers.k8s.auth.kube_auth_strategy import KubeAuthStrategy
from orb.providers.k8s.auth.kubeconfig import load_kubeconfig

__all__: list[str] = [
    "KubeAuthStrategy",
    "is_in_cluster",
    "load_in_cluster_config",
    "load_kubeconfig",
]
