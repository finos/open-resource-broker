"""Provider Execution Service - Registry-based strategy execution.

Metric emission
---------------
Metric emission uses ``ProviderMetricsPort`` (label-based), injected via
constructor (defaults to ``NoOpProviderMetrics`` when no DI container is used).

Instrument: ``orb.provider.operation.total{provider_id, operation, outcome}`` —
a single Counter with three label dimensions, keeping cardinality bounded.
"""

import time
from typing import Any, Optional

from orb.domain.base.ports import ConfigurationPort, LoggingPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.providers.base.metrics import NoOpProviderMetrics, ProviderMetricsPort
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderResult,
    ProviderStrategy,
)


class ProviderExecutionService:
    """
    Service for executing operations with provider strategies using Provider Registry.

    Replaces ProviderContext with a cleaner registry-based approach.
    """

    def __init__(
        self,
        logger: LoggingPort,
        config_port: ConfigurationPort,
        registry: ProviderRegistryPort,
        metrics: Optional[ProviderMetricsPort] = None,
    ) -> None:
        """Initialize the provider execution service.

        Args:
            logger: Injected logger.
            config_port: Configuration port for provider config lookups.
            registry: Provider registry for strategy creation.
            metrics: Optional ProviderMetricsPort; defaults to NoOpProviderMetrics.
                Pass ``None`` to use no-op (safe default).
        """
        self._logger = logger
        self._config_port = config_port
        self._registry = registry
        self._metrics: ProviderMetricsPort = (
            metrics if metrics is not None else NoOpProviderMetrics()
        )

    async def execute_operation(
        self, provider_identifier: str, operation: ProviderOperation
    ) -> ProviderResult:
        """
        Execute an operation using a specific provider strategy.

        Args:
            provider_identifier: Provider type or instance name
            operation: The operation to execute

        Returns:
            Result of the operation execution
        """
        start_time = time.time()

        try:
            # Create strategy from registry
            strategy = self._create_strategy(provider_identifier)
            if not strategy:
                return ProviderResult.error_result(
                    f"Provider strategy not found: {provider_identifier}", "STRATEGY_NOT_FOUND"
                )

            # Initialize strategy if needed
            if not strategy.is_initialized:
                if not strategy.initialize():
                    return ProviderResult.error_result(
                        f"Failed to initialize strategy {provider_identifier}",
                        "STRATEGY_INITIALIZATION_FAILED",
                    )

            # Check if strategy supports the operation
            capabilities = strategy.get_capabilities()
            if not capabilities.supports_operation(operation.operation_type):
                duration = time.time() - start_time
                self._metrics.record_operation(
                    service=provider_identifier,
                    operation=operation.operation_type.name.lower(),
                    duration_seconds=duration,
                    success=False,
                    error_code="OPERATION_NOT_SUPPORTED",
                )
                return ProviderResult.error_result(
                    f"Strategy {provider_identifier} does not support operation {operation.operation_type}",
                    "OPERATION_NOT_SUPPORTED",
                )

            # Execute the operation
            result = await strategy.execute_operation(operation)

            # Record metrics
            duration = time.time() - start_time
            self._metrics.record_operation(
                service=provider_identifier,
                operation=operation.operation_type.name.lower(),
                duration_seconds=duration,
                success=result.success,
                error_code=None if result.success else "OPERATION_FAILED",
            )

            self._logger.debug(
                "Operation %s executed by %s: success=%s, time=%.2fms",
                operation.operation_type,
                provider_identifier,
                result.success,
                duration * 1000,
            )

            return result

        except Exception as e:
            duration = time.time() - start_time
            self._metrics.record_operation(
                service=provider_identifier,
                operation=operation.operation_type.name.lower(),
                duration_seconds=duration,
                success=False,
                error_code="EXECUTION_ERROR",
            )

            self._logger.error(
                "Error executing operation %s with %s: %s",
                operation.operation_type,
                provider_identifier,
                e,
            )
            return ProviderResult.error_result(
                f"Operation execution failed: {e!s}", "EXECUTION_ERROR"
            )

    def get_strategy_capabilities(self, provider_identifier: str) -> Optional[ProviderCapabilities]:
        """Get capabilities of a specific provider strategy."""
        strategy = self._create_strategy(provider_identifier)
        if not strategy:
            return None
        return strategy.get_capabilities()

    def check_strategy_health(self, provider_identifier: str) -> Optional[ProviderHealthStatus]:
        """Check health of a specific provider strategy."""
        strategy = self._create_strategy(provider_identifier)
        if not strategy:
            return None

        try:
            health_status = strategy.check_health()
            # Record health check counter via port (label-based, not name-embedded)
            self._metrics.record_counter(
                "provider.strategy.health_checks.total",
                labels={"provider_id": provider_identifier},
            )
            return health_status
        except Exception as e:
            self._logger.error(
                "Error checking health of strategy %s: %s", provider_identifier, e, exc_info=True
            )
            return ProviderHealthStatus.unhealthy(
                f"Health check failed: {e!s}", {"exception": str(e)}
            )

    def _create_strategy(self, provider_identifier: str) -> Optional[ProviderStrategy]:
        """Create a provider strategy from registry."""
        try:
            # Try instance first
            if self._registry.is_provider_instance_registered(provider_identifier):
                provider_config = self._get_provider_config(provider_identifier)
                return self._registry.get_or_create_strategy(provider_identifier, provider_config)

            # Try provider type
            if self._registry.is_provider_registered(provider_identifier):
                provider_config = self._get_provider_config(provider_identifier)
                return self._registry.get_or_create_strategy(provider_identifier, provider_config)

            return None
        except Exception as e:
            self._logger.error(
                "Error creating strategy %s: %s", provider_identifier, e, exc_info=True
            )
            return None

    def _get_provider_config(self, provider_identifier: str) -> dict[str, Any]:
        """Get provider configuration from config port."""
        try:
            provider_instance_config = self._config_port.get_provider_instance_config(
                provider_identifier
            )
            return provider_instance_config.config if provider_instance_config else {}
        except Exception as e:
            self._logger.warning(
                "Could not get config for %s: %s", provider_identifier, e, exc_info=True
            )
            return {}
