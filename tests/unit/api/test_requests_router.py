"""Router-level tests for the requests API endpoints."""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_list_requests_orchestrator,
    get_list_return_requests_orchestrator,
    get_request_formatter,
    get_request_status_orchestrator,
    get_scheduler_strategy,
)
from orb.api.routers.requests import list_return_requests, router as requests_router
from orb.application.services.orchestration.dtos import (
    GetRequestStatusOutput,
    ListRequestsOutput,
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
    async def global_exception_handler(__request, exc):
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


def _make_formatter():
    formatter = MagicMock()
    formatter.format_request_status.return_value = MagicMock(data={"requests": []})
    return formatter


def _make_echo_formatter():
    """Formatter that echoes the request dicts so the body can be asserted.

    Mirrors the real formatter's single-request contract (``{"requests": [...]}``)
    without any camelCase transform, letting tests confirm a failed request's
    error block reaches the response body rather than being 404'd away.
    """
    formatter = MagicMock()
    formatter.format_request_status.side_effect = lambda requests: MagicMock(
        data={"requests": requests}
    )
    return formatter


def _make_client(app, overrides=None, formatter=None):
    scheduler = _make_scheduler()
    formatter = formatter if formatter is not None else _make_formatter()
    app.dependency_overrides[get_scheduler_strategy] = lambda: scheduler
    app.dependency_overrides[get_request_formatter] = lambda: formatter
    for dep, factory in (overrides or {}).items():
        app.dependency_overrides[dep] = factory
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
@pytest.mark.api
class TestGetRequestDetailsRemoved:
    """GET /{request_id} route now exists and returns request details."""

    def test_get_request_unknown_id_returns_404(self, requests_app):
        """GET /requests/req-123 (without /status) for an unknown ID must return 404.

        Exercises the ACTUAL production path: the orchestrator swallows the
        underlying RequestNotFoundError into a single dict carrying the explicit
        ``not_found`` flag, so result.requests is length-1 (never empty). The
        router must detect that flag and surface a 404.
        """
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetRequestStatusOutput(
                requests=[
                    {
                        "request_id": "req-123",
                        "not_found": True,
                        "error": "Request with ID 'req-123' not found",
                    }
                ]
            )
        )
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        resp = client.get("/requests/req-123")

        assert resp.status_code == 404

    def test_get_request_empty_result_returns_404(self, requests_app):
        """Defensive: a truly empty result (no entries at all) also yields 404."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        resp = client.get("/requests/req-123")

        assert resp.status_code == 404

    def test_get_request_found_returns_200(self, requests_app):
        """GET /requests/{id} for a real request (no 'error' key) returns 200."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetRequestStatusOutput(
                requests=[{"request_id": "req-123", "status": "completed"}]
            )
        )
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        resp = client.get("/requests/req-123")

        assert resp.status_code == 200

    def test_get_request_passes_request_id(self, requests_app):
        """GET /requests/req-xyz → request_ids=['req-xyz']."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        client.get("/requests/req-xyz")

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.request_ids == ["req-xyz"]

    def test_get_request_verbose_true_by_default(self, requests_app):
        """GET /requests/{id} with no ?verbose= → verbose=True (default)."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        client.get("/requests/req-123")

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.verbose is True

    def test_get_request_status_unknown_id_returns_404(self, requests_app):
        """GET /requests/req-123/status for an unknown ID must return 404.

        Exercises the ACTUAL production path: the orchestrator returns a
        single dict carrying the explicit ``not_found`` flag rather than an
        empty list.
        """
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetRequestStatusOutput(
                requests=[
                    {
                        "request_id": "req-123",
                        "not_found": True,
                        "error": "Request with ID 'req-123' not found",
                    }
                ]
            )
        )
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        resp = client.get("/requests/req-123/status")

        assert resp.status_code == 404

    def test_get_request_status_empty_result_returns_404(self, requests_app):
        """Defensive: a truly empty result (no entries at all) also yields 404."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        resp = client.get("/requests/req-123/status")

        assert resp.status_code == 404

    def test_get_request_status_found_returns_200(self, requests_app):
        """GET /requests/{id}/status for a real request (no 'error' key) returns 200."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetRequestStatusOutput(
                requests=[{"request_id": "req-123", "status": "completed"}]
            )
        )
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        resp = client.get("/requests/req-123/status")

        assert resp.status_code == 200

    def test_get_request_status_passes_verbose_true_by_default(self, requests_app):
        """GET /requests/{id}/status with no ?verbose= → verbose=True (default)."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        client.get("/requests/req-123/status")

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.verbose is True

    def test_get_request_status_passes_verbose_false_when_queried(self, requests_app):
        """GET /requests/{id}/status?verbose=false → verbose=False."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[]))
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        client.get("/requests/req-123/status?verbose=false")

        call_input = orchestrator.execute.call_args[0][0]
        assert call_input.verbose is False

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
class TestGetRequestFailedNotConfusedWithNotFound:
    """A REAL request that failed provisioning must return 200, never 404.

    RequestDTO carries a legitimate top-level ``error`` block, populated when a
    request fails provisioning, and ``to_dict()`` emits it alongside a "status"
    field. The single-ID endpoints must NOT mistake such a request for the
    orchestrator's synthetic not-found marker (``{"request_id", "error"}`` with
    no "status"). These are the regression guards.
    """

    @staticmethod
    def _failed_request_dict():
        """A failed request shaped exactly as RequestDTO.to_dict() emits it.

        Includes BOTH a "status" of "failed" and a populated structured "error"
        block, matching the real production key set (request_id, status,
        created_at, error, plus the always-present scalar fields).
        """
        return {
            "request_id": "req-1",
            "status": "failed",
            "requested_count": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "error": {
                "code": "InsufficientInstanceCapacity",
                "message": "no capacity",
            },
        }

    def test_get_request_failed_request_returns_200(self, requests_app):
        """GET /requests/{id} for a failed request (status + error) → 200."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetRequestStatusOutput(requests=[self._failed_request_dict()])
        )
        client = _make_client(
            requests_app,
            {get_request_status_orchestrator: lambda: orchestrator},
            formatter=_make_echo_formatter(),
        )

        resp = client.get("/requests/req-1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["requests"][0]["error"]["code"] == "InsufficientInstanceCapacity"

    def test_get_request_status_failed_request_returns_200(self, requests_app):
        """GET /requests/{id}/status for a failed request (status + error) → 200."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetRequestStatusOutput(requests=[self._failed_request_dict()])
        )
        client = _make_client(
            requests_app,
            {get_request_status_orchestrator: lambda: orchestrator},
            formatter=_make_echo_formatter(),
        )

        resp = client.get("/requests/req-1/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["requests"][0]["error"]["code"] == "InsufficientInstanceCapacity"

    @staticmethod
    def _sync_errored_entry():
        """An EXISTING request whose provider sync raised (non-not-found).

        This is the orchestrator's error marker for a request that exists but
        failed to sync (e.g. ProviderContractError): a string ``error`` and NO
        ``not_found`` flag. It must NOT be treated as a 404.
        """
        return {
            "request_id": "req-1",
            "error": ("Provider aws did not emit ProviderFulfilment for acquire request."),
        }

    def test_get_request_sync_errored_existing_request_returns_200(self, requests_app):
        """GET /requests/{id}: existing request, sync errored (no flag) → 200, not 404."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetRequestStatusOutput(requests=[self._sync_errored_entry()])
        )
        client = _make_client(
            requests_app,
            {get_request_status_orchestrator: lambda: orchestrator},
            formatter=_make_echo_formatter(),
        )

        resp = client.get("/requests/req-1")

        assert resp.status_code == 200
        body = resp.json()
        assert "ProviderFulfilment" in body["requests"][0]["error"]

    def test_get_request_status_sync_errored_existing_request_returns_200(self, requests_app):
        """GET /requests/{id}/status: existing request, sync errored (no flag) → 200."""
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(
            return_value=GetRequestStatusOutput(requests=[self._sync_errored_entry()])
        )
        client = _make_client(
            requests_app,
            {get_request_status_orchestrator: lambda: orchestrator},
            formatter=_make_echo_formatter(),
        )

        resp = client.get("/requests/req-1/status")

        assert resp.status_code == 200
        body = resp.json()
        assert "ProviderFulfilment" in body["requests"][0]["error"]


@pytest.mark.unit
@pytest.mark.api
class TestGetRequestRealOrchestrator:
    """End-to-end guard behaviour against the REAL orchestrator (no mock output).

    The orchestrator swallows the underlying RequestNotFoundError into an
    error-shaped dict so batch callers get partial failures. These tests wire
    the genuine orchestrator into the router to prove the single-ID endpoints
    translate that error entry into a 404 rather than a 200 success envelope.
    """

    @staticmethod
    def _make_orchestrator(query_bus):
        from orb.application.services.orchestration.get_request_status import (
            GetRequestStatusOrchestrator,
        )

        logger = MagicMock()
        return GetRequestStatusOrchestrator(
            command_bus=MagicMock(), query_bus=query_bus, logger=logger
        )

    def test_get_request_status_unknown_id_real_orchestrator_returns_404(self, requests_app):
        """Real orchestrator + raising query bus → error-dict → router 404."""
        from orb.domain.request.exceptions import RequestNotFoundError

        query_bus = MagicMock()
        query_bus.execute = AsyncMock(side_effect=RequestNotFoundError("does-not-exist"))
        orchestrator = self._make_orchestrator(query_bus)
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        resp = client.get("/requests/does-not-exist/status")

        assert resp.status_code == 404

    def test_get_request_unknown_id_real_orchestrator_returns_404(self, requests_app):
        """Real orchestrator on GET /{id} (no /status suffix) also returns 404."""
        from orb.domain.request.exceptions import RequestNotFoundError

        query_bus = MagicMock()
        query_bus.execute = AsyncMock(side_effect=RequestNotFoundError("does-not-exist"))
        orchestrator = self._make_orchestrator(query_bus)
        client = _make_client(requests_app, {get_request_status_orchestrator: lambda: orchestrator})

        resp = client.get("/requests/does-not-exist")

        assert resp.status_code == 404

    def test_get_request_status_sync_errored_real_orchestrator_returns_200(self, requests_app):
        """Real orchestrator + query bus raising ProviderContractError → 200, not 404.

        Drives the orchestrator's non-not-found except-branch: the request
        exists but its provider sync raised, so the orchestrator emits an error
        entry WITHOUT the not_found flag and the router must return 200.
        """
        from orb.domain.base.exceptions import ProviderContractError

        query_bus = MagicMock()
        query_bus.execute = AsyncMock(
            side_effect=ProviderContractError(
                "Provider aws did not emit ProviderFulfilment for acquire request."
            )
        )
        orchestrator = self._make_orchestrator(query_bus)
        client = _make_client(
            requests_app,
            {get_request_status_orchestrator: lambda: orchestrator},
            formatter=_make_echo_formatter(),
        )

        resp = client.get("/requests/req-1/status")

        assert resp.status_code == 200
        body = resp.json()
        assert "not_found" not in body["requests"][0]
        assert "ProviderFulfilment" in body["requests"][0]["error"]

    def test_get_request_sync_errored_real_orchestrator_returns_200(self, requests_app):
        """Real orchestrator on GET /{id} (no /status): ProviderContractError → 200."""
        from orb.domain.base.exceptions import ProviderContractError

        query_bus = MagicMock()
        query_bus.execute = AsyncMock(
            side_effect=ProviderContractError(
                "Provider aws did not emit ProviderFulfilment for acquire request."
            )
        )
        orchestrator = self._make_orchestrator(query_bus)
        client = _make_client(
            requests_app,
            {get_request_status_orchestrator: lambda: orchestrator},
            formatter=_make_echo_formatter(),
        )

        resp = client.get("/requests/req-1")

        assert resp.status_code == 200
        body = resp.json()
        assert "not_found" not in body["requests"][0]
        assert "ProviderFulfilment" in body["requests"][0]["error"]

    def test_get_request_status_real_failed_dto_returns_200(self, requests_app):
        """Real orchestrator + query bus returning a real failed RequestDTO → 200.

        Exercises the genuine RequestDTO.to_dict() serialisation path (not a
        hand-built dict) so the test cannot drift from the production shape. The
        DTO has status="failed" and a populated error block, which must be
        surfaced as 200 rather than swallowed into a 404.
        """
        from datetime import datetime, timezone

        from orb.application.request.dto import RequestDTO

        failed_dto = RequestDTO(
            request_id="req-1",
            status="failed",
            requested_count=1,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            error={"code": "InsufficientInstanceCapacity", "message": "no capacity"},
        )
        query_bus = MagicMock()
        query_bus.execute = AsyncMock(return_value=failed_dto)
        orchestrator = self._make_orchestrator(query_bus)
        client = _make_client(
            requests_app,
            {get_request_status_orchestrator: lambda: orchestrator},
            formatter=_make_echo_formatter(),
        )

        resp = client.get("/requests/req-1/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["requests"][0]["status"] == "failed"
        assert body["requests"][0]["error"]["code"] == "InsufficientInstanceCapacity"

    def test_get_request_real_failed_dto_returns_200(self, requests_app):
        """Real orchestrator on GET /{id} (no /status suffix) for a failed DTO → 200."""
        from datetime import datetime, timezone

        from orb.application.request.dto import RequestDTO

        failed_dto = RequestDTO(
            request_id="req-1",
            status="failed",
            requested_count=1,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            error={"code": "InsufficientInstanceCapacity", "message": "no capacity"},
        )
        query_bus = MagicMock()
        query_bus.execute = AsyncMock(return_value=failed_dto)
        orchestrator = self._make_orchestrator(query_bus)
        client = _make_client(
            requests_app,
            {get_request_status_orchestrator: lambda: orchestrator},
            formatter=_make_echo_formatter(),
        )

        resp = client.get("/requests/req-1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["requests"][0]["status"] == "failed"
        assert body["requests"][0]["error"]["code"] == "InsufficientInstanceCapacity"


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


@pytest.mark.unit
@pytest.mark.api
class TestListRequestsLimitOffsetBounds:
    """limit/offset query params on GET /requests/ and /requests/return must be bounded."""

    def _list_requests_client(self, requests_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=ListRequestsOutput(requests=[]))
        return _make_client(requests_app, {get_list_requests_orchestrator: lambda: orchestrator})

    @pytest.mark.parametrize("limit", [0, -1, 1001, 100000])
    def test_list_requests_limit_out_of_bounds_rejected(self, requests_app, limit):
        client = self._list_requests_client(requests_app)
        resp = client.get(f"/requests/?limit={limit}")
        assert resp.status_code == 422

    def test_list_requests_offset_negative_rejected(self, requests_app):
        client = self._list_requests_client(requests_app)
        resp = client.get("/requests/?offset=-1")
        assert resp.status_code == 422

    @pytest.mark.parametrize("limit", [1, 50, 1000])
    def test_list_requests_limit_within_bounds_accepted(self, requests_app, limit):
        client = self._list_requests_client(requests_app)
        resp = client.get(f"/requests/?limit={limit}")
        assert resp.status_code == 200

    def _list_return_requests_client(self, requests_app):
        orchestrator = AsyncMock()
        orchestrator.execute = AsyncMock(return_value=ListReturnRequestsOutput(requests=[]))
        return _make_client(
            requests_app, {get_list_return_requests_orchestrator: lambda: orchestrator}
        )

    @pytest.mark.parametrize("limit", [0, -1, 1001])
    def test_list_return_requests_limit_out_of_bounds_rejected(self, requests_app, limit):
        client = self._list_return_requests_client(requests_app)
        resp = client.get(f"/requests/return?limit={limit}")
        assert resp.status_code == 422

    def test_list_return_requests_offset_negative_rejected(self, requests_app):
        client = self._list_return_requests_client(requests_app)
        resp = client.get("/requests/return?offset=-1")
        assert resp.status_code == 422
