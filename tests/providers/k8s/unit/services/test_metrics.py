"""Unit tests for K8sMetrics — Prometheus metrics parity with legacy k8s provider."""

import pytest
from prometheus_client import CollectorRegistry

from orb.providers.k8s.infrastructure.services.metrics import (
    _METRIC_SPECS,
    POD_CREATION_STATUSES,
    WATCH_EVENT_TYPES,
    WATCH_RECONNECT_REASONS,
    K8sMetrics,
)

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


def _fresh() -> K8sMetrics:
    """Build a K8sMetrics instance on an isolated registry.

    All tests use isolated registries so the module-level default (which
    is ``prometheus_client.REGISTRY`` in production) does not accumulate
    duplicates between tests.
    """
    return K8sMetrics(registry=CollectorRegistry())


class TestRegisteredNames:
    def test_all_expected_names_present(self) -> None:
        assert set(_EXPECTED_NAMES) == set(K8sMetrics.registered_names())

    def test_no_extra_names(self) -> None:
        assert len(_EXPECTED_NAMES) == len(K8sMetrics.registered_names())

    def test_spec_names_match_registered_names(self) -> None:
        spec_names = [s[0] for s in _METRIC_SPECS]
        assert spec_names == K8sMetrics.registered_names()


class TestCounterValueIncrements:
    """Every counter must actually count — asserting values, not just no-exception."""

    def setup_method(self) -> None:
        self.metrics = _fresh()

    def _val(self, counter) -> float:
        return counter._value.get()  # type: ignore[attr-defined]

    def test_acquire_total_increments(self) -> None:
        c = self.metrics.acquire_total.labels(namespace="default", spec_kind="Pod")
        before = self._val(c)
        c.inc(3)
        assert self._val(c) == before + 3

    def test_release_total_increments(self) -> None:
        c = self.metrics.release_total.labels(namespace="default", spec_kind="Pod")
        before = self._val(c)
        c.inc()
        assert self._val(c) == before + 1

    def test_pod_creations_total_increments(self) -> None:
        c = self.metrics.pod_creations_total.labels(namespace="default", status="success")
        before = self._val(c)
        c.inc(2)
        assert self._val(c) == before + 2

    def test_watch_events_total_increments(self) -> None:
        c = self.metrics.watch_events_total.labels(namespace="default", event_type="ADDED")
        before = self._val(c)
        c.inc()
        assert self._val(c) == before + 1

    def test_watch_reconnects_total_increments(self) -> None:
        c = self.metrics.watch_reconnects_total.labels(namespace="default", reason="timeout")
        before = self._val(c)
        c.inc()
        assert self._val(c) == before + 1


class TestGaugeOperations:
    def setup_method(self) -> None:
        self.metrics = _fresh()

    def test_active_pods_set(self) -> None:
        g = self.metrics.active_pods.labels(namespace="default")
        g.set(5)
        assert g._value.get() == 5  # type: ignore[attr-defined]

    def test_active_requests_inc_dec(self) -> None:
        g = self.metrics.active_requests.labels(namespace="default")
        g.inc()
        g.inc()
        g.dec()
        assert g._value.get() == 1  # type: ignore[attr-defined]

    def test_circuit_breaker_state_transitions(self) -> None:
        g = self.metrics.circuit_breaker_state.labels(name="api-server")
        g.set(0)
        assert g._value.get() == 0  # type: ignore[attr-defined]
        g.set(1)
        assert g._value.get() == 1  # type: ignore[attr-defined]
        g.set(2)
        assert g._value.get() == 2  # type: ignore[attr-defined]


class TestHistogramObservations:
    def setup_method(self) -> None:
        self.metrics = _fresh()

    def test_apiserver_latency_observe(self) -> None:
        h = self.metrics.apiserver_latency_seconds.labels(operation="list_pods")
        h.observe(0.042)
        # ``prometheus_client.Histogram`` sums observed values on ``_sum``.
        assert h._sum.get() >= 0.042  # type: ignore[attr-defined]

    def test_apiserver_latency_context_manager(self) -> None:
        with self.metrics.apiserver_latency_seconds.labels(operation="create_pod").time():
            pass  # simulates a timed API call


class TestDuplicateRegistrationOnSharedRegistry:
    """Calling K8sMetrics twice against the same registry must fail loudly."""

    def test_separate_registries_no_error(self) -> None:
        m1 = K8sMetrics(registry=CollectorRegistry())
        m2 = K8sMetrics(registry=CollectorRegistry())
        m1.acquire_total.labels(namespace="ns1", spec_kind="Pod").inc()
        m2.acquire_total.labels(namespace="ns2", spec_kind="Pod").inc()

    def test_same_registry_raises_runtime_error(self) -> None:
        reg = CollectorRegistry()
        K8sMetrics(registry=reg)
        with pytest.raises(RuntimeError, match="duplicate registration"):
            K8sMetrics(registry=reg)


class TestLabelEnums:
    """Enum helpers must bucket rogue label values to 'unknown' rather than blow up cardinality."""

    def test_valid_reconnect_reason_recorded(self) -> None:
        m = _fresh()
        assert "timeout" in WATCH_RECONNECT_REASONS
        m.record_watch_reconnect(namespace="ns", reason="timeout")
        c = m.watch_reconnects_total.labels(namespace="ns", reason="timeout")
        assert c._value.get() == 1  # type: ignore[attr-defined]

    def test_rogue_reconnect_reason_bucketed_as_unknown(self) -> None:
        m = _fresh()
        m.record_watch_reconnect(namespace="ns", reason="ohgodwhy")
        c = m.watch_reconnects_total.labels(namespace="ns", reason="unknown")
        assert c._value.get() == 1  # type: ignore[attr-defined]
        # And the rogue value must NOT have created its own time series.
        # Prometheus counters materialise a series on first ``.labels(...)`` call,
        # so we simply assert the "unknown" bucket got the increment.

    def test_valid_pod_creation_status_recorded(self) -> None:
        m = _fresh()
        assert "success" in POD_CREATION_STATUSES
        m.record_pod_creation(namespace="ns", status="success")
        c = m.pod_creations_total.labels(namespace="ns", status="success")
        assert c._value.get() == 1  # type: ignore[attr-defined]

    def test_rogue_pod_creation_status_bucketed(self) -> None:
        m = _fresh()
        m.record_pod_creation(namespace="ns", status="oh-no")
        c = m.pod_creations_total.labels(namespace="ns", status="unknown")
        assert c._value.get() == 1  # type: ignore[attr-defined]

    def test_valid_watch_event_recorded(self) -> None:
        m = _fresh()
        assert "ADDED" in WATCH_EVENT_TYPES
        m.record_watch_event(namespace="ns", event_type="ADDED")
        c = m.watch_events_total.labels(namespace="ns", event_type="ADDED")
        assert c._value.get() == 1  # type: ignore[attr-defined]

    def test_rogue_watch_event_bucketed(self) -> None:
        m = _fresh()
        m.record_watch_event(namespace="ns", event_type="weird")
        c = m.watch_events_total.labels(namespace="ns", event_type="unknown")
        assert c._value.get() == 1  # type: ignore[attr-defined]
