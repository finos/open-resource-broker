"""POST /requests/{id}/confirm-terminated: authz, validation, conflict, principal capture."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    CurrentUser,
    get_confirm_terminated_orchestrator,
    get_current_user,
    get_request_formatter,
)
from orb.api.middleware.auth_middleware import AuthMiddleware
from orb.api.routers.requests import router as requests_router
from orb.application.services.orchestration.dtos import ConfirmTerminatedOutput
from orb.domain.base.exceptions import EntityNotFoundError, ValidationError
from orb.domain.request.termination_confirmation import InstanceStillVisibleError
from orb.infrastructure.adapters.ports.auth import AuthResult, AuthStatus
from orb.infrastructure.error.exception_handler import get_exception_handler

URL = "/requests/ret-1/confirm-terminated"
BODY = {"machine_ids": ["i-1"], "reason": "verified in console"}


def _output(**kwargs):
    return ConfirmTerminatedOutput(
        request_id="ret-1",
        confirmed=["i-1"],
        records=[
            {
                "machine_id": "i-1",
                "operator": "alice",
                "role": "operator",
                "reason": "verified in console",
                "verification": "not_visible",
                "at": "2026-01-01T00:00:00+00:00",
            }
        ],
        **kwargs,
    )


def _orchestrator(result=None, raises=None):
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(return_value=result or _output(), side_effect=raises)
    return orchestrator


def _formatter():
    return MagicMock()


def _app(orchestrator, user: CurrentUser | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(requests_router)
    app.dependency_overrides[get_confirm_terminated_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_request_formatter] = _formatter
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user

    @app.exception_handler(Exception)
    async def _map_domain_errors(request, exc):
        # Same mapping the server installs globally.
        error = get_exception_handler().handle_error_for_http(exc)
        return JSONResponse(status_code=error.http_status or 500, content={"success": False})

    return app


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
@pytest.mark.api
class TestAuthorisation:
    def test_viewer_gets_403_and_nothing_runs(self):
        orchestrator = _orchestrator()
        resp = _client(_app(orchestrator, CurrentUser("v", "viewer"))).post(URL, json=BODY)
        assert resp.status_code == 403
        orchestrator.execute.assert_not_awaited()

    def test_anonymous_viewer_gets_403(self):
        orchestrator = _orchestrator()
        resp = _client(_app(orchestrator, CurrentUser("anonymous", "viewer"))).post(URL, json=BODY)
        assert resp.status_code == 403
        orchestrator.execute.assert_not_awaited()

    @pytest.mark.parametrize("role", ["operator", "admin"])
    def test_operator_and_admin_pass(self, role):
        resp = _client(_app(_orchestrator(), CurrentUser("u", role))).post(URL, json=BODY)
        assert resp.status_code == 200

    def _authenticated_app(self, orchestrator, status, roles):
        app = _app(orchestrator)
        port = MagicMock()
        port.is_enabled.return_value = True
        port.authenticate = AsyncMock(
            return_value=AuthResult(
                status=status,
                user_id="alice" if status == AuthStatus.SUCCESS else None,
                user_roles=roles,
                permissions=[],
            )
        )
        app.add_middleware(AuthMiddleware, auth_port=port, require_auth=True, trusted_proxies=[])
        return app

    def test_unauthenticated_call_gets_401(self):
        orchestrator = _orchestrator()
        app = self._authenticated_app(orchestrator, AuthStatus.INVALID, [])
        resp = _client(app).post(URL, json=BODY)
        assert resp.status_code == 401
        orchestrator.execute.assert_not_awaited()

    def test_authenticated_viewer_gets_403_and_operator_gets_200(self):
        viewer = self._authenticated_app(_orchestrator(), AuthStatus.SUCCESS, ["viewer"])
        assert _client(viewer).post(URL, json=BODY).status_code == 403
        operator = self._authenticated_app(_orchestrator(), AuthStatus.SUCCESS, ["operator"])
        assert _client(operator).post(URL, json=BODY).status_code == 200


@pytest.mark.unit
@pytest.mark.api
class TestBehaviour:
    def test_principal_id_and_role_reach_the_orchestrator(self):
        orchestrator = _orchestrator()
        _client(_app(orchestrator, CurrentUser("alice", "admin"))).post(URL, json=BODY)
        sent = orchestrator.execute.await_args.args[0]
        assert (sent.operator, sent.role) == ("alice", "admin")
        assert sent.request_id == "ret-1"
        assert sent.machine_ids == ["i-1"]
        assert sent.reason == "verified in console"

    def test_success_body_carries_records_and_warnings(self):
        result = _output(warnings=["probe unavailable"])
        resp = _client(_app(_orchestrator(result), CurrentUser("a", "operator"))).post(
            URL, json=BODY
        )
        data = resp.json()
        assert data["confirmed"] == ["i-1"]
        assert data["warnings"] == ["probe unavailable"]
        assert data["records"][0]["operator"] == "alice"

    def test_still_visible_instance_is_409_naming_it(self):
        orchestrator = _orchestrator(
            raises=InstanceStillVisibleError("still shown", details={"machine_ids": ["i-1"]})
        )
        resp = _client(_app(orchestrator, CurrentUser("a", "operator"))).post(URL, json=BODY)
        assert resp.status_code == 409
        assert resp.json()["error"]["machine_ids"] == ["i-1"]

    def test_validation_failure_is_400(self):
        orchestrator = _orchestrator(raises=ValidationError("Machines not part of request: i-9"))
        resp = _client(_app(orchestrator, CurrentUser("a", "operator"))).post(URL, json=BODY)
        assert resp.status_code == 400
        assert "i-9" in resp.json()["error"]["message"]

    def test_unknown_request_is_404(self):
        orchestrator = _orchestrator(raises=EntityNotFoundError("Request", "ret-1"))
        resp = _client(_app(orchestrator, CurrentUser("a", "operator"))).post(URL, json=BODY)
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {"machine_ids": ["i-1"]},
            {"reason": "r"},
            {"machine_ids": ["i-1"], "reason": "r", "x": 1},
        ],
    )
    def test_malformed_body_is_rejected_before_the_orchestrator(self, body):
        orchestrator = _orchestrator()
        resp = _client(_app(orchestrator, CurrentUser("a", "operator"))).post(URL, json=body)
        assert resp.status_code == 422
        orchestrator.execute.assert_not_awaited()
