"""Domain and validation errors map to client-error statuses, with and without auth.

Every API route of the real application is probed. Faults are injected where routes
reach the application layer (orchestrators, buses, configuration), so the router,
its error decorator, the auth middleware and the application's exception handlers
all take part. The same cells run with authentication off and with a real bearer
token on.
"""

from __future__ import annotations

from typing import Any

from orb.domain.base.exceptions import (
    ApplicationError,
    BusinessRuleViolationError,
    ConcurrencyError,
    DuplicateError,
    EntityNotFoundError,
    InfrastructureError,
    ValidationError,
)
from orb.domain.machine.exceptions import MachineNotFoundError
from orb.domain.request.exceptions import RequestNotFoundError, RequestValidationError
from orb.domain.template.exceptions import TemplateNotFoundError, TemplateValidationError
from tests.api.contract.harness import build_probe, inject, route_keys, seams_for

# name -> (exception factory, expected status)
FAULTS: dict[str, tuple[Any, Any]] = {
    "entity_not_found": (lambda: EntityNotFoundError("Thing", "unknown-id"), 404),
    "request_not_found": (lambda: RequestNotFoundError("unknown-id"), 404),
    "machine_not_found": (lambda: MachineNotFoundError("unknown-id"), 404),
    "template_not_found": (lambda: TemplateNotFoundError("unknown-id"), 404),
    "validation": (lambda: ValidationError("rejected input"), 400),
    "request_validation": (lambda: RequestValidationError("rejected input"), 400),
    "template_validation": (lambda: TemplateValidationError("rejected template"), 400),
    "business_rule": (lambda: BusinessRuleViolationError("not allowed"), 422),
    "duplicate": (lambda: DuplicateError("already exists"), 409),
    "concurrency": (lambda: ConcurrencyError("changed"), 409),
    "infrastructure": (lambda: InfrastructureError("backend down"), 503),
    # Anything unclassified is reported as a server error; the decorator may present
    # it as an infrastructure failure (503) or as an unexpected one (500).
    "unexpected": (lambda: RuntimeError("boom"), (500, 503)),
    # Cleanup and polling failures are raised as ApplicationError and are server-side.
    "application_error": (lambda: ApplicationError("cleanup failed"), (500, 503)),
}

# Text of an injected server-side fault that must never reach the response body.
INTERNAL_TEXT: dict[str, str] = {
    "unexpected": "boom",
    "application_error": "cleanup failed",
}

# Routes with no injectable application seam, with the reason each is not in the
# fault matrix. A route missing from both this table and the seam discovery fails
# ``test_every_route_has_a_probe``.
NO_FAULT_SEAM: dict[str, str] = {
    "GET /favicon.ico": "static asset, no application call",
    "GET /livez": "process liveness, no application call",
    "GET /metrics": "serves the in-process metrics registry",
    "GET /info": "static server information",
    "GET /api/v1/me/": "echoes the authenticated identity",
    "GET /api/v1/events/": "server-sent events stream over an in-process bus",
    "GET /api/v1/observability/telemetry": "reads in-process telemetry state",
    "GET /api/v1/providers/schemas": "reads the provider registry",
    "GET /api/v1/providers/{name}/schema": "reads the provider registry",
    "GET /api/v1/providers/discover/{provider_api}/{resource_type}": "calls the provider registry",
    "DELETE /api/v1/machines/{machine_id}": "destructive admin route, guarded by configuration",
    "POST /api/v1/requests/{request_id}/purge": "destructive admin route, guarded by configuration",
    "POST /api/v1/admin/database/wipe": "destructive admin route, guarded by configuration",
    "POST /api/v1/admin/database/cleanup": "destructive admin route, guarded by configuration",
    "POST /api/v1/admin/init": "initialises local files, guarded by configuration",
    "POST /api/v1/admin/reload-config": "reloads local configuration",
}

# Routes that answer the same way whatever the application layer raises.
HANDLED_LOCALLY: dict[str, str] = {
    "GET /health": "reports unhealthy rather than raising",
    "GET /readyz": "reports not-ready rather than raising",
    "GET /api/v1/providers/": "degrades to an empty provider list when configuration fails",
    "GET /api/v1/providers/health": "degrades to an empty provider list when configuration fails",
    "GET /api/v1/requests/{request_id}/stream": "reports failures as an event inside the stream",
    "POST /api/v1/config/save": "guarded by the destructive-admin configuration",
}


def _cells(app: Any) -> list[tuple[str, str]]:
    cells = []
    for key, route in sorted(route_keys(app).items()):
        if key in NO_FAULT_SEAM or key in HANDLED_LOCALLY:
            continue
        if seams_for(route):
            cells.extend((key, fault) for fault in FAULTS)
    return cells


def test_every_route_has_a_probe(app):
    """A new route must either have an injectable seam or be listed with a reason."""
    unaccounted = [
        key
        for key, route in route_keys(app).items()
        if not seams_for(route) and key not in NO_FAULT_SEAM and key not in HANDLED_LOCALLY
    ]
    assert unaccounted == [], f"routes without a fault seam or a listed reason: {unaccounted}"


def test_listed_exceptions_are_real_routes(app):
    keys = set(route_keys(app))
    assert set(NO_FAULT_SEAM) | set(HANDLED_LOCALLY) <= keys


def test_every_probe_resolves_in_the_openapi_document(app):
    spec = app.openapi()
    missing = [
        key
        for key in route_keys(app)
        if key.split(" ", 1)[1] not in spec["paths"] and key != "GET /favicon.ico"
    ]
    assert missing == []


def test_matrix_is_not_empty(app):
    assert len(_cells(app)) > 100


def test_fault_matrix(app, client, clear_faults):
    """Run every (route, fault) cell; report all failures together."""
    spec = app.openapi()
    failures: list[str] = []
    routes = route_keys(app)

    for key, fault_name in _cells(app):
        factory, expected = FAULTS[fault_name]
        expected = expected if isinstance(expected, tuple) else (expected,)
        probe = build_probe(key, spec)
        inject(app, seams_for(routes[key]), factory())
        response = client.request(probe.method, probe.url, json=probe.json, params=probe.params)

        problems = _check(response, expected, INTERNAL_TEXT.get(fault_name))
        if problems:
            failures.append(f"{key} <- {fault_name}: {problems}")

    assert failures == [], "\n" + "\n".join(failures)


def test_template_rejection_reports_its_reason_to_the_caller(app, client, clear_faults):
    """A rejected template is the caller's to fix, so the reason is part of the 400."""
    spec = app.openapi()
    routes = route_keys(app)
    key, _ = next(cell for cell in _cells(app) if cell[1] == "template_validation")
    probe = build_probe(key, spec)
    inject(app, seams_for(routes[key]), TemplateValidationError("spot is not supported"))

    response = client.request(probe.method, probe.url, json=probe.json, params=probe.params)

    assert response.status_code == 400
    assert response.json()["error"]["details"]["reason"] == "spot is not supported"


def _check(response: Any, expected: tuple[int, ...], internal_text: str | None) -> list[str]:
    problems = []
    if internal_text and response.status_code >= 500 and internal_text in response.text:
        problems.append(f"internal message leaked: {response.text[:120]}")
    if response.status_code not in expected:
        problems.append(f"status {response.status_code}, expected {expected}")
    if min(expected) >= 400:
        if "application/json" not in response.headers.get("content-type", ""):
            problems.append(f"content-type {response.headers.get('content-type')!r}")
        else:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if not (isinstance(error, dict) and body.get("success") is False and error.get("code")):
                problems.append(f"not the standard error envelope: {response.text[:120]}")
    return problems
