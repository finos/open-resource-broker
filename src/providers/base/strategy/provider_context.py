"""Provider Context - Strategy pattern context for managing provider strategies.

This module implements the Context component of the Strategy pattern,
providing a integrated interface for executing operations across different
provider strategies while handling strategy selection, switching, and lifecycle.
"""

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, List, Optional

from src.domain.base.ports import LoggingPort
from src.providers.base.strategy.provider_strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderResult,
    ProviderStrategy,
)


@dataclass
class StrategyMetrics:
    """Metrics for tracking strategy performance and usage."""

    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    average_response_time_ms: float = 0.0
    last_used_time: Optional[float] = None
    health_check_count: int = 0
    last_health_check: Optional[float] = None

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_operations == 0:
            return 0.0
        return (self.successful_operations / self.total_operations) * 100.0

    def record_operation(self, success: bool, response_time_ms: float):
        """Record an operation execution."""
        self.total_operations += 1
        if success:
            self.successful_operations += 1
        else:
            self.failed_operations += 1

        # Update average response time
        if self.total_operations == 1:
            self.average_response_time_ms = response_time_ms
        else:
            self.average_response_time_ms = (
                self.average_response_time_ms * \
                    (self.total_operations - 1) + response_time_ms
            ) / self.total_operations

        self.last_used_time = time.time()


class ProviderContext:
    """
    Context class for managing provider strategies.

    This class implements the Context component of the Strategy pattern,
    providing a integrated interface for executing operations across different
    provider strategies. It handles strategy selection, lifecycle management,
    metrics collection, and error handling.

    Features:
    - Strategy registration and management
    - Automatic strategy selection based on capabilities
    - Health monitoring and failover
    - Performance metrics collection
    - Thread-safe operations
    - Context manager support
    """

    def __init__(self, logger: LoggingPort):
        """
        Initialize the provider context.

        Args:
            logger: Logging port for dependency injection
        """
        self._logger = logger
        self._strategies: Dict[str, ProviderStrategy] = {}
        self._strategy_metrics: Dict[str, StrategyMetrics] = {}
        self._current_strategy: Optional[ProviderStrategy] = None
        self._default_strategy_type: Optional[str] = None
        self._lock = Lock()
        self._initialized = False
        self._provider_selection_service: Optional[Any] = None

    @property
    def is_initialized(self) -> bool:
        """Check if context is initialized."""
        return self._initialized

    @property
    def current_strategy_type(self) -> Optional[str]:
        """Get the current strategy type."""
        if not self._current_strategy:
            return None

        # Find the full strategy identifier (e.g., "aws-aws-primary" instead of
        # just "aws")
        for strategy_id, strategy in self._strategies.items():
            if strategy == self._current_strategy:
                return strategy_id

        # Fallback to provider type if not found
        return self._current_strategy.provider_type

    @property
    def available_strategies(self) -> List[str]:
        """Get list of available strategy types."""
        return list(self._strategies.keys())

    def register_strategy(self, strategy: ProviderStrategy, instance_name: str = None) -> None:
        """
        Register a provider strategy.

        Args:
            strategy: The provider strategy to register
            instance_name: Optional instance name for unique identification

        Raises:
            ValueError: If strategy is invalid or already registered
        """
        if not isinstance(strategy, ProviderStrategy):
            raise ValueError("Strategy must implement ProviderStrategy interface")

        # Create unique strategy identifier
        base_type = strategy.provider_type
        if instance_name:
            strategy_type = f"{base_type}-{instance_name}"
        else:
            strategy_type = base_type

        with self._lock:
            if strategy_type in self._strategies:
                self._logger.debug(
                    f"Strategy {strategy_type} already registered, replacing")

            self._strategies[strategy_type] = strategy
            self._strategy_metrics[strategy_type] = StrategyMetrics()

            # Set as default if it's the first strategy
            if self._default_strategy_type is None:
                self._default_strategy_type = strategy_type
                self._current_strategy = strategy

            self._logger.debug(
                f"Loaded strategy for provider instance: {base_type}:{instance_name or 'default'}"
            )

    def unregister_strategy(self, strategy_type: str) -> bool:
        """
        Unregister a provider strategy.

        Args:
            strategy_type: Type of strategy to unregister

        Returns:
            True if strategy was unregistered, False if not found
        """
        with self._lock:
            if strategy_type not in self._strategies:
                return False

            strategy = self._strategies[strategy_type]

            # Clean up strategy resources
            try:
                strategy.cleanup()
            except Exception as e:
                self._logger.warning(f"Error cleaning up strategy {strategy_type}: {e}")

            # Remove from registry
            del self._strategies[strategy_type]
            del self._strategy_metrics[strategy_type]

            # Update current strategy if needed
            if self._current_strategy == strategy:
                self._current_strategy = None
                self._default_strategy_type = None

                # Set new default if other strategies exist
                if self._strategies:
                    new_default = next(iter(self._strategies.keys()))
                    self._default_strategy_type = new_default
                    self._current_strategy = self._strategies[new_default]

            self._logger.info(f"Unregistered provider strategy: {strategy_type}")
            return True

    def set_strategy(self, strategy_type: str) -> bool:
        """
        Set the current active strategy.

        Args:
            strategy_type: Type of strategy to activate

        Returns:
            True if strategy was set successfully, False otherwise
        """
        with self._lock:
            if strategy_type not in self._strategies:
                self._logger.error(f"Strategy {strategy_type} not found")
                return False

            strategy = self._strategies[strategy_type]

            # Initialize strategy if needed
            if not strategy.is_initialized:
                try:
                    if not strategy.initialize():
                        self._logger.error(
                            f"Failed to initialize strategy {strategy_type}")
                        return False
                except Exception as e:
                    self._logger.error(
                        f"Error initializing strategy {strategy_type}: {e}")
                    return False

            self._current_strategy = strategy
            self._logger.info(f"Set active strategy to: {strategy_type}")
            return True

    def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """
        Execute an operation using the current strategy.

        Args:
            operation: The operation to execute

        Returns:
            Result of the operation execution

        Raises:
            RuntimeError: If no strategy is available
            ValueError: If operation is invalid
        """
        # Trigger lazy loading if no strategies are available
        if not self._current_strategy and not self._strategies:
            self._trigger_lazy_loading()

        if not self._current_strategy:
            return ProviderResult.error_result(
                "No provider strategy available", "NO_STRATEGY_AVAILABLE"
            )

        strategy_type = self._current_strategy.provider_type
        start_time = time.time()

        try:
            # Check if strategy supports the operation
            capabilities = self._current_strategy.get_capabilities()
            if not capabilities.supports_operation(operation.operation_type):
                # Record failed operation for unsupported operation
                response_time_ms = (time.time() - start_time) * 1000
                with self._lock:
                    metrics = self._strategy_metrics[strategy_type]
                    metrics.record_operation(False, response_time_ms)

                return ProviderResult.error_result(
                    f"Strategy {strategy_type} does not support operation {operation.operation_type}",
                    "OPERATION_NOT_SUPPORTED",
                )

            # Execute the operation
            result = self._current_strategy.execute_operation(operation)

            # Record metrics
            response_time_ms = (time.time() - start_time) * 1000
            with self._lock:
                metrics = self._strategy_metrics[strategy_type]
                metrics.record_operation(result.success, response_time_ms)

            self._logger.debug(
                f"Operation {operation.operation_type} executed by {strategy_type}: "
                f"success={result.success}, time={response_time_ms:.2f}ms"
            )

            return result

        except Exception as e:
            # Record failed operation
            response_time_ms = (time.time() - start_time) * 1000
            with self._lock:
                metrics = self._strategy_metrics[strategy_type]
                metrics.record_operation(False, response_time_ms)

            self._logger.error(
                f"Error executing operation {operation.operation_type} with {strategy_type}: {e}"
            )
            return ProviderResult.error_result(
                f"Operation execution failed: {str(e)}", "EXECUTION_ERROR"
            )

    def execute_with_strategy(
        self, strategy_type: str, operation: ProviderOperation
    ) -> ProviderResult:
        """
        Execute an operation using a specific strategy.

        Args:
            strategy_type: Type of strategy to use
            operation: The operation to execute

        Returns:
            Result of the operation execution
        """
        strategy = self._strategies.get(strategy_type)
        if not strategy:
            return ProviderResult.error_result(
                f"Strategy {strategy_type} not found", "STRATEGY_NOT_FOUND"
            )

        # Initialize strategy if needed (architectural fix for reliability)
        if not strategy.is_initialized:
            try:
                if not strategy.initialize():
                    return ProviderResult.error_result(
                        f"Failed to initialize strategy {strategy_type}",
                        "STRATEGY_INITIALIZATION_FAILED",
                    )
            except Exception as e:
                return ProviderResult.error_result(
                    f"Error initializing strategy {strategy_type}: {str(e)}",
                    "STRATEGY_INITIALIZATION_ERROR",
                )

        start_time = time.time()

        try:
            # Check if strategy supports the operation
            capabilities = strategy.get_capabilities()
            if not capabilities.supports_operation(operation.operation_type):
                # Record failed operation for unsupported operation
                response_time_ms = (time.time() - start_time) * 1000
                with self._lock:
                    metrics = self._strategy_metrics[strategy_type]
                    metrics.record_operation(False, response_time_ms)

                return ProviderResult.error_result(
                    f"Strategy {strategy_type} does not support operation {operation.operation_type}",
                    "OPERATION_NOT_SUPPORTED",
                )

            # Execute the operation
            result = strategy.execute_operation(operation)

            # Record metrics
            response_time_ms = (time.time() - start_time) * 1000
            with self._lock:
                metrics = self._strategy_metrics[strategy_type]
                metrics.record_operation(result.success, response_time_ms)

            self._logger.debug(
                f"Operation {operation.operation_type} executed by {strategy_type}: "
                f"success={result.success}, time={response_time_ms:.2f}ms"
            )

            return result

        except Exception as e:
            # Record failed operation
            response_time_ms = (time.time() - start_time) * 1000
            with self._lock:
                metrics = self._strategy_metrics[strategy_type]
                metrics.record_operation(False, response_time_ms)

            self._logger.error(
                f"Error executing operation {operation.operation_type} with {strategy_type}: {e}"
            )
            return ProviderResult.error_result(
                f"Operation execution failed: {str(e)}", "EXECUTION_ERROR"
            )

    def get_strategy_capabilities(
        self, strategy_type: str = None
    ) -> Optional[ProviderCapabilities]:
        """
        Get capabilities of a specific strategy or current strategy.

        Args:
            strategy_type: Optional strategy type, uses current if None

        Returns:
            Strategy capabilities or None if strategy not found
        """
        if strategy_type is None:
            if not self._current_strategy:
                return None
            return self._current_strategy.get_capabilities()

        strategy = self._strategies.get(strategy_type)
        if not strategy:
            return None

        return strategy.get_capabilities()

    def check_strategy_health(self, strategy_type: str = None) -> Optional[ProviderHealthStatus]:
        """
        Check health of a specific strategy or current strategy.

        Args:
            strategy_type: Optional strategy type, uses current if None

        Returns:
            Health status or None if strategy not found
        """
        if strategy_type is None:
            if not self._current_strategy:
                return None
            strategy = self._current_strategy
            # Use the current strategy type property which returns the correct
            # identifier
            strategy_type = self.current_strategy_type
        else:
            strategy = self._strategies.get(strategy_type)
            if not strategy:
                return None

        try:
            health_status = strategy.check_health()

            # Update health check metrics using the correct strategy identifier
            if strategy_type and strategy_type in self._strategy_metrics:
                with self._lock:
                    metrics = self._strategy_metrics[strategy_type]
                    metrics.health_check_count += 1
                    metrics.last_health_check = time.time()

            return health_status

        except Exception as e:
            self._logger.error(
                f"Error checking health of strategy {strategy_type}: {e}")
            return ProviderHealthStatus.unhealthy(
                f"Health check failed: {str(e)}", {"exception": str(e)}
            )

    def get_strategy_metrics(self, strategy_type: str = None) -> Optional[StrategyMetrics]:
        """
        Get metrics for a specific strategy or current strategy.

        Args:
            strategy_type: Optional strategy type, uses current if None

        Returns:
            Strategy metrics or None if strategy not found
        """
        if strategy_type is None:
            if not self._current_strategy:
                return None
            # Use the current strategy type property which returns the correct
            # identifier
            strategy_type = self.current_strategy_type

        return self._strategy_metrics.get(strategy_type)

    def get_all_metrics(self) -> Dict[str, StrategyMetrics]:
        """Get metrics for all registered strategies."""
        with self._lock:
            return self._strategy_metrics.copy()

    def initialize(self) -> bool:
        """
        Initialize the provider context and all registered strategies.

        Returns:
            True if initialization successful, False otherwise
        """
        if self._initialized:
            return True

        # For lazy loading, don't trigger loading during initialize()
        # Only set up the lazy loading mechanism
        if hasattr(self, "_lazy_provider_loader") and self._lazy_provider_loader:
            self._logger.info(
                "Lazy loading configured - providers will load on first operation")
            self._initialized = True  # Mark as "ready for lazy loading"
            return True

        # For eager loading, proceed with normal initialization
        # Trigger lazy loading if no strategies are available
        if not self._strategies:
            self._trigger_lazy_loading()

        with self._lock:
            success_count = 0
            total_count = len(self._strategies)

            if total_count == 0:
                self._logger.error(
                    "Provider context initialization failed: no strategies available"
                )
                return False

            for strategy_type, strategy in self._strategies.items():
                try:
                    if strategy.initialize():
                        success_count += 1
                        self._logger.debug(f"Initialized strategy: {strategy_type}")
                    else:
                        self._logger.error(
                            f"Failed to initialize strategy: {strategy_type}")
                except Exception as e:
                    self._logger.error(
                        f"Error initializing strategy {strategy_type}: {e}")

            # Consider initialization successful if at least one strategy works
            self._initialized = success_count > 0

            if self._initialized:
                self._logger.info(
                    f"Provider context initialized: {success_count}/{total_count} strategies ready"
                )
            else:
                self._logger.error(
                    "Provider context initialization failed: no strategies available"
                )

            return self._initialized

    def _trigger_lazy_loading(self) -> None:
        """Trigger lazy loading of providers if available."""
        if hasattr(self, "_lazy_provider_loader") and self._lazy_provider_loader:
            try:
                self._logger.debug("Triggering lazy provider loading")
                self._lazy_provider_loader()
                # Remove the loader after use to prevent multiple calls
                self._lazy_provider_loader = None
            except Exception as e:
                self._logger.error(f"Failed to trigger lazy provider loading: {e}")

    def cleanup(self) -> None:
        """Clean up all registered strategies and resources."""
        with self._lock:
            for strategy_type, strategy in self._strategies.items():
                try:
                    strategy.cleanup()
                    self._logger.debug(f"Cleaned up strategy: {strategy_type}")
                except Exception as e:
                    self._logger.warning(
                        f"Error cleaning up strategy {strategy_type}: {e}")

            self._strategies.clear()
            self._strategy_metrics.clear()
            self._current_strategy = None
            self._default_strategy_type = None
            self._initialized = False

    def __enter__(self) -> "ProviderContext":
        """Context manager entry."""
        if not self._initialized and not self.initialize():
            raise RuntimeError("Failed to initialize provider context")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit with cleanup."""
        self.cleanup()
