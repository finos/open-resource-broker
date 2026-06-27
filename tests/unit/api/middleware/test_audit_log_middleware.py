"""Unit tests for AuditLogMiddleware."""

from __future__ import annotations

import logging
import time
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient
from fastapi import FastAPI

from orb.api.middleware.audit_log_middleware import AuditLogMiddleware


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_app_with_middleware():
    """Return a minimal FastAPI app with AuditLogMiddleware attached."""
    app = FastAPI()
    app.add_middleware(AuditLogMiddleware)

    @app.get("/api/v1/machines")
    async def machines():
        return {"ok": True}

    @app.post("/api/v1/machines")
    async def create_machine():
        return {"created": True}

    @app.get("/api/v1/config/settings")
    async def config_settings():
        return {"cfg": True}

    @app.get("/api/v1/admin/status")
    async def admin_status():
        return {"admin": True}

    @app.get("/api/v1/me")
    async def me():
        return {"user": "self"}

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/api/v1/requests")
    async def create_request():
        return {"req": True}

    return app


# ---------------------------------------------------------------------------
# dispatch matrix tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditLogMiddlewareDispatch:
    def test_get_non_audit_prefix_not_audited(self, caplog):
        """A plain GET not matching AUDIT_ALWAYS_PREFIXES is skipped."""
        app = _make_app_with_middleware()
        client = TestClient(app, raise_server_exceptions=True)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            resp = client.get("/api/v1/machines")

        assert resp.status_code == 200
        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert audit_records == [], "GET /api/v1/machines should not be audited"

    def test_get_config_prefix_is_audited(self, caplog):
        """GET /api/v1/config/... must be audited even though it's a safe verb."""
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            resp = client.get("/api/v1/config/settings")

        assert resp.status_code == 200
        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert len(audit_records) == 1

    def test_get_admin_prefix_is_audited(self, caplog):
        """GET /api/v1/admin/... must be audited."""
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client.get("/api/v1/admin/status")

        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert len(audit_records) == 1

    def test_get_me_prefix_is_audited(self, caplog):
        """GET /api/v1/me must be audited."""
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client.get("/api/v1/me")

        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert len(audit_records) == 1

    def test_non_get_mutating_request_is_audited(self, caplog):
        """POST /api/v1/requests must always be audited."""
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client.post("/api/v1/requests")

        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert len(audit_records) == 1

    def test_health_path_not_audited(self, caplog):
        """GET /health is in SAFE_PATHS and should never be audited."""
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client.get("/health")

        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert audit_records == []


# ---------------------------------------------------------------------------
# audit log fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditLogFields:
    def test_latency_ms_present_in_log(self, caplog):
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client.post("/api/v1/machines")

        records = [r for r in caplog.records if r.name == "orb.audit"]
        assert len(records) == 1
        rec = records[0]
        latency = rec.__dict__.get("latency_ms")
        assert latency is not None
        assert isinstance(latency, float)
        assert latency >= 0

    def test_client_ip_captured(self, caplog):
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client.post("/api/v1/machines")

        records = [r for r in caplog.records if r.name == "orb.audit"]
        assert records[0].__dict__.get("client_ip") is not None

    def test_user_id_defaults_to_anonymous(self, caplog):
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client.post("/api/v1/machines")

        records = [r for r in caplog.records if r.name == "orb.audit"]
        assert records[0].__dict__.get("user_id") == "anonymous"

    def test_status_code_recorded(self, caplog):
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client.post("/api/v1/machines")

        records = [r for r in caplog.records if r.name == "orb.audit"]
        assert records[0].__dict__.get("status_code") == 200

    def test_method_and_path_recorded(self, caplog):
        app = _make_app_with_middleware()
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client.post("/api/v1/requests")

        records = [r for r in caplog.records if r.name == "orb.audit"]
        rec = records[0]
        assert rec.__dict__.get("method") == "POST"
        assert rec.__dict__.get("path") == "/api/v1/requests"


# ---------------------------------------------------------------------------
# latency monotonic semantics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditLogLatencySemantics:
    def test_latency_uses_monotonic_clock(self, caplog):
        """Latency must be measured with time.monotonic, not wall clock."""
        app = _make_app_with_middleware()
        client = TestClient(app)

        call_times: list[float] = []
        original_monotonic = time.monotonic

        def patched_monotonic():
            t = original_monotonic()
            call_times.append(t)
            return t

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            with patch("orb.api.middleware.audit_log_middleware.time.monotonic", patched_monotonic):
                client.post("/api/v1/machines")

        # monotonic must have been called at least twice (start + end)
        assert len(call_times) >= 2
