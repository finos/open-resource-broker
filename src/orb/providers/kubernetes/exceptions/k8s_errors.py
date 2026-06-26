"""Kubernetes provider exception hierarchy."""

from __future__ import annotations


class KubernetesError(Exception):
    """Base class for all Kubernetes provider-specific errors."""


class KubernetesAuthError(KubernetesError):
    """Raised when Kubernetes API client authentication / config loading fails."""


class KubernetesHealthCheckError(KubernetesError):
    """Raised when the Kubernetes API server health check fails."""
