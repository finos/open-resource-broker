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
        from orb.infrastructure.di.container import get_container

        try:
            collector = get_container().get_optional(MetricsCollector)
            if collector is None:
                return Response(content="", media_type="text/plain; version=0.0.4")
            prometheus_text = collector.to_prometheus_text()
            return Response(content=prometheus_text, media_type="text/plain; version=0.0.4")
        except Exception:
            return Response(content="", media_type="text/plain; version=0.0.4")

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

    def test_no_collector_returns_empty_body(self):
        app = _make_app()
        with patch("orb.infrastructure.di.container.get_container") as mock_get:
            mock_container = MagicMock()
            mock_container.get_optional.return_value = None
            mock_get.return_value = mock_container
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/metrics")
        assert resp.status_code == 200
        assert resp.text == ""

    def test_container_exception_returns_empty_body(self):
        app = _make_app()
        with patch(
            "orb.infrastructure.di.container.get_container", side_effect=RuntimeError("boom")
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/metrics")
        assert resp.status_code == 200
        assert resp.text == ""
