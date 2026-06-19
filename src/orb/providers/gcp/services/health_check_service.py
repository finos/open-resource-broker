"""GCP provider health checks and operational metadata."""

from __future__ import annotations

import time
from typing import Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.mocking.dry_run_context import is_dry_run_active
from orb.providers.base.strategy import ProviderHealthStatus
from orb.providers.gcp.configuration.config import GCPProviderConfig


class GCPHealthCheckService:
    """Own GCP provider health checks and credential helpers."""

    def __init__(self, config: GCPProviderConfig, logger: LoggingPort) -> None:
        self._config = config
        self._logger = logger

    def check_health(self) -> ProviderHealthStatus:
        """Verify GCP credentials by refreshing the ADC token."""
        start_time = time.time()
        if is_dry_run_active():
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.healthy(
                f"GCP provider healthy (DRY-RUN) - Project: {self._config.project_id}",
                response_time_ms,
            )

        try:
            import google.auth
            import google.auth.transport.requests

            credentials, project = google.auth.default()
            credentials.refresh(google.auth.transport.requests.Request())

            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.healthy(
                f"GCP provider healthy - Project: {project or self._config.project_id}",
                response_time_ms,
            )
        except Exception as exc:
            self._logger.warning("GCP health check failed: %s", exc, exc_info=True)
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.unhealthy(
                f"GCP credential check failed: {exc!s}",
                {
                    "error": str(exc),
                    "project_id": self._config.project_id,
                    "response_time_ms": response_time_ms,
                    "hint": (
                        "Set GOOGLE_APPLICATION_CREDENTIALS or run under workload identity / gcloud ADC "
                        "(https://cloud.google.com/docs/authentication/application-default-credentials)"
                    ),
                },
            )

    def get_available_credential_sources(self) -> list[dict]:
        """Return supported credential sources."""
        return [
            {
                "name": "adc",
                "description": "Application Default Credentials / workload identity",
            }
        ]

    def test_credentials(self, credential_source: Optional[str] = None, **kwargs) -> dict:
        """Test ADC availability without fetching real SDK clients."""
        del credential_source, kwargs
        status = self.check_health()
        if status.is_healthy:
            return {"success": True, "project_id": self._config.project_id}
        return {
            "success": False,
            "error": status.status_message,
            "details": status.error_details or {},
        }

    def get_credential_requirements(self) -> dict:
        """Describe GCP auth requirements."""
        return {
            "application_default_credentials": {
                "required": True,
                "description": "GCP provider uses Application Default Credentials only",
            }
        }

    def get_operational_requirements(self) -> dict:
        """Describe non-secret operational requirements."""
        return {
            "project_id": {"required": True, "description": "GCP project ID"},
            "region": {"required": True, "description": "Default GCP region"},
        }
