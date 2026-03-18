"""Router-level tests for the requests API endpoints."""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_list_return_requests_orchestrator,
    get_request_status_orchestrator,
    get_scheduler_strategy,
)
from orb.api.routers.requests import list_return_requests, router as requests_router
from orb.application.services.orchestration.dtos import (
    GetRequestStatusOutput,
    ListReturnRequestsOutput,
)


@pytest.fixture()
def requests_app():
    from fastapi.responses import JSONResponse

    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(requests_router)

    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(__request, exc):  # noqa: N807
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


def _make_scheduler():
    scheduler = MagicMock()
    scheduler.format_request_status_response.return_value = {"requests": []}
    scheduler.format_request_response.return_value = {}
    return scheduler


def _make_client(app, overrides=None):
    scheduler = _make_scheduler()
    app.dependency_overrides[get_scheduler_strategy] = lambda: scheduler
    for dep, factory in (overrides or {}).items():
        app.dependency_overrides[dep] = factory
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
@pytest.mark.api
class TestGetRequestDetailsRemoved:
    """GET /{request_id} route exists and returns request details with detailed=True."""

    def test_get_request_details_route_removed(self, requests_app):
        """GET /requests/req-123 (no /status) must return 404 or 405 — route removed."""
        client = _make_client(requests_app)

        resp = client.get("/requests/req-123")

        assert resp.status_code in (404, 405)

    def test_get_request_status_route_exists(self, requests_app):
        """GET /requests/req-123/status must return 200."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        resp = client.get("/requests/req-123/status")

        assert resp.status_code == 200

    def test_get_request_status_passes_long_true_by_default(self, requests_app):
        """GET /requests/{id}/status with no ?long= → detailed=True (default)."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        client.get("/requests/req-123/status")

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.detailed is True

    def test_get_request_status_passes_long_false_when_queried(self, requests_app):
        """GET /requests/{id}/status?long=false → detailed=False."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        client.get("/requests/req-123/status?long=false")

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.detailed is False

    def test_get_request_status_passes_request_id(self, requests_app):
        """GET /requests/req-abc/status → request_ids=['req-abc']."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        client.get("/requests/req-abc/status")

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.request_ids == ["req-abc"]


@pytest.mark.unit
@pytest.mark.api
class TestListReturnRequestsLimitType:
    """limit on list_return_requests must be int."""

    def test_limit_annotation_is_int(self):
        """list_return_requests limit parameter must be int."""
        sig = inspect.signature(list_return_requests)
        limit_param = sig.parameters["limit"]
        assert limit_param.annotation is int

    def test_list_return_requests_limit_has_default(self):
        """list_return_requests limit parameter must have a default value."""
        sig = inspect.signature(list_return_requests)
        assert sig.parameters["limit"].default is not inspect.Parameter.empty

    def test_list_return_requests_explicit_limit(self, requests_app):
        """GET /requests/return?limit=10 → orchestrator receives limit=10."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=ListReturnRequestsOutput(requests=[]))
        client = _make_client(
            requests_app, {get_list_return_requests_orchestrator: lambda: orchestrator}
        )

        client.get("/requests/return?limit=10")

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.limit == 10

    def test_list_return_requests_default_limit(self, requests_app):
        """GET /requests/return (no limit) → orchestrator receives limit=50."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=ListReturnRequestsOutput(requests=[]))
        client = _make_client(
            requests_app, {get_list_return_requests_orchestrator: lambda: orchestrator}
        )

        client.get("/requests/return")

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.limit == 50
