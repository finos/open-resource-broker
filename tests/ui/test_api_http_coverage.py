"""Coverage tests for orb.ui.api_http — the httpx-based REST client.

Complements test_api_client.py / test_api_client_auth.py by exercising the
remaining endpoint wrappers, the verb helpers (_get/_post/_put/_delete),
query-param assembly (cursor vs offset, optional filters), URL-prefix
construction, and the graceful-degradation branches.

We never boot a Reflex server or hit the network: httpx.AsyncClient is
patched with a MagicMock context manager that records the verb calls, and
we assert on the URL / params / json that the client would have sent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers (mirrors test_api_client.py)
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int, json_body: Any | None = None, *, content: bytes = b""
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = content if content else (b"x" if json_body is not None else b"")
    if json_body is not None:
        resp.json = MagicMock(return_value=json_body)
    else:
        resp.json = MagicMock(side_effect=ValueError("no json"))
    if 200 <= status_code < 300:
        resp.raise_for_status = MagicMock(return_value=None)
    else:
        req = MagicMock()
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(f"HTTP {status_code}", request=req, response=resp)
        )
    return resp


def _make_client_ctx(response: MagicMock) -> MagicMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.put = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=response)
    return client


@pytest.fixture()
def api_http():
    import orb.ui.api_http as mod

    return mod


def _get_url(client: MagicMock) -> str:
    """Positional URL argument passed to the recorded GET call."""
    return client.get.call_args.args[0]


def _get_params(client: MagicMock) -> dict[str, Any]:
    return client.get.call_args.kwargs.get("params") or {}


def _post_url(client: MagicMock) -> str:
    return client.post.call_args.args[0]


def _post_json(client: MagicMock) -> Any:
    return client.post.call_args.kwargs.get("json")


def _put_json(client: MagicMock) -> Any:
    return client.put.call_args.kwargs.get("json")


# ---------------------------------------------------------------------------
# _client — base configuration
# ---------------------------------------------------------------------------


class TestClientConfig:
    def test_client_uses_base_url_timeout_and_headers(self, api_http):
        """_client() constructs an AsyncClient with base_url, TIMEOUT and _headers()."""
        with patch.object(api_http.httpx, "AsyncClient") as ctor:
            with patch.object(api_http, "_loopback_token", return_value=None):
                api_http._client()

        kwargs = ctor.call_args.kwargs
        assert kwargs["base_url"] == api_http.ORB_BASE_URL
        assert kwargs["timeout"] is api_http.TIMEOUT
        assert kwargs["headers"]["X-ORB-Scheduler"] == "default"


# ---------------------------------------------------------------------------
# Verb helpers
# ---------------------------------------------------------------------------


class TestVerbHelpers:
    @pytest.mark.asyncio
    async def test_get_builds_api_prefixed_url(self, api_http):
        resp = _mock_response(200, {"ok": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http._get("/thing", params={"a": 1})
        assert result == {"ok": True}
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/thing"
        assert _get_params(client) == {"a": 1}

    @pytest.mark.asyncio
    async def test_post_forwards_json_body(self, api_http):
        resp = _mock_response(200, {"created": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http._post("/thing", json={"name": "x"})
        assert result == {"created": True}
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/thing"
        assert _post_json(client) == {"name": "x"}

    @pytest.mark.asyncio
    async def test_put_forwards_json_body(self, api_http):
        resp = _mock_response(200, {"updated": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http._put("/thing/1", json={"v": 2})
        assert result == {"updated": True}
        assert client.put.call_args.args[0] == f"{api_http.ORB_API_PREFIX}/thing/1"
        assert _put_json(client) == {"v": 2}

    @pytest.mark.asyncio
    async def test_delete_returns_json_when_content_present(self, api_http):
        resp = _mock_response(200, {"deleted": True}, content=b'{"deleted":true}')
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http._delete("/thing/1")
        assert result == {"deleted": True}

    @pytest.mark.asyncio
    async def test_delete_returns_empty_dict_on_no_content(self, api_http):
        """204-style empty body → {} rather than a json() decode error."""
        resp = _mock_response(204)
        resp.content = b""
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http._delete("/thing/1")
        assert result == {}
        resp.json.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_raises_on_error_status(self, api_http):
        resp = _mock_response(500)
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            with pytest.raises(httpx.HTTPStatusError):
                await api_http._get("/thing")


# ---------------------------------------------------------------------------
# Top-level (root-prefixed) endpoints
# ---------------------------------------------------------------------------


class TestRootEndpoints:
    @pytest.mark.asyncio
    async def test_get_info_uses_root_prefix(self, api_http):
        resp = _mock_response(200, {"version": "9.9"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_info()
        assert result == {"version": "9.9"}
        assert _get_url(client) == f"{api_http.ORB_ROOT_PREFIX}/info"

    @pytest.mark.asyncio
    async def test_get_health_uses_root_prefix(self, api_http):
        resp = _mock_response(200, {"status": "ok"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.get_health()
        assert _get_url(client) == f"{api_http.ORB_ROOT_PREFIX}/health"

    @pytest.mark.asyncio
    async def test_get_me_uses_root_api_v1_path(self, api_http):
        resp = _mock_response(200, {"username": "bob", "role": "admin", "permissions": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.get_me()
        assert _get_url(client) == f"{api_http.ORB_ROOT_PREFIX}/api/v1/me"


# ---------------------------------------------------------------------------
# list_machines — param assembly
# ---------------------------------------------------------------------------


class TestListMachinesParams:
    @pytest.mark.asyncio
    async def test_cursor_takes_precedence_over_offset(self, api_http):
        resp = _mock_response(200, {"machines": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_machines(cursor="abc", offset=20, limit=10)
        params = _get_params(client)
        assert params["cursor"] == "abc"
        assert "offset" not in params
        assert params["limit"] == 10

    @pytest.mark.asyncio
    async def test_offset_used_when_no_cursor(self, api_http):
        resp = _mock_response(200, {"machines": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_machines(offset=30)
        params = _get_params(client)
        assert params["offset"] == 30
        assert "cursor" not in params

    @pytest.mark.asyncio
    async def test_all_optional_filters_forwarded(self, api_http):
        resp = _mock_response(200, {"machines": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_machines(
                status="running", provider_name="aws", q="web", sort="name"
            )
        params = _get_params(client)
        assert params["status"] == "running"
        assert params["provider_name"] == "aws"
        assert params["q"] == "web"
        assert params["sort"] == "name"

    @pytest.mark.asyncio
    async def test_no_offset_no_cursor_only_limit(self, api_http):
        """offset=0 (default) and no cursor → neither key set, only limit."""
        resp = _mock_response(200, {"machines": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_machines()
        params = _get_params(client)
        assert params == {"limit": 50}


# ---------------------------------------------------------------------------
# Single-machine endpoints
# ---------------------------------------------------------------------------


class TestMachineEndpoints:
    @pytest.mark.asyncio
    async def test_get_machine_builds_path(self, api_http):
        resp = _mock_response(200, {"machine_id": "m-1"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_machine("m-1")
        assert result == {"machine_id": "m-1"}
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/machines/m-1"

    @pytest.mark.asyncio
    async def test_sync_machine_hits_status_subpath(self, api_http):
        resp = _mock_response(200, {"machine_id": "m-1", "synced": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.sync_machine("m-1")
        assert result["synced"] is True
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/machines/m-1/status"

    @pytest.mark.asyncio
    async def test_request_machines_posts_body(self, api_http):
        body = {"template_id": "t-1", "count": 2}
        resp = _mock_response(200, {"request_id": "r-1"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.request_machines(body)
        assert result == {"request_id": "r-1"}
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/machines/request"
        assert _post_json(client) == body

    @pytest.mark.asyncio
    async def test_return_machines_posts_body(self, api_http):
        body = {"machine_ids": ["m-1", "m-2"]}
        resp = _mock_response(200, {"request_id": "r-2"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.return_machines(body)
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/machines/return"
        assert _post_json(client) == body


# ---------------------------------------------------------------------------
# Requests endpoints
# ---------------------------------------------------------------------------


class TestRequestEndpoints:
    @pytest.mark.asyncio
    async def test_list_requests_cursor_precedence(self, api_http):
        resp = _mock_response(200, {"requests": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_requests(
                cursor="cur", offset=5, status="pending", q="x", sort="-created"
            )
        params = _get_params(client)
        assert params["cursor"] == "cur"
        assert "offset" not in params
        assert params["status"] == "pending"
        assert params["q"] == "x"
        assert params["sort"] == "-created"

    @pytest.mark.asyncio
    async def test_list_requests_offset_branch(self, api_http):
        resp = _mock_response(200, {"requests": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_requests(offset=15)
        assert _get_params(client)["offset"] == 15

    @pytest.mark.asyncio
    async def test_get_request_uses_status_subpath(self, api_http):
        resp = _mock_response(200, {"request_id": "r-1"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.get_request("r-1")
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/requests/r-1/status"

    @pytest.mark.asyncio
    async def test_batch_get_request_status_posts_ids_and_verbose(self, api_http):
        resp = _mock_response(200, {"results": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.batch_get_request_status(["r-1", "r-2"], verbose=False)
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/requests/status"
        assert _post_json(client) == {"request_ids": ["r-1", "r-2"], "verbose": False}

    @pytest.mark.asyncio
    async def test_batch_get_request_status_default_verbose_true(self, api_http):
        resp = _mock_response(200, {"results": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.batch_get_request_status(["r-1"])
        assert _post_json(client)["verbose"] is True

    @pytest.mark.asyncio
    async def test_list_return_requests_cursor(self, api_http):
        resp = _mock_response(200, {"requests": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_return_requests(cursor="c1", status="pending", q="q", sort="s")
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/requests/return"
        params = _get_params(client)
        assert params["cursor"] == "c1"
        assert params["status"] == "pending"

    @pytest.mark.asyncio
    async def test_list_return_requests_offset(self, api_http):
        resp = _mock_response(200, {"requests": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_return_requests(offset=7)
        assert _get_params(client)["offset"] == 7

    @pytest.mark.asyncio
    async def test_cancel_request_uses_delete(self, api_http):
        resp = _mock_response(200, {"cancelled": True}, content=b"{}")
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.cancel_request("r-9")
        assert client.delete.call_args.args[0] == f"{api_http.ORB_API_PREFIX}/requests/r-9"


# ---------------------------------------------------------------------------
# Template endpoints
# ---------------------------------------------------------------------------


class TestTemplateEndpoints:
    @pytest.mark.asyncio
    async def test_list_templates_cursor_and_filters(self, api_http):
        resp = _mock_response(200, {"templates": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_templates(
                provider_api="aws", q="web", sort="name", cursor="c", offset=99
            )
        params = _get_params(client)
        assert params["provider_api"] == "aws"
        assert params["cursor"] == "c"
        assert "offset" not in params
        assert params["q"] == "web"
        assert params["sort"] == "name"

    @pytest.mark.asyncio
    async def test_list_templates_offset_branch(self, api_http):
        resp = _mock_response(200, {"templates": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.list_templates(offset=3)
        assert _get_params(client)["offset"] == 3

    @pytest.mark.asyncio
    async def test_get_template_path(self, api_http):
        resp = _mock_response(200, {"template_id": "t-1"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.get_template("t-1")
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/templates/t-1"

    @pytest.mark.asyncio
    async def test_create_template_posts_body(self, api_http):
        body = {"name": "new"}
        resp = _mock_response(200, {"template_id": "t-new"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.create_template(body)
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/templates/"
        assert _post_json(client) == body

    @pytest.mark.asyncio
    async def test_update_template_puts_body(self, api_http):
        body = {"name": "upd"}
        resp = _mock_response(200, {"template_id": "t-1"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.update_template("t-1", body)
        assert client.put.call_args.args[0] == f"{api_http.ORB_API_PREFIX}/templates/t-1"
        assert _put_json(client) == body

    @pytest.mark.asyncio
    async def test_delete_template_uses_delete(self, api_http):
        resp = _mock_response(200, {"deleted": True}, content=b"{}")
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.delete_template("t-1")
        assert client.delete.call_args.args[0] == f"{api_http.ORB_API_PREFIX}/templates/t-1"

    @pytest.mark.asyncio
    async def test_validate_template_posts_to_validate(self, api_http):
        body = {"name": "x"}
        resp = _mock_response(200, {"valid": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.validate_template(body)
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/templates/validate"
        assert _post_json(client) == body

    @pytest.mark.asyncio
    async def test_refresh_templates_posts_no_body(self, api_http):
        resp = _mock_response(200, {"refreshed": 3})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.refresh_templates()
        assert result == {"refreshed": 3}
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/templates/refresh"

    @pytest.mark.asyncio
    async def test_generate_templates_defaults_to_all_providers(self, api_http):
        resp = _mock_response(200, {"generated": 5})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.generate_templates()
        assert _post_json(client) == {"all_providers": True}

    @pytest.mark.asyncio
    async def test_generate_templates_uses_supplied_body(self, api_http):
        body = {"provider_name": "aws", "force": True}
        resp = _mock_response(200, {"generated": 1})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.generate_templates(body)
        assert _post_json(client) == body


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


class TestAdminEndpoints:
    @pytest.mark.asyncio
    async def test_wipe_database_sends_confirm_token(self, api_http):
        resp = _mock_response(200, {"rows_deleted": 1})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.wipe_database()
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/admin/database/wipe"
        assert _post_json(client) == {"confirm": "WIPE"}

    @pytest.mark.asyncio
    async def test_init_orb_merges_confirm_token_with_body(self, api_http):
        resp = _mock_response(200, {"initialized": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.init_orb({"provider": "aws"})
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/admin/init"
        assert _post_json(client) == {"confirm": "INIT", "provider": "aws"}

    @pytest.mark.asyncio
    async def test_init_orb_body_cannot_override_confirm(self, api_http):
        """The mandatory INIT confirmation token must stay authoritative even
        when the caller supplies a conflicting ``confirm`` in the body."""
        resp = _mock_response(200, {"initialized": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.init_orb({"confirm": "OVERRIDE"})
        assert _post_json(client)["confirm"] == "INIT"


# ---------------------------------------------------------------------------
# Dashboard / config endpoints
# ---------------------------------------------------------------------------


class TestConfigEndpoints:
    @pytest.mark.asyncio
    async def test_get_dashboard_summary_path(self, api_http):
        resp = _mock_response(200, {"machines": {"total": 0}})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.get_dashboard_summary()
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/system/dashboard"

    @pytest.mark.asyncio
    async def test_get_config_no_source_passes_none_params(self, api_http):
        resp = _mock_response(200, {"config": {}})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.get_config()
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/config/"
        assert _get_params(client) == {}

    @pytest.mark.asyncio
    async def test_get_config_with_source_sets_param(self, api_http):
        resp = _mock_response(200, {"config": {}})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.get_config(source="file")
        assert _get_params(client) == {"source": "file"}

    @pytest.mark.asyncio
    async def test_get_config_value_extracts_value_key(self, api_http):
        resp = _mock_response(200, {"value": 42, "key": "a.b"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_config_value("a.b")
        assert result == 42
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/config/a.b"

    @pytest.mark.asyncio
    async def test_get_config_value_missing_value_returns_none(self, api_http):
        resp = _mock_response(200, {"key": "a.b"})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_config_value("a.b")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_config_value_puts_value_body(self, api_http):
        resp = _mock_response(200, {"value": "on", "persisted": False})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.set_config_value("feature.flag", "on")
        assert client.put.call_args.args[0] == f"{api_http.ORB_API_PREFIX}/config/feature.flag"
        assert _put_json(client) == {"value": "on"}

    @pytest.mark.asyncio
    async def test_reload_config_posts_to_admin(self, api_http):
        resp = _mock_response(200, {"reloaded": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.reload_config()
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/admin/reload-config"

    @pytest.mark.asyncio
    async def test_save_config_without_path_sends_empty_body(self, api_http):
        resp = _mock_response(200, {"persisted": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.save_config()
        assert _post_url(client) == f"{api_http.ORB_API_PREFIX}/config/save"
        assert _post_json(client) == {}

    @pytest.mark.asyncio
    async def test_save_config_with_path_sets_path_key(self, api_http):
        resp = _mock_response(200, {"persisted": True})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.save_config(path="/tmp/config.yaml")
        assert _post_json(client) == {"path": "/tmp/config.yaml"}

    @pytest.mark.asyncio
    async def test_get_config_sources_path(self, api_http):
        resp = _mock_response(200, {"sources": []})
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            await api_http.get_config_sources()
        assert _get_url(client) == f"{api_http.ORB_API_PREFIX}/config/sources"


# ---------------------------------------------------------------------------
# get_provider_schemas — envelope handling & swallow-on-error
# ---------------------------------------------------------------------------


class TestProviderSchemas:
    @pytest.mark.asyncio
    async def test_extracts_inner_schemas_from_versioned_envelope(self, api_http):
        payload = {"schema_version": 1, "schemas": {"aws": [{"key": "id"}]}}
        resp = _mock_response(200, payload)
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_provider_schemas()
        assert result == {"aws": [{"key": "id"}]}

    @pytest.mark.asyncio
    async def test_returns_empty_when_inner_schemas_not_dict(self, api_http):
        payload = {"schema_version": 1, "schemas": ["not", "a", "dict"]}
        resp = _mock_response(200, payload)
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_provider_schemas()
        assert result == {}

    @pytest.mark.asyncio
    async def test_legacy_flat_dict_returned_as_is(self, api_http):
        payload = {"aws": [{"key": "id"}]}
        resp = _mock_response(200, payload)
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_provider_schemas()
        assert result == payload

    @pytest.mark.asyncio
    async def test_non_dict_result_returns_empty(self, api_http):
        resp = _mock_response(200, ["unexpected"])
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_provider_schemas()
        assert result == {}

    @pytest.mark.asyncio
    async def test_swallows_http_error_and_returns_empty(self, api_http):
        """Unlike other endpoints, get_provider_schemas never raises — it degrades to {}."""
        resp = _mock_response(500)
        client = _make_client_ctx(resp)
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_provider_schemas()
        assert result == {}

    @pytest.mark.asyncio
    async def test_swallows_transport_error_and_returns_empty(self, api_http):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch.object(api_http.httpx, "AsyncClient", return_value=client):
            result = await api_http.get_provider_schemas()
        assert result == {}


# ---------------------------------------------------------------------------
# subscribe_events — URL/param construction & delegation to stream_sse
# ---------------------------------------------------------------------------


class TestSubscribeEvents:
    @pytest.mark.asyncio
    async def test_builds_url_without_filter(self, api_http):
        captured: dict[str, Any] = {}

        async def _fake_stream(url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            yield "message", {"n": 1}

        fake_sse = MagicMock()
        fake_sse.stream_sse = _fake_stream
        with patch.dict("sys.modules", {"orb.ui.sse_client": fake_sse}):
            with patch.object(api_http, "_headers", return_value={"X-ORB-Scheduler": "default"}):
                events = [item async for item in api_http.subscribe_events()]

        assert events == [("message", {"n": 1})]
        expected = f"{api_http.ORB_BASE_URL}{api_http.ORB_ROOT_PREFIX}/api/v1/events"
        assert captured["url"] == expected
        assert captured["headers"] == {"X-ORB-Scheduler": "default"}

    @pytest.mark.asyncio
    async def test_builds_url_with_sorted_type_filter(self, api_http):
        captured: dict[str, Any] = {}

        async def _fake_stream(url, headers=None):
            captured["url"] = url
            if False:
                yield  # pragma: no cover - make this an async generator

        fake_sse = MagicMock()
        fake_sse.stream_sse = _fake_stream
        with patch.dict("sys.modules", {"orb.ui.sse_client": fake_sse}):
            with patch.object(api_http, "_headers", return_value={}):
                _ = [item async for item in api_http.subscribe_events({"machine", "alert"})]

        # types are joined sorted → alert,machine
        assert captured["url"].endswith("/api/v1/events?type=alert,machine")
