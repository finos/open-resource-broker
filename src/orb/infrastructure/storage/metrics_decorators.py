"""Metrics decorators for storage operations.

Migration note (bead 2512)
--------------------------
Storage operation metrics are now emitted to OTel instruments instead of
``MetricsCollector``.  The ``get_metrics`` parameter is retained so that
the decorator signature is unchanged and no call sites need updating.
However, the metrics object is no longer used — OTel instruments are
acquired from the global meter via ``get_meter(__name__)``.

The nine metric names are preserved in their OTel form as instrument names:

    MetricsCollector name                  OTel instrument name
    storage.json.{op}_total        →   orb.storage.json.{op}.total
    storage.json.{op}_errors_total →   orb.storage.json.{op}.errors.total
    storage.json.{op}_duration     →   orb.storage.json.{op}.duration

Dimensions are recorded as attributes on the instruments rather than
embedded in the name.

Graceful no-op
--------------
When ``opentelemetry-api`` is absent or no ``MeterProvider`` is configured,
``get_meter()`` returns a no-op meter and all recording calls are free.
"""

import time
from functools import wraps
from typing import Callable, Optional


def _get_meter():
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


# Module-level instrument cache — instruments are created once per op_name.
_success_counters: dict[str, object] = {}
_error_counters: dict[str, object] = {}
_duration_histograms: dict[str, object] = {}


def _success_counter(op_name: str) -> object:
    if op_name not in _success_counters:
        meter = _get_meter()
        _success_counters[op_name] = meter.create_counter(
            f"orb.storage.json.{op_name}.total",
            description=f"Successful storage JSON {op_name} operations.",
            unit="1",
        )
    return _success_counters[op_name]


def _error_counter(op_name: str) -> object:
    if op_name not in _error_counters:
        meter = _get_meter()
        _error_counters[op_name] = meter.create_counter(
            f"orb.storage.json.{op_name}.errors.total",
            description=f"Failed storage JSON {op_name} operations.",
            unit="1",
        )
    return _error_counters[op_name]


def _duration_histogram(op_name: str) -> object:
    if op_name not in _duration_histograms:
        meter = _get_meter()
        _duration_histograms[op_name] = meter.create_histogram(
            f"orb.storage.json.{op_name}.duration",
            description=f"Duration of storage JSON {op_name} operations.",
            unit="s",
        )
    return _duration_histograms[op_name]


def instrument_storage(get_metrics: Callable[[object], Optional[object]], op_name: str):
    """
    Decorator factory to instrument storage methods with OTel metrics.

    Args:
        get_metrics: Callable that extracts metrics collector from instance.
            Retained for signature compatibility; no longer used for emission.
        op_name: Operation name for metric naming (e.g., 'save', 'find_by_id').

    OTel instruments created (unit follows OTel convention):
        - orb.storage.json.{op_name}.total          (Counter)
        - orb.storage.json.{op_name}.errors.total   (Counter)
        - orb.storage.json.{op_name}.duration       (Histogram, unit s)

    If opentelemetry-api is absent operations proceed without instrumentation.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            start = time.time()
            error_occurred = False

            try:
                result = func(self, *args, **kwargs)
                return result
            except Exception:
                error_occurred = True
                raise
            finally:
                duration = time.time() - start
                if error_occurred:
                    _error_counter(op_name).add(1)  # type: ignore[union-attr]
                else:
                    _success_counter(op_name).add(1)  # type: ignore[union-attr]
                _duration_histogram(op_name).record(duration)  # type: ignore[union-attr]

        return wrapper

    return decorator
