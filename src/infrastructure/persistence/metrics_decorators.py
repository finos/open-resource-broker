"""Metrics decorators for storage operations."""

import time
from functools import wraps
from typing import Callable, Optional


def instrument_storage(get_metrics: Callable[[object], Optional[object]], op_name: str):
    """
    Decorator factory to instrument storage methods with metrics.

    Args:
        get_metrics: Callable that extracts metrics collector from instance
        op_name: Operation name for metric naming (e.g., 'save', 'find_by_id')

    Metrics generated:
        - storage.json.{op_name}_total: Success counter
        - storage.json.{op_name}_errors_total: Error counter
        - storage.json.{op_name}_duration: Operation duration in seconds

    If metrics collector is None, operations proceed without instrumentation.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            metrics = get_metrics(self)
            start = time.time()
            error_occurred = False

            try:
                result = func(self, *args, **kwargs)
                return result
            except Exception:
                error_occurred = True
                raise
            finally:
                # Always record duration, even on error
                duration = time.time() - start
                if metrics:
                    if error_occurred:
                        metrics.increment_counter(f"storage.json.{op_name}_errors_total")
                    else:
                        metrics.increment_counter(f"storage.json.{op_name}_total")
                    metrics.record_time(f"storage.json.{op_name}_duration", duration)

        return wrapper

    return decorator
