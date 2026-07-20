"""K8s Health Check Service — provider health monitoring.

Extracted from :class:`K8sProviderStrategy` to mirror the AWS
``AWSHealthCheckService`` pattern.  Owns the ``check_health`` logic
(``CoreV1Api.get_api_resources`` probe + enrichment) and the
``register_health_checks`` wiring so the strategy stays focused on
lifecycle management.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.base.strategy import ProviderHealthStatus
from orb.providers.k8s.configuration.config import K8sProviderConfig

if TYPE_CHECKING:  # pragma: no cover
    from orb.monitoring.health import HealthCheck
    from orb.providers.k8s.infrastructure.k8s_client import K8sClient


class K8sHealthCheckService:
    """Service for Kubernetes provider health monitoring.

    Args:
        config:  Validated :class:`K8sProviderConfig`.
        logger:  Injected :class:`LoggingPort` — no stdlib logging here.
        min_version_gate: When ``True``, the health check enforces the
            minimum API-server version declared in the config.  Defaults
            to ``False`` because the live version API call may not be
            available in all cluster flavours.
    """

    def __init__(
        self,
        config: K8sProviderConfig,
        logger: LoggingPort,
    ) -> None:
        self._config = config
        self._logger = logger

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def check_health(self, kubernetes_client: K8sClient) -> ProviderHealthStatus:
        """Probe the Kubernetes API server via ``CoreV1Api.get_api_resources``.

        On success the status message and ``error_details`` dict are
        enriched with the cluster endpoint, server version, and current
        namespace so operators running against multiple clusters can
        identify which cluster the probe hit.  Each enrichment field is
        fetched defensively — a failure to retrieve the server version or
        endpoint never causes the overall health check to fail.
        """
        start = time.time()
        try:
            resources = kubernetes_client.core_v1.get_api_resources()
            response_time_ms = (time.time() - start) * 1000.0
            resource_count = len(getattr(resources, "resources", []) or [])

            cluster_endpoint: Optional[str] = None
            try:
                cluster_endpoint = getattr(
                    kubernetes_client.api_client.configuration,  # type: ignore[union-attr]
                    "host",
                    None,
                )
            except Exception as exc:
                self._logger.debug("Could not read cluster endpoint from api_client: %s", exc)

            server_version: Optional[str] = None
            try:
                from kubernetes.client import VersionApi

                version_info = VersionApi(api_client=kubernetes_client.api_client).get_code()
                server_version = getattr(version_info, "git_version", None)
            except Exception as exc:
                self._logger.debug("Could not read server version from VersionApi: %s", exc)

            current_namespace: Optional[str] = self._config.namespace
            if current_namespace is None and self._config.in_cluster:
                try:
                    from orb.providers.k8s.configuration.config import (
                        _read_in_cluster_namespace,
                    )

                    current_namespace = _read_in_cluster_namespace()
                except Exception as exc:
                    self._logger.debug("Could not read in-cluster namespace: %s", exc)

            parts: list[str] = [
                f"Kubernetes API server reachable; {resource_count} core/v1 resources"
            ]
            if cluster_endpoint:
                parts.append(f"endpoint={cluster_endpoint}")
            if server_version:
                parts.append(f"version={server_version}")
            if current_namespace:
                parts.append(f"namespace={current_namespace}")

            details: dict[str, Any] = {"response_time_ms": response_time_ms}
            if cluster_endpoint:
                details["cluster_endpoint"] = cluster_endpoint
            if server_version:
                details["server_version"] = server_version
            if current_namespace:
                details["namespace"] = current_namespace

            return ProviderHealthStatus(
                is_healthy=True,
                status_message="; ".join(parts),
                response_time_ms=response_time_ms,
                error_details=details,
            )
        except Exception as exc:
            response_time_ms = (time.time() - start) * 1000.0
            self._logger.warning("Kubernetes health check failed: %s", exc, exc_info=True)
            return ProviderHealthStatus.unhealthy(
                message=f"Kubernetes API server unreachable: {exc}",
                error_details={
                    "error": str(exc),
                    "response_time_ms": response_time_ms,
                },
            )

    # ------------------------------------------------------------------
    # Health-check registration
    # ------------------------------------------------------------------

    def register_health_checks(
        self,
        health_check: HealthCheck,
        kubernetes_client: K8sClient,
    ) -> None:
        """Register Kubernetes-specific health checks."""
        from orb.providers.k8s.health import register_k8s_health_checks

        register_k8s_health_checks(health_check, kubernetes_client)


__all__ = ["K8sHealthCheckService"]
