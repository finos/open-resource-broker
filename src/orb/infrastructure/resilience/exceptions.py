"""Retry-specific exceptions."""

from orb.application.ports.resilience_port import (
    CircuitBreakerOpenError as CircuitBreakerOpenPortError,
)


class RetryError(Exception):
    """Base exception for retry-related errors."""


class MaxRetriesExceededError(RetryError):
    """Exception raised when maximum retry attempts are exceeded."""

    def __init__(self, attempts: int, last_exception: Exception) -> None:
        """
        Initialize MaxRetriesExceededError.

        Args:
            attempts: Number of attempts made
            last_exception: The last exception that occurred
        """
        self.attempts = attempts
        self.last_exception = last_exception
        super().__init__(
            f"Maximum retry attempts ({attempts}) exceeded. Last error: {last_exception}"
        )


class InvalidRetryStrategyError(RetryError):
    """Exception raised when an invalid retry strategy is specified."""

    def __init__(self, strategy: str) -> None:
        """
        Initialize InvalidRetryStrategyError.

        Args:
            strategy: The invalid strategy name
        """
        self.strategy = strategy
        super().__init__(f"Invalid retry strategy: {strategy}")


class RetryConfigurationError(RetryError):
    """Exception raised when retry configuration is invalid."""


class CircuitBreakerOpenError(RetryError, CircuitBreakerOpenPortError):
    """Exception raised when circuit breaker is in OPEN state.

    Subclasses the application-layer ``CircuitBreakerOpenError`` port base so
    the orchestration service can catch open-circuit failures without importing
    this infrastructure module.
    """

    def __init__(self, service_name: str, failure_count: int, last_failure_time: float) -> None:
        """
        Initialize CircuitBreakerOpenError.

        Args:
            service_name: Name of the service with open circuit
            failure_count: Number of failures that caused circuit to open
            last_failure_time: Timestamp of last failure
        """
        self.service_name = service_name
        self.failure_count = failure_count
        self.last_failure_time = last_failure_time
        super().__init__(
            f"Circuit breaker is OPEN for service '{service_name}' "
            f"after {failure_count} failures. Failing fast to prevent cascading failures."
        )
