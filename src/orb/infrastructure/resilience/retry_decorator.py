"""Integrated retry decorator supporting multiple strategies."""

import time
from functools import wraps
from typing import Any, Callable, TypeVar

from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.resilience.exceptions import (
    CircuitBreakerOpenError,
    InvalidRetryStrategyError,
    MaxRetriesExceededError,
)
from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy
from orb.infrastructure.resilience.strategy.exponential import ExponentialBackoffStrategy

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
    count_per_call: bool = False,
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
        count_per_call: Count one circuit failure per exhausted call rather than per
            attempt (circuit breaker only)
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
                count_per_call=count_per_call,
            )
        else:
            raise InvalidRetryStrategyError(
                f"Unsupported retry strategy: {strategy}. Supported: exponential, circuit_breaker"
            )

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            """Execute function with retry logic."""
            attempt = 0
            last_exception: Exception | None = None
            gate = getattr(retry_strategy, "before_call", None)

            while True:
                # Gate every attempt, not just the first: a circuit opened
                # mid-retry (by this or another caller) stops the next attempt,
                # and a half-open probe is exactly one attempt.
                if gate is not None:
                    try:
                        gate()
                    except CircuitBreakerOpenError as open_error:
                        if last_exception is not None:
                            # Keep the real failure visible behind the open circuit.
                            raise open_error from last_exception
                        raise

                try:
                    result = func(*args, **kwargs)

                    # Record success for circuit breaker
                    if hasattr(retry_strategy, "record_success"):
                        retry_strategy.record_success()  # type: ignore[attr-defined]

                    # Log successful retry if this wasn't the first attempt
                    if attempt > 0:
                        logger.info(
                            "Operation succeeded after %s attempts: %s",
                            attempt + 1,
                            func.__name__,
                        )

                    return result

                except Exception as e:
                    last_exception = e
                    # Check if we should retry
                    try:
                        retryable = retry_strategy.should_retry(attempt, e)
                    except CircuitBreakerOpenError as open_error:
                        # Keep the real failure visible behind the open circuit.
                        raise open_error from e
                    if not retryable:
                        give_up = getattr(retry_strategy, "on_give_up", None)
                        if give_up is not None:
                            give_up(attempt, e)
                        if attempt >= max_attempts:
                            logger.error(
                                "Max retry attempts (%s) exceeded for %s: %s",
                                max_attempts,
                                func.__name__,
                                e,
                            )
                            raise MaxRetriesExceededError(attempt + 1, e)
                        else:
                            logger.error("Non-retryable error in %s: %s", func.__name__, e)
                            raise

                    # Calculate delay
                    delay = retry_strategy.get_delay(attempt)

                    # Handle retry event (logging, metrics)
                    retry_strategy.on_retry(attempt, e)

                    # Log retry attempt
                    logger.warning(
                        "Retry attempt %s/%s for %s after %.2fs delay. Error: %s",
                        attempt + 1,
                        max_attempts,
                        func.__name__,
                        delay,
                        e,
                    )

                    # Wait before retry
                    time.sleep(delay)
                    attempt += 1

        return wrapper

    return decorator
