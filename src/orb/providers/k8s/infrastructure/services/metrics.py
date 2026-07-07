"""Kubernetes provider Prometheus metrics — parity with legacy k8s provider.

Metric names mirror the legacy open-hostfactory-plugin k8s exporter so that
existing dashboards and alerts continue to work without modification.

Registration model
------------------

By default the module registers all metrics against the shared
``prometheus_client.REGISTRY``, which is what
``prometheus_client.make_wsgi_app`` / ``start_http_server`` scrape.  This
means the standard ``/metrics`` endpoint served elsewhere in the process
picks the k8s metrics up automatically.

Tests (and any embedding scenario where the global registry is
undesirable) pass an explicit ``CollectorRegistry`` — the same
:class:`K8sMetrics` code path is used.  ``KeyError`` from duplicate
registration on the global registry is caught and remapped to a clear
:class:`RuntimeError` so double-initialisation surfaces at startup
rather than as a cryptic prometheus_client error.

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
from typing import ClassVar

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
)

# ---------------------------------------------------------------------------
# Label value enums — pinned to prevent cardinality explosions.
# ---------------------------------------------------------------------------

# Reasons a watch reconnect can occur.  Callers must map their concrete
# exception into one of these values before passing to
# ``watch_reconnects_total.labels(...)``.
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
    import logging

    logging.getLogger(__name__).warning(
        "k8s metrics: label %s got value %r not in enum; bucketing as 'unknown'",
        name,
        value,
    )
    return "unknown"


# Canonical label sets for each metric — kept here as the single source of truth
# so callers can introspect without importing prometheus_client directly.
_METRIC_SPECS: tuple[tuple[str, str, str, list[str]], ...] = (
    # (name, metric_type, docstring, label_names)
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
    ("orb_k8s_active_pods", "gauge", "Currently active pods", ["namespace"]),
    ("orb_k8s_active_requests", "gauge", "Currently active requests", ["namespace"]),
    (
        "orb_k8s_apiserver_latency_seconds",
        "histogram",
        "API server call latency in seconds",
        ["operation"],
    ),
    (
        "orb_k8s_circuit_breaker_state",
        "gauge",
        "Circuit breaker state: 0=closed 1=open 2=half_open",
        ["name"],
    ),
)


class K8sMetrics:
    """Container for all k8s provider Prometheus metrics.

    Instantiate once at provider start-up and share the instance.  By
    default the metrics register against ``prometheus_client.REGISTRY``
    so the standard ``/metrics`` endpoint scrapes them without any
    additional wiring.  Tests pass a private registry to avoid the
    ``ValueError: Duplicated timeseries`` that would otherwise fire when
    two instances share the global.

    Example::

        metrics = K8sMetrics()
        metrics.record_watch_reconnect(namespace="default", reason="resource_too_old")
    """

    _init_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        """Initialise all metrics against *registry*.

        Args:
            registry: A :class:`prometheus_client.CollectorRegistry` to
                register metrics against.  ``None`` uses
                ``prometheus_client.REGISTRY`` (the process-wide default)
                so the standard ``/metrics`` endpoint sees the k8s
                metrics automatically.
        """
        if registry is None:
            registry = REGISTRY
        self._registry = registry
        try:
            self.acquire_total = Counter(
                "orb_k8s_acquire_total",
                "Total acquire calls",
                ["namespace", "spec_kind"],
                registry=registry,
            )
            self.release_total = Counter(
                "orb_k8s_release_total",
                "Total release calls",
                ["namespace", "spec_kind"],
                registry=registry,
            )
            self.pod_creations_total = Counter(
                "orb_k8s_pod_creations_total",
                "Total pod creations",
                ["namespace", "status"],
                registry=registry,
            )
            self.watch_events_total = Counter(
                "orb_k8s_watch_events_total",
                "Total watch events received",
                ["namespace", "event_type"],
                registry=registry,
            )
            self.watch_reconnects_total = Counter(
                "orb_k8s_watch_reconnects_total",
                "Total watch reconnects",
                ["namespace", "reason"],
                registry=registry,
            )
            self.active_pods = Gauge(
                "orb_k8s_active_pods",
                "Currently active pods",
                ["namespace"],
                registry=registry,
            )
            self.active_requests = Gauge(
                "orb_k8s_active_requests",
                "Currently active requests",
                ["namespace"],
                registry=registry,
            )
            self.apiserver_latency_seconds = Histogram(
                "orb_k8s_apiserver_latency_seconds",
                "API server call latency in seconds",
                ["operation"],
                registry=registry,
            )
            self.circuit_breaker_state = Gauge(
                "orb_k8s_circuit_breaker_state",
                "Circuit breaker state: 0=closed 1=open 2=half_open",
                ["name"],
                registry=registry,
            )
        except ValueError as exc:
            # ``prometheus_client`` raises ValueError with a "Duplicated
            # timeseries" message when the same metric name is registered
            # twice against the same registry.  Remap to a RuntimeError
            # that surfaces the offender clearly.
            raise RuntimeError(
                "K8sMetrics: duplicate registration on registry — the k8s "
                "metrics should be instantiated exactly once per process. "
                f"Underlying error: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Enum-guarded helpers — prefer these over direct .labels() calls to
    # keep cardinality bounded.
    # ------------------------------------------------------------------

    def record_watch_reconnect(self, *, namespace: str, reason: str) -> None:
        """Increment ``watch_reconnects_total`` with an enum-checked reason."""
        safe_reason = _validate_label("reason", reason, WATCH_RECONNECT_REASONS)
        self.watch_reconnects_total.labels(namespace=namespace, reason=safe_reason).inc()

    def record_pod_creation(self, *, namespace: str, status: str) -> None:
        """Increment ``pod_creations_total`` with an enum-checked status."""
        safe_status = _validate_label("status", status, POD_CREATION_STATUSES)
        self.pod_creations_total.labels(namespace=namespace, status=safe_status).inc()

    def record_watch_event(self, *, namespace: str, event_type: str) -> None:
        """Increment ``watch_events_total`` with an enum-checked event_type."""
        safe_event = _validate_label("event_type", event_type, WATCH_EVENT_TYPES)
        self.watch_events_total.labels(namespace=namespace, event_type=safe_event).inc()

    @staticmethod
    def registered_names() -> list[str]:
        """Return the canonical metric names exported by this module.

        Useful for asserting parity with legacy provider dashboards.
        """
        return [spec[0] for spec in _METRIC_SPECS]
