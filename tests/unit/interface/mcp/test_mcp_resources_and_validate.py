"""Unit tests for MCP resources/read handler and handle_mcp_validate CLI handler."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.interface.mcp.server.core import OpenResourceBrokerMCPServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server() -> OpenResourceBrokerMCPServer:
    """Create an MCP server with a mock app."""
    app = MagicMock()
    return OpenResourceBrokerMCPServer(app=app)


def _make_args(**kwargs):
    """Create a simple args namespace."""
    defaults = {"format": "json", "config": None}
    defaults.update(kwargs)
    return type("Args", (), defaults)()


# ---------------------------------------------------------------------------
# resources/read — requests://
# ---------------------------------------------------------------------------


class TestResourcesReadRequests:
    @pytest.mark.asyncio
    async def test_resources_read_requests_uri_returns_contents(self):
        """resources/read with requests:// URI returns contents list with correct URI."""
        server = _make_server()
        mock_result = {"requests": [{"request_id": "req-abc"}]}
        server.tools["list_return_requests"] = AsyncMock(return_value=mock_result)

        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "resources/read",
            "params": {"uri": "requests://"},
        }
        response = await server.handle_message(json.dumps(message))
        data = json.loads(response)

        assert "result" in data
        contents = data["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == "requests://"
        assert contents[0]["mimeType"] == "application/json"
        # The text field must be valid JSON containing the mock result
        text_data = json.loads(contents[0]["text"])
        assert "requests" in text_data

    @pytest.mark.asyncio
    async def test_resources_read_requests_calls_list_requests_tool(self):
        """resources/read requests:// delegates to the list_requests tool."""
        server = _make_server()
        mock_tool = AsyncMock(return_value={"requests": []})
        server.tools["list_requests"] = mock_tool

        message = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "resources/read",
            "params": {"uri": "requests://"},
        }
        await server.handle_message(json.dumps(message))

        mock_tool.assert_awaited_once()


# ---------------------------------------------------------------------------
# resources/read — machines://
# ---------------------------------------------------------------------------


class TestResourcesReadMachines:
    @pytest.mark.asyncio
    async def test_resources_read_machines_uri_returns_contents(self):
        """resources/read with machines:// URI returns contents list."""
        server = _make_server()
        mock_result = {"machines": [{"machine_id": "m-123"}]}
        server.tools["list_machines"] = AsyncMock(return_value=mock_result)

        message = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/read",
            "params": {"uri": "machines://"},
        }
        response = await server.handle_message(json.dumps(message))
        data = json.loads(response)

        assert "result" in data
        contents = data["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == "machines://"
        assert contents[0]["mimeType"] == "application/json"
        text_data = json.loads(contents[0]["text"])
        assert "machines" in text_data

    @pytest.mark.asyncio
    async def test_resources_read_machines_calls_list_machines_tool(self):
        """resources/read machines:// delegates to the list_machines tool."""
        server = _make_server()
        mock_tool = AsyncMock(return_value={"machines": []})
        server.tools["list_machines"] = mock_tool

        message = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/read",
            "params": {"uri": "machines://"},
        }
        await server.handle_message(json.dumps(message))

        mock_tool.assert_awaited_once()


# ---------------------------------------------------------------------------
# resources/read — providers://
# ---------------------------------------------------------------------------


class TestResourcesReadProviders:
    @pytest.mark.asyncio
    async def test_resources_read_providers_uri_returns_contents(self):
        """resources/read with providers:// URI returns contents list."""
        server = _make_server()
        mock_result = {"providers": [{"name": "aws", "type": "aws"}]}
        server.tools["list_providers"] = AsyncMock(return_value=mock_result)

        message = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "resources/read",
            "params": {"uri": "providers://"},
        }
        response = await server.handle_message(json.dumps(message))
        data = json.loads(response)

        assert "result" in data
        contents = data["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == "providers://"
        text_data = json.loads(contents[0]["text"])
        assert "providers" in text_data

    @pytest.mark.asyncio
    async def test_resources_read_providers_calls_list_providers_tool(self):
        """resources/read providers:// delegates to the list_providers tool."""
        server = _make_server()
        mock_tool = AsyncMock(return_value={"providers": []})
        server.tools["list_providers"] = mock_tool

        message = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "resources/read",
            "params": {"uri": "providers://"},
        }
        await server.handle_message(json.dumps(message))

        mock_tool.assert_awaited_once()


# ---------------------------------------------------------------------------
# resources/read — unknown URI
# ---------------------------------------------------------------------------


class TestResourcesReadUnknownUri:
    @pytest.mark.asyncio
    async def test_resources_read_unknown_uri_returns_error(self):
        """resources/read with an unknown URI scheme returns a JSON-RPC error."""
        server = _make_server()

        message = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "resources/read",
            "params": {"uri": "unknown://something"},
        }
        response = await server.handle_message(json.dumps(message))
        data = json.loads(response)

        assert "error" in data
        assert data["error"]["code"] == -32603


# ---------------------------------------------------------------------------
# handle_mcp_validate
# ---------------------------------------------------------------------------


class TestHandleMcpValidate:
    @pytest.mark.asyncio
    async def test_validate_returns_valid_true_when_tools_initialize(self):
        """handle_mcp_validate returns valid=True when MCP tools initialize successfully."""
        from orb.interface.mcp_command_handlers import handle_mcp_validate

        mock_tools = MagicMock()
        mock_tools.__aenter__ = AsyncMock(return_value=mock_tools)
        mock_tools.__aexit__ = AsyncMock(return_value=False)
        mock_tools.get_stats.return_value = {"tools_discovered": 5}
        mock_tools.get_tools_by_type.return_value = []

        args = _make_args()

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_validate(args)

        assert result["valid"] is True
        assert len(result["checks"]) >= 1
        init_check = next(c for c in result["checks"] if "Initialization" in c["check"])
        assert init_check["status"] == "PASS"

    @pytest.mark.asyncio
    async def test_validate_returns_valid_false_when_tools_fail(self):
        """handle_mcp_validate returns valid=False when MCP tools raise on init."""
        from orb.interface.mcp_command_handlers import handle_mcp_validate

        mock_tools = MagicMock()
        mock_tools.__aenter__ = AsyncMock(side_effect=RuntimeError("init failed"))
        mock_tools.__aexit__ = AsyncMock(return_value=False)

        args = _make_args()

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_validate(args)

        assert result["valid"] is False
        fail_check = next(c for c in result["checks"] if c["status"] == "FAIL")
        assert "init failed" in fail_check["details"]

    @pytest.mark.asyncio
    async def test_validate_checks_config_file_when_provided(self, tmp_path):
        """handle_mcp_validate validates a config file when args.config is set."""
        from orb.interface.mcp_command_handlers import handle_mcp_validate

        config_file = tmp_path / "mcp_config.json"
        config_file.write_text(json.dumps({"key1": "val1", "key2": "val2"}))

        mock_tools = MagicMock()
        mock_tools.__aenter__ = AsyncMock(return_value=mock_tools)
        mock_tools.__aexit__ = AsyncMock(return_value=False)
        mock_tools.get_stats.return_value = {"tools_discovered": 3}
        mock_tools.get_tools_by_type.return_value = []

        args = _make_args(config=str(config_file))

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_validate(args)

        config_check = next(
            (c for c in result["checks"] if "Configuration File" in c["check"]), None
        )
        assert config_check is not None
        assert config_check["status"] == "PASS"
        assert "2 keys" in config_check["details"]

    @pytest.mark.asyncio
    async def test_validate_fails_when_config_file_missing(self):
        """handle_mcp_validate marks valid=False when config file does not exist."""
        from orb.interface.mcp_command_handlers import handle_mcp_validate

        mock_tools = MagicMock()
        mock_tools.__aenter__ = AsyncMock(return_value=mock_tools)
        mock_tools.__aexit__ = AsyncMock(return_value=False)
        mock_tools.get_stats.return_value = {"tools_discovered": 3}
        mock_tools.get_tools_by_type.return_value = []

        args = _make_args(config="/nonexistent/path/config.json")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_validate(args)

        assert result["valid"] is False
        fail_check = next(c for c in result["checks"] if c["status"] == "FAIL")
        assert (
            "not found" in fail_check["details"].lower()
            or "File not found" in fail_check["details"]
        )

    @pytest.mark.asyncio
    async def test_validate_table_format_returns_table_key(self):
        """handle_mcp_validate with format=table returns a table-structured response."""
        from orb.interface.mcp_command_handlers import handle_mcp_validate

        mock_tools = MagicMock()
        mock_tools.__aenter__ = AsyncMock(return_value=mock_tools)
        mock_tools.__aexit__ = AsyncMock(return_value=False)
        mock_tools.get_stats.return_value = {"tools_discovered": 2}
        mock_tools.get_tools_by_type.return_value = []

        args = _make_args(format="table")

        with patch(
            "orb.interface.mcp_command_handlers.OpenResourceBrokerMCPTools", return_value=mock_tools
        ):
            result = await handle_mcp_validate(args)

        assert "validation_table" in result
        assert "summary" in result
