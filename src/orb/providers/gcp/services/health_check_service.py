"""GCP provider health checks and operational metadata."""

from __future__ import annotations

import os
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
        """Perform a lightweight ADC-oriented health check."""
        start_time = time.time()
        if is_dry_run_active():
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.healthy(
                f"GCP provider healthy (DRY-RUN) - Project: {self._config.project_id}",
                response_time_ms,
            )
        credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        response_time_ms = (time.time() - start_time) * 1000
        if credential_path or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT"):
            return ProviderHealthStatus.healthy(
                f"GCP provider ready for project {self._config.project_id}",
                response_time_ms,
            )
        return ProviderHealthStatus.unhealthy(
            "No ADC context detected for GCP provider",
            {
                "project_id": self._config.project_id,
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
