"""Unit tests for the /me identity endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import CurrentUser, get_current_user
from orb.api.routers.me import router as me_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(me_router)
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestMeEndpoint:
    def test_anonymous_user_returns_401(self):
        """An anonymous caller (username == 'anonymous') is rejected with 401."""
        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="anonymous", role="viewer"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me/")
        assert resp.status_code == 401

    def test_viewer_returns_user_info(self):
        """An authenticated viewer receives their username, role and permissions."""
        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="alice", role="viewer"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "alice"
        assert body["role"] == "viewer"
        assert "read" in body["permissions"]

    def test_viewer_permissions_contain_only_read(self):
        """Viewer role grants only the 'read' permission."""
        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="bob", role="viewer"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me/")
        assert resp.status_code == 200
        assert resp.json()["permissions"] == ["read"]

    def test_admin_returns_role_admin(self):
        """An authenticated admin receives role='admin' and full permissions."""
        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="carol", role="admin"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "admin"
        # Admin permissions include template management.
        assert "create_template" in body["permissions"]
        assert "delete_template" in body["permissions"]

    def test_operator_returns_role_operator(self):
        """An authenticated operator receives role='operator'."""
        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="dave", role="operator"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "operator"
        assert "request_machines" in body["permissions"]

    def test_response_includes_username_field(self):
        """Response shape always includes a 'username' key."""
        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="eve", role="viewer"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me/")
        assert "username" in resp.json()

    def test_response_includes_permissions_list(self):
        """Response shape always includes a 'permissions' list."""
        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="frank", role="viewer"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/me/")
        assert isinstance(resp.json()["permissions"], list)
