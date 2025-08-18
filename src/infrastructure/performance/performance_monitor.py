"""Performance monitoring utilities."""

import functools
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

if TYPE_CHECKING:
    from domain.base.ports import LoggingPort


class PerformanceMonitor:
    """Performance monitoring utility for tracking execution times and bottlenecks."""

    def __init__(self, logger: Optional["LoggingPort"] = None):
        """Initialize performance monitor."""
        self.logger = logger
        self._metrics: Dict[str, Dict[str, Any]] = {}

    @contextmanager
    def measure(self, operation_name: str):
        """Context manager for measuring operation performance."""
        start_time = time.perf_counter()
        try:
            yield
        finally:
            end_time = time.perf_counter()
            duration = end_time - start_time
            self._record_metric(operation_name, duration)

    def _record_metric(self, operation_name: str, duration: float):
        """Record performance metric."""
        if operation_name not in self._metrics:
            self._metrics[operation_name] = {
                "count": 0,
                "total_time": 0.0,
                "min_time": float("inf"),
                "max_time": 0.0,
                "avg_time": 0.0,
            }

        metric = self._metrics[operation_name]
        metric["count"] += 1
        metric["total_time"] += duration
        metric["min_time"] = min(metric["min_time"], duration)
        metric["max_time"] = max(metric["max_time"], duration)
        metric["avg_time"] = metric["total_time"] / metric["count"]

        if self.logger and duration > 1.0:  # Log slow operations (>1 second)
            self.logger.warning("Slow operation detected: %s took %ss", operation_name, duration:.2f)

    def get_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get all recorded metrics."""
        return self._metrics.copy()

    def get_slowest_operations(self, limit: int = 10) -> Dict[str, Dict[str, Any]]:
        """Get the slowest operations by average time."""
        sorted_ops = sorted(self._metrics.items(), key=lambda x: x[1]["avg_time"], reverse=True)
        return dict(sorted_ops[:limit])

    def reset_metrics(self):
        """Reset all metrics."""
        self._metrics.clear()


def performance_monitor(operation_name: Optional[str] = None):
    """Monitor function performance."""

    def decorator(func: Callable) -> Callable:
        """Decorator that adds performance monitoring to a function."""
        name = operation_name or f"{func.__module__}.{func.__name__}"

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            """Wrapper function that measures and records performance metrics."""
            # Try to get monitor from first argument if it has one
            monitor = None
            if args and hasattr(args[0], "_performance_monitor"):
                monitor = args[0]._performance_monitor

            if monitor:
                with monitor.measure(name):
                    return func(*args, **kwargs)
            else:
                # Fallback to simple timing
                start_time = time.perf_counter()
                try:
                    return func(*args, **kwargs)
                finally:
                    duration = time.perf_counter() - start_time
                    if duration > 1.0:  # Log slow operations
                        # Try to get logger from global monitor, fallback to print if
                        # none available
                        global_monitor = get_global_monitor()
                        if global_monitor and global_monitor.logger:
                            global_monitor.logger.warning(
                                "Slow operation: %s took %ss", name, duration:.2f
                            )
                        # If no logger available, we have to use print as last resort
                        # This should only happen during early bootstrap before DI is
                        # ready

        return wrapper

    return decorator


# Global performance monitor instance
_global_monitor: Optional[PerformanceMonitor] = None


def get_global_monitor() -> PerformanceMonitor:
    """Get global performance monitor instance."""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = PerformanceMonitor()
    return _global_monitor


def measure_performance(operation_name: str):
    """Context manager for measuring performance using global monitor."""
    return get_global_monitor().measure(operation_name)


def get_performance_report() -> Dict[str, Any]:
    """Get performance report from global monitor."""
    monitor = get_global_monitor()
    return {
        "all_metrics": monitor.get_metrics(),
        "slowest_operations": monitor.get_slowest_operations(),
        "total_operations": len(monitor.get_metrics()),
    }
