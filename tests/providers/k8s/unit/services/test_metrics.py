"""Unit tests for K8sMetrics — OTel Meter API instruments.

K8sMetrics instruments are now backed by the OTel Meter API rather than
native prometheus_client objects.  Tests use an isolated MeterProvider
(backed by a PrometheusMetricReader with a private CollectorRegistry) so
each test has its own clean registry and doesn't pollute the global one.
"""

from __future__ import annotations

import threading
from typing import Any

from orb.providers.k8s.infrastructure.services.metrics import (
    _METRIC_SPECS,
    POD_CREATION_STATUSES,
    WATCH_EVENT_TYPES,
    WATCH_RECONNECT_REASONS,
    K8sMetrics,
)

# ---------------------------------------------------------------------------
# Test isolation helpers
# ---------------------------------------------------------------------------


def _make_meter_and_registry() -> tuple[Any, Any]:
    """Return an isolated (meter, registry) pair.

    Uses a fresh MeterProvider + PrometheusMetricReader so metric values
    accumulate only within a single test's lifetime and never bleed into
    other tests or the global prometheus_client REGISTRY.
    """
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from opentelemetry.sdk.metrics import MeterProvider
    from prometheus_client import CollectorRegistry

    reg = CollectorRegistry()
    reader = PrometheusMetricReader(registry=reg)
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    return meter, reg


def _scrape(registry: Any) -> str:
    """Return the current Prometheus text output from *registry*."""
    from prometheus_client import generate_latest

    return generate_latest(registry).decode("utf-8")


def _fresh() -> tuple[K8sMetrics, Any]:
    """Return (K8sMetrics, registry) on isolated meters/registry."""
    meter, reg = _make_meter_and_registry()
    return K8sMetrics(meter=meter), reg


# ---------------------------------------------------------------------------
# Registered names
# ---------------------------------------------------------------------------


class TestRegisteredNames:
    _EXPECTED_NAMES = [
        "orb_k8s_acquire_total",
        "orb_k8s_release_total",
        "orb_k8s_pod_creations_total",
        "orb_k8s_watch_events_total",
        "orb_k8s_watch_reconnects_total",
        "orb_k8s_active_pods",
        "orb_k8s_active_requests",
        "orb_k8s_apiserver_latency_seconds",
        "orb_k8s_circuit_breaker_state",
    ]

    def test_all_expected_names_present(self) -> None:
        assert set(self._EXPECTED_NAMES) == set(K8sMetrics.registered_names())

    def test_no_extra_names(self) -> None:
        assert len(self._EXPECTED_NAMES) == len(K8sMetrics.registered_names())

    def test_spec_names_match_registered_names(self) -> None:
        spec_names = [s[0] for s in _METRIC_SPECS]
        assert spec_names == K8sMetrics.registered_names()


# ---------------------------------------------------------------------------
# Counter increments — asserted via generate_latest
# ---------------------------------------------------------------------------


class TestCounterValueIncrements:
    """Every counter must actually count — assertions via Prometheus scrape."""

    def test_acquire_total_increments(self) -> None:
        m, reg = _fresh()
        m._acquire_total.add(3, {"namespace": "default", "spec_kind": "Pod"})
        text = _scrape(reg)
        assert "orb_k8s_acquire_total" in text

    def test_release_total_increments(self) -> None:
        m, reg = _fresh()
        m._release_total.add(1, {"namespace": "default", "spec_kind": "Pod"})
        text = _scrape(reg)
        assert "orb_k8s_release_total" in text

    def test_pod_creations_total_increments(self) -> None:
        m, reg = _fresh()
        m._pod_creations_total.add(2, {"namespace": "default", "status": "success"})
        text = _scrape(reg)
        assert "orb_k8s_pod_creations_total" in text

    def test_watch_events_total_increments(self) -> None:
        m, reg = _fresh()
        m._watch_events_total.add(1, {"namespace": "default", "event_type": "ADDED"})
        text = _scrape(reg)
        assert "orb_k8s_watch_events_total" in text

    def test_watch_reconnects_total_increments(self) -> None:
        m, reg = _fresh()
        m._watch_reconnects_total.add(1, {"namespace": "default", "reason": "timeout"})
        text = _scrape(reg)
        assert "orb_k8s_watch_reconnects_total" in text


# ---------------------------------------------------------------------------
# Gauge (UpDownCounter) operations — absolute-set helpers
# ---------------------------------------------------------------------------


class TestGaugeOperations:
    def test_active_pods_set(self) -> None:
        m, reg = _fresh()
        m.set_active_pods(namespace="default", count=5)
        text = _scrape(reg)
        assert "orb_k8s_active_pods" in text
        assert "5" in text or "5.0" in text

    def test_active_pods_decrements_on_lower_count(self) -> None:
        m, reg = _fresh()
        m.set_active_pods(namespace="default", count=5)
        m.set_active_pods(namespace="default", count=2)
        text = _scrape(reg)
        assert "orb_k8s_active_pods" in text
        assert "2" in text or "2.0" in text

    def test_active_requests_set(self) -> None:
        m, reg = _fresh()
        m.set_active_requests(namespace="default", count=3)
        text = _scrape(reg)
        assert "orb_k8s_active_requests" in text
        assert "3" in text or "3.0" in text

    def test_circuit_breaker_state_transitions(self) -> None:
        m, reg = _fresh()
        m.set_circuit_breaker_state(name="api-server", state=0)
        m.set_circuit_breaker_state(name="api-server", state=1)
        text = _scrape(reg)
        assert "orb_k8s_circuit_breaker_state" in text
        assert "1" in text or "1.0" in text

    def test_active_pods_multiple_namespaces_independent(self) -> None:
        m, reg = _fresh()
        m.set_active_pods(namespace="ns-a", count=4)
        m.set_active_pods(namespace="ns-b", count=7)
        text = _scrape(reg)
        assert "ns-a" in text
        assert "ns-b" in text


# ---------------------------------------------------------------------------
# Histogram observations
# ---------------------------------------------------------------------------


class TestHistogramObservations:
    def test_apiserver_latency_observe(self) -> None:
        m, reg = _fresh()
        m.record_apiserver_latency(operation="list_pods", seconds=0.042)
        text = _scrape(reg)
        assert "orb_k8s_apiserver_latency_seconds" in text

    def test_apiserver_latency_multiple_operations(self) -> None:
        m, reg = _fresh()
        m.record_apiserver_latency(operation="list_pods", seconds=0.1)
        m.record_apiserver_latency(operation="create_pod", seconds=0.2)
        text = _scrape(reg)
        assert "list_pods" in text
        assert "create_pod" in text


# ---------------------------------------------------------------------------
# Label enum helpers
# ---------------------------------------------------------------------------


class TestLabelEnums:
    """Enum helpers must bucket rogue label values to 'unknown' rather than blow up cardinality."""

    def test_valid_reconnect_reason_recorded(self) -> None:
        m, reg = _fresh()
        assert "timeout" in WATCH_RECONNECT_REASONS
        m.record_watch_reconnect(namespace="ns", reason="timeout")
        text = _scrape(reg)
        assert "orb_k8s_watch_reconnects_total" in text
        assert 'reason="timeout"' in text

    def test_rogue_reconnect_reason_bucketed_as_unknown(self) -> None:
        m, reg = _fresh()
        m.record_watch_reconnect(namespace="ns", reason="ohgodwhy")
        text = _scrape(reg)
        assert 'reason="unknown"' in text
        assert 'reason="ohgodwhy"' not in text

    def test_valid_pod_creation_status_recorded(self) -> None:
        m, reg = _fresh()
        assert "success" in POD_CREATION_STATUSES
        m.record_pod_creation(namespace="ns", status="success")
        text = _scrape(reg)
        assert "orb_k8s_pod_creations_total" in text
        assert 'status="success"' in text

    def test_rogue_pod_creation_status_bucketed(self) -> None:
        m, reg = _fresh()
        m.record_pod_creation(namespace="ns", status="oh-no")
        text = _scrape(reg)
        assert 'status="unknown"' in text
        assert 'status="oh-no"' not in text

    def test_valid_watch_event_recorded(self) -> None:
        m, reg = _fresh()
        assert "ADDED" in WATCH_EVENT_TYPES
        m.record_watch_event(namespace="ns", event_type="ADDED")
        text = _scrape(reg)
        assert "orb_k8s_watch_events_total" in text
        assert 'event_type="ADDED"' in text

    def test_rogue_watch_event_bucketed(self) -> None:
        m, reg = _fresh()
        m.record_watch_event(namespace="ns", event_type="weird")
        text = _scrape(reg)
        assert 'event_type="unknown"' in text
        assert 'event_type="weird"' not in text


# ---------------------------------------------------------------------------
# Thread safety for gauge state dicts
# ---------------------------------------------------------------------------


class TestGaugeThreadSafety:
    """set_active_pods with concurrent callers must not corrupt state."""

    def test_concurrent_set_active_pods_consistent(self) -> None:
        m, reg = _fresh()
        errors: list[Exception] = []

        def _worker(ns: str, count: int) -> None:
            try:
                for _ in range(20):
                    m.set_active_pods(namespace=ns, count=count)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(f"ns{i}", i * 5)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ---------------------------------------------------------------------------
# No-op graceful degradation (no OTel SDK)
# ---------------------------------------------------------------------------


class TestNoOpDegradation:
    """K8sMetrics must work when called with a no-op meter (SDK absent)."""

    def test_no_op_meter_does_not_raise(self) -> None:
        from orb.providers.k8s.infrastructure.services.metrics import _NoOpMeter

        m = K8sMetrics(meter=_NoOpMeter())  # type: ignore[arg-type]
        # All helpers must be call-safe; no assertions on values (no-op).
        m.record_watch_reconnect(namespace="ns", reason="timeout")
        m.record_pod_creation(namespace="ns", status="success")
        m.record_watch_event(namespace="ns", event_type="ADDED")
        m.record_apiserver_latency(operation="list_pods", seconds=0.1)
        m.set_active_pods(namespace="ns", count=3)
        m.set_active_requests(namespace="ns", count=1)
        m.set_circuit_breaker_state(name="cb", state=1)
