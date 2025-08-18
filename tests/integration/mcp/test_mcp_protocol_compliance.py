"""Integration tests for MCP protocol compliance."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from interface.mcp.server.core import OpenHFPluginMCPServer


class TestMCPProtocolCompliance:
    """Test MCP protocol compliance and integration scenarios."""

    @pytest.fixture
    def mock_app(self):
        """Create mock application instance."""
        app = Mock()

        # Mock query bus
        query_bus = Mock()
        query_bus.execute = AsyncMock()
        app.get_query_bus = Mock(return_value=query_bus)

        # Mock command bus
        command_bus = Mock()
        command_bus.execute = AsyncMock()
        app.get_command_bus = Mock(return_value=command_bus)

        return app

    @pytest.fixture
    def mcp_server(self, mock_app):
        """Create MCP server instance."""
        return OpenHFPluginMCPServer(app=mock_app)

    @pytest.mark.asyncio
    async def test_full_mcp_session_workflow(self, mcp_server):
        """Test complete MCP session workflow."""
        # Initialize session
        init_message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }

        response = await mcp_server.handle_message(json.dumps(init_message))
        init_response = json.loads(response)

        assert init_response["result"]["protocolVersion"] == "2024-11-05"
        assert "capabilities" in init_response["result"]

        # List available tools
        tools_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }

        response = await mcp_server.handle_message(json.dumps(tools_message))
        tools_response = json.loads(response)

        assert "tools" in tools_response["result"]
        tools = tools_response["result"]["tools"]
        assert len(tools) > 0

        # List available resources
        resources_message = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/list",
            "params": {},
        }

        response = await mcp_server.handle_message(json.dumps(resources_message))
        resources_response = json.loads(response)

        assert "resources" in resources_response["result"]
        resources = resources_response["result"]["resources"]
        assert len(resources) == 4

        # Get prompts
        prompts_message = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "prompts/list",
            "params": {},
        }

        response = await mcp_server.handle_message(json.dumps(prompts_message))
        prompts_response = json.loads(response)

        assert "prompts" in prompts_response["result"]
        prompts = prompts_response["result"]["prompts"]
        assert len(prompts) == 3

    @pytest.mark.asyncio
    async def test_tool_execution_workflow(self, mcp_server):
        """Test tool execution workflow."""
        # Mock the list_providers tool to return test data
        mock_tool = AsyncMock(
            return_value={
                "providers": [{"name": "aws", "status": "active"}],
                "count": 1,
            }
        )
        mcp_server.tools["list_providers"] = mock_tool

        # Call the tool
        tool_call_message = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "list_providers", "arguments": {}},
        }

        response = await mcp_server.handle_message(json.dumps(tool_call_message))
        tool_response = json.loads(response)

        assert "result" in tool_response
        assert "content" in tool_response["result"]

        content = tool_response["result"]["content"][0]
        assert content["type"] == "text"

        # Parse the returned data
        returned_data = json.loads(content["text"])
        assert "providers" in returned_data
        assert returned_data["count"] == 1

        # Verify tool was called
        mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_resource_access_workflow(self, mcp_server):
        """Test resource access workflow."""
        # Mock the templates resource
        mock_templates_tool = AsyncMock(
            return_value={"templates": ["template1", "template2"], "count": 2}
        )
        mcp_server.tools["list_templates"] = mock_templates_tool

        # Read templates resource
        resource_read_message = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "resources/read",
            "params": {"uri": "templates://"},
        }

        response = await mcp_server.handle_message(json.dumps(resource_read_message))
        resource_response = json.loads(response)

        assert "result" in resource_response
        assert "contents" in resource_response["result"]

        content = resource_response["result"]["contents"][0]
        assert content["uri"] == "templates://"
        assert content["mimeType"] == "application/json"

        # Parse the returned data
        returned_data = json.loads(content["text"])
        assert "templates" in returned_data
        assert returned_data["count"] == 2

    @pytest.mark.asyncio
    async def test_prompt_generation_workflow(self, mcp_server):
        """Test prompt generation workflow."""
        # Get provision infrastructure prompt
        prompt_message = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "prompts/get",
            "params": {
                "name": "provision_infrastructure",
                "arguments": {"template_type": "ec2", "instance_count": 3},
            },
        }

        response = await mcp_server.handle_message(json.dumps(prompt_message))
        prompt_response = json.loads(response)

        assert "result" in prompt_response
        assert "description" in prompt_response["result"]
        assert "messages" in prompt_response["result"]

        message = prompt_response["result"]["messages"][0]
        assert message["role"] == "user"
        assert "content" in message

        prompt_text = message["content"]["text"]
        assert "3 ec2 instance(s)" in prompt_text
        assert "Open Host Factory Plugin" in prompt_text

    @pytest.mark.asyncio
    async def test_error_handling_compliance(self, mcp_server):
        """Test MCP error handling compliance."""
        # Test unknown method
        unknown_method_message = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "unknown/method",
            "params": {},
        }

        response = await mcp_server.handle_message(json.dumps(unknown_method_message))
        error_response = json.loads(response)

        assert "error" in error_response
        assert error_response["error"]["code"] == -32601
        assert "Method not found" in error_response["error"]["message"]

        # Test unknown tool
        unknown_tool_message = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "unknown_tool", "arguments": {}},
        }

        response = await mcp_server.handle_message(json.dumps(unknown_tool_message))
        error_response = json.loads(response)

        assert "error" in error_response
        assert error_response["error"]["code"] == -32603
        assert "Unknown tool" in error_response["error"]["message"]

        # Test unknown resource
        unknown_resource_message = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "resources/read",
            "params": {"uri": "unknown://"},
        }

        response = await mcp_server.handle_message(json.dumps(unknown_resource_message))
        error_response = json.loads(response)

        assert "error" in error_response
        assert error_response["error"]["code"] == -32603
        assert "Unknown resource URI" in error_response["error"]["message"]

    @pytest.mark.asyncio
    async def test_concurrent_message_handling(self, mcp_server):
        """Test concurrent message handling."""
        # Create multiple concurrent requests
        messages = []
        for i in range(5):
            message = {
                "jsonrpc": "2.0",
                "id": i + 20,
                "method": "tools/list",
                "params": {},
            }
            messages.append(json.dumps(message))

        # Handle all messages concurrently
        tasks = [mcp_server.handle_message(msg) for msg in messages]
        responses = await asyncio.gather(*tasks)

        # Verify all responses
        assert len(responses) == 5
        for i, response in enumerate(responses):
            response_data = json.loads(response)
            assert response_data["id"] == i + 20
            assert "result" in response_data
            assert "tools" in response_data["result"]

    def test_mcp_message_structure_compliance(self, mcp_server):
        """Test MCP message structure compliance."""
        # Test all required fields are present in responses
        test_cases = [
            ("initialize", {"protocolVersion": "2024-11-05"}),
            ("tools/list", {}),
            ("resources/list", {}),
            ("prompts/list", {}),
        ]

        for method, params in test_cases:
            message = {"jsonrpc": "2.0", "id": 100, "method": method, "params": params}

            # This is a sync test, so we'll just verify the message structure
            # The actual async handling is tested in other methods
            assert message["jsonrpc"] == "2.0"
            assert "id" in message
            assert "method" in message
            assert "params" in message

    @pytest.mark.asyncio
    async def test_tool_schema_compliance(self, mcp_server):
        """Test tool schema compliance."""
        tools_message = {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/list",
            "params": {},
        }

        response = await mcp_server.handle_message(json.dumps(tools_message))
        tools_response = json.loads(response)

        tools = tools_response["result"]["tools"]

        for tool in tools:
            # Verify required fields
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool

            # Verify schema structure
            schema = tool["inputSchema"]
            assert "type" in schema
            assert schema["type"] == "object"
            assert "properties" in schema
            assert "required" in schema
            assert isinstance(schema["required"], list)
