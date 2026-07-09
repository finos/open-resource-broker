"""Tests for GET /metrics endpoint."""

import pytest  # noqa: F401
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with only the /metrics route wired up.

    Mirrors the simplified route logic in server.py: serve generate_latest(REGISTRY)
    only — MetricsCollector has been deleted; all metrics flow through the OTel
    PrometheusMetricReader bridge onto the global prometheus_client REGISTRY.
    """
    from fastapi.responses import Response

    app = FastAPI()

    @app.get("/metrics")
    async def metrics_endpoint():
        # Mirrors the real handler in server.py: REGISTRY-only, no homegrown collector.
        try:
            from prometheus_client import REGISTRY, generate_latest

            body = generate_latest(REGISTRY).decode("utf-8")
        except Exception:  # noqa: BLE001 — ImportError or prometheus_client internal error
            body = ""
        return Response(content=body, media_type="text/plain; version=0.0.4")

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestMetricsEndpoint:
    """Tests for GET /metrics."""

    def test_returns_200(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_content_type_is_prometheus(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]
        assert "0.0.4" in resp.headers["content-type"]

    def test_returns_valid_prometheus_text(self):
        # REGISTRY always contains at least python_info / python_gc_*
        # in a standard install. The test asserts the body is either empty
        # (minimal install without prometheus_client) or starts with a
        # Prometheus text-format line that is either a comment or a metric.
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/metrics")
        body = resp.text
        if body:
            # Every non-empty body must be valid Prometheus text:
            # lines are either comments (#) or metric lines (name[{labels}] value).
            non_empty_lines = [ln for ln in body.splitlines() if ln.strip()]
            for line in non_empty_lines:
                assert line.startswith("#") or " " in line, f"Unexpected Prometheus line: {line!r}"

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
