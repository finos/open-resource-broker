"""Base strategy interface for retry mechanisms."""

from abc import abstractmethod
from typing import Protocol


class RetryStrategy(Protocol):
    """Strategy interface for retry mechanisms."""

    @abstractmethod
    def should_retry(self, attempt: int, exception: Exception) -> bool:
        """
        Determine if operation should be retried.

        Args:
            attempt: Current attempt number (0-based)
            exception: Exception that occurred

        Returns:
            True if operation should be retried, False otherwise
        """
        ...

    @abstractmethod
    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay before next attempt.

        Args:
            attempt: Current attempt number (0-based)

        Returns:
            Delay in seconds before next retry
        """
        ...

    def on_retry(self, attempt: int, exception: Exception) -> None:
        """
        Handle retry event (logging, metrics).

        Args:
            attempt: Current attempt number (0-based)
            exception: Exception that occurred
        """
