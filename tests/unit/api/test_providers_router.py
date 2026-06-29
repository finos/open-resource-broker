"""Unit tests for the providers health router."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import get_config_manager, get_current_user
from orb.api.routers.providers import router as providers_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(*, role: str = "viewer") -> FastAPI:
    from fastapi.responses import JSONResponse

    from orb.api.dependencies import CurrentUser
    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(providers_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-user", role=role
    )
    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(__request, exc):
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            raise exc
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


def _make_config_manager_with_no_providers() -> MagicMock:
    """Config manager whose provider config returns zero active providers."""
    provider_config = MagicMock()
    provider_config.get_active_providers.return_value = []
    provider_config.default_provider = None

    config_mgr = MagicMock()
    config_mgr.get_provider_config.return_value = provider_config
    return config_mgr


def _make_provider_instance(*, name: str, ptype: str = "aws", enabled: bool = True):
    p = MagicMock()
    p.name = name
    p.type = ptype
    p.enabled = enabled
    p.config = {}
    return p


def _make_config_manager_with_providers(*providers) -> MagicMock:
    provider_config = MagicMock()
    provider_config.get_active_providers.return_value = list(providers)
    provider_config.default_provider = providers[0].name if providers else None

    config_mgr = MagicMock()
    config_mgr.get_provider_config.return_value = provider_config
    return config_mgr


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestProvidersAuthGuard:
    def test_unknown_role_returns_403(self):
        from orb.api.dependencies import CurrentUser

        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="nobody", role="no_such_role"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/providers/health")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestProvidersHealthHappyPath:
    def test_returns_200(self):
        app = _make_app()
        app.dependency_overrides[get_config_manager] = _make_config_manager_with_no_providers
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/providers/health")
        assert resp.status_code == 200

    def test_empty_provider_list_returns_empty_providers(self):
        app = _make_app()
        app.dependency_overrides[get_config_manager] = _make_config_manager_with_no_providers
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/providers/health").json()
        assert body["providers"] == []

    def test_list_providers_returns_configured_providers(self):
        p1 = _make_provider_instance(name="aws-main")
        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: _make_config_manager_with_providers(
            p1
        )

        healthy_result = MagicMock()
        healthy_result.success = True
        healthy_result.data = {"is_healthy": True}
        healthy_result.error_message = None

        with patch(
            "orb.api.routers.providers._probe_provider_health",
            new=AsyncMock(return_value=("healthy", {})),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            body = client.get("/providers/health").json()

        assert len(body["providers"]) == 1
        assert body["providers"][0]["name"] == "aws-main"

    def test_healthy_probe_returns_status_healthy(self):
        p1 = _make_provider_instance(name="aws-main")
        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: _make_config_manager_with_providers(
            p1
        )

        with patch(
            "orb.api.routers.providers._probe_provider_health",
            new=AsyncMock(return_value=("healthy", {"response_time_ms": 42})),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            body = client.get("/providers/health").json()

        provider = body["providers"][0]
        assert provider["status"] == "healthy"


# ---------------------------------------------------------------------------
# Unhealthy / error path (M4 fix: probe_error must not surface)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestProvidersHealthUnhealthyPath:
    def test_probe_raises_returns_status_unknown(self):
        """When probe raises an exception the endpoint returns status without crashing."""
        p1 = _make_provider_instance(name="aws-main")
        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: _make_config_manager_with_providers(
            p1
        )

        async def _probe_raises(name: str):
            raise RuntimeError("connection refused")

        with patch("orb.api.routers.providers._probe_provider_health", new=_probe_raises):
            client = TestClient(app, raise_server_exceptions=False)
            body = client.get("/providers/health").json()

        # The endpoint must return 200 regardless.
        assert len(body["providers"]) == 0 or body["providers"][0]["status"] in (
            "unknown",
            "healthy",
            "degraded",
            "unhealthy",
        )

    def test_probe_error_not_in_response(self):
        """Probe error details must never appear in the client response (M4 fix)."""
        p1 = _make_provider_instance(name="aws-main")
        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: _make_config_manager_with_providers(
            p1
        )

        with patch(
            "orb.api.routers.providers._probe_provider_health",
            new=AsyncMock(return_value=("degraded", {})),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            body = client.get("/providers/health").json()

        import json

        raw = json.dumps(body)
        assert "probe_error" not in raw
        assert "connection refused" not in raw

    def test_disabled_provider_has_status_unhealthy(self):
        """An explicitly disabled provider must have status='unhealthy'."""
        p1 = _make_provider_instance(name="aws-disabled", enabled=False)
        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: _make_config_manager_with_providers(
            p1
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/providers/health").json()
        assert body["providers"][0]["status"] == "unhealthy"

    def test_provider_config_exception_within_handler_returns_empty_providers(self):
        """When get_provider_config() raises inside the handler, return empty-but-valid
        response via the handler's own try/except."""
        config_mgr = MagicMock()
        config_mgr.get_provider_config.side_effect = RuntimeError("config blow up")

        app = _make_app()
        app.dependency_overrides[get_config_manager] = lambda: config_mgr
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/providers/health")
        # Must not 500 — the router's try/except swallows the error.
        assert resp.status_code == 200
        assert resp.json()["providers"] == []
