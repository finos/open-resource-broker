"""Infrastructure resilience package - Integrated retry mechanisms."""

from .config import RetryConfig
from .exceptions import (
    CircuitBreakerOpenError,
    InvalidRetryStrategyError,
    MaxRetriesExceededError,
    RetryConfigurationError,
    RetryError,
)
from .retry_decorator import get_retry_config_for_service, retry
from .strategy import (
    CircuitBreakerStrategy,
    CircuitState,
    ExponentialBackoffStrategy,
    RetryStrategy,
)

__all__: list[str] = [
    # Main retry decorator
    "retry",
    "get_retry_config_for_service",
    # Configuration
    "RetryConfig",
    # Exceptions
    "RetryError",
    "MaxRetriesExceededError",
    "InvalidRetryStrategyError",
    "RetryConfigurationError",
    "CircuitBreakerOpenError",
    # Strategies
    "RetryStrategy",
    "ExponentialBackoffStrategy",
    "CircuitBreakerStrategy",
    "CircuitState",
]
