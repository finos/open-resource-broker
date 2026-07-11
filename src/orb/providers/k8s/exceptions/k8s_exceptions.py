"""Kubernetes provider exception hierarchy."""

from __future__ import annotations

from orb.domain.base.exceptions import InfrastructureError


class K8sError(InfrastructureError):
    """Base class for all Kubernetes provider-specific errors."""


class K8sAuthError(K8sError):
    """Raised when Kubernetes API client authentication / config loading fails."""


class K8sHealthCheckError(K8sError):
    """Raised when the Kubernetes API server health check fails."""


class K8sDiscoveryError(K8sError):
    """Raised when Kubernetes infrastructure discovery encounters a non-recoverable error.

    Distinct from :class:`K8sAuthError` (authentication / config loading) and
    :class:`K8sHealthCheckError` (server reachability).  ``K8sDiscoveryError`` is
    raised when a specific discovery call fails in a way that prevents the
    discovery flow from continuing — for example a 404 on a named namespace that
    was expected to exist, or a kubeconfig that is absent or malformed.
    """
