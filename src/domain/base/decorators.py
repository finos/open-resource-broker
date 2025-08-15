"""Pure domain decorators using DI and ports pattern.

This module provides domain-layer decorators that maintain clean architecture
by using domain ports for dependency injection, avoiding direct infrastructure
dependencies and maintaining correct dependency direction.
"""

from functools import wraps
from typing import TYPE_CHECKING, Callable, Optional, TypeVar

if TYPE_CHECKING:
    from domain.base.ports import ContainerPort, ErrorHandlingPort

T = TypeVar("T")

# Global domain container - set during app initialization
_domain_container: Optional["ContainerPort"] = None


def set_domain_container(container: "ContainerPort") -> None:
    """Set the domain container (called during app initialization).

    This allows decorators to access dependencies through the domain port
    without violating Clean Architecture principles.

    Args:
        container: Container implementing ContainerPort interface
    """
    global _domain_container
    _domain_container = container


def get_domain_container() -> Optional["ContainerPort"]:
    """Get the current domain container.

    Returns:
        ContainerPort instance or None if not set
    """
    return _domain_container


def get_error_handling_port() -> Optional["ErrorHandlingPort"]:
    """Get error handler through domain container port.

    Uses the domain ContainerPort abstraction to maintain Clean Architecture
    compliance while accessing the ErrorHandlingPort implementation.

    Returns:
        ErrorHandlingPort instance or None if not available
    """
    if _domain_container:
        try:
            # Import at function level to avoid circular imports
            from domain.base.ports import ErrorHandlingPort

            return _domain_container.get(ErrorHandlingPort)
        except Exception:
            # Graceful fallback if service not registered - no logging in domain layer
            # Domain layer should not have direct infrastructure dependencies
            pass
    return None


def handle_domain_exceptions(context: str):
    """Domain decorator for exception handling using DI and ports pattern.

    This decorator maintains clean architecture by:
    1. Using the ErrorHandlingPort (domain interface)
    2. Getting implementation through ContainerPort (domain abstraction)
    3. Providing graceful fallback when DI is not available
    4. No direct infrastructure dependencies

    Args:
        context: Domain operation context (e.g., "template_validation")

    Returns:
        Decorated function with domain exception handling

    Example:

        def validate_template(self) -> None:
            # Domain validation logic
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        """Apply domain error handling to the function."""

        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            """Wrapper function that applies domain error handling."""
            # Try to get error handler through domain container port
            error_handler = get_error_handling_port()

            if error_handler:
                # Use infrastructure error handling through domain ports
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # Let the infrastructure handler deal with it
                    error_msg = error_handler.handle_domain_exceptions(e)
                    if error_msg:
                        # Re-raise with additional context
                        raise type(e)(f"{context}: {error_msg}") from e
                    raise
            else:
                # Fallback for when DI is not available (testing, etc.)
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # Simple re-raise with context
                    raise type(e)(f"{context}: {str(e)}") from e

        return wrapper

    return decorator
