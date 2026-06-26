"""Unit tests for the admin router — POST /admin/database/wipe."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.routers.admin import router as admin_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def admin_app():
    """Minimal FastAPI app with only the admin router mounted."""
    from fastapi.responses import JSONResponse

    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(admin_router)

    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(__request, exc):
        # Re-raise HTTPExceptions so FastAPI handles the status code itself.
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            raise exc
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


def _make_config_port(allow_destructive: bool = True, environment: str = "development"):
    """Return a MagicMock ConfigurationPort with the given settings."""
    config_port = MagicMock()
    config_port.get_configuration_value.side_effect = lambda key, default=None: {
        "allow_destructive_admin": allow_destructive,
        "environment": environment,
    }.get(key, default)
    return config_port


def _make_repositories(machines=None, requests=None, templates=None):
    """Return MagicMock repository objects with default empty find_all()."""
    machine_repo = MagicMock()
    machine_repo.find_all.return_value = machines or []

    request_repo = MagicMock()
    request_repo.find_all.return_value = requests or []

    template_repo = MagicMock()
    template_repo.find_all.return_value = templates or []

    return machine_repo, request_repo, template_repo


def _make_container(
    config_port,
    machine_repo,
    request_repo,
    template_repo,
):
    """Return a MagicMock DI container that resolves the given objects."""
    from orb.domain.base import UnitOfWorkFactory
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.domain.machine.repository import MachineRepository
    from orb.domain.request.repository import RequestRepository
    from orb.domain.template.repository import TemplateRepository

    # Wipe service now resolves via UnitOfWorkFactory → repos exposed on the UoW.
    uow = MagicMock()
    uow.machines = machine_repo
    uow.requests = request_repo
    uow.templates = template_repo
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    uow_factory = MagicMock()
    uow_factory.create_unit_of_work = MagicMock(return_value=uow)

    type_map = {
        ConfigurationPort: config_port,
        MachineRepository: machine_repo,
        RequestRepository: request_repo,
        TemplateRepository: template_repo,
        UnitOfWorkFactory: uow_factory,
    }
    container = MagicMock()
    container.get.side_effect = lambda t: type_map[t]
    return container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wipe_post(client: TestClient, body: dict | None = None) -> "httpx.Response":  # type: ignore[name-defined]
    if body is None:
        body = {"confirm": "WIPE"}
    return client.post("/admin/database/wipe", json=body)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestAdminWipeEndpoint:
    """Tests for POST /admin/database/wipe."""

    # ── Guard: feature disabled ─────────────────────────────────────────────

    def test_returns_403_when_allow_destructive_admin_is_false(self, admin_app):
        """Endpoint returns 403 when allow_destructive_admin=False."""
        config_port = _make_config_port(allow_destructive=False, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with patch(
            "orb.api.routers.admin.get_di_container", return_value=container
        ):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client)

        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["code"] == "DESTRUCTIVE_ADMIN_DISABLED"

    # ── Guard: production environment ───────────────────────────────────────

    def test_returns_403_when_environment_is_production(self, admin_app):
        """Endpoint returns 403 when environment='production', even with flag enabled."""
        config_port = _make_config_port(allow_destructive=True, environment="production")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with patch(
            "orb.api.routers.admin.get_di_container", return_value=container
        ):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client)

        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["code"] == "PRODUCTION_ENVIRONMENT"

    def test_returns_403_for_production_environment_case_insensitive(self, admin_app):
        """Production check is case-insensitive ('Production', 'PRODUCTION', etc.)."""
        for env_value in ("Production", "PRODUCTION", "production"):
            config_port = _make_config_port(allow_destructive=True, environment=env_value)
            machine_repo, request_repo, template_repo = _make_repositories()
            container = _make_container(config_port, machine_repo, request_repo, template_repo)

            with patch(
                "orb.api.routers.admin.get_di_container", return_value=container
            ):
                client = TestClient(admin_app, raise_server_exceptions=False)
                r = _wipe_post(client)

            assert r.status_code == 403, f"expected 403 for environment='{env_value}'"
            assert r.json()["detail"]["code"] == "PRODUCTION_ENVIRONMENT"

    # ── Guard: bad confirmation token ───────────────────────────────────────

    def test_returns_400_when_confirm_token_is_wrong(self, admin_app):
        """Endpoint returns 400 when body has wrong confirm value."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with patch(
            "orb.api.routers.admin.get_di_container", return_value=container
        ):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client, body={"confirm": "wipe"})  # lowercase — must not match

        assert r.status_code == 400
        body = r.json()
        assert body["error"]["code"] == "MISSING_CONFIRMATION"

    def test_returns_400_when_confirm_token_is_missing(self, admin_app):
        """Endpoint returns 400 when confirm key is absent from body."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with patch(
            "orb.api.routers.admin.get_di_container", return_value=container
        ):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client, body={})

        assert r.status_code == 400
        body = r.json()
        assert body["error"]["code"] == "MISSING_CONFIRMATION"

    def test_returns_400_when_confirm_token_is_empty_string(self, admin_app):
        """Endpoint returns 400 when confirm is an empty string."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with patch(
            "orb.api.routers.admin.get_di_container", return_value=container
        ):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client, body={"confirm": ""})

        assert r.status_code == 400

    # ── Happy path ──────────────────────────────────────────────────────────

    def test_returns_200_and_wipes_on_happy_path(self, admin_app):
        """Happy path: 200 with wiped=True and correct counts."""
        config_port = _make_config_port(allow_destructive=True, environment="development")

        # Fake aggregate objects with id attributes that the service calls delete() with.
        fake_machine = MagicMock()
        fake_request = MagicMock()
        fake_template = MagicMock()

        machine_repo, request_repo, template_repo = _make_repositories(
            machines=[fake_machine],
            requests=[fake_request],
            templates=[fake_template],
        )
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with patch(
            "orb.api.routers.admin.get_di_container", return_value=container
        ):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client)

        assert r.status_code == 200
        body = r.json()
        assert body["wiped"] is True
        assert body["rows_deleted"] == 3
        assert set(body["tables_truncated"]) == {"machines", "requests", "templates"}

    def test_delete_called_for_each_entity(self, admin_app):
        """Verifies delete() is called once per entity in each repository.

        Force the fallback path (per-entity ``repo.delete``) by clearing
        ``storage_strategy`` — MagicMock auto-creates it, which would
        otherwise route through ``delete_batch`` and never touch
        ``repo.delete``.
        """
        config_port = _make_config_port(allow_destructive=True, environment="development")

        machine_a = MagicMock()
        machine_b = MagicMock()
        machine_repo, request_repo, template_repo = _make_repositories(
            machines=[machine_a, machine_b],
        )
        for repo in (machine_repo, request_repo, template_repo):
            del repo.storage_strategy
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with patch(
            "orb.api.routers.admin.get_di_container", return_value=container
        ):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client)

        assert r.status_code == 200
        assert machine_repo.delete.call_count == 2
        # request and template repos had empty find_all
        assert request_repo.delete.call_count == 0
        assert template_repo.delete.call_count == 0

    def test_empty_database_returns_zero_rows_deleted(self, admin_app):
        """Wipe on an already-empty database returns rows_deleted=0."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with patch(
            "orb.api.routers.admin.get_di_container", return_value=container
        ):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client)

        assert r.status_code == 200
        assert r.json()["rows_deleted"] == 0

    # ── Non-production environments ─────────────────────────────────────────

    def test_staging_environment_is_allowed(self, admin_app):
        """Non-production environments (staging, testing) are permitted."""
        for env in ("staging", "testing", "development"):
            config_port = _make_config_port(allow_destructive=True, environment=env)
            machine_repo, request_repo, template_repo = _make_repositories()
            container = _make_container(config_port, machine_repo, request_repo, template_repo)

            with patch(
                "orb.api.routers.admin.get_di_container", return_value=container
            ):
                client = TestClient(admin_app, raise_server_exceptions=False)
                r = _wipe_post(client)

            assert r.status_code == 200, (
                f"expected 200 for environment='{env}', got {r.status_code}: {r.text}"
            )

    # ── Fail-closed when config is unavailable ──────────────────────────────

    def test_returns_403_when_config_cannot_be_read(self, admin_app):
        """When DI container raises, the endpoint fails closed with 403."""
        container = MagicMock()
        container.get.side_effect = RuntimeError("DI container exploded")

        with patch(
            "orb.api.routers.admin.get_di_container", return_value=container
        ):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client)

        # Fails closed — production environment assumed when config unreadable.
        assert r.status_code == 403
