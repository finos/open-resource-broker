"""Base handler implementation."""

import time
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypeVar

from infrastructure.logging.logger import get_logger

T = TypeVar("T")
R = TypeVar("R")


class BaseHandler:
    """
    Base class for all handlers.

    This class provides common functionality for all handlers,
    including logging, error handling, and metrics collection.
    """

    def __init__(self, logger=None, metrics=None) -> None:
        """
        Initialize the handler.

        Args:
            logger: Optional logger instance
            metrics: Optional metrics collector
        """
        self.logger = logger or get_logger(self.__class__.__name__)
        self.metrics = metrics

    def log_entry(self, method_name: str, **kwargs) -> None:
        """
        Log method entry with parameters.

        Args:
            method_name: Name of the method being entered
            **kwargs: Additional logging context
        """
        self.logger.debug("Entering %s", method_name, extra=kwargs)

    def log_exit(self, method_name: str, result=None, **kwargs) -> None:
        """
        Log method exit with result.

        Args:
            method_name: Name of the method being exited
            result: Optional result to log
            **kwargs: Additional logging context
        """
        self.logger.debug("Exiting %s", method_name, extra=kwargs)

    def log_error(self, method_name: str, error: Exception, **kwargs) -> None:
        """
        Log method error.

        Args:
            method_name: Name of the method where the error occurred
            error: Exception that was raised
            **kwargs: Additional logging context
        """
        self.logger.error("Error in %s: %s", method_name, str(error), exc_info=True, extra=kwargs)

    def with_logging(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Add logging to methods.

        Args:
            func: Function to decorate

        Returns:
            Decorated function with logging
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            """Wrapper function for logging method entry and exit."""
            method_name = func.__name__
            self.log_entry(method_name, args=args, kwargs=kwargs)
            try:
                result = func(*args, **kwargs)
                self.log_exit(method_name, result=result)
                return result
            except Exception as e:
                self.log_error(method_name, e)
                raise

        return wrapper

    def with_metrics(self, func: Callable[..., T], name: Optional[str] = None) -> Callable[..., T]:
        """
        Add metrics to methods.

        Args:
            func: Function to decorate
            name: Optional name for the metric

        Returns:
            Decorated function with metrics
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            """Wrapper function for metrics collection."""
            if not self.metrics:
                return func(*args, **kwargs)

            method_name = name or func.__name__
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                self.metrics.record_success(method_name, time.time() - start_time)
                return result
            except Exception as e:
                self.metrics.record_error(method_name, time.time() - start_time, error=str(e))
                raise

        return wrapper

    def with_error_handling(
        self,
        func: Callable[..., T],
        error_map: Optional[Dict[type, Callable[[Exception], Any]]] = None,
    ) -> Callable[..., T]:
        """
        Provide standardized error handling.

        Args:
            func: Function to decorate
            error_map: Optional mapping of exception types to handler methods

        Returns:
            Decorated function with error handling
        """
        error_map = error_map or {}

        @wraps(func)
        def wrapper(*args, **kwargs):
            """Wrapper function for error handling."""
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # Check if we have a specific handler for this error type
                for error_type, handler in error_map.items():
                    if isinstance(e, error_type):
                        return handler(e)

                # No specific handler, re-raise
                raise

        return wrapper
