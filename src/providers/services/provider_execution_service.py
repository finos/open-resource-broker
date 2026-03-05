"""Provider Execution Service - Registry-based strategy execution."""

import time
from typing import Any, Optional

from domain.base.ports import ConfigurationPort, LoggingPort
from monitoring.metrics import MetricsCollector
from providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderResult,
    ProviderStrategy,
)
from providers.registry import get_provider_registry


class ProviderExecutionService:
    """
    Service for executing operations with provider strategies using Provider Registry.

    Replaces ProviderContext with a cleaner registry-based approach.
    """

    def __init__(
        self,
        logger: LoggingPort,
        config_port: ConfigurationPort,
        metrics: Optional[MetricsCollector] = None,
    ) -> None:
        """Initialize the provider execution service."""
        self._logger = logger
        self._config_port = config_port
        self._registry = get_provider_registry()
        self._metrics = metrics or MetricsCollector(config={"METRICS_ENABLED": True})

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
                response_time_ms = (time.time() - start_time) * 1000
                if self._metrics:
                    op_base = (
                        f"provider.{provider_identifier}.{operation.operation_type.name.lower()}"
                    )
                    self._metrics.increment_counter(f"{op_base}.error_total")
                    self._metrics.record_time(f"{op_base}.duration", response_time_ms / 1000.0)
                return ProviderResult.error_result(
                    f"Strategy {provider_identifier} does not support operation {operation.operation_type}",
                    "OPERATION_NOT_SUPPORTED",
                )

            # Execute the operation
            result = await strategy.execute_operation(operation)

            # Record metrics
            response_time_ms = (time.time() - start_time) * 1000
            if self._metrics:
                op_base = f"provider.{provider_identifier}.{operation.operation_type.name.lower()}"
                if result.success:
                    self._metrics.increment_counter(f"{op_base}.success_total")
                else:
                    self._metrics.increment_counter(f"{op_base}.error_total")
                self._metrics.record_time(f"{op_base}.duration", response_time_ms / 1000.0)

            self._logger.debug(
                "Operation %s executed by %s: success=%s, time=%.2fms",
                operation.operation_type,
                provider_identifier,
                result.success,
                response_time_ms,
            )

            return result

        except Exception as e:
            response_time_ms = (time.time() - start_time) * 1000
            if self._metrics:
                op_base = f"provider.{provider_identifier}.{operation.operation_type.name.lower()}"
                self._metrics.increment_counter(f"{op_base}.error_total")
                self._metrics.record_time(f"{op_base}.duration", response_time_ms / 1000.0)

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
            self._metrics.increment_counter("provider_strategy_health_checks_total", 1.0)
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
