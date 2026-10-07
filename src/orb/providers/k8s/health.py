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

if TYPE_CHECKING:
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient


def register_k8s_health_checks(
    health_check: HealthCheckPort,
    kubernetes_client: K8sClient,
    *,
    kind: str = "provider",
) -> None:
    """Register Kubernetes-specific health checks with the given HealthCheck instance.

    The ``kubernetes_api`` check validates connectivity to the API server
    using a cheap, read-only ``get_api_resources`` call.

    Args:
        health_check: The application HealthCheckPort to register checks on.
        kubernetes_client: Authenticated K8sClient used by the checks.
        kind: Readiness classification for the connectivity check
            (``"core"`` when Kubernetes is the sole enabled provider so a
            broken provider gates ``/readyz``; ``"provider"`` otherwise). See
            ``providers.health_scoping.connectivity_check_kind``.
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
            # An unreachable API server is a degraded signal for this provider,
            # not a hard failure of the process. /health maps degraded -> 200,
            # so a single unreachable cluster no longer forces the endpoint to
            # 503.
            return HealthStatus(
                name="kubernetes_api",
                status="degraded",
                details={"error": str(exc)},
                dependencies=["kubernetes_api"],
            )

    health_check.register_check("kubernetes_api", _check_kubernetes_api_health, kind=kind)
