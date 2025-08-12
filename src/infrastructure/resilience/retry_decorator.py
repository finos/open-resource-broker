"""Integrated retry decorator supporting multiple strategies."""

import time
from functools import wraps
from typing import Any, Callable, TypeVar

from src.infrastructure.logging.logger import get_logger
from src.infrastructure.resilience.exceptions import (
    InvalidRetryStrategyError,
    MaxRetriesExceededError,
)
from src.infrastructure.resilience.strategies.circuit_breaker import (
    CircuitBreakerStrategy,
)
from src.infrastructure.resilience.strategies.exponential import (
    ExponentialBackoffStrategy,
)

T = TypeVar("T")
logger = get_logger(__name__)


def retry(
    strategy: str = "exponential",
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    service: str = "default",
    # Circuit breaker specific parameters
    failure_threshold: int = 5,
    reset_timeout: int = 60,
    half_open_timeout: int = 30,
    **kwargs,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Retry decorator supporting multiple strategies.

    Args:
        strategy: Retry strategy ("exponential", "circuit_breaker", "adaptive")
        max_attempts: Maximum retry attempts
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        jitter: Whether to add jitter to delays
        service: Service name for error classification and circuit breaker identification
        failure_threshold: Number of failures before opening circuit (circuit breaker only)
        reset_timeout: Seconds to wait before transitioning to half-open (circuit breaker only)
        half_open_timeout: Seconds to wait in half-open before closing (circuit breaker only)
        **kwargs: Additional strategy-specific parameters

    Returns:
        Decorated function with retry behavior

    Raises:
        InvalidRetryStrategyError: If strategy is not supported
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        """Apply retry strategy to function."""
        # Create strategy instance
        if strategy == "exponential":
            retry_strategy = ExponentialBackoffStrategy(
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
                service=service,
            )
        elif strategy == "circuit_breaker":
            retry_strategy = CircuitBreakerStrategy(
                service_name=service,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
                failure_threshold=failure_threshold,
                reset_timeout=reset_timeout,
                half_open_timeout=half_open_timeout,
            )
        else:
            raise InvalidRetryStrategyError(
                f"Unsupported retry strategy: {strategy}. Supported: exponential, circuit_breaker"
            )

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            """Execute function with retry logic."""
            attempt = 0

            while True:
                try:
                    result = func(*args, **kwargs)

                    # Record success for circuit breaker
                    if hasattr(retry_strategy, "record_success"):
                        retry_strategy.record_success()

                    # Log successful retry if this wasn't the first attempt
                    if attempt > 0:
                        logger.info(
                            f"Operation succeeded after { attempt + 1} attempts: { func.__name__}"
                        )

                    return result

                except Exception as e:
                    pass

                    # Check if we should retry
                    if not retry_strategy.should_retry(attempt, e):
                        if attempt >= max_attempts:
                            logger.error(
                                f"Max retry attempts ({max_attempts}) exceeded for { func.__name__}: {e}"
                            )
                            raise MaxRetriesExceededError(attempt + 1, e)
                        else:
                            logger.error(f"Non-retryable error in {func.__name__}: {e}")
                            raise e

                    # Calculate delay
                    delay = retry_strategy.get_delay(attempt)

                    # Handle retry event (logging, metrics)
                    retry_strategy.on_retry(attempt, e)

                    # Log retry attempt
                    logger.warning(
                        f"Retry attempt {attempt + 1}/{max_attempts} for {func.__name__} "
                        f"after {delay:.2f}s delay. Error: {e}"
                    )

                    # Wait before retry
                    time.sleep(delay)
                    attempt += 1

        return wrapper

    return decorator


def get_retry_config_for_service(service: str) -> dict:
    """
    Get default retry configuration for a specific AWS service.

    Args:
        service: AWS service name

    Returns:
        Dictionary with retry configuration parameters
    """
    service_configs = {
        "ec2": {
            "max_attempts": 3,
            "base_delay": 1.0,
            "max_delay": 30.0,
            "jitter": True,
        },
        "dynamodb": {
            "max_attempts": 5,
            "base_delay": 0.5,
            "max_delay": 20.0,
            "jitter": True,
        },
        "s3": {"max_attempts": 4, "base_delay": 0.5, "max_delay": 15.0, "jitter": True},
    }

    return service_configs.get(
        service,
        {"max_attempts": 3, "base_delay": 1.0, "max_delay": 60.0, "jitter": True},
    )
