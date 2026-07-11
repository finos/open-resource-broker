"""Kubernetes provider metrics — OpenTelemetry Meter API.

Metric names follow standard Prometheus naming convention.  Instruments are
created via the OTel Meter API so they flow through the shared
``PrometheusMetricReader`` (registered by ``configure_telemetry``) and surface
on the ``/metrics`` endpoint alongside the rest of ORB's metrics.

Note: prior to this migration K8sMetrics used native ``prometheus_client``
instruments registered directly on ``prometheus_client.REGISTRY``.  The OTel
``PrometheusMetricReader`` also registers a collector on that same global
registry, which would produce ``ValueError: Duplicated timeseries`` if both
stacks exported the same names.  The migration resolves this by making
K8sMetrics the *only* registration path — via OTel instruments — so there is
no double registration.

Registration model
------------------

Instruments are created via an OTel ``Meter`` obtained from
``opentelemetry.metrics.get_meter(__name__)``.  When the SDK is not installed
or OTel is disabled, ``get_meter`` returns a no-op meter and all emit calls
are cheap no-ops.  This means the k8s provider works correctly (silently) in
any deployment that does not include the ``[monitoring]`` extra.

Tests obtain an isolated meter by constructing a ``MeterProvider`` backed by a
``PrometheusMetricReader`` with a private ``CollectorRegistry``, then passing
the meter to ``K8sMetrics(meter=…)``.

OTel → Prometheus name translation
-----------------------------------

The OTel Prometheus exporter (``opentelemetry-exporter-prometheus``) translates
instrument names to Prometheus metric names as follows:

* Dots are replaced with underscores.
* A ``_total`` suffix is added for Counters.
* The unit suffix (if any) is appended, separated by ``_``.

Naming strategy used here to preserve the exact legacy Prometheus names
(``orb_k8s_*``):

* Counters that must land on ``orb_k8s_<base>_total``:
  name = ``"orb_k8s_<base>_total"``, unit omitted → exporter emits
  ``orb_k8s_<base>_total``.

* UpDownCounters that must land on ``orb_k8s_active_pods`` /
  ``orb_k8s_active_requests`` / ``orb_k8s_circuit_breaker_state``:
  name = exact target string, unit omitted.

* Histogram that must land on ``orb_k8s_apiserver_latency_seconds``:
  name = ``"orb_k8s_apiserver_latency_seconds"``, unit omitted → exporter
  emits ``orb_k8s_apiserver_latency_seconds``.

Label value enums
-----------------

Free-form label values would blow up Prometheus cardinality.  The label
values for ``reason``, ``status``, and ``event_type`` are pinned to
enum-style string constants defined below.  Callers must use these
constants; passing arbitrary strings is a bug that should be caught at
lint time (the module exports the enum sets for ``in`` checks).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:  # pragma: no cover — type annotations only
    from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter

# ---------------------------------------------------------------------------
# Label value enums — pinned to prevent cardinality explosions.
# ---------------------------------------------------------------------------

# Reasons a watch reconnect can occur.  Callers must map their concrete
# exception into one of these values before passing to
# ``record_watch_reconnect``.
WATCH_RECONNECT_REASONS: frozenset[str] = frozenset(
    {"resource_too_old", "timeout", "network", "unknown"}
)

# Pod-creation outcomes.  A raw exception ``str`` must be mapped to one
# of these buckets before use.
POD_CREATION_STATUSES: frozenset[str] = frozenset(
    {"success", "conflict", "quota_exceeded", "forbidden", "error"}
)

# Watch event types (the four Kubernetes watch verbs).  Anything else is
# a bug.
WATCH_EVENT_TYPES: frozenset[str] = frozenset({"ADDED", "MODIFIED", "DELETED", "BOOKMARK", "ERROR"})


def _validate_label(name: str, value: str, allowed: frozenset[str]) -> str:
    """Return *value* when it belongs to *allowed*, else ``"unknown"``.

    Preserves observability under caller error (a rogue value gets
    bucketed) without opening a cardinality-explosion vector.  The event
    is logged so callers can catch and fix the source.
    """
    if value in allowed:
        return value
    from orb.infrastructure.logging.logger import get_logger

    get_logger(__name__).warning(
        "k8s metrics: label %s got value %r not in enum; bucketing as 'unknown'",
        name,
        value,
    )
    return "unknown"


# Canonical label sets for each metric — kept here as the single source of truth
# so callers can introspect without importing any metrics library directly.
_METRIC_SPECS: tuple[tuple[str, str, str, list[str]], ...] = (
    # (name, instrument_kind, docstring, label_names)
    ("orb_k8s_acquire_total", "counter", "Total acquire calls", ["namespace", "spec_kind"]),
    ("orb_k8s_release_total", "counter", "Total release calls", ["namespace", "spec_kind"]),
    ("orb_k8s_pod_creations_total", "counter", "Total pod creations", ["namespace", "status"]),
    (
        "orb_k8s_watch_events_total",
        "counter",
        "Total watch events received",
        ["namespace", "event_type"],
    ),
    (
        "orb_k8s_watch_reconnects_total",
        "counter",
        "Total watch reconnects",
        ["namespace", "reason"],
    ),
    ("orb_k8s_active_pods", "updowncounter", "Currently active pods", ["namespace"]),
    ("orb_k8s_active_requests", "updowncounter", "Currently active requests", ["namespace"]),
    (
        "orb_k8s_apiserver_latency_seconds",
        "histogram",
        "API server call latency in seconds",
        ["operation"],
    ),
    (
        "orb_k8s_circuit_breaker_state",
        "updowncounter",
        "Circuit breaker state: 0=closed 1=open 2=half_open",
        ["name"],
    ),
)


def _get_default_meter() -> "Meter":
    """Return the global OTel Meter (no-op when SDK absent or disabled)."""
    try:
        from opentelemetry import metrics

        return metrics.get_meter(__name__)
    except ImportError:
        # opentelemetry-api not installed; return a local no-op stand-in.
        return _NoOpMeter()  # type: ignore[return-value]


class _NoOpCounter:
    """Minimal no-op counter used when opentelemetry-api is absent."""

    def add(self, amount: float, attributes: dict[str, Any] | None = None) -> None:
        pass  # no-op


class _NoOpHistogram:
    """Minimal no-op histogram used when opentelemetry-api is absent."""

    def record(self, amount: float, attributes: dict[str, Any] | None = None) -> None:
        pass  # no-op


class _NoOpUpDownCounter:
    """Minimal no-op UpDownCounter used when opentelemetry-api is absent."""

    def add(self, amount: float, attributes: dict[str, Any] | None = None) -> None:
        pass  # no-op


class _NoOpMeter:
    """Minimal no-op meter used when opentelemetry-api is absent."""

    def create_counter(self, name: str, **kwargs: Any) -> _NoOpCounter:
        return _NoOpCounter()

    def create_histogram(self, name: str, **kwargs: Any) -> _NoOpHistogram:
        return _NoOpHistogram()

    def create_up_down_counter(self, name: str, **kwargs: Any) -> _NoOpUpDownCounter:
        return _NoOpUpDownCounter()


class K8sMetrics:
    """Container for all k8s provider metrics, backed by the OTel Meter API.

    Instantiate once at provider start-up and share the instance.  Instruments
    are created via an OTel ``Meter``; when the SDK is not installed or OTel is
    not enabled the meter is a no-op and all emit calls are cheap no-ops.

    The ``[monitoring]`` extra wires up a ``PrometheusMetricReader`` that
    bridges these OTel instruments onto ``prometheus_client.REGISTRY``, so
    the standard ``/metrics`` endpoint exposes the ``orb_k8s_*`` series
    automatically without any additional wiring in K8sMetrics.

    Construction:

    * **Production** — call ``K8sMetrics()`` with no arguments; the instance
      obtains the global OTel meter via ``opentelemetry.metrics.get_meter``.
    * **Tests** — pass an explicit ``meter`` obtained from a fresh
      ``MeterProvider`` so each test uses an isolated registry::

          reader = PrometheusMetricReader(registry=CollectorRegistry())
          provider = MeterProvider(metric_readers=[reader])
          meter = provider.get_meter("test")
          metrics = K8sMetrics(meter=meter)

    Example::

        metrics = K8sMetrics()
        metrics.record_watch_reconnect(namespace="default", reason="resource_too_old")
    """

    _init_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, meter: "Meter | None" = None) -> None:
        """Initialise all metric instruments via *meter*.

        Args:
            meter: An OTel ``Meter`` instance.  ``None`` acquires the global
                meter via ``opentelemetry.metrics.get_meter(__name__)``, which
                returns a no-op meter when the SDK is absent or OTel is
                disabled — guaranteeing graceful degradation with zero
                configuration.
        """
        if meter is None:
            meter = _get_default_meter()

        # --- counters ---
        self._acquire_total: Counter = meter.create_counter(
            "orb_k8s_acquire_total",
            description="Total acquire calls",
        )
        self._release_total: Counter = meter.create_counter(
            "orb_k8s_release_total",
            description="Total release calls",
        )
        self._pod_creations_total: Counter = meter.create_counter(
            "orb_k8s_pod_creations_total",
            description="Total pod creations",
        )
        self._watch_events_total: Counter = meter.create_counter(
            "orb_k8s_watch_events_total",
            description="Total watch events received",
        )
        self._watch_reconnects_total: Counter = meter.create_counter(
            "orb_k8s_watch_reconnects_total",
            description="Total watch reconnects",
        )

        # --- gauges (UpDownCounter with absolute-set semantics tracked in state dicts) ---
        # active_pods and active_requests are set to an absolute count on each
        # cache update, so we track the last-known value per namespace and use
        # UpDownCounter.add() with the delta.
        self._active_pods: UpDownCounter = meter.create_up_down_counter(
            "orb_k8s_active_pods",
            description="Currently active pods",
        )
        self._active_pods_state: dict[str, int] = {}

        self._active_requests: UpDownCounter = meter.create_up_down_counter(
            "orb_k8s_active_requests",
            description="Currently active requests",
        )
        self._active_requests_state: dict[str, int] = {}

        self._circuit_breaker_state: UpDownCounter = meter.create_up_down_counter(
            "orb_k8s_circuit_breaker_state",
            description="Circuit breaker state: 0=closed 1=open 2=half_open",
        )
        self._circuit_breaker_state_values: dict[str, int] = {}

        # --- histogram ---
        self._apiserver_latency_seconds: Histogram = meter.create_histogram(
            "orb_k8s_apiserver_latency_seconds",
            description="API server call latency in seconds",
        )

        # Lock that guards the state dicts shared between callers and the
        # delta-computation in the set_* helpers.
        self._gauge_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Enum-guarded helpers — prefer these over internal instrument access
    # to keep cardinality bounded.
    # ------------------------------------------------------------------

    def record_acquire(self, *, namespace: str, spec_kind: str) -> None:
        """Increment ``orb_k8s_acquire_total`` for *namespace* and *spec_kind*."""
        self._acquire_total.add(1, {"namespace": namespace, "spec_kind": spec_kind})

    def record_release(self, *, namespace: str, spec_kind: str) -> None:
        """Increment ``orb_k8s_release_total`` for *namespace* and *spec_kind*."""
        self._release_total.add(1, {"namespace": namespace, "spec_kind": spec_kind})

    def record_watch_reconnect(self, *, namespace: str, reason: str) -> None:
        """Increment ``orb_k8s_watch_reconnects_total`` with an enum-checked reason."""
        safe_reason = _validate_label("reason", reason, WATCH_RECONNECT_REASONS)
        self._watch_reconnects_total.add(1, {"namespace": namespace, "reason": safe_reason})

    def record_pod_creation(self, *, namespace: str, status: str) -> None:
        """Increment ``orb_k8s_pod_creations_total`` with an enum-checked status."""
        safe_status = _validate_label("status", status, POD_CREATION_STATUSES)
        self._pod_creations_total.add(1, {"namespace": namespace, "status": safe_status})

    def record_watch_event(self, *, namespace: str, event_type: str) -> None:
        """Increment ``orb_k8s_watch_events_total`` with an enum-checked event_type."""
        safe_event = _validate_label("event_type", event_type, WATCH_EVENT_TYPES)
        self._watch_events_total.add(1, {"namespace": namespace, "event_type": safe_event})

    def record_apiserver_latency(self, *, operation: str, seconds: float) -> None:
        """Observe a single API server call latency sample.

        ``operation`` is a free-form label (e.g. ``"list_pods"``) — keep
        the cardinality low by using a small fixed set of operation names.
        """
        self._apiserver_latency_seconds.record(seconds, {"operation": operation})

    def set_active_pods(self, *, namespace: str, count: int) -> None:
        """Set the ``orb_k8s_active_pods`` gauge for *namespace* to an absolute *count*.

        OTel UpDownCounter is delta-based; we track the previous value per
        namespace and emit only the difference so the Prometheus bridge sees
        the correct absolute value.
        """
        with self._gauge_lock:
            prev = self._active_pods_state.get(namespace, 0)
            delta = count - prev
            self._active_pods_state[namespace] = count
        self._active_pods.add(delta, {"namespace": namespace})

    def set_active_requests(self, *, namespace: str, count: int) -> None:
        """Set the ``orb_k8s_active_requests`` gauge for *namespace* to an absolute *count*."""
        with self._gauge_lock:
            prev = self._active_requests_state.get(namespace, 0)
            delta = count - prev
            self._active_requests_state[namespace] = count
        self._active_requests.add(delta, {"namespace": namespace})

    def set_circuit_breaker_state(self, *, name: str, state: int) -> None:
        """Set the ``orb_k8s_circuit_breaker_state`` gauge.

        ``state`` must be one of: 0=closed, 1=open, 2=half_open.
        """
        with self._gauge_lock:
            prev = self._circuit_breaker_state_values.get(name, 0)
            delta = state - prev
            self._circuit_breaker_state_values[name] = state
        self._circuit_breaker_state.add(delta, {"name": name})

    @staticmethod
    def registered_names() -> list[str]:
        """Return the canonical Prometheus metric names exported by this module.

        Names follow standard Prometheus naming convention; there is no
        lexical relationship to any legacy exporter.
        """
        return [spec[0] for spec in _METRIC_SPECS]
