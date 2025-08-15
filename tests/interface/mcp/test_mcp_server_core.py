"""Tests for MCP server core functionality."""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from interface.mcp.server.core import MCPMessage, OpenHFPluginMCPServer


class TestMCPMessage:
    """Test MCP message structure."""

    def test_mcp_message_defaults(self):
        """Test MCP message with default values."""
        msg = MCPMessage()
        assert msg.jsonrpc == "2.0"
        assert msg.id is None
        assert msg.method is None
        assert msg.params is None
        assert msg.result is None
        assert msg.error is None

    def test_mcp_message_with_values(self):
        """Test MCP message with custom values."""
        msg = MCPMessage(id=1, method="test/method", params={"key": "value"})
        assert msg.jsonrpc == "2.0"
        assert msg.id == 1
        assert msg.method == "test/method"
        assert msg.params == {"key": "value"}


class TestOpenHFPluginMCPServer:
    """Test MCP server implementation."""

    @pytest.fixture
    def mock_app(self):
        """Create mock application instance."""
        app = Mock()
        app.get_query_bus = Mock(return_value=Mock())
        app.get_command_bus = Mock(return_value=Mock())
        return app

    @pytest.fixture
    def mcp_server(self, mock_app):
        """Create MCP server instance."""
        return OpenHFPluginMCPServer(app=mock_app)

    def test_server_initialization(self, mcp_server):
        """Test server initializes with correct tools and resources."""
        # Check tools are registered
        assert "check_provider_health" in mcp_server.tools
        assert "list_providers" in mcp_server.tools
        assert "list_templates" in mcp_server.tools
        assert "request_machines" in mcp_server.tools

        # Check resources are registered
        assert "templates" in mcp_server.resources
        assert "requests" in mcp_server.resources
        assert "machines" in mcp_server.resources
        assert "providers" in mcp_server.resources

        # Check prompts are registered
        assert "provision_infrastructure" in mcp_server.prompts
        assert "troubleshoot_deployment" in mcp_server.prompts
        assert "infrastructure_best_practices" in mcp_server.prompts

    @pytest.mark.asyncio
    async def test_handle_initialize_message(self, mcp_server):
        """Test initialize message handling."""
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }

        response = await mcp_server.handle_message(json.dumps(message))
        response_data = json.loads(response)

        assert response_data["jsonrpc"] == "2.0"
        assert response_data["id"] == 1
        assert "result" in response_data
        assert response_data["result"]["protocolVersion"] == "2024-11-05"
        assert "capabilities" in response_data["result"]
        assert "serverInfo" in response_data["result"]

    @pytest.mark.asyncio
    async def test_handle_tools_list_message(self, mcp_server):
        """Test tools/list message handling."""
        message = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

        response = await mcp_server.handle_message(json.dumps(message))
        response_data = json.loads(response)

        assert response_data["jsonrpc"] == "2.0"
        assert response_data["id"] == 2
        assert "result" in response_data
        assert "tools" in response_data["result"]

        tools = response_data["result"]["tools"]
        assert len(tools) > 0

        # Check tool structure
        tool = tools[0]
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool

    @pytest.mark.asyncio
    async def test_handle_resources_list_message(self, mcp_server):
        """Test resources/list message handling."""
        message = {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}}

        response = await mcp_server.handle_message(json.dumps(message))
        response_data = json.loads(response)

        assert response_data["jsonrpc"] == "2.0"
        assert response_data["id"] == 3
        assert "result" in response_data
        assert "resources" in response_data["result"]

        resources = response_data["result"]["resources"]
        assert len(resources) == 4

        # Check resource URIs
        uris = [r["uri"] for r in resources]
        assert "templates://" in uris
        assert "requests://" in uris
        assert "machines://" in uris
        assert "providers://" in uris

    @pytest.mark.asyncio
    async def test_handle_prompts_list_message(self, mcp_server):
        """Test prompts/list message handling."""
        message = {"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {}}

        response = await mcp_server.handle_message(json.dumps(message))
        response_data = json.loads(response)

        assert response_data["jsonrpc"] == "2.0"
        assert response_data["id"] == 4
        assert "result" in response_data
        assert "prompts" in response_data["result"]

        prompts = response_data["result"]["prompts"]
        assert len(prompts) == 3

        prompt_names = [p["name"] for p in prompts]
        assert "provision_infrastructure" in prompt_names
        assert "troubleshoot_deployment" in prompt_names
        assert "infrastructure_best_practices" in prompt_names

    @pytest.mark.asyncio
    async def test_handle_tools_call_message(self, mcp_server):
        """Test tools/call message handling."""
        # Mock the tool function
        mock_tool = AsyncMock(return_value={"status": "success", "data": "test"})
        mcp_server.tools["test_tool"] = mock_tool

        message = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "test_tool", "arguments": {"param1": "value1"}},
        }

        response = await mcp_server.handle_message(json.dumps(message))
        response_data = json.loads(response)

        assert response_data["jsonrpc"] == "2.0"
        assert response_data["id"] == 5
        assert "result" in response_data
        assert "content" in response_data["result"]

        # Verify tool was called
        mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_invalid_method(self, mcp_server):
        """Test handling of invalid method."""
        message = {"jsonrpc": "2.0", "id": 6, "method": "invalid/method", "params": {}}

        response = await mcp_server.handle_message(json.dumps(message))
        response_data = json.loads(response)

        assert response_data["jsonrpc"] == "2.0"
        assert response_data["id"] == 6
        assert "error" in response_data
        assert response_data["error"]["code"] == -32601
        assert "Method not found" in response_data["error"]["message"]

    @pytest.mark.asyncio
    async def test_handle_invalid_json(self, mcp_server):
        """Test handling of invalid JSON."""
        invalid_json = "{ invalid json }"

        response = await mcp_server.handle_message(invalid_json)
        response_data = json.loads(response)

        assert response_data["jsonrpc"] == "2.0"
        assert "error" in response_data
        assert response_data["error"]["code"] == -32700
        assert "Parse error" in response_data["error"]["message"]

    def test_generate_provision_prompt(self, mcp_server):
        """Test provision infrastructure prompt generation."""
        arguments = {"template_type": "ec2", "instance_count": 5}
        prompt = mcp_server._generate_provision_prompt(arguments)

        assert "5 ec2 instance(s)" in prompt
        assert "Open Host Factory Plugin" in prompt
        assert "List available templates" in prompt

    def test_generate_troubleshoot_prompt(self, mcp_server):
        """Test troubleshoot deployment prompt generation."""
        arguments = {"request_id": "req-12345"}
        prompt = mcp_server._generate_troubleshoot_prompt(arguments)

        assert "req-12345" in prompt
        assert "troubleshooting" in prompt
        assert "Check the current status" in prompt

    def test_generate_best_practices_prompt(self, mcp_server):
        """Test best practices prompt generation."""
        arguments = {"provider": "aws"}
        prompt = mcp_server._generate_best_practices_prompt(arguments)

        assert "aws" in prompt
        assert "best practices" in prompt
        assert "Template selection" in prompt

    def test_get_tool_schema(self, mcp_server):
        """Test tool schema generation."""
        # Test template tool schema
        schema = mcp_server._get_tool_schema("list_templates")
        assert "template_id" in schema

        # Test request tool schema
        schema = mcp_server._get_tool_schema("get_request_status")
        assert "request_id" in schema

        # Test machine tool schema
        schema = mcp_server._get_tool_schema("request_machines")
        assert "template_id" in schema
        assert "count" in schema

        # Test provider tool schema
        schema = mcp_server._get_tool_schema("check_provider_health")
        assert "provider" in schema
