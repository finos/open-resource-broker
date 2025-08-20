"""
Fallback Provider Strategy - Resilience and failover for provider operations.

This module implements fallback and resilience patterns for provider strategies,
enabling automatic failover, circuit breaker patterns, and graceful degradation
when primary providers fail or become unavailable.
"""

import time
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Any, Dict, List, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from infrastructure.interfaces.provider import BaseProviderConfig
from providers.base.strategy.provider_strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)


@injectable
class FallbackMode(str, Enum):
    """Modes for fallback strategy behavior."""

    IMMEDIATE = "immediate"  # Fallback immediately on any failure
    CIRCUIT_BREAKER = "circuit_breaker"  # Use circuit breaker pattern
    RETRY_THEN_FALLBACK = "retry_then_fallback"  # Retry primary, then fallback
    HEALTH_BASED = "health_based"  # Fallback based on health checks


@injectable
class CircuitState(str, Enum):
    """States for circuit breaker pattern."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit open, using fallback
    HALF_OPEN = "half_open"  # Testing if primary is recovered


@dataclass
class FallbackConfig:
    """Configuration for fallback strategy behavior."""

    mode: FallbackMode = FallbackMode.IMMEDIATE
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    circuit_breaker_threshold: int = 5  # Failures before opening circuit
    circuit_breaker_timeout_seconds: float = 60.0  # Time before trying half-open
    health_check_interval_seconds: float = 30.0
    fallback_timeout_seconds: float = 30.0
    enable_graceful_degradation: bool = True

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be non-negative")
        if self.circuit_breaker_threshold < 1:
            raise ValueError("circuit_breaker_threshold must be at least 1")
        if self.circuit_breaker_timeout_seconds <= 0:
            raise ValueError("circuit_breaker_timeout_seconds must be positive")


@dataclass
class CircuitBreakerState:
    """State tracking for circuit breaker pattern."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    total_requests: int = 0
    successful_requests: int = 0

    @property
    def failure_rate(self) -> float:
        """Calculate current failure rate."""
        if self.total_requests == 0:
            return 0.0
        return (self.total_requests - self.successful_requests) / self.total_requests

    def record_success(self) -> None:
        """Record a successful request."""
        self.successful_requests += 1
        self.total_requests += 1
        self.last_success_time = time.time()
        self.failure_count = 0  # Reset failure count on success

    def record_failure(self) -> None:
        """Record a failed request."""
        self.total_requests += 1
        self.failure_count += 1
        self.last_failure_time = time.time()


@injectable
class FallbackProviderStrategy(ProviderStrategy):
    """
    Fallback provider strategy with resilience and failover capabilities.

    This class implements various resilience patterns including:
    - Automatic failover to backup providers
    - Circuit breaker pattern for fault tolerance
    - Retry mechanisms with exponential backoff
    - Health-based provider selection
    - Graceful degradation when all providers fail

    Features:
    - Multiple fallback modes (immediate, circuit breaker, retry-based)
    - Configurable retry policies and timeouts
    - Circuit breaker with automatic recovery testing
    - Health monitoring and automatic failover
    - Performance metrics and failure tracking
    - Thread-safe state management
    """

    def __init__(
        self,
        logger: LoggingPort,
        primary_strategy: ProviderStrategy,
        fallback_strategies: List[ProviderStrategy],
        config: FallbackConfig = None,
    ) -> None:
        """
        Initialize fallback provider strategy.

        Args:
            primary_strategy: Primary provider strategy
            fallback_strategies: List of fallback strategies in priority order
            config: Fallback configuration
            logger: Optional logger instance

        Raises:
            ValueError: If primary strategy is None or fallback list is empty
        """
        if primary_strategy is None:
            raise ValueError("Primary strategy is required")
        if not fallback_strategies:
            raise ValueError("At least one fallback strategy is required")

        # Create a dummy config for the parent class

        dummy_config = BaseProviderConfig(provider_type="fallback")
        super().__init__(dummy_config)

        self._primary_strategy = primary_strategy
        self._fallback_strategies = fallback_strategies
        self._config = config or FallbackConfig()
        self._logger = logger

        # Circuit breaker state
        self._circuit_state = CircuitBreakerState()
        self._lock = Lock()

        # Health monitoring
        self._last_health_check = 0.0
        self._primary_healthy = True

        # Current active strategy
        self._current_strategy = primary_strategy

    @property
    def provider_type(self) -> str:
        """Get the provider type identifier."""
        primary_type = self._primary_strategy.provider_type
        fallback_types = [s.provider_type for s in self._fallback_strategies]
        return f"fallback({primary_type}->[{','.join(fallback_types)}])"

    @property
    def primary_strategy(self) -> ProviderStrategy:
        """Get the primary strategy."""
        return self._primary_strategy

    @property
    def fallback_strategies(self) -> List[ProviderStrategy]:
        """Get the fallback strategies."""
        return self._fallback_strategies.copy()

    @property
    def current_strategy(self) -> ProviderStrategy:
        """Get the currently active strategy."""
        return self._current_strategy

    @property
    def circuit_state(self) -> CircuitState:
        """Get the current circuit breaker state."""
        return self._circuit_state.state

    @property
    def circuit_metrics(self) -> Dict[str, Any]:
        """Get circuit breaker metrics."""
        with self._lock:
            return {
                "state": self._circuit_state.state.value,
                "failure_count": self._circuit_state.failure_count,
                "failure_rate": self._circuit_state.failure_rate,
                "total_requests": self._circuit_state.total_requests,
                "successful_requests": self._circuit_state.successful_requests,
                "last_failure_time": self._circuit_state.last_failure_time,
                "last_success_time": self._circuit_state.last_success_time,
            }

    def initialize(self) -> bool:
        """
        Initialize primary and fallback strategies.

        Returns:
            True if at least one strategy initializes successfully
        """
        if self._initialized:
            return True

        self._self._logger.info("Initializing fallback strategy")

        success_count = 0

        # Initialize primary strategy
        try:
            if not self._primary_strategy.is_initialized:
                if self._primary_strategy.initialize():
                    success_count += 1
                    self._self._logger.info(
                        "Primary strategy initialized: %s", self._primary_strategy.provider_type
                    )
                else:
                    self._self._logger.error(
                        "Failed to initialize primary strategy: %s",
                        self._primary_strategy.provider_type,
                    )
            else:
                success_count += 1
                self._self._logger.debug(
                    "Primary strategy already initialized: %s", self._primary_strategy.provider_type
                )
        except Exception as e:
            self._self._logger.error("Error initializing primary strategy: %s", e)

        # Initialize fallback strategies
        for i, strategy in enumerate(self._fallback_strategies):
            try:
                if not strategy.is_initialized:
                    if strategy.initialize():
                        success_count += 1
                        self._self._logger.info(
                            "Fallback strategy %s initialized: %s", i + 1, strategy.provider_type
                        )
                    else:
                        self._self._logger.error(
                            "Failed to initialize fallback strategy %s: %s",
                            i + 1,
                            strategy.provider_type,
                        )
                else:
                    success_count += 1
                    self._self._logger.debug(
                        "Fallback strategy %s already initialized: %s",
                        i + 1,
                        strategy.provider_type,
                    )
            except Exception as e:
                self._self._logger.error("Error initializing fallback strategy %s: %s", i + 1, e)

        # Consider initialization successful if at least one strategy works
        self._initialized = success_count > 0

        if self._initialized:
            total_strategies = 1 + len(self._fallback_strategies)
            self._self._logger.info(
                "Fallback strategy initialized: %s/%s strategies ready",
                success_count,
                total_strategies,
            )
        else:
            self._self._logger.error(
                "Fallback strategy initialization failed: no strategies available"
            )

        return self._initialized

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """
        Execute operation with fallback logic.

        Args:
            operation: The operation to execute

        Returns:
            Result from primary or fallback strategy
        """
        if not self._initialized:
            return ProviderResult.error_result(
                "Fallback strategy not initialized", "NOT_INITIALIZED"
            )

        start_time = time.time()

        try:
            # Check if we need to update health status
            self._update_health_status()

            # Execute based on fallback mode
            if self._config.mode == FallbackMode.CIRCUIT_BREAKER:
                result = await self._execute_with_circuit_breaker(operation)
            elif self._config.mode == FallbackMode.RETRY_THEN_FALLBACK:
                result = await self._execute_with_retry_fallback(operation)
            elif self._config.mode == FallbackMode.HEALTH_BASED:
                result = await self._execute_health_based(operation)
            else:  # IMMEDIATE
                result = await self._execute_immediate_fallback(operation)

            # Add execution metadata
            total_time_ms = (time.time() - start_time) * 1000
            result.metadata.update(
                {
                    "fallback_mode": self._config.mode.value,
                    "total_execution_time_ms": total_time_ms,
                    "active_strategy": self._current_strategy.provider_type,
                    "circuit_state": self._circuit_state.state.value,
                }
            )

            return result

        except Exception as e:
            total_time_ms = (time.time() - start_time) * 1000
            self._self._logger.error(
                "Fallback operation %s failed: %s", operation.operation_type, e
            )
            return ProviderResult.error_result(
                f"Fallback operation failed: {str(e)}",
                "FALLBACK_EXECUTION_ERROR",
                {"total_execution_time_ms": total_time_ms},
            )

    async def _execute_with_circuit_breaker(self, operation: ProviderOperation) -> ProviderResult:
        """Execute operation using circuit breaker pattern."""
        with self._lock:
            current_time = time.time()

            # Check circuit state
            if self._circuit_state.state == CircuitState.OPEN:
                # Check if we should try half-open
                if (
                    self._circuit_state.last_failure_time
                    and current_time - self._circuit_state.last_failure_time
                    >= self._config.circuit_breaker_timeout_seconds
                ):
                    self._circuit_state.state = CircuitState.HALF_OPEN
                    self._self._logger.info("Circuit breaker moving to half-open state")
                else:
                    # Circuit is open, use fallback immediately
                    return await self._execute_fallback_chain(operation)

            # Try primary strategy
            try:
                result = await self._primary_strategy.execute_operation(operation)

                if result.success:
                    # Success - record and potentially close circuit
                    self._circuit_state.record_success()
                    if self._circuit_state.state == CircuitState.HALF_OPEN:
                        self._circuit_state.state = CircuitState.CLOSED
                        self._self._logger.info(
                            "Circuit breaker closed - primary strategy recovered"
                        )
                    self._current_strategy = self._primary_strategy
                    return result
                else:
                    # Failure - record and potentially open circuit
                    self._circuit_state.record_failure()
                    if self._circuit_state.failure_count >= self._config.circuit_breaker_threshold:
                        self._circuit_state.state = CircuitState.OPEN
                        self._self._logger.warning(
                            "Circuit breaker opened after %s failures",
                            self._circuit_state.failure_count,
                        )

                    # Try fallback
                    return await self._execute_fallback_chain(operation)

            except Exception as e:
                # Exception - record failure and try fallback
                self._circuit_state.record_failure()
                if self._circuit_state.failure_count >= self._config.circuit_breaker_threshold:
                    self._circuit_state.state = CircuitState.OPEN
                    self._self._logger.warning("Circuit breaker opened after exception: %s", e)

                return await self._execute_fallback_chain(operation)

    async def _execute_with_retry_fallback(self, operation: ProviderOperation) -> ProviderResult:
        """Execute operation with retry then fallback."""
        last_error = None

        # Try primary strategy with retries
        for attempt in range(self._config.max_retries + 1):
            try:
                result = await self._primary_strategy.execute_operation(operation)

                if result.success:
                    self._current_strategy = self._primary_strategy
                    return result
                else:
                    last_error = result.error_message
                    if attempt < self._config.max_retries:
                        self._self._logger.debug(
                            "Primary strategy failed, retrying in %ss (attempt %s)",
                            self._config.retry_delay_seconds,
                            attempt + 1,
                        )
                        time.sleep(self._config.retry_delay_seconds)

            except Exception as e:
                last_error = str(e)
                if attempt < self._config.max_retries:
                    self._self._logger.debug(
                        "Primary strategy exception, retrying in %ss: %s",
                        self._config.retry_delay_seconds,
                        e,
                    )
                    time.sleep(self._config.retry_delay_seconds)

        # Primary failed after retries, try fallback
        self._self._logger.warning(
            "Primary strategy failed after %s retries: %s", self._config.max_retries, last_error
        )
        return await self._execute_fallback_chain(operation)

    async def _execute_health_based(self, operation: ProviderOperation) -> ProviderResult:
        """Execute operation based on health status."""
        # Check primary health
        if self._primary_healthy:
            try:
                result = await self._primary_strategy.execute_operation(operation)
                if result.success:
                    self._current_strategy = self._primary_strategy
                    return result
                else:
                    # Mark as potentially unhealthy and try fallback
                    self._primary_healthy = False
                    return await self._execute_fallback_chain(operation)
            except Exception as e:
                self._primary_healthy = False
                self._self._logger.warning("Primary strategy failed, marking unhealthy: %s", e)
                return await self._execute_fallback_chain(operation)
        else:
            # Primary is unhealthy, use fallback directly
            return await self._execute_fallback_chain(operation)

    async def _execute_immediate_fallback(self, operation: ProviderOperation) -> ProviderResult:
        """Execute operation with immediate fallback on any failure."""
        try:
            result = await self._primary_strategy.execute_operation(operation)
            if result.success:
                self._current_strategy = self._primary_strategy
                return result
            else:
                return await self._execute_fallback_chain(operation)
        except Exception as e:
            self._self._logger.debug("Primary strategy failed, trying fallback: %s", e)
            return await self._execute_fallback_chain(operation)

    async def _execute_fallback_chain(self, operation: ProviderOperation) -> ProviderResult:
        """Execute operation through the fallback chain."""
        last_error = None

        for i, fallback_strategy in enumerate(self._fallback_strategies):
            try:
                self._self._logger.debug(
                    "Trying fallback strategy %s: %s", i + 1, fallback_strategy.provider_type
                )
                result = await fallback_strategy.execute_operation(operation)

                if result.success:
                    self._current_strategy = fallback_strategy
                    self._self._logger.info(
                        "Fallback strategy %s succeeded: %s", i + 1, fallback_strategy.provider_type
                    )
                    return result
                else:
                    last_error = result.error_message
                    self._self._logger.debug("Fallback strategy %s failed: %s", i + 1, last_error)

            except Exception as e:
                last_error = str(e)
                self._self._logger.debug("Fallback strategy %s exception: %s", i + 1, e)

        # All strategies failed
        if self._config.enable_graceful_degradation:
            return self._graceful_degradation(operation, last_error)
        else:
            return ProviderResult.error_result(
                f"All fallback strategies failed. Last error: {last_error}",
                "ALL_STRATEGIES_FAILED",
                {"attempted_strategies": len(self._fallback_strategies) + 1},
            )

    def _graceful_degradation(
        self, operation: ProviderOperation, last_error: str
    ) -> ProviderResult:
        """Provide graceful degradation when all strategies fail."""
        # Provide minimal functionality based on operation type
        if operation.operation_type == ProviderOperationType.HEALTH_CHECK:
            return ProviderResult.success_result(
                {
                    "is_healthy": False,
                    "status": "degraded",
                    "message": "All providers failed",
                },
                {"degraded": True, "last_error": last_error},
            )
        elif operation.operation_type == ProviderOperationType.GET_AVAILABLE_TEMPLATES:
            return ProviderResult.success_result(
                {
                    "templates": [],
                    "message": "No templates available - all providers failed",
                },
                {"degraded": True, "last_error": last_error},
            )
        else:
            return ProviderResult.error_result(
                f"Operation not available in degraded mode: {operation.operation_type}",
                "DEGRADED_MODE",
                {"degraded": True, "last_error": last_error},
            )

    def _update_health_status(self) -> None:
        """Update health status of primary strategy if needed."""
        current_time = time.time()
        if current_time - self._last_health_check >= self._config.health_check_interval_seconds:
            try:
                health = self._primary_strategy.check_health()
                self._primary_healthy = health.is_healthy
                self._last_health_check = current_time

                if not self._primary_healthy:
                    self._self._logger.debug(
                        "Primary strategy unhealthy: %s", health.status_message
                    )

            except Exception as e:
                self._primary_healthy = False
                self._last_health_check = current_time
                self._self._logger.debug("Primary strategy health check failed: %s", e)

    def get_capabilities(self) -> ProviderCapabilities:
        """
        Get combined capabilities from primary and fallback strategies.

        Returns:
            Merged capabilities with fallback information
        """
        # Start with primary capabilities
        try:
            primary_capabilities = self._primary_strategy.get_capabilities()
            all_operations = set(primary_capabilities.supported_operations)
            combined_features = primary_capabilities.features.copy()
            combined_limitations = primary_capabilities.limitations.copy()
        except Exception as e:
            self._self._logger.warning("Error getting primary capabilities: %s", e)
            all_operations = set()
            combined_features = {}
            combined_limitations = {}

        # Add fallback capabilities
        fallback_info = []
        for i, strategy in enumerate(self._fallback_strategies):
            try:
                capabilities = strategy.get_capabilities()
                all_operations.update(capabilities.supported_operations)
                fallback_info.append(
                    {
                        "provider_type": strategy.provider_type,
                        "operations": [op.value for op in capabilities.supported_operations],
                        "features": capabilities.features,
                    }
                )
            except Exception as e:
                self._self._logger.warning("Error getting fallback %s capabilities: %s", i + 1, e)

        # Add fallback-specific features
        combined_features.update(
            {
                "fallback_enabled": True,
                "fallback_mode": self._config.mode.value,
                "circuit_breaker": self._config.mode == FallbackMode.CIRCUIT_BREAKER,
                "retry_support": self._config.max_retries > 0,
                "graceful_degradation": self._config.enable_graceful_degradation,
                "fallback_strategies": fallback_info,
            }
        )

        return ProviderCapabilities(
            provider_type=self.provider_type,
            supported_operations=list(all_operations),
            features=combined_features,
            limitations=combined_limitations,
            performance_metrics={
                "circuit_breaker_threshold": self._config.circuit_breaker_threshold,
                "max_retries": self._config.max_retries,
                "retry_delay_seconds": self._config.retry_delay_seconds,
            },
        )

    def check_health(self) -> ProviderHealthStatus:
        """
        Check health of primary and fallback strategies.

        Returns:
            Aggregated health status with fallback information
        """
        start_time = time.time()
        health_details = {}

        # Check primary health
        try:
            primary_health = self._primary_strategy.check_health()
            health_details["primary"] = {
                "provider": self._primary_strategy.provider_type,
                "healthy": primary_health.is_healthy,
                "message": primary_health.status_message,
            }
            primary_healthy = primary_health.is_healthy
        except Exception as e:
            health_details["primary"] = {
                "provider": self._primary_strategy.provider_type,
                "healthy": False,
                "error": str(e),
            }
            primary_healthy = False

        # Check fallback health
        fallback_healthy_count = 0
        for i, strategy in enumerate(self._fallback_strategies):
            try:
                health = strategy.check_health()
                health_details[f"fallback_{i+1}"] = {
                    "provider": strategy.provider_type,
                    "healthy": health.is_healthy,
                    "message": health.status_message,
                }
                if health.is_healthy:
                    fallback_healthy_count += 1
            except Exception as e:
                health_details[f"fallback_{i+1}"] = {
                    "provider": strategy.provider_type,
                    "healthy": False,
                    "error": str(e),
                }

        response_time_ms = (time.time() - start_time) * 1000

        # Determine overall health
        total_fallbacks = len(self._fallback_strategies)
        if primary_healthy:
            status_message = f"Fallback strategy healthy - Primary operational, {fallback_healthy_count}/{total_fallbacks} fallbacks ready"
            return ProviderHealthStatus.healthy(status_message, response_time_ms)
        elif fallback_healthy_count > 0:
            status_message = f"Fallback strategy degraded - Primary down, {fallback_healthy_count}/{total_fallbacks} fallbacks available"
            return ProviderHealthStatus.healthy(status_message, response_time_ms)
        else:
            status_message = f"Fallback strategy unhealthy - Primary and all {total_fallbacks} fallbacks unavailable"
            return ProviderHealthStatus.unhealthy(status_message, health_details)

    def cleanup(self) -> None:
        """Clean up primary and fallback strategies."""
        try:
            # Clean up primary strategy
            self._primary_strategy.cleanup()
            self._self._logger.debug(
                "Cleaned up primary strategy: %s", self._primary_strategy.provider_type
            )

            # Clean up fallback strategies
            for i, strategy in enumerate(self._fallback_strategies):
                try:
                    strategy.cleanup()
                    self._self._logger.debug(
                        "Cleaned up fallback strategy %s: %s", i + 1, strategy.provider_type
                    )
                except Exception as e:
                    self._self._logger.warning(
                        "Error cleaning up fallback strategy %s: %s", i + 1, e
                    )

            self._initialized = False

        except Exception as e:
            self._self._logger.warning("Error during fallback strategy cleanup: %s", e)

    def __str__(self) -> str:
        """Return string representation for debugging."""
        return f"FallbackProviderStrategy(primary={self._primary_strategy.provider_type}, fallbacks={len(self._fallback_strategies)}, mode={self._config.mode.value})"

    def __repr__(self) -> str:
        """Return detailed representation for debugging."""
        fallback_types = [s.provider_type for s in self._fallback_strategies]
        return (
            f"FallbackProviderStrategy("
            f"primary={self._primary_strategy.provider_type}, "
            f"fallbacks={fallback_types}, "
            f"mode={self._config.mode.value}, "
            f"circuit_state={self._circuit_state.state.value}"
            f")"
        )
