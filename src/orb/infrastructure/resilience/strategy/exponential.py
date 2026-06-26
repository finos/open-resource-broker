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

    # HTTP status codes that indicate a permanent client error and must not be
    # retried regardless of the retry budget.  400 (bad request), 403 (RBAC
    # denied), 404 (not found), 409 (conflict / already exists), 410 (gone —
    # the watcher path resets rv independently; handler-side 410s must not
    # spin), and 422 (unprocessable entity / validation failure).
    _NON_RETRYABLE_K8S_STATUSES: frozenset[int] = frozenset({400, 403, 404, 409, 410, 422})

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

        # Fast-fail on non-retryable Kubernetes API status codes.  The import
        # is lazy so the generic resilience layer has no hard dependency on the
        # kubernetes SDK — it only runs this branch when the SDK is installed
        # and the exception is actually an ApiException.
        try:
            from kubernetes.client.exceptions import ApiException  # noqa: PLC0415

            if isinstance(exception, ApiException):
                status = getattr(exception, "status", None)
                if status in self._NON_RETRYABLE_K8S_STATUSES:
                    return False
        except ImportError:
            pass

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
