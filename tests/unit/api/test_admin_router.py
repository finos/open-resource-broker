"""Unit tests for the admin router — POST /admin/database/wipe and POST /admin/init."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import get_current_user
from orb.api.routers.admin import router as admin_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def admin_app():
    """Minimal FastAPI app with only the admin router mounted.

    Overrides ``get_current_user`` to return an admin identity so the
    ``require_role("admin")`` dependency guard is always satisfied.  Individual
    tests focus on the *config/environment* guards inside
    ``check_destructive_admin_allowed`` — not the role check.
    """
    from fastapi.responses import JSONResponse

    from orb.api.dependencies import CurrentUser
    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(admin_router)

    # Supply a synthetic admin identity so role-guard never interferes.
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-admin", role="admin"
    )

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


def _make_server_config(auth_enabled: bool = True):
    """Return a MagicMock ServerConfig with auth.enabled set."""
    server_config = MagicMock()
    server_config.auth.enabled = auth_enabled
    return server_config


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


def _wipe_post(client: TestClient, body: dict | None = None):
    if body is None:
        body = {"confirm": "WIPE"}
    return client.post("/admin/database/wipe", json=body)


def _patch_container(container, server_config=None):
    """Return a context manager that patches get_di_container in both the admin
    router module and the dependencies module (where check_destructive_admin_allowed lives).

    Also patches get_server_config in dependencies so Guard 0 (auth check) passes.
    """
    if server_config is None:
        server_config = _make_server_config(auth_enabled=True)

    return [
        patch("orb.api.routers.admin.get_di_container", return_value=container),
        patch("orb.api.dependencies.get_di_container", return_value=container),
        patch("orb.api.dependencies.get_server_config", return_value=server_config),
    ]


class _MultiPatch:
    """Context manager that applies a list of patch objects."""

    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *args):
        for p in reversed(self._patches):
            p.__exit__(*args)


def _patch_ctx(container, server_config=None):
    return _MultiPatch(_patch_container(container, server_config))


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

        with _patch_ctx(container):
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

        with _patch_ctx(container):
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

            with _patch_ctx(container):
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

        with _patch_ctx(container):
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

        with _patch_ctx(container):
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

        with _patch_ctx(container):
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

        with _patch_ctx(container):
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

        with _patch_ctx(container):
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

        with _patch_ctx(container):
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

            with _patch_ctx(container):
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

        with _patch_ctx(container):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client)

        # Fails closed — production environment assumed when config unreadable.
        assert r.status_code == 403

    # ── Guard: auth disabled ────────────────────────────────────────────────

    def test_returns_403_when_auth_is_disabled(self, admin_app):
        """Destructive admin is blocked when authentication is disabled."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)
        server_config = _make_server_config(auth_enabled=False)

        with _patch_ctx(container, server_config=server_config):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _wipe_post(client)

        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["code"] == "AUTH_DISABLED"


# ---------------------------------------------------------------------------
# Tests for POST /admin/init — error response safety (orb-1.16)
# ---------------------------------------------------------------------------


def _init_post(client: TestClient, body: dict | None = None):
    if body is None:
        body = {"confirm": "INIT"}
    return client.post("/admin/init", json=body)


def _patch_init_to_raise(error_message: str):
    """Patch get_config_location (the first call inside the outer try) to raise.

    This ensures the error propagates to the outer ``except Exception as exc`` handler
    in ``init_orb``, which is the code path being tested.  The function is imported
    lazily inside ``init_orb`` from ``orb.config.platform_dirs``, so we patch it there.
    """
    return patch(
        "orb.config.platform_dirs.get_config_location",
        side_effect=RuntimeError(error_message),
    )


@pytest.mark.unit
@pytest.mark.api
class TestAdminInitErrorResponse:
    """Verify that POST /admin/init 500 responses never leak internal exception text
    and always include a correlation_id for server-side log correlation."""

    def test_init_500_does_not_contain_exception_text_in_body(self, admin_app):
        """The raw exception message must not appear in the 500 response body."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        secret_error = "InternalDatabaseSecret: host=db.internal port=5432"

        with _patch_ctx(container), _patch_init_to_raise(secret_error):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _init_post(client)

        assert r.status_code == 500
        raw = r.text
        assert secret_error not in raw, (
            f"Exception detail leaked into response body: {secret_error!r} found in {raw!r}"
        )

    def test_init_500_contains_correlation_id(self, admin_app):
        """500 response must include a correlation_id UUID for log lookup."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with _patch_ctx(container), _patch_init_to_raise("something went wrong"):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _init_post(client)

        assert r.status_code == 500
        body = r.json()
        error = body.get("error", {})
        assert "correlation_id" in error, f"correlation_id missing from error: {error}"
        # Must be a valid UUID4 (xxxxxxxx-xxxx-4xxx-...)
        cid = error["correlation_id"]
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            re.IGNORECASE,
        )
        assert uuid_pattern.match(cid), f"correlation_id is not a valid UUID4: {cid!r}"

    def test_init_500_contains_generic_message(self, admin_app):
        """500 error message must be a generic string, not the raw exception."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with _patch_ctx(container), _patch_init_to_raise("private infra detail"):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _init_post(client)

        assert r.status_code == 500
        body = r.json()
        message = body.get("error", {}).get("message", "")
        assert "private infra detail" not in message
        assert len(message) > 0, "error.message must not be empty"

    def test_init_500_error_code_is_init_failed(self, admin_app):
        """500 error response must carry code=INIT_FAILED."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with _patch_ctx(container), _patch_init_to_raise("boom"):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _init_post(client)

        assert r.status_code == 500
        assert r.json()["error"]["code"] == "INIT_FAILED"

    def test_init_error_is_logged_server_side(self, admin_app, caplog):
        """The full exception must be logged at ERROR level server-side."""
        import logging

        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with _patch_ctx(container), _patch_init_to_raise("logged-exception-marker"):
            with caplog.at_level(logging.ERROR, logger="orb.api.routers.admin"):
                client = TestClient(admin_app, raise_server_exceptions=False)
                _init_post(client)

        error_messages = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("logged-exception-marker" in m for m in error_messages), (
            f"Expected exception text in ERROR log, got: {error_messages}"
        )


# ---------------------------------------------------------------------------
# Tests for executor offload — wipe and cleanup don't block the event loop
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
@pytest.mark.asyncio
class TestAdminExecutorOffload:
    """Verify that wipe/cleanup are offloaded to a thread-pool executor so
    that concurrent requests on other endpoints are not stalled."""

    async def test_wipe_uses_run_in_executor(self, admin_app):
        """WipeDatabaseService.execute is called via run_in_executor.

        We mock ``asyncio.get_running_loop`` inside the router to intercept the
        ``run_in_executor`` call and verify it is invoked with the sync callable
        rather than being called directly on the event loop thread.
        """
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        executor_calls: list[tuple] = []

        async def fake_run_in_executor(executor, fn, *args):
            executor_calls.append((executor, fn, args))
            # Still run fn so the endpoint gets a valid result.
            return fn(*args)

        mock_loop = MagicMock()
        mock_loop.run_in_executor = fake_run_in_executor

        from httpx import ASGITransport, AsyncClient

        with _patch_ctx(container):
            with patch("orb.api.routers.admin.asyncio.get_running_loop", return_value=mock_loop):
                async with AsyncClient(
                    transport=ASGITransport(app=admin_app), base_url="http://test"
                ) as ac:
                    r = await ac.post("/admin/database/wipe", json={"confirm": "WIPE"})

        assert r.status_code == 200, r.text
        assert len(executor_calls) >= 1, "run_in_executor was never called for wipe"
        # Executor should be None (default thread pool) and fn callable
        exec_arg, fn_arg, _ = executor_calls[0]
        assert exec_arg is None
        assert callable(fn_arg)

    async def test_cleanup_uses_run_in_executor(self, admin_app):
        """CleanupDatabaseService.bulk_cleanup is called via run_in_executor."""

        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        # Wire up a cleanup result on the UoW
        uow = MagicMock()
        uow.requests = request_repo
        uow.machines = machine_repo
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow_factory = MagicMock()
        uow_factory.create_unit_of_work = MagicMock(return_value=uow)
        request_repo.find_all.return_value = []
        machine_repo.find_all.return_value = []

        executor_calls: list[tuple] = []

        async def fake_run_in_executor(executor, fn, *args):
            executor_calls.append((executor, fn, args))
            return fn(*args)

        mock_loop = MagicMock()
        mock_loop.run_in_executor = fake_run_in_executor

        from httpx import ASGITransport, AsyncClient

        with _patch_ctx(container):
            with patch("orb.api.routers.admin.asyncio.get_running_loop", return_value=mock_loop):
                async with AsyncClient(
                    transport=ASGITransport(app=admin_app), base_url="http://test"
                ) as ac:
                    r = await ac.post(
                        "/admin/database/cleanup",
                        json={"confirm": "CLEANUP", "request_statuses": ["completed"]},
                    )

        assert r.status_code == 200, r.text
        assert len(executor_calls) >= 1, "run_in_executor was never called for cleanup"
        exec_arg, fn_arg, _ = executor_calls[0]
        assert exec_arg is None
        assert callable(fn_arg)

    async def test_wipe_does_not_stall_concurrent_get(self, admin_app):
        """While a wipe is running (simulated slow executor), a concurrent
        lightweight GET on the same app completes without being blocked.

        Strategy: patch WipeDatabaseService.execute to sleep in a real executor
        thread.  Because the router now uses run_in_executor the event loop stays
        free during that sleep.  We race the wipe against a /ping GET and confirm
        /ping returns before the wipe finishes.
        """
        import asyncio as _asyncio
        import time

        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        # Add a trivial /ping route so we have something to hit concurrently.
        from fastapi.responses import JSONResponse as _JSONResponse

        @admin_app.get("/ping-wipe")
        async def _ping_wipe():
            return _JSONResponse(content={"pong": True})

        wipe_started = _asyncio.Event()

        # Capture the running event loop HERE (in the async context) so the
        # worker thread can call call_soon_threadsafe on it.  Worker threads
        # cannot call asyncio.get_running_loop() — it raises RuntimeError.
        running_loop = _asyncio.get_running_loop()

        def slow_execute(self_service):
            """Blocking sleep in the executor thread — event loop must stay free."""
            # Notify the test that we've entered the executor.
            running_loop.call_soon_threadsafe(wipe_started.set)
            time.sleep(0.15)
            result = MagicMock()
            result.tables_truncated = []
            result.rows_deleted = 0
            return result

        from httpx import ASGITransport, AsyncClient

        from orb.application.services.admin.wipe_database import WipeDatabaseService

        with _patch_ctx(container):
            with patch.object(WipeDatabaseService, "execute", slow_execute):
                async with AsyncClient(
                    transport=ASGITransport(app=admin_app), base_url="http://test"
                ) as ac:
                    # Launch the slow wipe concurrently.
                    wipe_task = _asyncio.create_task(
                        ac.post("/admin/database/wipe", json={"confirm": "WIPE"})
                    )
                    # Wait until the executor thread has started (event is set from thread).
                    await _asyncio.wait_for(wipe_started.wait(), timeout=2.0)
                    # The event loop must be free — ping should complete immediately.
                    ping_r = await ac.get("/ping-wipe")
                    wipe_r = await wipe_task

        assert ping_r.status_code == 200, "concurrent GET stalled while wipe was running"
        assert wipe_r.status_code == 200


# ---------------------------------------------------------------------------
# Security: information-leak regression tests for cleanup and reload
# ---------------------------------------------------------------------------


def _cleanup_post(client: TestClient, body: dict | None = None):
    if body is None:
        body = {"confirm": "CLEANUP", "request_statuses": ["cancelled"]}
    return client.post("/admin/database/cleanup", json=body)


def _reload_post(client: TestClient):
    return client.post("/admin/reload-config")


@pytest.mark.unit
@pytest.mark.api
class TestCleanupInvalidStatusNoInfoLeak:
    """InvalidCleanupStatusError text must not appear in the 400 response body
    but must be visible in the server WARNING log."""

    def test_invalid_status_error_message_not_in_response_body(self, admin_app, caplog):
        """Raw InvalidCleanupStatusError text must not be returned to the client."""
        import logging

        from orb.application.services.admin.cleanup_database import InvalidCleanupStatusError

        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        # Inject a recognisable secret into the error message.
        secret = "valid-statuses-are-secret-enum-abc123"
        with _patch_ctx(container):
            with patch(
                "orb.api.routers.admin.CleanupDatabaseService.bulk_cleanup",
                side_effect=InvalidCleanupStatusError(secret),
            ):
                with caplog.at_level(logging.WARNING, logger="orb.api.routers.admin"):
                    client = TestClient(admin_app, raise_server_exceptions=False)
                    r = _cleanup_post(client)

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_STATUS"
        assert secret not in r.text, (
            f"InvalidCleanupStatusError detail leaked: {secret!r} found in {r.text!r}"
        )
        warning_messages = [rec.message for rec in caplog.records if rec.levelno >= logging.WARNING]
        assert any(secret in m for m in warning_messages), (
            f"Expected {secret!r} in server WARNING log, got: {warning_messages}"
        )

    def test_invalid_status_error_code_unchanged(self, admin_app):
        """HTTP status code and error code must not change after the fix."""
        from orb.application.services.admin.cleanup_database import InvalidCleanupStatusError

        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()
        container = _make_container(config_port, machine_repo, request_repo, template_repo)

        with _patch_ctx(container):
            with patch(
                "orb.api.routers.admin.CleanupDatabaseService.bulk_cleanup",
                side_effect=InvalidCleanupStatusError("any detail"),
            ):
                client = TestClient(admin_app, raise_server_exceptions=False)
                r = _cleanup_post(client)

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_STATUS"


@pytest.mark.unit
@pytest.mark.api
class TestReloadConfigNoInfoLeak:
    """Broad Exception during config reload must not expose internal text in the
    500 response body, but must be visible in the server ERROR log."""

    def _make_failing_container(
        self, config_port, machine_repo, request_repo, template_repo, secret
    ):
        """Return a DI container whose ConfigurationManager.reload() raises with *secret*."""
        from orb.domain.base import UnitOfWorkFactory
        from orb.domain.base.ports.configuration_port import ConfigurationPort
        from orb.domain.machine.repository import MachineRepository
        from orb.domain.request.repository import RequestRepository
        from orb.domain.template.repository import TemplateRepository

        uow = MagicMock()
        uow.machines = machine_repo
        uow.requests = request_repo
        uow.templates = template_repo
        uow.__enter__ = MagicMock(return_value=uow)
        uow.__exit__ = MagicMock(return_value=False)
        uow_factory = MagicMock()
        uow_factory.create_unit_of_work = MagicMock(return_value=uow)

        # A ConfigurationManager mock whose reload() raises with the secret.
        cm_mock = MagicMock()
        cm_mock.reload.side_effect = Exception(secret)

        # Import lazily to match what the router does at call time.
        from orb.config.managers.configuration_manager import ConfigurationManager

        type_map = {
            ConfigurationPort: config_port,
            MachineRepository: machine_repo,
            RequestRepository: request_repo,
            TemplateRepository: template_repo,
            UnitOfWorkFactory: uow_factory,
            ConfigurationManager: cm_mock,
        }
        container = MagicMock()
        container.get.side_effect = lambda t: type_map[t]
        return container

    def test_reload_exception_message_not_in_response_body(self, admin_app, caplog):
        """Raw exception text must not be returned to the client on reload failure."""
        import logging

        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()

        secret = "config-file-path-secret-/etc/orb/secret.json"
        container = self._make_failing_container(
            config_port, machine_repo, request_repo, template_repo, secret
        )

        with _patch_ctx(container):
            with caplog.at_level(logging.ERROR, logger="orb.api.routers.admin"):
                client = TestClient(admin_app, raise_server_exceptions=False)
                r = _reload_post(client)

        assert r.status_code == 500
        assert r.json()["error"]["code"] == "RELOAD_FAILED"
        assert secret not in r.text, (
            f"Exception detail leaked into reload response: {secret!r} found in {r.text!r}"
        )
        error_messages = [rec.message for rec in caplog.records if rec.levelno >= logging.ERROR]
        assert any(secret in m for m in error_messages), (
            f"Expected {secret!r} in server ERROR log, got: {error_messages}"
        )

    def test_reload_error_code_and_status_unchanged(self, admin_app):
        """HTTP 500 and code=RELOAD_FAILED must be preserved after the fix."""
        config_port = _make_config_port(allow_destructive=True, environment="development")
        machine_repo, request_repo, template_repo = _make_repositories()

        container = self._make_failing_container(
            config_port, machine_repo, request_repo, template_repo, "any internal detail"
        )

        with _patch_ctx(container):
            client = TestClient(admin_app, raise_server_exceptions=False)
            r = _reload_post(client)

        assert r.status_code == 500
        assert r.json()["error"]["code"] == "RELOAD_FAILED"
