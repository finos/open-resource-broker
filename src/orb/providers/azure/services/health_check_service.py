"""Azure provider health checks."""

from __future__ import annotations

import time

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.infrastructure.credential_factory import (
    AsyncDefaultAzureAccessTokenProvider,
    DefaultAzureAccessTokenProvider,
)
from orb.providers.base.strategy import ProviderHealthStatus


class AzureHealthCheckService:
    """Own the Azure provider health-check interaction."""

    _MANAGEMENT_SCOPE = "https://management.azure.com/.default"

    def __init__(self, config: AzureProviderConfig, logger: LoggingPort) -> None:
        self._config = config
        self._logger = logger

    def check_health(self) -> ProviderHealthStatus:
        """Perform a synchronous health check with short-lived Azure credentials."""
        start_time = time.time()

        try:
            from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

            if is_dry_run_active():
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.healthy(
                    f"Azure provider healthy (DRY-RUN) - Region: {self._config.region}",
                    response_time_ms,
                )

            token_provider = DefaultAzureAccessTokenProvider(
                client_id=self._config.client_id,
                logger=self._logger,
            )
            token_provider.get_access_token(self._MANAGEMENT_SCOPE)

            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.healthy(
                f"Azure provider healthy - Token fetched successfully in {response_time_ms}, "
                f"Region: {self._config.region}",
                response_time_ms,
            )
        except Exception as exc:
            self._logger.warning("Azure health check failed: %s", exc, exc_info=True)
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.unhealthy(
                f"Health check error: {exc!s}",
                {"error": str(exc), "response_time_ms": response_time_ms},
            )

    async def check_health_async(self) -> ProviderHealthStatus:
        """Perform an async health check with short-lived async Azure credentials."""
        start_time = time.time()

        try:
            from orb.infrastructure.mocking.dry_run_context import is_dry_run_active

            if is_dry_run_active():
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.healthy(
                    f"Azure provider healthy (DRY-RUN) - Region: {self._config.region}",
                    response_time_ms,
                )

            token_provider = AsyncDefaultAzureAccessTokenProvider(
                client_id=self._config.client_id,
                logger=self._logger,
            )
            await token_provider.get_access_token(self._MANAGEMENT_SCOPE)

            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.healthy(
                f"Azure provider healthy - Token fetched successfully in {response_time_ms}, "
                f"Region: {self._config.region}",
                response_time_ms,
            )
        except Exception as exc:
            self._logger.warning("Azure health check failed: %s", exc, exc_info=True)
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.unhealthy(
                f"Health check error: {exc!s}",
                {"error": str(exc), "response_time_ms": response_time_ms},
            )
