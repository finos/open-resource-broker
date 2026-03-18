"""Tests for the SSE stream endpoint on the requests router."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import get_request_status_orchestrator, get_scheduler_strategy
from orb.api.routers.requests import router as requests_router
from orb.application.services.orchestration.dtos import GetRequestStatusOutput


@pytest.fixture()
def requests_app():
    app = FastAPI()
    app.include_router(requests_router)
    return app


def _make_orchestrator_returning(*statuses):
    """Return an orchestrator whose execute() yields successive status dicts."""
    orchestrator = MagicMock()
    side_effects = []
    for status in statuses:
        output = GetRequestStatusOutput(requests=[{"request_id": "req-stream-1", "status": status}])
        side_effects.append(output)
    orchestrator.execute = AsyncMock(side_effect=side_effects)
    return orchestrator


def _make_scheduler():
    """Return a mock scheduler that passes through the requests list."""
    scheduler = MagicMock()
    scheduler.format_request_status_response.side_effect = lambda reqs: {"requests": reqs}
    return scheduler


def _collect_sse_lines(response) -> list[dict]:
    """Parse SSE data lines from a streaming response into a list of dicts."""
    parsed = []
    for raw in response.iter_lines():
        line = raw if isinstance(raw, str) else raw.decode()
        if line.startswith("data: "):
            payload = line[len("data: ") :]
            try:
                obj = json.loads(payload)
                if obj:
                    parsed.append(obj)
            except json.JSONDecodeError:
                pass  # Malformed SSE lines are intentionally skipped
    return parsed


@pytest.mark.unit
@pytest.mark.api
class TestStreamEndpoint:
    """Tests for GET /{request_id}/stream SSE endpoint."""

    def _make_client(self, app, orchestrator):
        app.dependency_overrides[get_request_status_orchestrator] = lambda: orchestrator
        app.dependency_overrides[get_scheduler_strategy] = _make_scheduler
        return TestClient(app, raise_server_exceptions=False)

    def test_happy_path_sse_data_lines_format(self, requests_app):
        """SSE lines are prefixed with 'data: ' and contain valid JSON."""
        orchestrator = _make_orchestrator_returning("running", "completed")
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=30") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            lines = list(resp.iter_lines())

        data_lines = [
            l if isinstance(l, str) else l.decode()
            for l in lines
            if (l if isinstance(l, str) else l.decode()).startswith("data: ")
        ]
        assert len(data_lines) >= 1
        for line in data_lines:
            payload = line[len("data: ") :]
            # Must be valid JSON
            json.loads(payload)

    def test_stream_ends_on_completed_status(self, requests_app):
        """Stream closes after receiving COMPLETED terminal status."""
        orchestrator = _make_orchestrator_returning("running", "completed")
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=30") as resp:
            events = _collect_sse_lines(resp)

        statuses = [e["requests"][0]["status"] for e in events if e.get("requests")]
        assert "completed" in statuses
        # Orchestrator should not be called more times than needed
        assert orchestrator.execute.await_count <= 3

    def test_stream_ends_on_failed_status(self, requests_app):
        """Stream closes after receiving FAILED terminal status."""
        orchestrator = _make_orchestrator_returning("running", "failed")
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=30") as resp:
            events = _collect_sse_lines(resp)

        statuses = [e["requests"][0]["status"] for e in events if e.get("requests")]
        assert "failed" in statuses

    def test_stream_ends_on_cancelled_status(self, requests_app):
        """Stream closes after receiving CANCELLED terminal status."""
        orchestrator = _make_orchestrator_returning("pending", "cancelled")
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=30") as resp:
            events = _collect_sse_lines(resp)

        statuses = [e["requests"][0]["status"] for e in events if e.get("requests")]
        assert "cancelled" in statuses

    def test_stream_timeout_expiry(self, requests_app):
        """Stream closes after timeout even if no terminal status is reached."""
        # Always return non-terminal status
        orchestrator = MagicMock()
        output = GetRequestStatusOutput(
            requests=[{"request_id": "req-stream-1", "status": "running"}]
        )
        orchestrator.execute = AsyncMock(return_value=output)
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=1") as resp:
            assert resp.status_code == 200
            events = _collect_sse_lines(resp)

        # Stream must have closed; at least one event emitted before timeout
        assert len(events) >= 1
        # Orchestrator called a bounded number of times (timeout / interval)
        assert orchestrator.execute.await_count < 20

    def test_stream_error_mid_stream_closes_cleanly(self, requests_app):
        """When orchestrator raises mid-stream, stream sends empty sentinel and closes."""
        orchestrator = MagicMock()
        orchestrator.execute = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=30") as resp:
            assert resp.status_code == 200
            raw_lines = [l if isinstance(l, str) else l.decode() for l in resp.iter_lines()]

        data_lines = [l for l in raw_lines if l.startswith("data: ")]
        # The error path yields exactly one sentinel "data: {}\n\n" then returns
        assert len(data_lines) == 1
        assert data_lines[0] == "data: {}"
        # Orchestrator called exactly once before the error
        orchestrator.execute.assert_awaited_once()
