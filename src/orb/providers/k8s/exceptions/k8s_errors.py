"""Kubernetes provider exception hierarchy."""

from __future__ import annotations


class K8sError(Exception):
    """Base class for all Kubernetes provider-specific errors."""


class K8sAuthError(K8sError):
    """Raised when Kubernetes API client authentication / config loading fails."""


class K8sHealthCheckError(K8sError):
    """Raised when the Kubernetes API server health check fails."""
