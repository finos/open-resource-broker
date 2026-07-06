"""Parametrised role-denial tests for every guarded route.

For every route that declares require_role("operator") or require_role("admin"),
this module verifies:
  1. The correct role passes (200/202/other success).
  2. An insufficient role is denied with 403.
  3. An anonymous viewer (role="viewer") cannot reach operator/admin routes.

Strategy: mount each router in an isolated FastAPI app and override
get_current_user to inject a fabricated CurrentUser.  No network, no real AWS.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from orb.api.dependencies import CurrentUser, get_current_user
from orb.api.routers.machines import router as machines_router
from orb.api.routers.requests import router as requests_router
from orb.api.routers.templates import router as templates_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(role: str, username: str = "test") -> CurrentUser:
    return CurrentUser(username=username, role=role)


def _app_with_router(router, role: str, extra_overrides: dict | None = None) -> FastAPI:
    """Return a minimal FastAPI app with the given router and role override.

    extra_overrides is a mapping of dependency → factory that callers supply
    to prevent orchestrator/service resolution errors.
    """
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    for dep, factory in (extra_overrides or {}).items():
        app.dependency_overrides[dep] = factory
    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Shared stub factories
# ---------------------------------------------------------------------------


def _noop_async(*_args, **_kwargs):
    """Async callable that returns a minimal output object."""

    async def _inner(*a, **kw):
        return MagicMock()

    return _inner


def _stub_acquire_orchestrator():
    from orb.application.services.orchestration.dtos import AcquireMachinesOutput

    orc = AsyncMock()
    orc.execute = AsyncMock(
        return_value=AcquireMachinesOutput(request_id="req-1", status="pending", machine_ids=[])
    )
    return orc


def _stub_return_orchestrator():
    from orb.application.services.orchestration.dtos import ReturnMachinesOutput

    orc = AsyncMock()
    orc.execute = AsyncMock(return_value=ReturnMachinesOutput(request_id="req-2", status="pending"))
    return orc


def _stub_cancel_orchestrator():
    from orb.application.services.orchestration.dtos import CancelRequestOutput

    orc = AsyncMock()
    orc.execute = AsyncMock(
        return_value=CancelRequestOutput(request_id="req-3", status="cancelled")
    )
    return orc


def _stub_scheduler():
    scheduler = MagicMock()
    scheduler.format_request_response.return_value = {}
    scheduler.format_machine_status_response.return_value = {"machines": []}
    scheduler.format_templates_response.return_value = {"templates": []}
    scheduler.format_template_mutation_response.return_value = {}
    return scheduler


def _stub_templates_orchestrator():
    orc = AsyncMock()
    orc.execute = AsyncMock(return_value=MagicMock(templates=[]))
    return orc


# ---------------------------------------------------------------------------
# machines router: operator-guarded routes
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestMachinesRouterRoleGuard:
    """POST /machines/request and POST /machines/return require operator."""

    def _overrides(self):
        from orb.api.dependencies import (
            get_acquire_machines_orchestrator,
            get_return_machines_orchestrator,
            get_scheduler_strategy,
        )

        return {
            get_acquire_machines_orchestrator: _stub_acquire_orchestrator,
            get_return_machines_orchestrator: _stub_return_orchestrator,
            get_scheduler_strategy: _stub_scheduler,
        }

    # ── POST /machines/request ───────────────────────────────────────────────

    def test_request_machines_viewer_gets_403(self):
        """Viewer cannot reach POST /machines/request (operator required)."""
        app = _app_with_router(machines_router, "viewer", self._overrides())
        resp = _client(app).post(
            "/machines/request",
            json={"template_id": "t-1", "count": 1},
        )
        assert resp.status_code == 403

    def test_request_machines_operator_passes(self):
        """Operator can reach POST /machines/request."""
        app = _app_with_router(machines_router, "operator", self._overrides())
        resp = _client(app).post(
            "/machines/request",
            json={"template_id": "t-1", "count": 1},
        )
        assert resp.status_code in (200, 202, 400, 422, 500)
        assert resp.status_code != 403

    def test_request_machines_admin_passes(self):
        """Admin inherits operator rank and can also reach POST /machines/request."""
        app = _app_with_router(machines_router, "admin", self._overrides())
        resp = _client(app).post(
            "/machines/request",
            json={"template_id": "t-1", "count": 1},
        )
        assert resp.status_code != 403

    # ── POST /machines/return ────────────────────────────────────────────────

    def test_return_machines_viewer_gets_403(self):
        """Viewer cannot reach POST /machines/return (operator required)."""
        app = _app_with_router(machines_router, "viewer", self._overrides())
        resp = _client(app).post(
            "/machines/return",
            json={"machine_ids": ["m-1"]},
        )
        assert resp.status_code == 403

    def test_return_machines_operator_passes(self):
        """Operator can reach POST /machines/return."""
        app = _app_with_router(machines_router, "operator", self._overrides())
        resp = _client(app).post(
            "/machines/return",
            json={"machine_ids": ["m-1"]},
        )
        assert resp.status_code != 403

    def test_return_machines_anonymous_gets_403(self):
        """Anonymous viewer cannot reach POST /machines/return (operator required)."""
        app = _app_with_router(machines_router, "viewer", self._overrides())
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="anonymous", role="viewer"
        )
        resp = _client(app).post(
            "/machines/return",
            json={"machine_ids": ["m-1"]},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# machines router: admin-guarded route (DELETE /{machine_id})
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestMachinesAdminRouteGuard:
    """DELETE /machines/{machine_id} requires admin."""

    def test_viewer_gets_403_on_delete(self):
        app = _app_with_router(machines_router, "viewer")
        resp = _client(app).delete("/machines/m-1?purge=true")
        assert resp.status_code == 403

    def test_operator_gets_403_on_delete(self):
        app = _app_with_router(machines_router, "operator")
        resp = _client(app).delete("/machines/m-1?purge=true")
        assert resp.status_code == 403

    def test_admin_passes_role_check_on_delete(self):
        """Admin passes the role guard on DELETE.

        Without ?purge=true the endpoint returns 400 PURGE_REQUIRED, which is
        the earliest possible non-role response and proves the role guard did not
        block the request with 403.
        """
        app = _app_with_router(machines_router, "admin")
        # Omit ?purge=true → handler returns 400 before the destructive-admin check.
        resp = _client(app).delete("/machines/m-1")
        assert resp.status_code == 400  # PURGE_REQUIRED, not 403


# ---------------------------------------------------------------------------
# requests router: operator-guarded route (DELETE /{request_id})
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestRequestsOperatorRouteGuard:
    """DELETE /requests/{request_id} requires operator."""

    def _overrides(self):
        from orb.api.dependencies import (
            get_cancel_request_orchestrator,
            get_scheduler_strategy,
        )

        return {
            get_cancel_request_orchestrator: _stub_cancel_orchestrator,
            get_scheduler_strategy: _stub_scheduler,
        }

    def test_viewer_gets_403(self):
        app = _app_with_router(requests_router, "viewer", self._overrides())
        resp = _client(app).delete("/requests/req-1")
        assert resp.status_code == 403

    def test_operator_passes(self):
        app = _app_with_router(requests_router, "operator", self._overrides())
        resp = _client(app).delete("/requests/req-1")
        assert resp.status_code != 403

    def test_admin_passes(self):
        app = _app_with_router(requests_router, "admin", self._overrides())
        resp = _client(app).delete("/requests/req-1")
        assert resp.status_code != 403

    def test_anonymous_viewer_gets_403(self):
        app = _app_with_router(requests_router, "viewer", self._overrides())
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="anonymous", role="viewer"
        )
        resp = _client(app).delete("/requests/req-1")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# requests router: admin-guarded route (POST /{request_id}/purge)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestRequestsAdminRouteGuard:
    """POST /requests/{request_id}/purge requires admin."""

    def test_viewer_gets_403_on_purge(self):
        app = _app_with_router(requests_router, "viewer")
        resp = _client(app).post("/requests/req-1/purge")
        assert resp.status_code == 403

    def test_operator_gets_403_on_purge(self):
        app = _app_with_router(requests_router, "operator")
        resp = _client(app).post("/requests/req-1/purge")
        assert resp.status_code == 403

    def test_admin_passes_role_check_on_purge(self):
        """Admin passes the role guard on POST /requests/{id}/purge.

        The destructive-admin guard (a separate Depends) is neutralised by
        override so we isolate the role check.
        """
        from orb.api.dependencies import check_destructive_admin_allowed

        app = _app_with_router(requests_router, "admin")
        # Neutralise the destructive-admin guard so only the role guard matters.
        app.dependency_overrides[check_destructive_admin_allowed] = lambda: None
        resp = _client(app).post("/requests/req-1/purge")
        # 403 would mean role guard fired; anything else means role passed.
        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# templates router: admin-guarded routes
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestTemplatesAdminRouteGuard:
    """POST /templates/refresh and POST /templates/generate require admin."""

    def _overrides(self):
        from orb.api.dependencies import (
            get_refresh_templates_orchestrator,
            get_scheduler_strategy,
            get_template_generation_service,
        )

        refresh_orc = AsyncMock()
        refresh_orc.execute = AsyncMock(return_value=MagicMock(templates=[]))

        tgs = AsyncMock()
        tgs.generate_templates = AsyncMock(
            return_value=MagicMock(
                status="ok",
                message="done",
                total_templates=0,
                created_count=0,
                skipped_count=0,
                providers=[],
            )
        )

        return {
            get_refresh_templates_orchestrator: lambda: refresh_orc,
            get_scheduler_strategy: _stub_scheduler,
            get_template_generation_service: lambda: tgs,
        }

    # ── POST /templates/refresh ──────────────────────────────────────────────

    def test_viewer_gets_403_on_refresh(self):
        app = _app_with_router(templates_router, "viewer", self._overrides())
        resp = _client(app).post("/templates/refresh")
        assert resp.status_code == 403

    def test_operator_gets_403_on_refresh(self):
        app = _app_with_router(templates_router, "operator", self._overrides())
        resp = _client(app).post("/templates/refresh")
        assert resp.status_code == 403

    def test_admin_passes_on_refresh(self):
        app = _app_with_router(templates_router, "admin", self._overrides())
        resp = _client(app).post("/templates/refresh")
        assert resp.status_code != 403

    # ── POST /templates/generate ─────────────────────────────────────────────

    def test_viewer_gets_403_on_generate(self):
        app = _app_with_router(templates_router, "viewer", self._overrides())
        resp = _client(app).post("/templates/generate", json={})
        assert resp.status_code == 403

    def test_operator_gets_403_on_generate(self):
        app = _app_with_router(templates_router, "operator", self._overrides())
        resp = _client(app).post("/templates/generate", json={})
        assert resp.status_code == 403

    def test_admin_passes_on_generate(self):
        app = _app_with_router(templates_router, "admin", self._overrides())
        resp = _client(app).post("/templates/generate", json={})
        assert resp.status_code != 403

    def test_anonymous_gets_403_on_admin_route(self):
        """Anonymous viewer cannot reach admin-only template routes."""
        app = _app_with_router(templates_router, "viewer", self._overrides())
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="anonymous", role="viewer"
        )
        resp = _client(app).post("/templates/refresh")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Parametrised cross-router denial matrix
# ---------------------------------------------------------------------------

# Each entry: (router, http_method, path, body, required_role)
_GUARDED_ROUTES: list[tuple[Any, str, str, dict | None, str]] = [
    (machines_router, "POST", "/machines/request", {"template_id": "t-1", "count": 1}, "operator"),
    (machines_router, "POST", "/machines/return", {"machine_ids": ["m-1"]}, "operator"),
    (machines_router, "DELETE", "/machines/m-1", None, "admin"),
    (requests_router, "DELETE", "/requests/req-1", None, "operator"),
    (requests_router, "POST", "/requests/req-1/purge", None, "admin"),
    (templates_router, "POST", "/templates/refresh", None, "admin"),
    (templates_router, "POST", "/templates/generate", {}, "admin"),
]

# Roles that are BELOW the required role for each entry.
_ROLE_BELOW: dict[str, list[str]] = {
    "operator": ["viewer"],
    "admin": ["viewer", "operator"],
}


@pytest.mark.unit
@pytest.mark.api
@pytest.mark.parametrize(
    "router_obj,method,path,body,required_role",
    [pytest.param(r, m, p, b, rr, id=f"{m}:{p}->needs:{rr}") for r, m, p, b, rr in _GUARDED_ROUTES],
)
def test_insufficient_role_denied(router_obj, method, path, body, required_role):
    """Every guarded route returns 403 for each role below its minimum."""
    insufficient_roles = _ROLE_BELOW.get(required_role, [])
    for role in insufficient_roles:
        app = _app_with_router(router_obj, role)
        client = _client(app)
        if method == "GET":
            resp = client.get(path)
        elif method == "POST":
            resp = client.post(path, json=body)
        elif method == "DELETE":
            resp = client.delete(path)
        else:
            resp = client.request(method, path, json=body)
        assert resp.status_code == 403, (
            f"Expected 403 for {method} {path} with role='{role}' "
            f"(requires '{required_role}'), got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Read endpoints: require_role("viewer") enforced
#
# These 11 endpoints were previously unauthenticated.  They now carry an
# explicit _user=Depends(require_role("viewer")) so the auth dependency graph
# is auditable and a caller whose role is below viewer (rank 0, e.g. an
# unrecognised claim that the auth middleware resolves to "none") is denied.
#
# Test strategy:
#   - Inject CurrentUser(role="none") → rank 0 < viewer rank 1 → 403.
#   - Inject CurrentUser(role="viewer") → rank 1 >= 1 → not 403.
# ---------------------------------------------------------------------------


def _app_with_no_rank_user(router) -> FastAPI:
    """Return an app where get_current_user resolves to a zero-rank user."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="anonymous", role="none"
    )
    return app


# Hand-listed read endpoints and the router they belong to.
# Format: (router_obj, http_method, path, json_body_or_None)
_READ_ENDPOINTS: list[tuple[Any, str, str, dict | None]] = [
    # requests router
    (requests_router, "GET", "/requests/", None),
    (requests_router, "GET", "/requests/return", None),
    (requests_router, "GET", "/requests/req-1/status", None),
    (requests_router, "POST", "/requests/status", {"request_ids": ["req-1"]}),
    (requests_router, "GET", "/requests/req-1/stream", None),
    # machines router
    (machines_router, "GET", "/machines/", None),
    (machines_router, "GET", "/machines/m-1/status", None),
    (machines_router, "GET", "/machines/m-1", None),
    # templates router
    (templates_router, "GET", "/templates/", None),
    (templates_router, "POST", "/templates/validate", {"template_id": "t-1"}),
    (templates_router, "GET", "/templates/t-1", None),
]


@pytest.mark.unit
@pytest.mark.api
@pytest.mark.parametrize(
    "router_obj,method,path,body",
    [pytest.param(r, m, p, b, id=f"anon_denied:{m}:{p}") for r, m, p, b in _READ_ENDPOINTS],
)
def test_read_endpoint_anonymous_gets_403(router_obj, method, path, body):
    """Every viewer-guarded read endpoint returns 403 for a zero-rank caller.

    A caller whose role is not in the rank table (e.g. "none") has effective
    rank 0, which is below the viewer threshold (rank 1).  This simulates a
    request that reaches the route without valid credentials when auth is
    enabled.
    """
    app = _app_with_no_rank_user(router_obj)
    client = _client(app)
    if method == "GET":
        resp = client.get(path)
    elif method == "POST":
        resp = client.post(path, json=body or {})
    else:
        resp = client.request(method, path, json=body)
    assert resp.status_code == 403, (
        f"Expected 403 for {method} {path} with role='none', got {resp.status_code}"
    )


@pytest.mark.unit
@pytest.mark.api
@pytest.mark.parametrize(
    "router_obj,method,path,body",
    [pytest.param(r, m, p, b, id=f"viewer_passes:{m}:{p}") for r, m, p, b in _READ_ENDPOINTS],
)
def test_read_endpoint_viewer_is_not_denied(router_obj, method, path, body):
    """A viewer-role caller is not rejected with 403 on any read endpoint.

    The dependency is require_role("viewer") — viewer is the minimum rank so
    any authenticated caller should pass the role gate.  (The response may be
    4xx/5xx for other reasons such as missing orchestrator stubs; we only
    assert it is not 403.)
    """
    app = _app_with_router(router_obj, "viewer")
    client = _client(app)
    if method == "GET":
        resp = client.get(path)
    elif method == "POST":
        resp = client.post(path, json=body or {})
    else:
        resp = client.request(method, path, json=body)
    assert resp.status_code != 403, (
        f"Viewer should not be denied on {method} {path}, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Dynamic route enumeration: protected routes discovered from app.routes
#
# For each APIRoute that carries a Depends(require_role(...)) parameter with a
# min_role above "viewer", we parametrize an anonymous-403 test.  The test
# mounts the route's router in a minimal app, injects a "viewer"-role user,
# and asserts a 403 is returned.  Adding a new guarded route auto-adds
# coverage — no manual update to a hand-maintained list is required.
# ---------------------------------------------------------------------------

_ROUTER_MAP = {
    "machines": machines_router,
    "requests": requests_router,
    "templates": templates_router,
}

# Roles below "viewer" are not representable in the normal rank table, but
# "viewer" itself is below "operator" and "admin", so we use it as the
# underprivileged user for these tests.
_ANONYMOUS_ROLE = "viewer"

# Minimal placeholder bodies so requests parse without a 422.
_PLACEHOLDER_BODIES: dict[tuple[str, str], dict] = {
    ("POST", "/machines/request"): {"template_id": "t-1", "count": 1},
    ("POST", "/machines/return"): {"machine_ids": ["m-1"]},
    ("POST", "/requests/status"): {"request_ids": ["req-1"]},
    ("POST", "/templates/validate"): {"template_id": "t-1"},
    ("POST", "/templates/generate"): {},
}


def _collect_protected_routes() -> list[tuple[str, str, str, dict | None, str]]:
    """Discover routes with require_role(min_role > viewer) by inspecting closures.

    Returns a list of (router_name, method, path, body_or_None, required_role).
    """
    discovered: list[tuple[str, str, str, dict | None, str]] = []
    for router_name, router_obj in _ROUTER_MAP.items():
        for route in router_obj.routes:
            if not isinstance(route, APIRoute):
                continue
            sig = inspect.signature(route.endpoint)
            for param in sig.parameters.values():
                if not hasattr(param.default, "dependency"):
                    continue
                dep_fn = param.default.dependency
                if not callable(dep_fn) or dep_fn.__name__ != "_check":
                    continue
                # Extract min_role from the require_role closure.
                closurevars = inspect.getclosurevars(dep_fn)
                min_role: str = closurevars.nonlocals.get("min_role", "viewer")
                # Only collect routes that require more than viewer.
                if min_role in ("operator", "admin"):
                    for method in sorted(route.methods or []):
                        path = route.path
                        body = _PLACEHOLDER_BODIES.get((method, path))
                        discovered.append((router_name, method, path, body, min_role))
                break  # One require_role dep per route is sufficient.
    return discovered


_DYNAMIC_PROTECTED_ROUTES = _collect_protected_routes()

# Floor guard: if the require_role factory is renamed or restructured the
# closure-inspection logic in _collect_protected_routes() will silently
# return an empty list and every parametrized test below will vacuously pass.
# This assertion catches that silent regression at collection time.
# The floor (7) is intentionally below the current count (10) to allow new
# guarded routes to be added without updating this file, while still failing
# loudly if discovery completely breaks.
assert len(_DYNAMIC_PROTECTED_ROUTES) >= 7, (
    f"dynamic route discovery found only {len(_DYNAMIC_PROTECTED_ROUTES)} protected routes "
    f"(expected >= 7). Either the require_role factory was renamed/restructured and "
    f"_collect_protected_routes() needs updating, or guarded routes were removed."
)


@pytest.mark.unit
@pytest.mark.api
@pytest.mark.parametrize(
    "router_name,method,path,body,required_role",
    [
        pytest.param(rn, m, p, b, rr, id=f"dynamic:{m}:{p}->needs:{rr}")
        for rn, m, p, b, rr in _DYNAMIC_PROTECTED_ROUTES
    ],
)
def test_dynamic_protected_route_denies_anonymous(router_name, method, path, body, required_role):
    """Every dynamically discovered protected route returns 403 for a viewer.

    This test is auto-parametrized: adding a new require_role("operator") or
    require_role("admin") route is automatically covered without any manual
    update to the test file.
    """
    router_obj = _ROUTER_MAP[router_name]
    app = _app_with_router(router_obj, _ANONYMOUS_ROLE)
    client = _client(app)
    if method == "GET":
        resp = client.get(path)
    elif method == "POST":
        resp = client.post(path, json=body or {})
    elif method == "DELETE":
        resp = client.delete(path)
    else:
        resp = client.request(method, path, json=body)
    assert resp.status_code == 403, (
        f"Expected 403 for {method} {path} (requires '{required_role}') "
        f"with role='{_ANONYMOUS_ROLE}', got {resp.status_code}"
    )
