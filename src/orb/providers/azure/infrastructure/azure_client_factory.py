"""Factory helpers for explicit Azure client runtime construction."""

from __future__ import annotations

from typing import Any

from orb.config import PerformanceConfig
from orb.domain.base.ports import LoggingPort
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.infrastructure.azure_client import (
    AzureClient,
    AzureClientRuntimeConfig,
)


class AzureClientFactory:
    """Compose Azure client runtime dependencies in Azure infrastructure."""

    # noinspection PyArgumentList
    @staticmethod
    def resolve_performance_config(
        config_port: Any,
        logger: LoggingPort,
    ) -> PerformanceConfig:
        """Resolve shared performance config from an explicit config source."""
        if config_port is None:
            logger.debug("No shared config port available; using default Azure performance config")
            return PerformanceConfig()

        try:
            perf_config = config_port.get_typed(PerformanceConfig)
        except Exception as exc:
            logger.debug("Could not load performance config from shared config port: %s", exc)
            return PerformanceConfig()

        if not isinstance(perf_config, PerformanceConfig):
            logger.debug(
                "Ignoring unexpected performance config type from shared config port: %s",
                type(perf_config).__name__,
            )
            return PerformanceConfig()

        return perf_config

    @classmethod
    def build_runtime_config(
        cls,
        azure_config: AzureProviderConfig,
        logger: LoggingPort,
        *,
        performance_config: PerformanceConfig | None = None,
        config_port: Any = None,
    ) -> AzureClientRuntimeConfig:
        """Build the typed runtime config required by AzureClient."""
        resolved_performance_config = performance_config or cls.resolve_performance_config(
            config_port,
            logger,
        )
        return AzureClientRuntimeConfig(
            azure_config=azure_config,
            performance_config=resolved_performance_config,
        )

    @staticmethod
    def create_client(
        runtime_config: AzureClientRuntimeConfig,
        logger: LoggingPort,
    ) -> AzureClient:
        """Create an Azure client from fully resolved runtime config."""
        return AzureClient(runtime_config=runtime_config, logger=logger)
