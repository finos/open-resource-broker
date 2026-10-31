"""Tests for the SSE stream endpoint on the requests router."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import get_request_status_orchestrator, get_response_formatting_service
from orb.api.routers.requests import router as requests_router
from orb.application.dto.interface_response import InterfaceResponse
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


def _make_formatter():
    """Return a mock formatter that passes through the requests list."""
    formatter = MagicMock()
    formatter.format_request_status.side_effect = lambda reqs: InterfaceResponse(
        data={"requests": reqs}
    )
    return formatter


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
        from orb.api.dependencies import get_request_formatter

        app.dependency_overrides[get_request_status_orchestrator] = lambda: orchestrator
        # Router uses get_request_formatter (header-aware); also override
        # the non-header variant for safety.
        app.dependency_overrides[get_request_formatter] = _make_formatter
        app.dependency_overrides[get_response_formatting_service] = _make_formatter
        return TestClient(app, raise_server_exceptions=False)

    def test_happy_path_sse_data_lines_format(self, requests_app):
        """SSE lines are prefixed with 'data: ' and contain valid JSON."""
        orchestrator = _make_orchestrator_returning("running", "complete")
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
        """Stream closes after receiving COMPLETE terminal status.

        The domain enum uses "complete" (not "completed") as the wire value;
        the router now delegates to RequestStatus.is_terminal() so only valid
        enum values close the stream.
        """
        orchestrator = _make_orchestrator_returning("running", "complete")
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=30") as resp:
            events = _collect_sse_lines(resp)

        statuses = [e["requests"][0]["status"] for e in events if e.get("requests")]
        assert "complete" in statuses
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
        # The error is announced by a named frame, then the stream ends with
        # the empty sentinel that SDK readers treat as end-of-stream.
        assert "event: error" in raw_lines
        assert data_lines == ['data: {"error": "internal"}', "data: {}"]
        assert "provider unavailable" not in "".join(raw_lines)
        # Orchestrator called exactly once before the error
        orchestrator.execute.assert_awaited_once()

    def test_terminal_status_is_announced_then_sentinel_is_last(self, requests_app):
        orchestrator = _make_orchestrator_returning("running", "failed")
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=30") as resp:
            raw_lines = [l if isinstance(l, str) else l.decode() for l in resp.iter_lines()]

        non_blank = [l for l in raw_lines if l]
        assert non_blank[-3:] == ["event: complete", 'data: {"status": "failed"}', "data: {}"]
        assert "event: error" not in raw_lines

    def test_named_frames_carry_no_requests_list(self, requests_app):
        orchestrator = _make_orchestrator_returning("running", "complete")
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=30") as resp:
            raw_lines = [l if isinstance(l, str) else l.decode() for l in resp.iter_lines()]

        for i, line in enumerate(raw_lines):
            if line.startswith("event: "):
                assert "requests" not in json.loads(raw_lines[i + 1][len("data: ") :])

    def test_timeout_is_announced_then_sentinel_is_last(self, requests_app):
        orchestrator = MagicMock()
        output = GetRequestStatusOutput(
            requests=[{"request_id": "req-stream-1", "status": "running"}]
        )
        orchestrator.execute = AsyncMock(return_value=output)
        client = self._make_client(requests_app, orchestrator)

        with client.stream("GET", "/requests/req-stream-1/stream?interval=0.5&timeout=1") as resp:
            raw_lines = [l if isinstance(l, str) else l.decode() for l in resp.iter_lines()]

        non_blank = [l for l in raw_lines if l]
        assert non_blank[-3:] == ["event: timeout", 'data: {"status": "timeout"}', "data: {}"]

    def _stream_lines(self, app, entry):
        orchestrator = MagicMock()
        orchestrator.execute = AsyncMock(return_value=GetRequestStatusOutput(requests=[entry]))
        client = self._make_client(app, orchestrator)
        with client.stream("GET", "/requests/req-x/stream?interval=0.5&timeout=30") as resp:
            assert resp.status_code == 200
            lines = [l if isinstance(l, str) else l.decode() for l in resp.iter_lines()]
        return lines, orchestrator

    def test_unknown_request_ends_promptly_with_not_found_error(self, requests_app):
        lines, orchestrator = self._stream_lines(
            requests_app,
            {"request_id": "req-x", "not_found": True, "error": "Request req-x not found"},
        )
        non_blank = [l for l in lines if l]
        assert non_blank == ["event: error", 'data: {"error": "not_found"}', "data: {}"]
        orchestrator.execute.assert_awaited_once()  # no polling until the timeout

    def _stream_sequence(self, app, outputs, query="interval=0.5&timeout=30"):
        orchestrator = MagicMock()
        orchestrator.execute = AsyncMock(side_effect=outputs)
        client = self._make_client(app, orchestrator)
        with client.stream("GET", f"/requests/req-x/stream?{query}") as resp:
            assert resp.status_code == 200
            lines = [l if isinstance(l, str) else l.decode() for l in resp.iter_lines()]
        return lines, orchestrator

    @staticmethod
    def _blip(detail="1 validation error for RequestId"):
        return GetRequestStatusOutput(requests=[{"request_id": "req-x", "error": detail}])

    @staticmethod
    def _status(status):
        return GetRequestStatusOutput(requests=[{"request_id": "req-x", "status": status}])

    def test_single_lookup_failure_does_not_end_the_stream(self, requests_app):
        lines, orchestrator = self._stream_sequence(
            requests_app, [self._blip(), self._status("running"), self._status("complete")]
        )
        non_blank = [l for l in lines if l]
        assert "event: error" not in non_blank
        assert non_blank[-3:] == ["event: complete", 'data: {"status": "complete"}', "data: {}"]
        assert orchestrator.execute.await_count == 3
        assert "validation" not in "".join(lines)

    def test_three_consecutive_failures_end_with_generic_error(self, requests_app):
        lines, orchestrator = self._stream_sequence(
            requests_app, [self._blip(), self._blip(), self._blip(), self._status("complete")]
        )
        non_blank = [l for l in lines if l]
        assert non_blank == ["event: error", 'data: {"error": "unavailable"}', "data: {}"]
        assert orchestrator.execute.await_count == 3
        assert "validation" not in "".join(lines)

    def test_a_successful_poll_resets_the_failure_count(self, requests_app):
        outputs = [
            self._blip(),
            self._blip(),
            self._status("running"),
            self._blip(),
            self._blip(),
            self._status("complete"),
        ]
        lines, orchestrator = self._stream_sequence(requests_app, outputs)
        non_blank = [l for l in lines if l]
        assert "event: error" not in non_blank
        assert non_blank[-1] == "data: {}"
        assert orchestrator.execute.await_count == 6

    def test_failures_at_the_deadline_end_with_error_not_timeout(self, requests_app):
        lines, _ = self._stream_sequence(
            requests_app,
            [self._status("running"), self._blip(), self._blip()],
            query="interval=0.5&timeout=1.5",
        )
        non_blank = [l for l in lines if l]
        assert non_blank[-3:] == ["event: error", 'data: {"error": "unavailable"}', "data: {}"]
