"""Error handling port for application layer."""

from abc import ABC, abstractmethod
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class ErrorHandlingPort(ABC):
    """Port for error handling and decorators."""

    @abstractmethod
    def handle_exceptions(self, func: Callable[..., T]) -> Callable[..., T]:
        """Handle exceptions in application methods."""

    @abstractmethod
    def log_errors(self, func: Callable[..., T]) -> Callable[..., T]:
        """Log errors."""

    @abstractmethod
    def retry_on_failure(self, max_retries: int = 3, delay: float = 1.0) -> Callable:
        """Retry operations on failure."""

    @abstractmethod
    def handle_domain_exceptions(self, exception: Exception) -> Optional[str]:
        """Handle domain-specific exceptions and return error message."""
