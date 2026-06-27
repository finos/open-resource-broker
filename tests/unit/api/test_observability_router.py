"""Unit tests for the observability router — machine metrics and request timeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import (
    get_current_user,
    get_machine_orchestrator,
    get_request_status_orchestrator,
)
from orb.api.routers.observability import router as observability_router
from orb.application.services.orchestration.dtos import (
    GetMachineOutput,
    GetRequestStatusOutput,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_app(*, role: str = "viewer") -> FastAPI:
    from fastapi.responses import JSONResponse

    from orb.api.dependencies import CurrentUser
    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(observability_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-user", role=role
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


def _make_machine_orchestrator(machine=None):
    orc = AsyncMock()
    orc.execute = AsyncMock(return_value=GetMachineOutput(machine=machine))
    return orc


def _make_request_orchestrator(requests: list | None = None):
    orc = AsyncMock()
    orc.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=requests or []))
    return orc


def _fake_machine(machine_id: str = "m-abc"):
    m = MagicMock()
    m.machine_id = machine_id
    return m


# ---------------------------------------------------------------------------
# Auth guard tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestObservabilityAuthGuard:
    def test_metrics_requires_viewer_role(self):
        from orb.api.dependencies import CurrentUser

        app = _make_app()
        # Override with an unknown role (rank 0 < viewer rank 1 → 403)
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="nobody", role="no_such_role"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/machines/m-1/metrics")
        assert resp.status_code == 403

    def test_timeline_requires_viewer_role(self):
        from orb.api.dependencies import CurrentUser

        app = _make_app()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            username="nobody", role="no_such_role"
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/requests/req-1/timeline")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Machine metrics tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestGetMachineMetrics:
    def test_returns_200_for_known_machine(self):
        app = _make_app()
        app.dependency_overrides[get_machine_orchestrator] = lambda: _make_machine_orchestrator(
            machine=_fake_machine("m-abc")
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/machines/m-abc/metrics")
        assert resp.status_code == 200

    def test_response_includes_series(self):
        app = _make_app()
        app.dependency_overrides[get_machine_orchestrator] = lambda: _make_machine_orchestrator(
            machine=_fake_machine("m-abc")
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/machines/m-abc/metrics").json()
        assert "series" in body
        assert isinstance(body["series"], list)
        assert len(body["series"]) == 4

    def test_response_machine_id_matches(self):
        app = _make_app()
        app.dependency_overrides[get_machine_orchestrator] = lambda: _make_machine_orchestrator(
            machine=_fake_machine("m-xyz")
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/machines/m-xyz/metrics").json()
        assert body["machine_id"] == "m-xyz"

    def test_unknown_range_normalises_to_1h(self):
        """An unrecognised ?range= value is silently normalised to '1h'."""
        app = _make_app()
        app.dependency_overrides[get_machine_orchestrator] = lambda: _make_machine_orchestrator(
            machine=_fake_machine("m-1")
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/machines/m-1/metrics?range=forever")
        assert resp.status_code == 200
        assert resp.json()["range"] == "1h"

    def test_valid_range_is_returned(self):
        app = _make_app()
        app.dependency_overrides[get_machine_orchestrator] = lambda: _make_machine_orchestrator(
            machine=_fake_machine("m-1")
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/machines/m-1/metrics?range=7d")
        assert resp.json()["range"] == "7d"

    def test_unknown_machine_id_returns_404(self):
        app = _make_app()
        # Orchestrator returns machine=None → 404.
        app.dependency_overrides[get_machine_orchestrator] = lambda: _make_machine_orchestrator(
            machine=None
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/machines/unknown-id/metrics")
        assert resp.status_code == 404

    def test_source_field_is_stub(self):
        app = _make_app()
        app.dependency_overrides[get_machine_orchestrator] = lambda: _make_machine_orchestrator(
            machine=_fake_machine("m-1")
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/machines/m-1/metrics")
        assert resp.json()["source"] == "stub"


# ---------------------------------------------------------------------------
# Request timeline tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestGetRequestTimeline:
    def _make_req(self, **kwargs):
        base = {
            "request_id": "req-001",
            "status": "complete",
            "created_at": "2026-01-01T00:00:00+00:00",
            "started_at": "2026-01-01T00:01:00+00:00",
            "first_status_check": None,
            "last_status_check": None,
            "completed_at": "2026-01-01T00:02:00+00:00",
        }
        base.update(kwargs)
        return base

    def test_returns_200_for_known_request(self):
        app = _make_app()
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([self._make_req()])
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/requests/req-001/timeline")
        assert resp.status_code == 200

    def test_response_contains_request_id(self):
        app = _make_app()
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([self._make_req()])
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/requests/req-001/timeline").json()
        assert body["request_id"] == "req-001"

    def test_events_list_is_present(self):
        app = _make_app()
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([self._make_req()])
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/requests/req-001/timeline").json()
        assert "events" in body
        assert isinstance(body["events"], list)

    def test_events_synthesised_from_request_fields(self):
        app = _make_app()
        req = self._make_req(
            created_at="2026-01-01T00:00:00+00:00",
            started_at="2026-01-01T00:01:00+00:00",
            completed_at="2026-01-01T00:02:00+00:00",
        )
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([req])
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/requests/req-001/timeline").json()
        event_types = {e["type"] for e in body["events"]}
        assert "created" in event_types
        assert "started" in event_types
        assert "completed" in event_types

    def test_none_timestamp_fields_are_omitted(self):
        """Fields with null timestamps must not produce an event entry."""
        app = _make_app()
        req = self._make_req(
            started_at=None,
            first_status_check=None,
            last_status_check=None,
            completed_at=None,
        )
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([req])
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/requests/req-001/timeline").json()
        event_types = {e["type"] for e in body["events"]}
        assert "started" not in event_types
        assert "completed" not in event_types

    def test_unknown_request_returns_404(self):
        app = _make_app()
        # Empty list → 404.
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([])
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/requests/unknown-req/timeline")
        assert resp.status_code == 404

    def test_error_dict_response_returns_404(self):
        """Orchestrator returning {'error': '...'} signals not-found."""
        app = _make_app()
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([{"error": "not found"}])
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/requests/req-gone/timeline")
        assert resp.status_code == 404

    def test_failure_event_anchored_to_last_status_check(self):
        """Failed request: failure event ts == last_status_check when present."""
        app = _make_app()
        req = self._make_req(
            status="failed",
            last_status_check="2026-01-01T01:00:00+00:00",
            completed_at=None,
        )
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([req])
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/requests/req-001/timeline").json()
        failure_events = [e for e in body["events"] if e["type"] == "failed"]
        assert len(failure_events) == 1
        assert failure_events[0]["ts"] == "2026-01-01T01:00:00+00:00"

    def test_partial_status_produces_partial_event(self):
        app = _make_app()
        req = self._make_req(
            status="partial",
            last_status_check="2026-01-01T01:00:00+00:00",
        )
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([req])
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/requests/req-001/timeline").json()
        event_types = {e["type"] for e in body["events"]}
        assert "partial" in event_types

    def test_failure_event_falls_back_to_created_at_when_no_other_timestamp(self):
        """Failure anchoring falls back to created_at when last_status_check and
        completed_at are both None."""
        app = _make_app()
        req = self._make_req(
            status="failed",
            last_status_check=None,
            completed_at=None,
            created_at="2026-01-01T00:00:00+00:00",
        )
        app.dependency_overrides[get_request_status_orchestrator] = lambda: (
            _make_request_orchestrator([req])
        )
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/requests/req-001/timeline").json()
        failure_events = [e for e in body["events"] if e["type"] == "failed"]
        assert len(failure_events) == 1
        assert failure_events[0]["ts"] == "2026-01-01T00:00:00+00:00"
