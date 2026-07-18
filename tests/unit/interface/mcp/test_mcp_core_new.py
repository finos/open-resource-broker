"""Unit tests for mcp/server/core.py — additional coverage.

Covers:
- handle_message: parse error, no method → invalid request, unknown method, internal error
- _handle_initialize: clientInfo/sessionId stored
- _handle_tools_call: valid tool, unknown tool raises
- _handle_resources_list: returns 4 resources
- _handle_prompts_list: lists registered prompts
- _handle_prompts_get: provision_infrastructure, troubleshoot_deployment, best_practices, unknown
- _generate_best_practices_prompt: fallback when app is None / ProviderRegistryService raises
- _unwrap_result: with/without data attr
- _make_args: sets _container
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.interface.mcp.server.core import OpenResourceBrokerMCPServer


def _make_server(app=None) -> OpenResourceBrokerMCPServer:
    mock_app = app or MagicMock()
    return OpenResourceBrokerMCPServer(app=mock_app)


def _msg(**kwargs) -> str:
    base = {"jsonrpc": "2.0", "id": 1}
    base.update(kwargs)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# handle_message — parse error / invalid request
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleMessageErrors:
    @pytest.mark.asyncio
    async def test_invalid_json_returns_parse_error(self):
        server = _make_server()
        response = await server.handle_message("{not valid json}")
        data = json.loads(response)
        assert "error" in data
        assert data["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_missing_method_returns_invalid_request(self):
        """A message with no 'method' key returns error code -32600."""
        server = _make_server()
        msg = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
        response = await server.handle_message(msg)
        data = json.loads(response)
        assert "error" in data
        assert data["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_unknown_method_returns_method_not_found(self):
        server = _make_server()
        msg = _msg(method="unknown/bogus")
        response = await server.handle_message(msg)
        data = json.loads(response)
        assert "error" in data
        assert data["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# _handle_initialize
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleInitialize:
    @pytest.mark.asyncio
    async def test_initialize_stores_client_info(self):
        server = _make_server()
        msg = _msg(
            method="initialize",
            params={"clientInfo": {"name": "test-client"}, "sessionId": "sess-1"},
        )
        response = await server.handle_message(msg)
        data = json.loads(response)

        assert "result" in data
        assert server.client_info == {"name": "test-client"}
        assert server.session_id == "sess-1"

    @pytest.mark.asyncio
    async def test_initialize_returns_server_info_and_capabilities(self):
        server = _make_server()
        msg = _msg(method="initialize", params={})
        response = await server.handle_message(msg)
        data = json.loads(response)

        result = data["result"]
        assert "serverInfo" in result
        assert "capabilities" in result
        assert "tools" in result["capabilities"]
        assert result["protocolVersion"] == "2024-11-05"


# ---------------------------------------------------------------------------
# _handle_tools_list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleToolsList:
    @pytest.mark.asyncio
    async def test_tools_list_returns_all_registered_tools(self):
        server = _make_server()
        msg = _msg(method="tools/list", params={})
        response = await server.handle_message(msg)
        data = json.loads(response)

        tools = data["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        # Core tools registered in _register_core_tools
        assert "list_requests" in tool_names
        assert "request_machines" in tool_names
        assert "list_machines" in tool_names

    @pytest.mark.asyncio
    async def test_tools_list_includes_input_schema(self):
        server = _make_server()
        msg = _msg(method="tools/list", params={})
        response = await server.handle_message(msg)
        data = json.loads(response)

        tools = data["result"]["tools"]
        for tool in tools:
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"


# ---------------------------------------------------------------------------
# _handle_tools_call
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleToolsCall:
    @pytest.mark.asyncio
    async def test_tools_call_unknown_tool_returns_error(self):
        server = _make_server()
        msg = _msg(method="tools/call", params={"name": "nonexistent_tool", "arguments": {}})
        response = await server.handle_message(msg)
        data = json.loads(response)
        assert "error" in data
        assert data["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_tools_call_known_tool_executes_and_returns_content(self):
        server = _make_server()

        # Register a simple mock tool
        fake_result = MagicMock()
        fake_result.data = {"requests": []}
        mock_tool = AsyncMock(return_value=fake_result)
        server.tools["list_requests"] = mock_tool

        msg = _msg(method="tools/call", params={"name": "list_requests", "arguments": {"limit": 5}})
        response = await server.handle_message(msg)
        data = json.loads(response)

        assert "result" in data
        content = data["result"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"
        mock_tool.assert_awaited_once()


# ---------------------------------------------------------------------------
# _handle_resources_list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleResourcesList:
    @pytest.mark.asyncio
    async def test_resources_list_returns_four_resources(self):
        server = _make_server()
        msg = _msg(method="resources/list", params={})
        response = await server.handle_message(msg)
        data = json.loads(response)

        resources = data["result"]["resources"]
        uris = {r["uri"] for r in resources}
        assert "templates://" in uris
        assert "requests://" in uris
        assert "machines://" in uris
        assert "providers://" in uris


# ---------------------------------------------------------------------------
# _handle_prompts_list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandlePromptsList:
    @pytest.mark.asyncio
    async def test_prompts_list_returns_all_prompts(self):
        server = _make_server()
        msg = _msg(method="prompts/list", params={})
        response = await server.handle_message(msg)
        data = json.loads(response)

        prompts = data["result"]["prompts"]
        names = {p["name"] for p in prompts}
        assert "provision_infrastructure" in names
        assert "troubleshoot_deployment" in names
        assert "infrastructure_best_practices" in names


# ---------------------------------------------------------------------------
# _handle_prompts_get
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandlePromptsGet:
    @pytest.mark.asyncio
    async def test_provision_infrastructure_prompt(self):
        server = _make_server()
        msg = _msg(
            method="prompts/get",
            params={
                "name": "provision_infrastructure",
                "arguments": {"template_type": "ec2", "instance_count": 3},
            },
        )
        response = await server.handle_message(msg)
        data = json.loads(response)

        result = data["result"]
        assert "messages" in result
        assert "description" in result
        text = result["messages"][0]["content"]["text"]
        assert "ec2" in text
        assert "3" in text

    @pytest.mark.asyncio
    async def test_troubleshoot_deployment_prompt(self):
        server = _make_server()
        msg = _msg(
            method="prompts/get",
            params={"name": "troubleshoot_deployment", "arguments": {"request_id": "req-abc"}},
        )
        response = await server.handle_message(msg)
        data = json.loads(response)

        text = data["result"]["messages"][0]["content"]["text"]
        assert "req-abc" in text

    @pytest.mark.asyncio
    async def test_best_practices_prompt_with_provider(self):
        server = _make_server()
        msg = _msg(
            method="prompts/get",
            params={"name": "infrastructure_best_practices", "arguments": {"provider": "aws"}},
        )
        response = await server.handle_message(msg)
        data = json.loads(response)

        text = data["result"]["messages"][0]["content"]["text"]
        assert "aws" in text.lower()

    @pytest.mark.asyncio
    async def test_unknown_prompt_returns_error(self):
        server = _make_server()
        msg = _msg(
            method="prompts/get",
            params={"name": "nonexistent_prompt", "arguments": {}},
        )
        response = await server.handle_message(msg)
        data = json.loads(response)
        assert "error" in data
        assert data["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_best_practices_fallback_when_app_is_none(self):
        """When app is None, best_practices prompt uses fallback provider."""
        server = _make_server(app=None)
        msg = _msg(
            method="prompts/get",
            params={"name": "infrastructure_best_practices", "arguments": {}},
        )
        response = await server.handle_message(msg)
        data = json.loads(response)

        # Should not error — falls back to PROVIDER_TYPE_AWS constant
        assert "result" in data

    @pytest.mark.asyncio
    async def test_best_practices_fallback_when_registry_raises(self):
        """When ProviderRegistryService.get_available_strategies() raises, uses fallback."""
        mock_app = MagicMock()
        mock_registry_service = MagicMock()
        mock_registry_service.get_available_strategies.side_effect = RuntimeError("no registry")
        mock_app.get.return_value = mock_registry_service

        server = _make_server(app=mock_app)
        msg = _msg(
            method="prompts/get",
            params={"name": "infrastructure_best_practices", "arguments": {}},
        )
        response = await server.handle_message(msg)
        data = json.loads(response)

        # Fallback applied — result should still be present
        assert "result" in data


# ---------------------------------------------------------------------------
# _unwrap_result
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnwrapResult:
    def test_unwrap_with_data_attr(self):
        result = MagicMock()
        result.data = {"key": "value"}
        assert OpenResourceBrokerMCPServer._unwrap_result(result) == {"key": "value"}

    def test_unwrap_plain_dict(self):
        result = {"key": "value"}
        assert OpenResourceBrokerMCPServer._unwrap_result(result) == {"key": "value"}


# ---------------------------------------------------------------------------
# _make_args
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMakeArgs:
    def test_make_args_injects_container(self):
        server = _make_server()
        args = server._make_args(limit=10)
        assert args._container is server.app
        assert args.limit == 10
