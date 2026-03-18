"""Integration tests for the async request/poll pattern.

Covers:
- POST /api/v1/machines/request returns 202 with a requestId immediately
- GET /api/v1/requests/{request_id}/status polls correctly
- GET /api/v1/requests/{request_id}/stream emits SSE events and closes on terminal state
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import orb.api.dependencies as deps
from orb.api.server import create_fastapi_app
from orb.config.schemas.server_schema import AuthConfig, ServerConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_acquire_result(request_id: str):
    """Build a minimal orchestrator result for acquire machines."""
    mock = MagicMock()
    mock.raw = {"requestId": request_id, "message": "Request VM success."}
    return mock


def _make_status_result(request_id: str, status: str):
    """Build a minimal orchestrator result for request status."""
    mock = MagicMock()
    mock.requests = [{"requestId": request_id, "status": status}]
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """FastAPI app with real routers. Tests install dependency_overrides per-test."""
    server_config = ServerConfig(enabled=True, auth=AuthConfig(enabled=False, strategy="none"))  # type: ignore[call-arg]
    return create_fastapi_app(server_config)


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def machines_orchestrator(app):
    """Install a mock acquire-machines orchestrator via dependency_overrides."""
    orchestrator = AsyncMock()
    app.dependency_overrides[deps.get_acquire_machines_orchestrator] = lambda: orchestrator
    yield orchestrator
    app.dependency_overrides.pop(deps.get_acquire_machines_orchestrator, None)


@pytest.fixture
def status_orchestrator(app):
    """Install a mock request-status orchestrator via dependency_overrides."""
    orchestrator = AsyncMock()
    app.dependency_overrides[deps.get_request_status_orchestrator] = lambda: orchestrator
    yield orchestrator
    app.dependency_overrides.pop(deps.get_request_status_orchestrator, None)


# ---------------------------------------------------------------------------
# POST /api/v1/machines/request — async acceptance
# ---------------------------------------------------------------------------


class TestRequestMachinesAsync:
    def test_returns_202(self, client, machines_orchestrator):
        """POST /machines/request must return 202 Accepted, not 200."""
        fake_request_id = "req-00000000-0000-0000-0000-000000000001"
        machines_orchestrator.execute.return_value = _make_acquire_result(fake_request_id)

        response = client.post(
            "/api/v1/machines/request",
            json={"templateId": "tmpl-1", "machineCount": 2},
        )

        assert response.status_code == 202

    def test_response_contains_request_id(self, client, machines_orchestrator):
        """Response body must include a requestId for subsequent polling."""
        fake_request_id = "req-00000000-0000-0000-0000-000000000002"
        machines_orchestrator.execute.return_value = _make_acquire_result(fake_request_id)

        response = client.post(
            "/api/v1/machines/request",
            json={"templateId": "tmpl-1", "machineCount": 1},
        )

        assert response.status_code == 202
        body = response.json()
        assert "requestId" in body
        assert body["requestId"] == fake_request_id

    def test_request_id_has_req_prefix(self, client, machines_orchestrator):
        """requestId in response must start with 'req-'."""
        fake_request_id = "req-00000000-0000-0000-0000-000000000003"
        machines_orchestrator.execute.return_value = _make_acquire_result(fake_request_id)

        response = client.post(
            "/api/v1/machines/request",
            json={"templateId": "tmpl-1", "machineCount": 3},
        )

        body = response.json()
        assert body["requestId"].startswith("req-")

    def test_missing_template_id_returns_error(self, client):
        """Missing templateId must be rejected before reaching the orchestrator."""
        response = client.post(
            "/api/v1/machines/request",
            json={"machineCount": 1},
        )
        assert response.status_code in (400, 422)

    def test_missing_machine_count_returns_error(self, client):
        """Missing machineCount must be rejected before reaching the orchestrator."""
        response = client.post(
            "/api/v1/machines/request",
            json={"templateId": "tmpl-1"},
        )
        assert response.status_code in (400, 422)


# ---------------------------------------------------------------------------
# GET /api/v1/requests/{request_id}/status — polling
# ---------------------------------------------------------------------------


class TestRequestStatusPolling:
    def test_poll_returns_200(self, client, status_orchestrator):
        """GET /requests/{id}/status must return 200."""
        request_id = "req-00000000-0000-0000-0000-000000000010"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "running")

        response = client.get(f"/api/v1/requests/{request_id}/status")

        assert response.status_code == 200

    def test_poll_response_contains_requests_list(self, client, status_orchestrator):
        """Status response must include a 'requests' list."""
        request_id = "req-00000000-0000-0000-0000-000000000011"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "running")

        response = client.get(f"/api/v1/requests/{request_id}/status")

        body = response.json()
        assert "requests" in body
        assert isinstance(body["requests"], list)

    def test_poll_reflects_status(self, client, status_orchestrator):
        """Status field in response must match what the orchestrator returns."""
        request_id = "req-00000000-0000-0000-0000-000000000012"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "complete")

        response = client.get(f"/api/v1/requests/{request_id}/status")

        body = response.json()
        assert body["requests"][0]["status"] == "complete"

    def test_poll_long_false(self, client, status_orchestrator):
        """long=false query param is forwarded to the orchestrator."""
        request_id = "req-00000000-0000-0000-0000-000000000013"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "running")

        response = client.get(f"/api/v1/requests/{request_id}/status?long=false")

        assert response.status_code == 200
        call_arg = status_orchestrator.execute.call_args[0][0]
        assert call_arg.detailed is False

    def test_poll_long_true_by_default(self, client, status_orchestrator):
        """long defaults to True."""
        request_id = "req-00000000-0000-0000-0000-000000000014"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "running")

        client.get(f"/api/v1/requests/{request_id}/status")

        call_arg = status_orchestrator.execute.call_args[0][0]
        assert call_arg.detailed is True


# ---------------------------------------------------------------------------
# GET /api/v1/requests/{request_id}/stream — SSE
# ---------------------------------------------------------------------------


class TestRequestStatusStream:
    def _collect_sse_events(self, raw: bytes) -> list[dict]:
        """Parse raw SSE bytes into a list of parsed data payloads."""
        events = []
        for line in raw.decode().splitlines():
            if line.startswith("data:"):
                data_str = line[len("data:") :].strip()
                if data_str and data_str != "{}":
                    try:
                        events.append(json.loads(data_str))
                    except json.JSONDecodeError:
                        pass
        return events

    def test_stream_returns_200_with_sse_content_type(self, client, status_orchestrator):
        """Stream endpoint must return 200 with text/event-stream content type."""
        request_id = "req-00000000-0000-0000-0000-000000000020"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "complete")

        response = client.get(f"/api/v1/requests/{request_id}/stream?interval=0.5&timeout=5")

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_stream_emits_at_least_one_data_event(self, client, status_orchestrator):
        """Stream must emit at least one data event before closing."""
        request_id = "req-00000000-0000-0000-0000-000000000021"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "complete")

        response = client.get(f"/api/v1/requests/{request_id}/stream?interval=0.5&timeout=5")

        events = self._collect_sse_events(response.content)
        assert len(events) >= 1

    def test_stream_closes_on_terminal_status(self, client, status_orchestrator):
        """Stream must close after emitting a terminal-status event."""
        request_id = "req-00000000-0000-0000-0000-000000000022"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "complete")

        response = client.get(f"/api/v1/requests/{request_id}/stream?interval=0.5&timeout=10")

        assert status_orchestrator.execute.call_count >= 1
        raw = response.content.decode()
        assert "done" in raw or "complete" in raw

    def test_stream_data_contains_requests_field(self, client, status_orchestrator):
        """Each SSE data event must contain a 'requests' list."""
        request_id = "req-00000000-0000-0000-0000-000000000023"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "complete")

        response = client.get(f"/api/v1/requests/{request_id}/stream?interval=0.5&timeout=5")

        events = self._collect_sse_events(response.content)
        assert len(events) >= 1
        assert "requests" in events[0]

    def test_stream_no_cache_header(self, client, status_orchestrator):
        """Stream response must include Cache-Control: no-cache."""
        request_id = "req-00000000-0000-0000-0000-000000000024"
        status_orchestrator.execute.return_value = _make_status_result(request_id, "complete")

        response = client.get(f"/api/v1/requests/{request_id}/stream?interval=0.5&timeout=5")

        assert "no-cache" in response.headers.get("cache-control", "")

    def test_stream_interval_bounds(self, client):
        """interval below 0.5 must be rejected with 422."""
        request_id = "req-00000000-0000-0000-0000-000000000025"
        response = client.get(f"/api/v1/requests/{request_id}/stream?interval=0.1&timeout=5")
        assert response.status_code == 422

    def test_stream_timeout_bounds(self, client):
        """timeout above 3600 must be rejected with 422."""
        request_id = "req-00000000-0000-0000-0000-000000000026"
        response = client.get(f"/api/v1/requests/{request_id}/stream?interval=2&timeout=9999")
        assert response.status_code == 422
