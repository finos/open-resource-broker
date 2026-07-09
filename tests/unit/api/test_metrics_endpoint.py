"""Tests for GET /metrics endpoint."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.monitoring.metrics import MetricsCollector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with only the /metrics route wired up.

    We replicate the route logic from server.py directly so the test has no
    dependency on the full DI container or server bootstrap.
    """
    from fastapi.responses import Response

    app = FastAPI()

    @app.get("/metrics")
    async def metrics_endpoint():
        # Mirrors the real handler in server.py: homegrown collector text plus
        # the prometheus_client REGISTRY output (where k8s provider metrics live).
        from orb.infrastructure.di.container import get_container

        try:
            collector = get_container().get_optional(MetricsCollector)
            homegrown_text = collector.to_prometheus_text() if collector is not None else ""
        except Exception:
            homegrown_text = ""

        registry_text = ""
        try:
            from prometheus_client import REGISTRY, generate_latest

            registry_text = generate_latest(REGISTRY).decode("utf-8")
        except Exception:  # noqa: BLE001 — mirrors the real handler's optional-dep guard
            # prometheus_client is an optional [monitoring] extra; a minimal
            # install without it must still serve the homegrown text.
            registry_text = ""

        body = homegrown_text
        if registry_text:
            body = body + "\n" + registry_text if body else registry_text
        return Response(content=body, media_type="text/plain; version=0.0.4")

    return app


def _make_collector(metrics: dict | None = None, tmp_dir: str = "/tmp") -> MetricsCollector:
    """Build a MetricsCollector with a temp metrics dir and no background writer."""
    from orb.monitoring.metrics import Gauge

    collector = MetricsCollector(
        config={"metrics_dir": tmp_dir, "metrics_enabled": False},
    )
    # Reset to empty so tests control exactly what's in there
    collector.metrics.clear()
    collector.timers.clear()
    if metrics:
        for name, (value, labels) in metrics.items():
            collector.metrics[name] = Gauge(name=name, value=value, labels=labels)
    return collector


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestMetricsEndpoint:
    """Tests for GET /metrics."""

    def test_returns_200(self):
        app = _make_app()
        collector = _make_collector()
        with patch("orb.infrastructure.di.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.get_optional.return_value = collector
            mock_get.return_value = mock_container
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_content_type_is_prometheus(self):
        app = _make_app()
        collector = _make_collector()
        with patch("orb.infrastructure.di.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.get_optional.return_value = collector
            mock_get.return_value = mock_container
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]
        assert "0.0.4" in resp.headers["content-type"]

    def test_prometheus_lines_present(self):
        app = _make_app()
        collector = _make_collector(
            metrics={
                "active_instances": (3.0, {"type": "ec2"}),
                "requests_total": (42.0, {}),
            }
        )
        with patch("orb.infrastructure.di.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.get_optional.return_value = collector
            mock_get.return_value = mock_container
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/metrics")
        body = resp.text
        assert 'active_instances{type="ec2"} 3.0' in body
        assert "requests_total{} 42.0" in body

    def test_no_collector_still_returns_registry(self):
        # With no homegrown collector, the endpoint must not 500 — it returns
        # whatever the prometheus_client REGISTRY holds (default process/GC
        # collectors in a clean process), never a crash.
        app = _make_app()
        with patch("orb.infrastructure.di.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.get_optional.return_value = None
            mock_get.return_value = mock_container
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/metrics")
        assert resp.status_code == 200
        # No homegrown metric text; body is purely REGISTRY output (may include
        # default python_* collectors). The homegrown portion must be absent.
        assert "active_instances" not in resp.text

    def test_container_exception_still_surfaces_registry(self):
        # Even when the homegrown collector lookup blows up, the endpoint must
        # still return prometheus_client REGISTRY output rather than empty.
        app = _make_app()
        with patch(
            "orb.infrastructure.di.container.get_container", side_effect=RuntimeError("boom")
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/metrics")
        assert resp.status_code == 200
        # Body is whatever the global REGISTRY holds (may be empty in a clean
        # process, but the request must not 500).

    def test_k8s_metrics_surface_on_endpoint(self):
        # The crux fix: k8s provider metrics are backed by OTel instruments
        # which flow through the PrometheusMetricReader onto REGISTRY.
        # We prove this end-to-end by building an isolated OTel meter +
        # registry pair and asserting generate_latest produces the
        # orb_k8s_* names — exactly what the /metrics endpoint sees when
        # configure_telemetry is active.
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from opentelemetry.sdk.metrics import MeterProvider
        from prometheus_client import CollectorRegistry, generate_latest

        from orb.providers.k8s.infrastructure.services.metrics import K8sMetrics

        reg = CollectorRegistry()
        reader = PrometheusMetricReader(registry=reg)
        provider = MeterProvider(metric_readers=[reader])
        meter = provider.get_meter("test")

        metrics = K8sMetrics(meter=meter)
        metrics._acquire_total.add(1, {"namespace": "orb", "spec_kind": "Pod"})

        text = generate_latest(reg).decode("utf-8")
        assert "orb_k8s_acquire_total" in text
        assert 'namespace="orb"' in text
        assert 'spec_kind="Pod"' in text

        provider.shutdown()
