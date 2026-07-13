"""Kubernetes provider exceptions."""

from orb.providers.k8s.exceptions.k8s_exceptions import (
    K8sAuthError,
    K8sAuthorizationError,
    K8sConflictError,
    K8sDiscoveryError,
    K8sEntityNotFoundError,
    K8sError,
    K8sHealthCheckError,
    K8sQuotaExceededError,
    K8sRateLimitError,
    K8sValidationError,
    classify_api_exception,
)

__all__: list[str] = [
    "K8sAuthError",
    "K8sAuthorizationError",
    "K8sConflictError",
    "K8sDiscoveryError",
    "K8sEntityNotFoundError",
    "K8sError",
    "K8sHealthCheckError",
    "K8sQuotaExceededError",
    "K8sRateLimitError",
    "K8sValidationError",
    "classify_api_exception",
]
