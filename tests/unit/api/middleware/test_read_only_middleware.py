"""Unit tests for ReadOnlyMiddleware."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from orb.api.middleware.read_only_middleware import ReadOnlyMiddleware, _ALLOWED_PATHS


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_app(enabled: bool):
    """Return a FastAPI app with ReadOnlyMiddleware configured."""
    app = FastAPI()
    app.add_middleware(ReadOnlyMiddleware, enabled=enabled)

    @app.get("/api/v1/machines")
    async def get_machines():
        return {"machines": []}

    @app.post("/api/v1/machines")
    async def create_machine():
        return {"created": True}

    @app.post("/api/v1/requests")
    async def create_request():
        return {"req": True}

    @app.post("/_event/some-event")
    async def reflex_event():
        return {"ok": True}

    @app.post("/_upload/file")
    async def reflex_upload():
        return {"ok": True}

    @app.post("/health")
    async def health_post():
        return {"ok": True}

    return app


# ---------------------------------------------------------------------------
# Enabled + mutating → 403
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadOnlyMiddlewareEnabled:
    def test_post_is_blocked_when_enabled(self):
        client = TestClient(_make_app(enabled=True), raise_server_exceptions=True)
        resp = client.post("/api/v1/machines")
        assert resp.status_code == 403

    def test_403_body_contains_read_only_mode_code(self):
        client = TestClient(_make_app(enabled=True))
        resp = client.post("/api/v1/machines")
        body = resp.json()
        assert body["error"]["code"] == "READ_ONLY_MODE"
        assert body["success"] is False

    def test_post_to_request_blocked(self):
        client = TestClient(_make_app(enabled=True))
        resp = client.post("/api/v1/requests")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Enabled + safe verb → passes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadOnlyMiddlewareSafeVerbs:
    def test_get_passes_when_enabled(self):
        client = TestClient(_make_app(enabled=True))
        resp = client.get("/api/v1/machines")
        assert resp.status_code == 200

    def test_get_returns_normal_body(self):
        client = TestClient(_make_app(enabled=True))
        resp = client.get("/api/v1/machines")
        assert resp.json() == {"machines": []}


# ---------------------------------------------------------------------------
# Enabled + allowed path → passes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadOnlyMiddlewareAllowedPaths:
    def test_post_health_passes_when_enabled(self):
        client = TestClient(_make_app(enabled=True))
        resp = client.post("/health")
        assert resp.status_code == 200

    def test_post_reflex_event_prefix_passes(self):
        """/_event/... is an allowed path prefix — Reflex websocket sub-paths."""
        client = TestClient(_make_app(enabled=True))
        resp = client.post("/_event/some-event")
        assert resp.status_code == 200

    def test_post_reflex_upload_prefix_passes(self):
        """/_upload/... is an allowed path prefix."""
        client = TestClient(_make_app(enabled=True))
        resp = client.post("/_upload/file")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Disabled + mutating → passes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadOnlyMiddlewareDisabled:
    def test_post_passes_when_disabled(self):
        client = TestClient(_make_app(enabled=False))
        resp = client.post("/api/v1/machines")
        assert resp.status_code == 200

    def test_get_passes_when_disabled(self):
        client = TestClient(_make_app(enabled=False))
        resp = client.get("/api/v1/machines")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Allowed path constants
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAllowedPathConstants:
    def test_event_path_in_allowed_paths(self):
        assert "/_event" in _ALLOWED_PATHS

    def test_upload_path_in_allowed_paths(self):
        assert "/_upload" in _ALLOWED_PATHS

    def test_health_in_allowed_paths(self):
        assert "/health" in _ALLOWED_PATHS
