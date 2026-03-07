"""
Exception type mapping and handler registration.

Provides centralized exception type to handler mapping with MRO-based lookup
for efficient and maintainable exception handling.
"""

from functools import lru_cache
from typing import Any, Callable, Optional


class ExceptionTypeMapper:
    """
    Maps exception types to their appropriate handlers.

    Provides efficient handler lookup using Method Resolution Order (MRO)
    and supports both regular and HTTP-specific handler registration.
    """

    def __init__(self) -> None:
        """Initialize the mapper with empty handler dictionaries."""
        self._handlers: dict[type[Exception], Callable] = {}
        self._http_handlers: dict[type[Exception], Callable] = {}

    def register_handler(
        self, exception_type: type[Exception], handler: Callable[..., Any]
    ) -> None:
        """
        Register a handler for a specific exception type.

        Args:
            exception_type: The exception type to handle
            handler: The handler function for this exception type
        """
        self._handlers[exception_type] = handler

    def register_http_handler(
        self, exception_type: type[Exception], handler: Callable[..., Any]
    ) -> None:
        """
        Register an HTTP-specific handler for a specific exception type.

        Args:
            exception_type: The exception type to handle
            handler: The HTTP handler function for this exception type
        """
        self._http_handlers[exception_type] = handler

    @lru_cache(maxsize=128)
    def get_handler(
        self, exception_type: type[Exception], fallback_handler: Optional[Callable[..., Any]] = None
    ) -> Callable[..., Any]:
        """
        Find the most specific handler for this exception type.

        Uses Method Resolution Order (MRO) to find the best match.
        Cached for performance.

        Args:
            exception_type: The exception type to find a handler for
            fallback_handler: Handler to use if no specific handler found

        Returns:
            The most specific handler for the exception type
        """
        # 1. Check for exact type match first
        if exception_type in self._handlers:
            return self._handlers[exception_type]

        # 2. Walk up inheritance hierarchy (MRO)
        for parent_type in exception_type.__mro__[1:]:  # Skip self
            if parent_type in self._handlers:
                return self._handlers[parent_type]

        # 3. Fall back to provided handler or raise error
        if fallback_handler is not None:
            return fallback_handler

        raise ValueError(f"No handler found for exception type: {exception_type}")

    @lru_cache(maxsize=128)
    def get_http_handler(
        self, exception_type: type[Exception], fallback_handler: Optional[Callable[..., Any]] = None
    ) -> Callable[..., Any]:
        """
        Find the most specific HTTP handler for this exception type.

        Uses Method Resolution Order (MRO) to find the best match.
        Cached for performance.

        Args:
            exception_type: The exception type to find a handler for
            fallback_handler: Handler to use if no specific handler found

        Returns:
            The most specific HTTP handler for the exception type
        """
        # 1. Check for exact type match first
        if exception_type in self._http_handlers:
            return self._http_handlers[exception_type]

        # 2. Walk up inheritance hierarchy (MRO)
        for parent_type in exception_type.__mro__[1:]:  # Skip self
            if parent_type in self._http_handlers:
                return self._http_handlers[parent_type]

        # 3. Fall back to provided handler or raise error
        if fallback_handler is not None:
            return fallback_handler

        raise ValueError(f"No HTTP handler found for exception type: {exception_type}")

    def has_handler(self, exception_type: type[Exception]) -> bool:
        """
        Check if a handler is registered for the exception type.

        Args:
            exception_type: The exception type to check

        Returns:
            True if a handler is registered, False otherwise
        """
        if exception_type in self._handlers:
            return True

        # Check inheritance hierarchy
        for parent_type in exception_type.__mro__[1:]:
            if parent_type in self._handlers:
                return True

        return False

    def has_http_handler(self, exception_type: type[Exception]) -> bool:
        """
        Check if an HTTP handler is registered for the exception type.

        Args:
            exception_type: The exception type to check

        Returns:
            True if an HTTP handler is registered, False otherwise
        """
        if exception_type in self._http_handlers:
            return True

        # Check inheritance hierarchy
        for parent_type in exception_type.__mro__[1:]:
            if parent_type in self._http_handlers:
                return True

        return False

    def clear_handlers(self) -> None:
        """Clear all registered handlers."""
        self._handlers.clear()
        self._http_handlers.clear()
        # Clear LRU cache
        self.get_handler.cache_clear()
        self.get_http_handler.cache_clear()

    def get_registered_types(self) -> set[type[Exception]]:
        """
        Get all registered exception types.

        Returns:
            Set of all registered exception types
        """
        return set(self._handlers.keys()) | set(self._http_handlers.keys())
