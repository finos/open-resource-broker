"""Error handling port for application layer."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TypeVar

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

    async def handle_error(self, exception: Exception, context: Any = None) -> None:
        """Handle an error with optional context. Default implementation re-raises."""
        raise exception
