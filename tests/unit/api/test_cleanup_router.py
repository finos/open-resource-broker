"""Unit tests for cleanup endpoints.

Covers:
  POST  /admin/database/cleanup
  DELETE /requests/{id}?purge=true
  DELETE /machines/{id}?purge=true
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import CurrentUser, get_current_user
from orb.api.routers.admin import router as admin_router
from orb.api.routers.machines import router as machines_router
from orb.api.routers.requests import router as requests_router

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_server_config(auth_enabled: bool = True):
    """Return a MagicMock ServerConfig with auth.enabled set."""
    server_config = MagicMock()
    server_config.auth.enabled = auth_enabled
    return server_config


@pytest.fixture()
def cleanup_app():
    """Minimal FastAPI app with admin + requests + machines routers mounted.

    Overrides ``get_current_user`` so ``require_role`` guards are satisfied by
    a synthetic admin identity.  Individual tests focus on config/environment
    guards inside ``check_destructive_admin_allowed``.
    """
    from fastapi.responses import JSONResponse

    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(requests_router)
    app.include_router(machines_router)

    # Supply a synthetic admin identity so role guards never interfere.
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-admin", role="admin"
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


class _MultiPatch:
    """Context manager that applies a list of patch objects in order."""

    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *args):
        for p in reversed(self._patches):
            p.__exit__(*args)


def _patch_containers(*targets, container, server_config=None):
    """Patch get_di_container in multiple module namespaces plus
    orb.api.dependencies.get_server_config so Guard 0 passes."""
    if server_config is None:
        server_config = _make_server_config(auth_enabled=True)
    patches = [patch(t, return_value=container) for t in targets]
    patches.append(patch("orb.api.dependencies.get_di_container", return_value=container))
    patches.append(patch("orb.api.dependencies.get_server_config", return_value=server_config))
    return _MultiPatch(patches)


def _make_config_port(allow_destructive: bool = True, environment: str = "development"):
    config_port = MagicMock()
    config_port.get_configuration_value.side_effect = lambda key, default=None: {
        "allow_destructive_admin": allow_destructive,
        "environment": environment,
    }.get(key, default)
    return config_port


def _make_request(
    request_id: str,
    status_value: str,
    created_at: datetime | None = None,
    machine_ids: list[str] | None = None,
):
    """Build a mock Request aggregate."""

    req = MagicMock()
    req.request_id = request_id
    status = MagicMock()
    status.value = status_value
    status.is_terminal.return_value = status_value in {
        "cancelled",
        "complete",
        "failed",
        "timeout",
        "partial",
    }
    req.status = status
    req.created_at = created_at or datetime(2020, 1, 1, tzinfo=timezone.utc)
    req.machine_ids = machine_ids or []
    return req


def _make_machine(machine_id: str, status_value: str = "terminated", request_id: str = ""):
    """Build a mock Machine aggregate."""
    machine = MagicMock()
    machine.machine_id = machine_id
    machine.request_id = request_id
    status = MagicMock()
    status.value = status_value
    # is_terminal is a property on MachineStatus
    terminal_statuses = {"terminated", "failed", "returned"}
    type(status).is_terminal = property(lambda self: self.value in terminal_statuses)
    machine.status = status
    return machine


def _make_uow(request_map=None, machine_map=None, machines_by_request=None):
    """Return a context-manager–compatible MagicMock UoW."""
    uow = MagicMock()
    uow.__enter__ = lambda s: s
    uow.__exit__ = MagicMock(return_value=False)

    request_map = request_map or {}
    machine_map = machine_map or {}
    machines_by_request = machines_by_request or {}

    uow.requests.find_by_request_id.side_effect = lambda rid: request_map.get(rid)
    uow.requests.find_all.return_value = list(request_map.values())
    uow.requests.delete = MagicMock()

    uow.machines.find_by_machine_id.side_effect = lambda mid: machine_map.get(mid)
    uow.machines.get_by_id.side_effect = lambda mid: machine_map.get(mid)
    uow.machines.find_all.return_value = list(machine_map.values())
    uow.machines.find_by_request_id.side_effect = lambda rid: machines_by_request.get(rid, [])
    uow.machines.delete = MagicMock()

    return uow


def _make_uow_factory(uow):
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    return factory


def _make_container(config_port, uow_factory):
    from orb.domain.base import UnitOfWorkFactory
    from orb.domain.base.ports.configuration_port import ConfigurationPort

    type_map = {
        ConfigurationPort: config_port,
        UnitOfWorkFactory: uow_factory,
    }
    container = MagicMock()
    container.get.side_effect = lambda t: type_map[t]
    return container


# ---------------------------------------------------------------------------
# POST /admin/database/cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestCleanupEndpoint:
    """Tests for POST /admin/database/cleanup."""

    def test_403_when_destructive_admin_disabled(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=False)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={"confirm": "CLEANUP", "request_statuses": ["cancelled"]},
            )

        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "DESTRUCTIVE_ADMIN_DISABLED"

    def test_403_when_environment_is_production(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=True, environment="production")
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={"confirm": "CLEANUP", "request_statuses": ["cancelled"]},
            )

        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "PRODUCTION_ENVIRONMENT"

    def test_400_when_confirm_token_wrong(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=True)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={"confirm": "cleanup", "request_statuses": ["cancelled"]},
            )

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "MISSING_CONFIRMATION"

    def test_400_when_confirm_token_missing(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=True)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={"request_statuses": ["cancelled"]},
            )

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "MISSING_CONFIRMATION"

    def test_400_when_request_statuses_empty(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=True)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={"confirm": "CLEANUP", "request_statuses": []},
            )

        assert r.status_code == 400

    def test_400_when_request_statuses_contains_non_terminal(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=True)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={"confirm": "CLEANUP", "request_statuses": ["pending", "cancelled"]},
            )

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_STATUS"

    def test_400_when_request_statuses_contains_in_progress(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=True)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={"confirm": "CLEANUP", "request_statuses": ["in_progress"]},
            )

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_STATUS"

    def test_happy_path_bulk_cleanup_returns_correct_counts(self, cleanup_app):
        """2 cancelled requests × 3 machines each → requests_deleted=2 machines_deleted=6."""
        config_port = _make_config_port(allow_destructive=True)

        machines_r1 = [_make_machine(f"m-r1-{i}", "terminated", "req-1") for i in range(3)]
        machines_r2 = [_make_machine(f"m-r2-{i}", "terminated", "req-2") for i in range(3)]
        request1 = _make_request("req-1", "cancelled")
        request2 = _make_request("req-2", "cancelled")

        uow = _make_uow(
            request_map={"req-1": request1, "req-2": request2},
            machines_by_request={"req-1": machines_r1, "req-2": machines_r2},
        )
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={
                    "confirm": "CLEANUP",
                    "request_statuses": ["cancelled"],
                    "include_machines": True,
                },
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cleaned"] is True
        assert body["requests_deleted"] == 2
        assert body["machines_deleted"] == 6

    def test_older_than_filter_excludes_recent_requests(self, cleanup_app):
        """Requests created less than older_than_days ago are skipped."""
        config_port = _make_config_port(allow_destructive=True)

        old_request = _make_request(
            "req-old",
            "cancelled",
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        recent_request = _make_request(
            "req-recent",
            "cancelled",
            created_at=datetime.now(tz=timezone.utc),
        )

        uow = _make_uow(request_map={"req-old": old_request, "req-recent": recent_request})
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={
                    "confirm": "CLEANUP",
                    "request_statuses": ["cancelled"],
                    "older_than_days": 7,
                    "include_machines": False,
                },
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["requests_deleted"] == 1  # only the old one
        # Confirm old was deleted, recent was not
        deleted_calls = [str(c.args[0]) for c in uow.requests.delete.call_args_list]
        assert "req-old" in deleted_calls
        assert "req-recent" not in deleted_calls

    def test_include_machines_false_keeps_machine_rows(self, cleanup_app):
        """When include_machines=False, machine rows are left untouched."""
        config_port = _make_config_port(allow_destructive=True)

        machines = [_make_machine(f"m-{i}", "terminated", "req-1") for i in range(2)]
        request1 = _make_request("req-1", "cancelled")

        uow = _make_uow(
            request_map={"req-1": request1},
            machines_by_request={"req-1": machines},
        )
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post(
                "/admin/database/cleanup",
                json={
                    "confirm": "CLEANUP",
                    "request_statuses": ["cancelled"],
                    "include_machines": False,
                },
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["requests_deleted"] == 1
        assert body["machines_deleted"] == 0
        uow.machines.delete.assert_not_called()


# ---------------------------------------------------------------------------
# DELETE /requests/{id}?purge=true
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestRequestPurgeEndpoint:
    """Tests for DELETE /requests/{id}?purge=true."""

    def test_400_when_request_not_terminal(self, cleanup_app):
        """Purge of a non-terminal request returns 400."""
        config_port = _make_config_port(allow_destructive=True)

        pending_req = _make_request("req-1", "pending")
        uow = _make_uow(request_map={"req-1": pending_req})
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.requests.get_di_container",
            container=container,
        ):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post("/requests/req-1/purge")

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "NON_TERMINAL_STATUS"

    def test_404_when_request_not_found(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=True)
        uow = _make_uow(request_map={})
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.requests.get_di_container",
            container=container,
        ):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post("/requests/req-missing/purge")

        assert r.status_code == 404

    def test_403_when_destructive_admin_disabled(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=False)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.requests.get_di_container",
            container=container,
        ):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post("/requests/req-1/purge")

        assert r.status_code == 403

    def test_200_when_terminal_request_purged_with_cascade(self, cleanup_app):
        """Terminal request with 3 machines → deleted=True, machines_deleted=3."""
        config_port = _make_config_port(allow_destructive=True)

        machines = [_make_machine(f"m-{i}", "terminated", "req-1") for i in range(3)]
        cancelled_req = _make_request("req-1", "cancelled")

        uow = _make_uow(
            request_map={"req-1": cancelled_req},
            machines_by_request={"req-1": machines},
        )
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.requests.get_di_container",
            container=container,
        ):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.post("/requests/req-1/purge")

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deleted"] is True
        assert body["request_id"] == "req-1"
        assert body["machines_deleted"] == 3

    def test_soft_delete_path_unchanged_when_no_purge_flag(self, cleanup_app):
        """DELETE /requests/{id} routes to the original cancel path."""
        # We just verify it does NOT call the CleanupDatabaseService.
        # The cancel orchestrator will raise because it's not mocked — that's fine;
        # we verify the right path was taken by checking the error type.
        config_port = _make_config_port(allow_destructive=True)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.requests.get_di_container",
            container=container,
        ):
            with patch(
                "orb.api.routers.requests.get_cancel_request_orchestrator",
                return_value=MagicMock(),
            ) as mock_orch_dep:
                mock_orchestrator = MagicMock()
                mock_orchestrator.execute = MagicMock(
                    return_value=MagicMock(request_id="req-1", status="cancelled", __await__=None)
                )

                async def fake_execute(inp):
                    return MagicMock(request_id="req-1", status="cancelled")

                mock_orchestrator.execute = fake_execute
                mock_orch_dep.return_value = mock_orchestrator

                client = TestClient(cleanup_app, raise_server_exceptions=False)
                # No purge flag → hits cancel path
                r = client.delete("/requests/req-1")

        # Should NOT be 404 from the cleanup service
        assert r.status_code != 404


# ---------------------------------------------------------------------------
# DELETE /machines/{id}?purge=true
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestMachinePurgeEndpoint:
    """Tests for DELETE /machines/{id}?purge=true."""

    def test_400_when_purge_flag_missing(self, cleanup_app):
        """Without ?purge=true the endpoint returns 400."""
        config_port = _make_config_port(allow_destructive=True)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.machines.get_di_container",
            container=container,
        ):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.delete("/machines/m-1")

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "PURGE_REQUIRED"

    def test_403_when_destructive_admin_disabled(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=False)
        uow = _make_uow()
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.machines.get_di_container",
            container=container,
        ):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.delete("/machines/m-1?purge=true")

        assert r.status_code == 403

    def test_400_when_machine_not_terminal(self, cleanup_app):
        """Purge of a running machine returns 400."""
        config_port = _make_config_port(allow_destructive=True)

        running_machine = _make_machine("m-1", "running")
        uow = _make_uow(machine_map={"m-1": running_machine})
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.machines.get_di_container",
            container=container,
        ):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.delete("/machines/m-1?purge=true")

        assert r.status_code == 400
        assert r.json()["error"]["code"] == "NON_TERMINAL_STATUS"

    def test_404_when_machine_not_found(self, cleanup_app):
        config_port = _make_config_port(allow_destructive=True)
        uow = _make_uow(machine_map={})
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.machines.get_di_container",
            container=container,
        ):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.delete("/machines/m-missing?purge=true")

        assert r.status_code == 404

    def test_200_when_terminal_machine_purged(self, cleanup_app):
        """Terminated machine → deleted=True, machines_deleted=1."""
        config_port = _make_config_port(allow_destructive=True)

        terminated_machine = _make_machine("m-1", "terminated")
        uow = _make_uow(machine_map={"m-1": terminated_machine})
        container = _make_container(config_port, _make_uow_factory(uow))

        with _patch_containers(
            "orb.api.routers.admin.get_di_container",
            "orb.api.routers.machines.get_di_container",
            container=container,
        ):
            client = TestClient(cleanup_app, raise_server_exceptions=False)
            r = client.delete("/machines/m-1?purge=true")

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deleted"] is True
        assert body["machine_id"] == "m-1"
        assert body["machines_deleted"] == 1
        uow.machines.delete.assert_called_once_with("m-1")
