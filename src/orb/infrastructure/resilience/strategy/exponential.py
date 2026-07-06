"""Exponential backoff retry strategy."""

from __future__ import annotations

import secrets

from orb.infrastructure.resilience.retry_classifier_registry import (
    is_non_retryable as _registry_is_non_retryable,
)


class ExponentialBackoffStrategy:
    """
    Exponential backoff retry strategy.

    This strategy implements exponential backoff with jitter, matching the
    current usage patterns in the codebase (max_retries=3, base_delay=1.0).
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: bool = True,
    ) -> None:
        """
        Initialize exponential backoff strategy.

        Args:
            max_attempts: Maximum number of retry attempts
            base_delay: Base delay in seconds
            max_delay: Maximum delay in seconds
            jitter: Whether to add jitter to delays
        """
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

    def should_retry(self, attempt: int, exception: Exception) -> bool:
        """
        Determine if operation should be retried.

        Args:
            attempt: Current attempt number (0-based)
            exception: Exception that occurred

        Returns:
            True if operation should be retried, False otherwise
        """
        # Check if we've exceeded max attempts
        if attempt >= self.max_attempts:
            return False

        # Fast-fail when a provider-registered classifier flags the exception
        # as a permanent client error (e.g. 4xx from a provider SDK).  The
        # registry is consulted so this layer carries no provider-specific
        # imports.
        if _registry_is_non_retryable(exception):
            return False

        return True

    def get_delay(self, attempt: int) -> float:
        """
        Calculate exponential backoff delay with optional jitter.

        Args:
            attempt: Current attempt number (0-based)

        Returns:
            Delay in seconds before next retry
        """
        # Calculate exponential delay: base_delay * (2 ^ attempt)
        delay = self.base_delay * (2**attempt)

        # Cap at maximum delay
        delay = min(delay, self.max_delay)

        # Add jitter if enabled (randomize between 50% and 100% of calculated delay)
        if self.jitter:
            # Use secrets for cryptographically secure randomness
            random_float = secrets.SystemRandom().random() * 0.5  # Range from 0 to 0.5
            delay *= 0.5 + random_float

        return delay

    def on_retry(self, attempt: int, exception: Exception) -> None:
        """
        Handle retry event (logging, metrics).

        This is a hook for subclasses to override — e.g. AWSRetryStrategy
        logs the attempt number and exception details here.

        Args:
            attempt: Current attempt number (0-based)
            exception: Exception that occurred
        """
        # Base implementation is intentionally empty — subclasses override this
        # to add logging, metrics, or other side effects on each retry.
