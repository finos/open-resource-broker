"""Tests for orb.ui.api_http — pure httpx-based REST client.

No reflex dependency. We patch httpx at the transport level using
``respx`` if available, or via ``unittest.mock.patch`` on the httpx
AsyncClient.  We verify that:

- happy-path responses are parsed and returned as dicts
- 4xx/5xx HTTP errors are raised (not swallowed)
- network-level errors propagate to the caller
- ``get_me`` degrades gracefully on 404 → returns the default admin payload
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int, json_body: Any | None = None, *, content: bytes = b""
) -> MagicMock:
    """Build a minimal httpx.Response-like mock."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = content if content else (b"x" if json_body is not None else b"")

    if json_body is not None:
        resp.json = MagicMock(return_value=json_body)
    else:
        resp.json = MagicMock(side_effect=ValueError("no json"))

    # raise_for_status: 2xx → no-op, else raise HTTPStatusError
    if 200 <= status_code < 300:
        resp.raise_for_status = MagicMock(return_value=None)
    else:
        req = MagicMock()
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(f"HTTP {status_code}", request=req, response=resp)
        )
    return resp


def _make_client_ctx(response: MagicMock) -> MagicMock:
    """Return a MagicMock that acts as both the AsyncClient and its __aenter__ result."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.put = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_http():
    """Import the api_http module fresh for each test."""
    # api_http has no reflex dependency so we can import directly.
    import orb.ui.api_http as mod

    return mod


# ---------------------------------------------------------------------------
# get_health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_health_returns_dict(api_http):
    """get_health() parses and returns the JSON body."""
    payload = {"status": "ok", "checks": {}}
    resp = _mock_response(200, payload)
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        result = await api_http.get_health()

    assert result == payload


@pytest.mark.asyncio
async def test_get_health_raises_on_server_error(api_http):
    """get_health() does NOT swallow HTTP errors — callers must handle them."""
    resp = _mock_response(500, {"detail": "internal server error"})
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await api_http.get_health()


# ---------------------------------------------------------------------------
# get_me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_me_returns_user_on_200(api_http):
    """get_me() returns the server's user payload on a 200 response."""
    payload = {
        "username": "alice",
        "role": "operator",
        "permissions": ["request_machines"],
    }
    resp = _mock_response(200, payload)
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        result = await api_http.get_me()

    assert result["username"] == "alice"
    assert result["role"] == "operator"


@pytest.mark.asyncio
async def test_get_me_degrades_gracefully_on_404(api_http):
    """get_me() falls back to the default admin payload when the endpoint is absent (404)."""
    resp = _mock_response(404)
    resp.content = b""
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        result = await api_http.get_me()

    assert result["role"] == "admin"
    assert "request_machines" in result["permissions"]
    assert "return_machines" in result["permissions"]


@pytest.mark.asyncio
async def test_get_me_raises_on_403(api_http):
    """get_me() propagates non-404 HTTP errors to the caller."""
    resp = _mock_response(403, {"detail": "forbidden"})
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await api_http.get_me()

    assert exc_info.value.response.status_code == 403


# ---------------------------------------------------------------------------
# list_machines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_machines_passes_status_filter(api_http):
    """list_machines() includes ``status`` query param when provided."""
    payload = {"machines": [], "total": 0}
    resp = _mock_response(200, payload)
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        result = await api_http.list_machines(status="running")

    assert result == payload
    # Verify the status param was forwarded to httpx.get
    call_kwargs = client.get.call_args
    assert call_kwargs is not None
    # params is passed as a keyword argument: c.get(url, params={...})
    params = call_kwargs.kwargs.get("params") or {}
    assert params.get("status") == "running"


@pytest.mark.asyncio
async def test_list_machines_raises_on_4xx(api_http):
    """list_machines() raises HTTPStatusError on a 422 response."""
    resp = _mock_response(422, {"detail": "validation error"})
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await api_http.list_machines()


# ---------------------------------------------------------------------------
# list_templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_templates_returns_payload(api_http):
    """list_templates() returns the full response dict including the list."""
    tmpl = {"template_id": "t-123", "name": "my-template", "provider_api": "aws"}
    payload = {"templates": [tmpl], "total": 1}
    resp = _mock_response(200, payload)
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        result = await api_http.list_templates()

    assert result["templates"][0]["template_id"] == "t-123"


@pytest.mark.asyncio
async def test_list_templates_raises_on_error(api_http):
    """list_templates() does not swallow HTTP errors."""
    resp = _mock_response(503)
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await api_http.list_templates()


# ---------------------------------------------------------------------------
# list_requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requests_raises_on_500(api_http):
    """list_requests() propagates server errors — callers own error state."""
    resp = _mock_response(500)
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await api_http.list_requests()


# ---------------------------------------------------------------------------
# wipe_database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wipe_database_raises_on_403(api_http):
    """wipe_database() raises when the server rejects the request (env guard)."""
    resp = _mock_response(403, {"detail": "not allowed in production"})
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await api_http.wipe_database()

    assert exc_info.value.response.status_code == 403


@pytest.mark.asyncio
async def test_wipe_database_success(api_http):
    """wipe_database() returns the result dict on success."""
    payload = {"rows_deleted": 42, "tables_truncated": ["machines", "requests"]}
    resp = _mock_response(200, payload)
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        result = await api_http.wipe_database()

    assert result["rows_deleted"] == 42


# ---------------------------------------------------------------------------
# get_dashboard_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dashboard_summary_raises_on_404(api_http):
    """get_dashboard_summary() raises on 404 — endpoint may not be registered."""
    resp = _mock_response(404)
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await api_http.get_dashboard_summary()


@pytest.mark.asyncio
async def test_get_dashboard_summary_returns_data(api_http):
    """get_dashboard_summary() returns the full response on success."""
    payload = {
        "machines": {"total": 3, "by_status": {"running": 3}},
        "requests": {"total": 1, "in_flight": 0, "by_status": {"complete": 1}},
        "templates": {"total": 2, "by_provider_api": {"aws": 2}},
        "recent_activity": [],
    }
    resp = _mock_response(200, payload)
    client = _make_client_ctx(resp)

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        result = await api_http.get_dashboard_summary()

    assert result["machines"]["total"] == 3
    assert result["templates"]["total"] == 2


# ---------------------------------------------------------------------------
# Network-level error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_health_propagates_connect_error(api_http):
    """get_health() lets ConnectError propagate — it does not swallow transport errors."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch.object(api_http.httpx, "AsyncClient", return_value=client):
        with pytest.raises(httpx.ConnectError):
            await api_http.get_health()
