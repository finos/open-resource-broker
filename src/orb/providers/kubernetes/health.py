"""Kubernetes health checks — registered with the application HealthCheck instance.

Mirrors ``orb.providers.aws.health.register_aws_health_checks`` for the
modern kubernetes provider.  The ``kubernetes_api`` check calls
``CoreV1Api.get_api_resources`` which is the cheapest authenticated probe
of the API server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orb.domain.base.ports.health_check_port import HealthCheckPort
from orb.monitoring.health import HealthStatus

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.providers.kubernetes.infrastructure.kubernetes_client import KubernetesClient


def register_kubernetes_health_checks(
    health_check: HealthCheckPort,
    kubernetes_client: "KubernetesClient",
) -> None:
    """Register Kubernetes-specific health checks with the given HealthCheck instance.

    The ``kubernetes_api`` check validates connectivity to the API server
    using a cheap, read-only ``get_api_resources`` call.

    Args:
        health_check: The application HealthCheckPort to register checks on.
        kubernetes_client: Authenticated KubernetesClient used by the checks.
    """

    def _check_kubernetes_api_health() -> HealthStatus:
        try:
            resources = kubernetes_client.core_v1.get_api_resources()
            resource_count = len(getattr(resources, "resources", []) or [])
            return HealthStatus(
                name="kubernetes_api",
                status="healthy",
                details={
                    "group_version": getattr(resources, "group_version", "v1"),
                    "resource_count": resource_count,
                    "api_status": "available",
                },
                dependencies=["kubernetes_api"],
            )
        except Exception as exc:
            return HealthStatus(
                name="kubernetes_api",
                status="unhealthy",
                details={"error": str(exc)},
                dependencies=["kubernetes_api"],
            )

    health_check.register_check("kubernetes_api", _check_kubernetes_api_health)
