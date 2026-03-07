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
    "CircuitBreakerOpenError",
    "CircuitBreakerStrategy",
    "CircuitState",
    "ExponentialBackoffStrategy",
    "InvalidRetryStrategyError",
    "MaxRetriesExceededError",
    # Configuration
    "RetryConfig",
    "RetryConfigurationError",
    # Exceptions
    "RetryError",
    # Strategies
    "RetryStrategy",
    "get_retry_config_for_service",
    # Main retry decorator
    "retry",
]
