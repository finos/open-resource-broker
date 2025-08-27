"""Error handling adapter implementing ErrorHandlingPort."""

from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from domain.base.exceptions import DomainException
from domain.base.ports.error_handling_port import ErrorHandlingPort
from infrastructure.di.decorators import injectable
from infrastructure.error.decorators import (
    handle_application_exceptions,
    handle_exceptions,
)

T = TypeVar("T")


@injectable
class ErrorHandlingAdapter(ErrorHandlingPort):
    """Adapter that implements ErrorHandlingPort using infrastructure error handling."""

    def __init__(self) -> None:
        """Initialize the error handling adapter."""

    def handle_exceptions(self, func: Callable[..., T]) -> Callable[..., T]:
        """Handle exceptions in application methods."""
        return handle_application_exceptions(context="application_service")(func)

    def log_errors(self, func: Callable[..., T]) -> Callable[..., T]:
        """Log errors."""
        # Use the general handle_exceptions decorator for logging
        return handle_exceptions(context="error_logging", layer="application")(func)

    def retry_on_failure(self, max_retries: int = 3, delay: float = 1.0) -> Callable:
        """Retry operations on failure."""

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            """Decorator that applies retry logic to a function."""

            @wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                """Wrapper function that implements retry logic."""
                last_exception = None
                for attempt in range(max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        if attempt < max_retries:
                            import time

                            time.sleep(delay)
                        else:
                            raise last_exception
                return None  # Should never reach here

            return wrapper

        return decorator

    def handle_domain_exceptions(self, exception: Exception) -> Optional[str]:
        """Handle domain-specific exceptions and return error message."""
        if isinstance(exception, DomainException):
            return str(exception)
        return None
