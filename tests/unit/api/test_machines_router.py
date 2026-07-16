"""Tests for REST router fixes — Task 4.

Verifies:
- list_machines forwards provider_name query param to ListMachinesInput
- validate_template accepts a typed body and returns 200
- validate_template with no body returns 422
- return_machines accepts request_id body field
- return_machines enforces mutual-exclusion (422 on bad combos)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_current_user,
    get_list_machines_orchestrator,
    get_return_machines_orchestrator,
    get_scheduler_strategy,
    get_validate_template_orchestrator,
)
from orb.api.routers.machines import router as machines_router
from orb.api.routers.templates import router as templates_router
from orb.application.services.orchestration.dtos import (
    ListMachinesInput,
    ListMachinesOutput,
    ReturnMachinesOutput,
    ValidateTemplateOutput,
)


@pytest.fixture()
def machines_app():
    from fastapi.responses import JSONResponse

    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(machines_router)
    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(__request, exc):
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


@pytest.fixture()
def templates_app():
    from fastapi.responses import JSONResponse

    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(templates_router)
    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(__request, exc):
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


def _make_machines_client(app, overrides=None):
    from orb.api.dependencies import CurrentUser

    scheduler = MagicMock()
    scheduler.format_machine_status_response.return_value = {"machines": []}
    scheduler.format_machine_details_response.return_value = {}
    scheduler.format_request_response.return_value = {}
    app.dependency_overrides[get_scheduler_strategy] = lambda: scheduler
    # Supply an operator identity so role guards pass on all machines endpoints.
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-operator", role="operator"
    )
    for dep, factory in (overrides or {}).items():
        app.dependency_overrides[dep] = factory
    return TestClient(app, raise_server_exceptions=False)


def _make_templates_client(app, overrides=None):
    scheduler = MagicMock()
    scheduler.format_templates_response.return_value = {"templates": []}
    scheduler.format_template_mutation_response.return_value = {"valid": True}
    app.dependency_overrides[get_scheduler_strategy] = lambda: scheduler
    for dep, factory in (overrides or {}).items():
        app.dependency_overrides[dep] = factory
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
@pytest.mark.api
class TestListMachinesProviderNameFilter:
    def test_list_machines_forwards_provider_name_to_orchestrator(self, machines_app):
        captured = {}

        async def fake_execute(inp: ListMachinesInput):
            captured["input"] = inp
            return ListMachinesOutput(machines=[])

        orchestrator = MagicMock()
        orchestrator.execute = fake_execute

        client = _make_machines_client(
            machines_app, {get_list_machines_orchestrator: lambda: orchestrator}
        )
        resp = client.get("/machines/?provider_name=aws")

        assert resp.status_code == 200
        assert captured["input"].provider_name == "aws"


@pytest.mark.unit
@pytest.mark.api
class TestReturnMachinesByRequestId:
    def test_return_with_request_id_calls_orchestrator(self, machines_app):
        """POST /machines/return with request_id → orchestrator called with request_id set."""
        captured = {}

        async def fake_execute(inp):
            captured["input"] = inp
            return ReturnMachinesOutput(request_id="ret-1", status="pending")

        orchestrator = MagicMock()
        orchestrator.execute = fake_execute

        client = _make_machines_client(
            machines_app, {get_return_machines_orchestrator: lambda: orchestrator}
        )
        resp = client.post(
            "/machines/return",
            json={"request_id": "req-abc"},
        )

        assert resp.status_code == 200
        assert captured["input"].request_id == "req-abc"
        assert captured["input"].machine_ids == []
        assert captured["input"].all_machines is False

    def test_return_machine_ids_and_request_id_is_422(self, machines_app):
        """machine_ids + request_id → 422 (mutual exclusion)."""
        client = _make_machines_client(machines_app)
        resp = client.post(
            "/machines/return",
            json={"machine_ids": ["i-1"], "request_id": "req-abc"},
        )
        assert resp.status_code == 422

    def test_return_no_target_is_noop_200(self, machines_app):
        """Empty body (no targeting mode) → 200; handled downstream as a no-op.

        Preserves the pre-existing contract that an empty return request is a
        no-op rather than a validation error.
        """

        async def fake_execute(inp):
            return ReturnMachinesOutput(request_id=None, status="pending")

        orchestrator = MagicMock()
        orchestrator.execute = fake_execute

        client = _make_machines_client(
            machines_app, {get_return_machines_orchestrator: lambda: orchestrator}
        )
        resp = client.post("/machines/return", json={})
        assert resp.status_code == 200

    def test_return_machine_ids_alone_accepted(self, machines_app):
        """machine_ids provided without request_id → 200."""

        async def fake_execute(inp):
            return ReturnMachinesOutput(request_id="ret-1", status="pending")

        orchestrator = MagicMock()
        orchestrator.execute = fake_execute

        client = _make_machines_client(
            machines_app, {get_return_machines_orchestrator: lambda: orchestrator}
        )
        resp = client.post(
            "/machines/return",
            json={"machine_ids": ["i-1"]},
        )
        assert resp.status_code == 200


@pytest.mark.unit
@pytest.mark.api
class TestValidateTemplateTypedBody:
    def test_validate_template_accepts_typed_body(self, templates_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=ValidateTemplateOutput(
                valid=True,
                errors=[],
                template_id="t1",
            )
        )
        client = _make_templates_client(
            templates_app, {get_validate_template_orchestrator: lambda: orchestrator}
        )
        resp = client.post(
            "/templates/validate",
            json={"template_id": "t1", "provider_api": "EC2Fleet"},
        )
        assert resp.status_code == 200

    def test_validate_template_missing_body_returns_422(self, templates_app):
        client = _make_templates_client(templates_app)
        resp = client.post("/templates/validate")
        assert resp.status_code == 422
