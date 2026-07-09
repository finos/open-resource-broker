"""Base handler implementation.

Migration note (bead 2512)
--------------------------
``with_metrics`` now emits to OTel instruments instead of
``MetricsCollector``.  The ``metrics`` constructor parameter is kept for
backward compatibility (existing callers may pass a ``MetricsCollector``),
but it is no longer used for metric emission.  OTel instruments are
acquired via ``get_meter(__name__)`` so the path is no-op when the SDK is
absent.

OTel instruments
----------------
- ``orb.handler.{method}.total`` (Counter) — one series per
  success/error outcome, distinguished by ``outcome`` attribute.
- ``orb.handler.{method}.duration`` (Histogram, unit ``s``) — latency of
  each decorated method.

The method name is normalised from the decorated function name so existing
metric consumers that keyed off ``{method}_success_total`` will need to
query ``orb.handler.{method}.total{outcome="success"}`` instead.
"""

import time
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from orb.infrastructure.logging.logger import get_logger

T = TypeVar("T")
R = TypeVar("R")


def _get_meter():
    """Return an OTel Meter (or a no-op when SDK is absent)."""
    try:
        from opentelemetry import metrics as otel_metrics  # type: ignore[import-not-found]

        return otel_metrics.get_meter(__name__)
    except ImportError:
        return _NoOpMeter()


class _NoOpMeter:
    def create_counter(self, *a, **kw):
        return _NoOpInstrument()

    def create_histogram(self, *a, **kw):
        return _NoOpInstrument()


class _NoOpInstrument:
    def add(self, *a, **kw) -> None:
        pass

    def record(self, *a, **kw) -> None:
        pass


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
            metrics: Optional metrics collector (retained for API compat;
                metric emission now goes to OTel — see module docstring).
        """
        self.logger = logger or get_logger(self.__class__.__name__)
        self.metrics = metrics

        # OTel instrument caches — keyed by method name.
        self._otel_counters: dict[str, object] = {}
        self._otel_histograms: dict[str, object] = {}

    def _otel_counter(self, method_name: str) -> object:
        if method_name not in self._otel_counters:
            meter = _get_meter()
            self._otel_counters[method_name] = meter.create_counter(
                f"orb.handler.{method_name}.total",
                description=f"Total {method_name} handler invocations.",
                unit="1",
            )
        return self._otel_counters[method_name]

    def _otel_histogram(self, method_name: str) -> object:
        if method_name not in self._otel_histograms:
            meter = _get_meter()
            self._otel_histograms[method_name] = meter.create_histogram(
                f"orb.handler.{method_name}.duration",
                description=f"Duration of {method_name} handler invocations.",
                unit="s",
            )
        return self._otel_histograms[method_name]

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
        Add OTel metrics to methods.

        Emits ``orb.handler.{method}.total`` (Counter) with ``outcome``
        attribute and ``orb.handler.{method}.duration`` (Histogram).

        Args:
            func: Function to decorate
            name: Optional override for the method name used in metric names.

        Returns:
            Decorated function with OTel metrics.
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            """Wrapper function for OTel metrics collection."""
            method_name = name or func.__name__
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                self._otel_counter(method_name).add(  # type: ignore[union-attr]
                    1, attributes={"outcome": "success"}
                )
                self._otel_histogram(method_name).record(  # type: ignore[union-attr]
                    duration, attributes={"outcome": "success"}
                )
                return result
            except Exception as e:
                duration = time.time() - start_time
                self._otel_counter(method_name).add(  # type: ignore[union-attr]
                    1, attributes={"outcome": "error", "error": type(e).__name__}
                )
                self._otel_histogram(method_name).record(  # type: ignore[union-attr]
                    duration, attributes={"outcome": "error"}
                )
                raise

        return wrapper

    def with_error_handling(
        self,
        func: Callable[..., T],
        error_map: Optional[dict[type, Callable[[Exception], Any]]] = None,
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
