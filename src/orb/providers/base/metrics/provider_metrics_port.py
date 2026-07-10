"""ProviderMetricsPort — abstract interface for provider metric emission.

This port sits in ``providers/base/`` (a provider-infrastructure concern)
rather than in ``domain/base/ports/`` which must stay instrumentation-free
and portable.

Design notes
------------
``record_operation`` intentionally accepts ``duration_seconds: float | None``.
This is a deliberate design choice, not a smell: k8s event-driven handlers
have no timed duration for an individual watch event, while AWS botocore
handlers always have a measurable call duration.  Optional duration lets a
single interface serve both providers without forcing fake zeros or splitting
the port.

Name-to-label promotion
-----------------------
Earlier code embedded provider_id and operation in metric names
(``provider.{id}.{op}.success_total``), causing unbounded key-space growth.
This port requires dimensions to be passed as *labels/attributes* so the
OTel exporter maps them to Prometheus label sets rather than separate series.

No-op guarantee
---------------
``NoOpProviderMetrics`` provides a trivial all-``pass`` implementation so
callers that receive the default (no metrics configured) behave correctly
without any ``if self._metrics is not None`` guards throughout the codebase.

OTel-backed implementation
--------------------------
``OtelProviderMetrics`` acquires instruments lazily on first use (via
``get_meter(__name__)``).  When OTel is not installed or when no
``MeterProvider`` has been configured the OTel API returns no-op instruments
transparently — the application runs fully without the ``[monitoring]`` extra.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Optional


class ProviderMetricsPort(ABC):
    """Abstract interface for recording provider-layer metrics.

    All method bodies use ``pass`` (not ``...``) per the project's
    Protocol/ABC stub body convention.  Concrete subclasses must provide
    full implementations.
    """

    @abstractmethod
    def record_operation(
        self,
        service: str,
        operation: str,
        duration_seconds: Optional[float],
        success: bool,
        error_code: Optional[str] = None,
    ) -> None:
        """Record a single provider API operation.

        Args:
            service: Service or resource type (e.g. ``"ec2"``, ``"pods"``).
            operation: Operation name (e.g. ``"run_instances"``, ``"create"``).
            duration_seconds: Wall-clock duration of the operation, or
                ``None`` if no duration is available (e.g. watch events).
            success: ``True`` if the operation completed without error.
            error_code: Optional error code string when ``success=False``.
        """
        pass  # type: ignore[return]

    @abstractmethod
    def record_counter(self, name: str, labels: Optional[dict[str, str]] = None) -> None:
        """Increment a named counter by 1.

        Args:
            name: Instrument name (dot-separated, e.g. ``"circuit_breaker.opened"``).
            labels: Optional label set attached to the data point.
        """
        pass  # type: ignore[return]

    @abstractmethod
    def record_gauge(
        self, name: str, value: float, labels: Optional[dict[str, str]] = None
    ) -> None:
        """Record an absolute gauge value.

        Use this for metrics where the absolute current value matters
        (e.g. ``active_instances``, ``pending_requests``).  For
        ``active_instances`` the value is set unconditionally — it is NOT a
        delta counter.

        Args:
            name: Instrument name.
            value: Absolute value to record.
            labels: Optional label set.
        """
        pass  # type: ignore[return]

    @abstractmethod
    def record_histogram(
        self, name: str, value: float, labels: Optional[dict[str, str]] = None
    ) -> None:
        """Record a single observation into a histogram.

        Use this for latency / duration distributions where percentiles and
        bucket counts are more useful than a running mean.

        Args:
            name: Instrument name.
            value: Observation value (unit implied by instrument name, e.g.
                ``"duration_seconds"`` → seconds).
            labels: Optional label set.
        """
        pass  # type: ignore[return]


class NoOpProviderMetrics(ProviderMetricsPort):
    """Default no-op implementation — all methods are silent pass-throughs.

    Registered as the default in DI so callers never need ``if metrics`` guards.
    Replaced by ``OtelProviderMetrics`` when OTel is enabled.
    """

    def record_operation(
        self,
        service: str,
        operation: str,
        duration_seconds: Optional[float],
        success: bool,
        error_code: Optional[str] = None,
    ) -> None:
        pass

    def record_counter(self, name: str, labels: Optional[dict[str, str]] = None) -> None:
        pass

    def record_gauge(
        self, name: str, value: float, labels: Optional[dict[str, str]] = None
    ) -> None:
        pass

    def record_histogram(
        self, name: str, value: float, labels: Optional[dict[str, str]] = None
    ) -> None:
        pass


class OtelProviderMetrics(ProviderMetricsPort):
    """OpenTelemetry-backed ProviderMetricsPort.

    Instruments are acquired lazily the first time each is needed so that
    import order does not matter and a ``MeterProvider`` does not need to
    exist at construction time.

    When ``opentelemetry-api`` is absent (``ImportError``) or when no
    ``MeterProvider`` has been set on the global API, OTel returns no-op
    instruments and all recording calls are effectively free.

    Naming convention
    -----------------
    Instrument names follow the pattern ``orb.provider.<domain>`` with
    dimensions (provider_id, service, operation, outcome, error_code) carried
    as attributes/labels rather than name fragments.  The Prometheus exporter
    translates dots → underscores and appends unit suffixes as required by the
    OTel → Prometheus bridge specification.
    """

    def __init__(self, meter_name: str = __name__) -> None:
        """Initialise the OTel-backed metrics port.

        Args:
            meter_name: Instrumentation scope name passed to ``get_meter()``.
                Defaults to this module's ``__name__``.
        """
        self._meter_name = meter_name
        # Lazy instrument caches — populated on first use.
        self._operation_duration: object = None
        self._operation_counter: object = None
        self._counters: dict[str, object] = {}
        self._gauges: dict[str, object] = {}
        self._histograms: dict[str, object] = {}
        # Tracks the last-known absolute value per gauge key so that each
        # record_gauge(name, abs_value) call can compute the UpDownCounter
        # delta.  Initialised here (not lazily) to avoid the hasattr
        # anti-pattern and a theoretical data-race on first access.
        self._gauge_current: dict[str, float] = {}

    def _get_meter(self) -> object:
        """Return the OTel Meter (or a no-op if SDK absent)."""
        try:
            from opentelemetry import metrics as otel_metrics  # type: ignore[import-not-found]

            return otel_metrics.get_meter(self._meter_name)
        except ImportError:
            return _NoOpMeter()

    def record_operation(
        self,
        service: str,
        operation: str,
        duration_seconds: Optional[float],
        success: bool,
        error_code: Optional[str] = None,
    ) -> None:
        """Record a provider API operation as OTel instruments.

        Maps to two instruments:
          - ``orb.provider.operation.total`` (Counter) with attributes
            ``{service, operation, outcome}``.
          - ``orb.provider.operation.duration`` (Histogram, unit ``s``)
            recorded only when ``duration_seconds`` is not ``None``.
        """
        attrs: dict[str, str] = {
            "service": service,
            "operation": operation,
            "outcome": "success" if success else "error",
        }
        if error_code:
            attrs["error_code"] = error_code

        meter = self._get_meter()

        # Counter
        if self._operation_counter is None:
            self._operation_counter = _create_counter(
                meter,
                "orb.provider.operation.total",
                description="Total number of provider API operations.",
                unit="1",
            )
        _add(self._operation_counter, 1, attrs)

        # Histogram (only when duration is available)
        if duration_seconds is not None:
            if self._operation_duration is None:
                self._operation_duration = _create_histogram(
                    meter,
                    "orb.provider.operation.duration",
                    description="Duration of provider API operations.",
                    unit="s",
                )
            _record(self._operation_duration, duration_seconds, attrs)

    def record_counter(self, name: str, labels: Optional[dict[str, str]] = None) -> None:
        """Increment a named OTel Counter by 1."""
        meter = self._get_meter()
        if name not in self._counters:
            self._counters[name] = _create_counter(
                meter,
                _to_otel_name(name),
                description=f"Counter: {name}",
                unit="1",
            )
        _add(self._counters[name], 1, labels or {})

    def record_gauge(
        self, name: str, value: float, labels: Optional[dict[str, str]] = None
    ) -> None:
        """Record an absolute value via an OTel UpDownCounter.

        OTel does not have a "set" primitive for synchronous gauges
        (ObservableGauge requires a callback; synchronous Gauge was added
        in 1.23).  UpDownCounter is used here for compatibility with 1.20+.
        For ``active_instances`` the caller always sets the absolute value
        by recording a delta relative to a tracked current value.

        Implementation: we maintain a ``_current`` dict so that each
        ``record_gauge(name, abs_value)`` call computes the delta and passes
        it to the UpDownCounter, preserving absolute-set semantics.
        """
        key = f"{name}:{sorted((labels or {}).items())}"
        prev = self._gauge_current.get(key, 0.0)
        delta = value - prev
        self._gauge_current[key] = value

        meter = self._get_meter()
        if name not in self._gauges:
            self._gauges[name] = _create_updown_counter(
                meter,
                _to_otel_name(name),
                description=f"Gauge (absolute-set): {name}",
                unit="1",
            )
        _add(self._gauges[name], delta, labels or {})

    def record_histogram(
        self, name: str, value: float, labels: Optional[dict[str, str]] = None
    ) -> None:
        """Record an observation into a named OTel Histogram.

        **Value unit**: seconds.  The Prometheus exporter appends ``_seconds``
        to the translated metric name, so *name* MUST NOT already end in
        ``_seconds`` or ``_duration_seconds`` — that would produce a double
        suffix (``_seconds_seconds``) in Prometheus output.  Use a bare
        descriptive name such as ``"provisioning.duration"`` or
        ``"operation.duration"`` and let the unit suffix come from the
        exporter.
        """
        meter = self._get_meter()
        if name not in self._histograms:
            self._histograms[name] = _create_histogram(
                meter,
                _to_otel_name(name),
                description=f"Histogram: {name}",
                unit="s",
            )
        _record(self._histograms[name], value, labels or {})


# ---------------------------------------------------------------------------
# Internal helpers — thin wrappers so the main class body stays readable and
# the ``try/except ImportError`` guards are isolated to one place.
# ---------------------------------------------------------------------------


def _to_otel_name(name: str) -> str:
    """Convert a dot-or-underscore name to a canonical OTel dot-separated name.

    ``storage.json.save_total``     → ``orb.storage.json.save.total``
    ``circuit_breaker_opened_total``→ ``orb.circuit_breaker.opened.total``
    ``requests_total``              → ``orb.requests.total``
    ``active_instances``            → ``orb.active.instances``
    """
    if not name.startswith("orb."):
        name = "orb." + name
    # Normalise underscores to dots for OTel convention
    return name.replace("_", ".")


class _NoOpMeter:
    """Fallback no-op meter when opentelemetry-api is not installed."""

    def create_counter(self, *a, **kw) -> "_NoOpInstrument":  # type: ignore[return]
        return _NoOpInstrument()

    def create_up_down_counter(self, *a, **kw) -> "_NoOpInstrument":  # type: ignore[return]
        return _NoOpInstrument()

    def create_histogram(self, *a, **kw) -> "_NoOpInstrument":  # type: ignore[return]
        return _NoOpInstrument()


class _NoOpInstrument:
    """No-op instrument for use when SDK is absent."""

    def add(self, *a, **kw) -> None:
        pass

    def record(self, *a, **kw) -> None:
        pass


def _create_counter(meter: object, name: str, description: str, unit: str) -> object:
    try:
        return meter.create_counter(name, description=description, unit=unit)  # type: ignore[union-attr]
    except Exception:
        return _NoOpInstrument()


def _create_updown_counter(meter: object, name: str, description: str, unit: str) -> object:
    try:
        return meter.create_up_down_counter(name, description=description, unit=unit)  # type: ignore[union-attr]
    except Exception:
        return _NoOpInstrument()


def _create_histogram(meter: object, name: str, description: str, unit: str) -> object:
    try:
        return meter.create_histogram(name, description=description, unit=unit)  # type: ignore[union-attr]
    except Exception:
        return _NoOpInstrument()


def _add(instrument: object, value: float, attrs: dict) -> None:
    # Metrics recording must never break the caller; swallow all errors.
    with suppress(Exception):
        instrument.add(value, attributes=attrs)  # type: ignore[union-attr]


def _record(instrument: object, value: float, attrs: dict) -> None:
    # Metrics recording must never break the caller; swallow all errors.
    with suppress(Exception):
        instrument.record(value, attributes=attrs)  # type: ignore[union-attr]
