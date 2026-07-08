"""Tests for orb.ui.sse_client.stream_sse.

The SSE client has no reflex dependency — it only uses httpx.
We mock the httpx.AsyncClient at the class level so stream_sse
processes the lines we supply without a real network connection.

Test scenarios:
1. Nominal: well-formed SSE lines → correct (event_type, data) tuples
2. Heartbeat events are skipped
3. Comment lines (starting with ':') are skipped
4. Non-JSON data is returned as {"raw": "<value>"}
5. Multi-line data fields are joined before JSON parsing
6. A transport error triggers a backoff-and-retry (we verify the retry is
   attempted rather than testing the sleep duration)
7. A non-200 HTTP status triggers a backoff-and-retry
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers for building mock SSE streams
# ---------------------------------------------------------------------------


async def _lines_iter(lines: list[str]) -> AsyncIterator[str]:
    """Yield lines from a list — simulates resp.aiter_lines()."""
    for line in lines:
        yield line


def _make_sse_response(lines: list[str], status_code: int = 200) -> MagicMock:
    """Build a minimal mock of the context-managed httpx streaming response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.request = MagicMock()
    resp.aiter_lines = MagicMock(return_value=_lines_iter(lines))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_client(resp: MagicMock) -> MagicMock:
    """Build a mock httpx.AsyncClient whose stream() returns the given response."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.stream = MagicMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# Helpers for collecting N events from stream_sse before cancelling
# ---------------------------------------------------------------------------


async def _collect_n(url: str, n: int, client_mock: MagicMock) -> list[tuple[str, Any]]:
    """Consume exactly *n* events from stream_sse and return them."""
    from orb.ui.sse_client import stream_sse

    events: list[tuple[str, Any]] = []
    with patch("orb.ui.sse_client.httpx.AsyncClient", return_value=client_mock):
        async for evt, data in stream_sse(url):
            events.append((evt, data))
            if len(events) >= n:
                break
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_message_event_parsed():
    """A minimal SSE event emits the correct (event_type, data) pair."""
    payload = {"request_id": "req-1", "status": "complete"}
    lines = [
        "data: " + json.dumps(payload),
        "",  # empty line = event dispatch
    ]
    resp = _make_sse_response(lines)
    client = _make_client(resp)

    events = await _collect_n("http://localhost/events", 1, client)

    assert len(events) == 1
    event_type, data = events[0]
    assert event_type == "message"
    assert data["request_id"] == "req-1"
    assert data["status"] == "complete"


@pytest.mark.asyncio
async def test_named_event_type_is_preserved():
    """``event:`` field sets the event_type on the yielded tuple."""
    payload = {"machine_id": "i-abc"}
    lines = [
        "event: machine_status_changed",
        "data: " + json.dumps(payload),
        "",
    ]
    resp = _make_sse_response(lines)
    client = _make_client(resp)

    events = await _collect_n("http://localhost/events", 1, client)

    event_type, data = events[0]
    assert event_type == "machine_status_changed"
    assert data["machine_id"] == "i-abc"


@pytest.mark.asyncio
async def test_heartbeat_events_are_skipped():
    """Heartbeat events must not be yielded to the caller."""
    real_payload = {"kind": "update"}
    lines = [
        "event: heartbeat",
        "data: ping",
        "",  # heartbeat event — must be skipped
        "event: request_updated",
        "data: " + json.dumps(real_payload),
        "",  # real event — must be yielded
    ]
    resp = _make_sse_response(lines)
    client = _make_client(resp)

    events = await _collect_n("http://localhost/events", 1, client)

    assert len(events) == 1
    event_type, data = events[0]
    assert event_type == "request_updated"
    assert data["kind"] == "update"


@pytest.mark.asyncio
async def test_comment_lines_are_skipped():
    """Lines starting with ':' are SSE comments and must be ignored."""
    payload = {"x": 1}
    lines = [
        ": this is a comment",
        "data: " + json.dumps(payload),
        "",
    ]
    resp = _make_sse_response(lines)
    client = _make_client(resp)

    events = await _collect_n("http://localhost/events", 1, client)

    assert len(events) == 1
    _, data = events[0]
    assert data == payload


@pytest.mark.asyncio
async def test_non_json_data_wrapped_in_raw_key():
    """Non-JSON data is wrapped as ``{"raw": ...}`` rather than raising."""
    lines = [
        "data: this is not json",
        "",
    ]
    resp = _make_sse_response(lines)
    client = _make_client(resp)

    events = await _collect_n("http://localhost/events", 1, client)

    _, data = events[0]
    assert "raw" in data
    assert data["raw"] == "this is not json"


@pytest.mark.asyncio
async def test_multiline_data_fields_joined():
    """Multiple ``data:`` lines in one event are joined before JSON parsing."""
    # SSE spec: multiple data lines are joined with '\n'
    obj = {"a": 1, "b": 2}
    json_str = json.dumps(obj)
    # Split the JSON across two data lines
    half = len(json_str) // 2
    lines = [
        "data: " + json_str[:half],
        "data: " + json_str[half:],
        "",
    ]
    resp = _make_sse_response(lines)
    client = _make_client(resp)

    events = await _collect_n("http://localhost/events", 1, client)

    _, data = events[0]
    assert data == obj


@pytest.mark.asyncio
async def test_multiple_events_yielded_in_order():
    """Two successive events in the stream are yielded in order."""
    lines = [
        "event: created",
        "data: " + json.dumps({"n": 1}),
        "",
        "event: updated",
        "data: " + json.dumps({"n": 2}),
        "",
    ]
    resp = _make_sse_response(lines)
    client = _make_client(resp)

    events = await _collect_n("http://localhost/events", 2, client)

    assert events[0][0] == "created"
    assert events[0][1]["n"] == 1
    assert events[1][0] == "updated"
    assert events[1][1]["n"] == 2


@pytest.mark.asyncio
async def test_transport_error_triggers_retry():
    """On a transport error the generator reconnects (retries).

    We make the first AsyncClient raise ConnectError; the second returns a
    valid event.  Both clients are returned by a side_effect list.
    The sleep between retries is patched out so the test runs instantly.
    """
    from orb.ui.sse_client import stream_sse

    payload = {"k": "v"}
    good_resp = _make_sse_response(["data: " + json.dumps(payload), ""])
    good_client = _make_client(good_resp)

    # First client raises on __aenter__
    bad_client = MagicMock()
    bad_client.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("refused"))
    bad_client.__aexit__ = AsyncMock(return_value=False)

    call_count = 0

    def _client_factory(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return bad_client
        return good_client

    events: list[tuple[str, Any]] = []

    with (
        patch("orb.ui.sse_client.httpx.AsyncClient", side_effect=_client_factory),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        async for evt, data in stream_sse("http://localhost/events"):
            events.append((evt, data))
            if events:
                break

    assert call_count >= 2, "Expected at least one retry after transport error"
    assert len(events) == 1
    _, data = events[0]
    assert data["k"] == "v"


@pytest.mark.asyncio
async def test_non_200_status_triggers_retry():
    """A non-200 HTTP status causes the client to back off and retry.

    The first response returns 503; the second returns 200 with a valid event.
    """
    from orb.ui.sse_client import stream_sse

    payload = {"status": "recovered"}
    good_resp = _make_sse_response(["data: " + json.dumps(payload), ""])
    good_client = _make_client(good_resp)

    # First response: 503 (triggers HTTPStatusError inside stream_sse)
    bad_resp = _make_sse_response([], status_code=503)
    bad_client = _make_client(bad_resp)

    call_count = 0

    def _client_factory(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return bad_client
        return good_client

    events: list[tuple[str, Any]] = []

    with (
        patch("orb.ui.sse_client.httpx.AsyncClient", side_effect=_client_factory),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        async for evt, data in stream_sse("http://localhost/events"):
            events.append((evt, data))
            break

    assert call_count >= 2
    assert events[0][1]["status"] == "recovered"
