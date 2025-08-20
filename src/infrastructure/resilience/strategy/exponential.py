"""Exponential backoff retry strategy."""

import secrets


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
        service: str = "ec2",
    ) -> None:
        """
        Initialize exponential backoff strategy.

        Args:
            max_attempts: Maximum number of retry attempts
            base_delay: Base delay in seconds
            max_delay: Maximum delay in seconds
            jitter: Whether to add jitter to delays
            service: AWS service name for error classification
        """
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self.service = service

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

        # Generic retry logic - provider-specific logic should be in provider layer
        # For now, retry on any exception (can be overridden by provider strategies)
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

        Args:
            attempt: Current attempt number (0-based)
            exception: Exception that occurred
        """
        # This method can be extended for metrics collection in the future
