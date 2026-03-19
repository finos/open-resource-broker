"""Tests for REST router fixes — Task 4.

Verifies:
- list_machines forwards provider_name query param to ListMachinesInput
- validate_template accepts a typed body and returns 200
- validate_template with no body returns 422
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_list_machines_orchestrator,
    get_scheduler_strategy,
    get_validate_template_orchestrator,
)
from orb.api.routers.machines import router as machines_router
from orb.api.routers.templates import router as templates_router
from orb.application.services.orchestration.dtos import (
    ListMachinesInput,
    ListMachinesOutput,
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
    async def global_exception_handler(__request, exc):  # noqa: N807
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
    async def global_exception_handler(__request, exc):  # noqa: N807
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


def _make_machines_client(app, overrides=None):
    scheduler = MagicMock()
    scheduler.format_machine_status_response.return_value = {"machines": []}
    scheduler.format_machine_details_response.return_value = {}
    scheduler.format_request_response.return_value = {}
    app.dependency_overrides[get_scheduler_strategy] = lambda: scheduler
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
