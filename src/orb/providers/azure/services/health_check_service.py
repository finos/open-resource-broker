"""Azure provider health checks."""

from __future__ import annotations

import time
from typing import Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.base.strategy import ProviderHealthStatus


class AzureHealthCheckService:
    """Own the Azure provider health-check interaction."""

    def __init__(self, config: AzureProviderConfig, logger: LoggingPort) -> None:
        self._config = config
        self._logger = logger

    def check_health(self, azure_client: Optional[AzureClient]) -> ProviderHealthStatus:
        start_time = time.time()

        try:
            if not azure_client:
                return ProviderHealthStatus.unhealthy(
                    "Azure client initialization failed",
                    {"error": "client_initialization_failed"},
                )

            from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

            if is_dry_run_active():
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.healthy(
                    f"Azure provider healthy (DRY-RUN) - Region: {self._config.region}",
                    response_time_ms,
                )

            token = azure_client.credential.get_token("https://management.azure.com/.default")
            if token is None:
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.unhealthy(
                    "Azure provider unhealthy - no token found",
                    {"error": "no_token", "response_time_ms": response_time_ms},
                )

            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.healthy(
                f"Azure provider healthy - Token fetched successfully in {response_time_ms}, "
                f"Region: {self._config.region}",
                response_time_ms,
            )
        except Exception as exc:
            self._logger.warning("Azure health check failed: %s", exc)
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.unhealthy(
                f"Health check error: {exc!s}",
                {"error": str(exc), "response_time_ms": response_time_ms},
            )
