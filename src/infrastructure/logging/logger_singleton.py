"""Logger singleton implementation."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from infrastructure.logging.logger import ContextLogger, with_context
from infrastructure.patterns.singleton_registry import SingletonRegistry

# Global singleton instance - initialized early to avoid circular imports
_logger_singleton_instance = None


class LoggerSingleton:
    """
    Singleton for managing loggers.

    This class ensures that only one instance of each named logger exists,
    preventing duplicate log entries and providing a centralized way to
    manage logging throughout the application.
    """

    def __init__(self) -> None:
        """
        Initialize logger singleton.

        Note: This constructor should not be called directly.
        Use get_logger_singleton() instead.
        """
        self._loggers: Dict[str, ContextLogger] = {}
        self._context: Dict[str, Any] = {}
        self._lock = threading.RLock()  # Use RLock for reentrant locking

    def get_logger(self, name: str) -> ContextLogger:
        """
        Get a logger by name.

        This method ensures that only one instance of each named logger exists,
        preventing duplicate log entries.

        Args:
            name: Logger name

        Returns:
            Logger instance
        """
        with self._lock:
            if name not in self._loggers:
                # Import the logger module's get_logger function directly to avoid
                # recursion
                from infrastructure.logging.logger import (
                    get_logger as logger_module_get_logger,
                )

                self._loggers[name] = logger_module_get_logger(name)
            return self._loggers[name]

    def with_context(self, **context: Any) -> logging.LoggerAdapter:
        """
        Create a logger adapter with context.

        Args:
            **context: Context key-value pairs

        Returns:
            Logger adapter with context
        """
        return with_context(**context)

    def set_correlation_id(self, correlation_id: str) -> None:
        """
        Set correlation ID for request tracking.

        Args:
            correlation_id: Correlation ID
        """
        with self._lock:
            self._context["correlation_id"] = correlation_id

    def clear_correlation_id(self) -> None:
        """Clear correlation ID."""
        with self._lock:
            if "correlation_id" in self._context:
                del self._context["correlation_id"]

    def get_correlation_id(self) -> Optional[str]:
        """
        Get current correlation ID.

        Returns:
            Correlation ID if set, None otherwise
        """
        with self._lock:
            return self._context.get("correlation_id")


def get_logger_singleton() -> LoggerSingleton:
    """
    Get or initialize the logger singleton instance.

    This function returns the global logger singleton instance.
    If the instance hasn't been initialized yet, it will be created.

    This function should be called early in the application bootstrap process,
    before any other components that might need logging.

    Returns:
        Logger singleton instance
    """
    global _logger_singleton_instance
    if _logger_singleton_instance is None:
        # Try to use the singleton registry if available
        try:
            # Create a new instance and register it with the registry
            instance = LoggerSingleton()
            registry = SingletonRegistry.get_instance()
            registry.register(LoggerSingleton, instance)
            _logger_singleton_instance = instance
        except ImportError:
            # Fall back to direct instantiation if the registry is not available yet
            _logger_singleton_instance = LoggerSingleton()

    return _logger_singleton_instance


def register_logger_with_container() -> None:
    """
    Register the logger singleton with the DI container.

    This function should be called after the DI container is initialized,
    to register the already-created logger singleton instance.
    """
    try:
        from infrastructure.di.container import get_container

        container = get_container()

        # Get the logger singleton instance
        logger_singleton = get_logger_singleton()

        # Register the singleton if it's not already registered
        if LoggerSingleton not in container._singletons:
            container.register_singleton(LoggerSingleton, logger_singleton)

        # Also register with the singleton registry if available
        try:
            from infrastructure.patterns.singleton_registry import SingletonRegistry

            registry = SingletonRegistry.get_instance()
            # Check if the singleton is already registered by checking if it exists in
            # the registry's _singletons dict
            if (
                LoggerSingleton not in registry._singletons
                or registry._singletons[LoggerSingleton] is None
            ):
                registry.register(LoggerSingleton, logger_singleton)
        except ImportError:
            # If the registry module can't be imported yet, that's okay
            pass
    except ImportError:
        # If the container module can't be imported yet, that's okay
        # The logger will be registered later in the bootstrap process
        pass
