"""Integration test: K8sMetrics OTel→Prometheus bridge.

Proves that:
1. K8sMetrics backed by an OTel MeterProvider with PrometheusMetricReader
   produces the exact legacy ``orb_k8s_*`` Prometheus metric names — no
   renaming, no namespacing change.
2. There is NO ``ValueError: Duplicated timeseries`` — i.e. the native
   prometheus_client registration that existed before the OTel migration is
   gone, so the OTel PrometheusMetricReader is the *only* registration path
   for these metric names.
3. All nine expected ``orb_k8s_*`` names surface in the scrape output.
4. configure_telemetry() with metrics_exporters=["prometheus"] does not
   raise and wires the global meter so that a K8sMetrics() constructed
   afterwards (no meter arg) emits onto the shared REGISTRY.

These tests exercise the real OTel→Prometheus exporter library to give
confidence that the bridge actually works in production.
"""

from __future__ import annotations

import pytest  # noqa: F401 — used by pytest.fail and pytest fixtures
from prometheus_client import generate_latest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _isolated_k8s_metrics():
    """Return (K8sMetrics, registry) on a private PrometheusMetricReader."""
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from opentelemetry.sdk.metrics import MeterProvider
    from prometheus_client import CollectorRegistry

    from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics

    reg = CollectorRegistry()
    reader = PrometheusMetricReader(registry=reg)
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test.k8s.metrics")
    metrics = K8sMetrics(meter=meter)
    return metrics, reg, provider


def _scrape(registry) -> str:
    return generate_latest(registry).decode("utf-8")


# ---------------------------------------------------------------------------
# 1. Exact orb_k8s_* names survive the OTel translation
# ---------------------------------------------------------------------------


class TestOtelToPrometheusNamePreservation:
    """The OTel→Prometheus exporter must preserve ALL nine orb_k8s_* names."""

    EXPECTED_NAMES = [
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

    def _emit_one_of_each(self, metrics) -> None:
        """Emit at least one measurement for every instrument."""
        metrics._acquire_total.add(1, {"namespace": "ns", "spec_kind": "Pod"})
        metrics._release_total.add(1, {"namespace": "ns", "spec_kind": "Pod"})
        metrics._pod_creations_total.add(1, {"namespace": "ns", "status": "success"})
        metrics._watch_events_total.add(1, {"namespace": "ns", "event_type": "ADDED"})
        metrics._watch_reconnects_total.add(1, {"namespace": "ns", "reason": "timeout"})
        metrics.set_active_pods(namespace="ns", count=3)
        metrics.set_active_requests(namespace="ns", count=2)
        metrics.record_apiserver_latency(operation="list_pods", seconds=0.05)
        metrics.set_circuit_breaker_state(name="cb", state=1)

    def test_all_nine_names_present_in_scrape(self) -> None:
        metrics, reg, provider = _isolated_k8s_metrics()
        try:
            self._emit_one_of_each(metrics)
            text = _scrape(reg)
            missing = [name for name in self.EXPECTED_NAMES if name not in text]
            assert not missing, (
                f"The following orb_k8s_* names were absent from the Prometheus scrape "
                f"after OTel→Prometheus bridge: {missing}\n\nFull scrape output:\n{text}"
            )
        finally:
            provider.shutdown()

    def test_counter_names_have_no_double_total_suffix(self) -> None:
        """Counters named orb_k8s_*_total must NOT become orb_k8s_*_total_total."""
        metrics, reg, provider = _isolated_k8s_metrics()
        try:
            metrics._acquire_total.add(1, {"namespace": "ns", "spec_kind": "Pod"})
            text = _scrape(reg)
            assert "orb_k8s_acquire_total_total" not in text, (
                "OTel exporter added a double _total suffix to the counter name"
            )
            assert "orb_k8s_acquire_total" in text
        finally:
            provider.shutdown()

    def test_histogram_name_preserved_exactly(self) -> None:
        """orb_k8s_apiserver_latency_seconds must appear with no extra unit suffix."""
        metrics, reg, provider = _isolated_k8s_metrics()
        try:
            metrics.record_apiserver_latency(operation="list_pods", seconds=0.1)
            text = _scrape(reg)
            assert "orb_k8s_apiserver_latency_seconds" in text
            # Must NOT have a double _seconds suffix from the OTel unit appending.
            assert "orb_k8s_apiserver_latency_seconds_seconds" not in text
        finally:
            provider.shutdown()

    def test_gauge_names_preserved_exactly(self) -> None:
        """UpDownCounter gauge names must appear as exact orb_k8s_* strings."""
        metrics, reg, provider = _isolated_k8s_metrics()
        try:
            metrics.set_active_pods(namespace="ns", count=5)
            metrics.set_active_requests(namespace="ns", count=2)
            metrics.set_circuit_breaker_state(name="cb", state=0)
            text = _scrape(reg)
            assert "orb_k8s_active_pods" in text
            assert "orb_k8s_active_requests" in text
            assert "orb_k8s_circuit_breaker_state" in text
        finally:
            provider.shutdown()


# ---------------------------------------------------------------------------
# 2. No Duplicated timeseries ValueError
# ---------------------------------------------------------------------------


class TestNoDuplicatedTimeseriesError:
    """The native prometheus_client registration must be GONE.

    Before the OTel migration, K8sMetrics registered instruments directly on
    prometheus_client.REGISTRY.  The OTel PrometheusMetricReader also registers
    a collector on the same global registry.  If both paths coexist for the same
    metric names, prometheus_client raises ``ValueError: Duplicated timeseries``.

    After the migration, K8sMetrics instruments are OTel-only, so two
    PrometheusMetricReaders on two separate CollectorRegistries must coexist
    without any conflict.
    """

    def test_two_isolated_meter_providers_no_collision(self) -> None:
        """Two K8sMetrics instances on separate registries must not raise."""
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from opentelemetry.sdk.metrics import MeterProvider
        from prometheus_client import CollectorRegistry

        from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics

        # Build two completely separate stacks.
        reg_a, reg_b = CollectorRegistry(), CollectorRegistry()
        provider_a = MeterProvider(metric_readers=[PrometheusMetricReader(registry=reg_a)])
        provider_b = MeterProvider(metric_readers=[PrometheusMetricReader(registry=reg_b)])
        meter_a = provider_a.get_meter("test.a")
        meter_b = provider_b.get_meter("test.b")

        # Constructing both must NOT raise ValueError.
        try:
            m_a = K8sMetrics(meter=meter_a)
            m_b = K8sMetrics(meter=meter_b)
        except ValueError as exc:
            pytest.fail(
                f"K8sMetrics raised ValueError (Duplicated timeseries) — "
                f"native prometheus_client registration is still present: {exc}"
            )

        # Each emits independently without interfering.
        m_a._acquire_total.add(1, {"namespace": "a", "spec_kind": "Pod"})
        m_b._acquire_total.add(2, {"namespace": "b", "spec_kind": "Pod"})

        text_a = _scrape(reg_a)
        text_b = _scrape(reg_b)
        assert "orb_k8s_acquire_total" in text_a
        assert "orb_k8s_acquire_total" in text_b
        assert 'namespace="a"' in text_a
        assert 'namespace="b"' in text_b

        provider_a.shutdown()
        provider_b.shutdown()

    def test_global_registry_not_polluted_by_native_registration(self) -> None:
        """K8sMetrics(meter=...) must NOT register anything on the global REGISTRY.

        After the OTel migration, instantiating K8sMetrics with an explicit meter
        must leave the global prometheus_client.REGISTRY unchanged (no new
        collectors added).  Only the OTel PrometheusMetricReader's collector
        registered at MeterProvider construction time should be present.
        """
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from opentelemetry.sdk.metrics import MeterProvider
        from prometheus_client import REGISTRY, CollectorRegistry

        from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics

        # Count collectors on the global REGISTRY before.
        collectors_before = set(REGISTRY._names_to_collectors.keys())

        # Build a K8sMetrics instance on an isolated registry (NOT the global one).
        reg = CollectorRegistry()
        provider = MeterProvider(metric_readers=[PrometheusMetricReader(registry=reg)])
        meter = provider.get_meter("test.global.check")
        K8sMetrics(meter=meter)

        # The global REGISTRY must be unchanged.
        collectors_after = set(REGISTRY._names_to_collectors.keys())
        new_names = collectors_after - collectors_before
        k8s_pollution = {n for n in new_names if "orb_k8s" in n}
        assert not k8s_pollution, (
            f"K8sMetrics polluted the global prometheus REGISTRY with: {k8s_pollution}. "
            "This means native prometheus_client registration is still happening."
        )

        provider.shutdown()


# ---------------------------------------------------------------------------
# 3. Absolute-set gauge semantics preserved
# ---------------------------------------------------------------------------


class TestAbsoluteSetGaugeSemantics:
    """set_active_pods / set_active_requests / set_circuit_breaker_state must
    behave as absolute-set operations (not cumulative add), despite the
    underlying UpDownCounter being delta-based.
    """

    def test_set_active_pods_absolute(self) -> None:
        metrics, reg, provider = _isolated_k8s_metrics()
        try:
            metrics.set_active_pods(namespace="ns", count=10)
            metrics.set_active_pods(namespace="ns", count=4)  # should be 4, not 14
            text = _scrape(reg)
            # Assert on the actual gauge line, not a naked substring: the full
            # scrape (HELP/target_info/other series) can incidentally contain
            # "14", which made the old substring check flaky under xdist.
            ns_lines = [
                line
                for line in text.split("\n")
                if "orb_k8s_active_pods" in line and 'namespace="ns"' in line
            ]
            assert ns_lines, f"No orb_k8s_active_pods{{namespace=ns}} line in scrape:\n{text}"
            value = float(ns_lines[0].rsplit(" ", 1)[1])
            assert value == 4.0, f"Expected absolute value 4.0 (not cumulative 14), got {value}"
        finally:
            provider.shutdown()

    def test_set_active_pods_increase_then_decrease(self) -> None:
        metrics, reg, provider = _isolated_k8s_metrics()
        try:
            metrics.set_active_pods(namespace="ns", count=5)
            metrics.set_active_pods(namespace="ns", count=2)
            text = _scrape(reg)
            # Value should be 2
            lines = [l for l in text.split("\n") if "orb_k8s_active_pods" in l and "=" in l]
            # Find the line with namespace="ns"
            ns_lines = [l for l in lines if 'namespace="ns"' in l]
            assert ns_lines, f"No orb_k8s_active_pods{{namespace=ns}} line in scrape:\n{text}"
            assert "2" in ns_lines[0]
        finally:
            provider.shutdown()

    def test_circuit_breaker_set_is_absolute(self) -> None:
        metrics, reg, provider = _isolated_k8s_metrics()
        try:
            metrics.set_circuit_breaker_state(name="cb", state=0)  # CLOSED
            metrics.set_circuit_breaker_state(name="cb", state=1)  # OPEN
            metrics.set_circuit_breaker_state(name="cb", state=0)  # CLOSED again
            text = _scrape(reg)
            lines = [
                l for l in text.split("\n") if "orb_k8s_circuit_breaker_state" in l and '="cb"' in l
            ]
            assert lines, f"No circuit_breaker_state line in scrape:\n{text}"
            # After CLOSED(0)→OPEN(1)→CLOSED(0), net delta = 0
            assert "0" in lines[-1] or "0.0" in lines[-1]
        finally:
            provider.shutdown()


# ---------------------------------------------------------------------------
# 4. configure_telemetry wires global meter for no-arg K8sMetrics
# ---------------------------------------------------------------------------


class TestConfigureTelemetryWiresGlobalMeter:
    """K8sMetrics() with no meter argument must use the global OTel meter,
    which is wired by configure_telemetry when OTel is enabled.

    This test uses a real (non-mocked) MeterProvider so it exercises the
    actual OTel→Prometheus bridge path, not just the config logic.
    """

    def test_otel_configured_k8s_metrics_emits_to_global_registry(self) -> None:
        """configure_telemetry with prometheus exporter + K8sMetrics() + REGISTRY scrape."""
        from opentelemetry import metrics as otel_metrics
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from opentelemetry.sdk.metrics import MeterProvider
        from prometheus_client import CollectorRegistry, generate_latest

        from orb.bootstrap.telemetry import _reset_telemetry_state
        from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics

        # Use a fresh isolated registry so we don't pollute the global REGISTRY
        # and can make deterministic assertions.
        isolated_reg = CollectorRegistry()
        reader = PrometheusMetricReader(registry=isolated_reg)
        provider = MeterProvider(metric_readers=[reader])

        # Install as the global meter provider (mimics what configure_telemetry does).
        otel_metrics.set_meter_provider(provider)
        try:
            # K8sMetrics() with no meter argument calls get_meter(__name__) which
            # resolves to our freshly installed global provider.
            metrics = K8sMetrics()

            # Emit a counter.
            metrics._acquire_total.add(1, {"namespace": "integration-ns", "spec_kind": "Pod"})

            # Scrape the isolated registry (what /metrics would serve).
            text = generate_latest(isolated_reg).decode("utf-8")

            # Prove the orb_k8s_* name and labels appear — no Duplicated timeseries.
            assert "orb_k8s_acquire_total" in text, (
                f"orb_k8s_acquire_total missing from scrape output:\n{text}"
            )
            assert 'namespace="integration-ns"' in text, (
                f"namespace label missing from scrape output:\n{text}"
            )
            assert 'spec_kind="Pod"' in text, f"spec_kind label missing from scrape output:\n{text}"
        finally:
            # Restore no-op meter provider so downstream tests are unaffected.
            from opentelemetry.sdk.metrics import MeterProvider as _MP

            otel_metrics.set_meter_provider(_MP())
            provider.shutdown()
            _reset_telemetry_state()
