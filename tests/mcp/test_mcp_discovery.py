"""Unit tests for MCP discovery following existing test patterns."""

from unittest.mock import Mock

import pytest

from src.mcp.discovery import MCPToolDefinition, MCPToolDiscovery
from src.sdk.client import OpenHFPluginSDK
from src.sdk.discovery import MethodInfo


class TestMCPToolDiscovery:
    """Test cases for MCPToolDiscovery following existing test patterns."""

    def test_initialization(self):
        """Test discovery service initialization."""
        discovery = MCPToolDiscovery()

        assert discovery._tool_definitions == {}
        assert discovery.list_tool_names() == []

    def test_discover_mcp_tools_sdk_not_initialized(self):
        """Test discovery fails when SDK not initialized."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_sdk.initialized = False

        discovery = MCPToolDiscovery()

        with pytest.raises(ValueError, match="SDK must be initialized"):
            discovery.discover_mcp_tools(mock_sdk)

    def test_discover_mcp_tools_success(self):
        """Test successful MCP tool discovery."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_sdk.initialized = True
        mock_sdk.list_available_methods = Mock(
            return_value=["test_method", "another_method"])

        mock_method_info = Mock(spec=MethodInfo)
        mock_method_info.description = "Test method description"
        mock_method_info.parameters = {"param1": {"type": str, "required": True}}

        mock_sdk.get_method_info = Mock(return_value=mock_method_info)

        discovery = MCPToolDiscovery()

        tools = discovery.discover_mcp_tools(mock_sdk)

        assert len(tools) == 2
        assert "test_method" in tools
        assert "another_method" in tools

        # Check tool definition structure
        tool_def = tools["test_method"]
        assert isinstance(tool_def, MCPToolDefinition)
        assert tool_def.name == "test_method"
        assert tool_def.method_name == "test_method"
        assert tool_def.method_info is mock_method_info

    def test_get_tool_definition_existing(self):
        """Test getting existing tool definition."""
        discovery = MCPToolDiscovery()

        mock_tool_def = Mock(spec=MCPToolDefinition)
        discovery._tool_definitions["test_tool"] = mock_tool_def

        result = discovery.get_tool_definition("test_tool")

        assert result is mock_tool_def

    def test_get_tool_definition_nonexistent(self):
        """Test getting nonexistent tool definition."""
        discovery = MCPToolDiscovery()

        result = discovery.get_tool_definition("nonexistent_tool")

        assert result is None

    def test_list_tool_names(self):
        """Test listing tool names."""
        discovery = MCPToolDiscovery()

        discovery._tool_definitions = {"tool1": Mock(), "tool2": Mock()}

        names = discovery.list_tool_names()

        assert "tool1" in names
        assert "tool2" in names
        assert len(names) == 2

    def test_get_tools_list(self):
        """Test getting tools list in MCP format."""
        mock_tool_def1 = Mock(spec=MCPToolDefinition)
        mock_tool_def1.name = "tool1"
        mock_tool_def1.description = "Tool 1 description"
        mock_tool_def1.input_schema = {"type": "object"}

        mock_tool_def2 = Mock(spec=MCPToolDefinition)
        mock_tool_def2.name = "tool2"
        mock_tool_def2.description = "Tool 2 description"
        mock_tool_def2.input_schema = {"type": "object"}

        discovery = MCPToolDiscovery()
        discovery._tool_definitions = {"tool1": mock_tool_def1, "tool2": mock_tool_def2}

        tools_list = discovery.get_tools_list()

        assert len(tools_list) == 2

        # Check format
        tool1_entry = next(t for t in tools_list if t["name"] == "tool1")
        assert tool1_entry["description"] == "Tool 1 description"
        assert tool1_entry["inputSchema"] == {"type": "object"}

    def test_generate_description_with_method_info(self):
        """Test description generation with method info."""
        discovery = MCPToolDiscovery()

        mock_method_info = Mock(spec=MethodInfo)
        mock_method_info.description = "Existing description"

        description = discovery._generate_description("test_method", mock_method_info)

        assert description == "Existing description"

    def test_generate_description_without_method_info(self):
        """Test description generation without method info."""
        discovery = MCPToolDiscovery()

        description = discovery._generate_description("test_method", None)

        assert description == "Test Method - Execute test_method operation"

    def test_generate_description_fallback(self):
        """Test description generation fallback."""
        discovery = MCPToolDiscovery()

        mock_method_info = Mock(spec=MethodInfo)
        mock_method_info.description = None

        description = discovery._generate_description(
            "create_request", mock_method_info)

        assert description == "Create Request - Execute create_request operation"

    def test_generate_schema_no_method_info(self):
        """Test schema generation without method info."""
        discovery = MCPToolDiscovery()

        schema = discovery._generate_schema("test_method", None)

        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert schema["additionalProperties"] is True
        assert "test_method" in schema["description"]

    def test_generate_schema_no_parameters(self):
        """Test schema generation with method info but no parameters."""
        discovery = MCPToolDiscovery()

        mock_method_info = Mock(spec=MethodInfo)
        mock_method_info.parameters = {}

        schema = discovery._generate_schema("test_method", mock_method_info)

        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert schema["additionalProperties"] is True

    def test_generate_schema_with_parameters(self):
        """Test schema generation with parameters."""
        discovery = MCPToolDiscovery()

        mock_method_info = Mock(spec=MethodInfo)
        mock_method_info.parameters = {
            "param1": {
                "type": str,
                "required": True,
                "description": "String parameter",
            },
            "param2": {
                "type": int,
                "required": False,
                "description": "Integer parameter",
            },
        }

        schema = discovery._generate_schema("test_method", mock_method_info)

        assert schema["type"] == "object"
        assert "param1" in schema["properties"]
        assert "param2" in schema["properties"]
        assert schema["required"] == ["param1"]

        # Check parameter conversion
        assert schema["properties"]["param1"]["type"] == "string"
        assert schema["properties"]["param2"]["type"] == "integer"

    def test_convert_param_to_schema_string(self):
        """Test parameter conversion for string type."""
        discovery = MCPToolDiscovery()

        param_info = {"type": str, "description": "String param"}

        schema = discovery._convert_param_to_schema("test_param", param_info)

        assert schema["type"] == "string"
        assert schema["description"] == "String param"

    def test_convert_param_to_schema_integer(self):
        """Test parameter conversion for integer type."""
        discovery = MCPToolDiscovery()

        param_info = {"type": int, "description": "Integer param"}

        schema = discovery._convert_param_to_schema("test_param", param_info)

        assert schema["type"] == "integer"
        assert schema["description"] == "Integer param"

    def test_convert_param_to_schema_boolean(self):
        """Test parameter conversion for boolean type."""
        discovery = MCPToolDiscovery()

        param_info = {"type": bool, "description": "Boolean param"}

        schema = discovery._convert_param_to_schema("test_param", param_info)

        assert schema["type"] == "boolean"
        assert schema["description"] == "Boolean param"

    def test_convert_param_to_schema_list(self):
        """Test parameter conversion for list type."""
        discovery = MCPToolDiscovery()

        param_info = {"type": "list", "description": "List param"}

        schema = discovery._convert_param_to_schema("test_param", param_info)

        assert schema["type"] == "array"
        assert schema["items"]["type"] == "string"
        assert schema["description"] == "List param"

    def test_convert_param_to_schema_dict(self):
        """Test parameter conversion for dict type."""
        discovery = MCPToolDiscovery()

        param_info = {"type": "dict", "description": "Dict param"}

        schema = discovery._convert_param_to_schema("test_param", param_info)

        assert schema["type"] == "object"
        assert schema["additionalProperties"] is True
        assert schema["description"] == "Dict param"

    def test_convert_param_to_schema_unknown_type(self):
        """Test parameter conversion for unknown type."""
        discovery = MCPToolDiscovery()

        param_info = {"type": "UnknownType", "description": "Unknown param"}

        schema = discovery._convert_param_to_schema("test_param", param_info)

        assert schema["type"] == "string"  # Default fallback
        assert schema["description"] == "Unknown param"

    def test_get_stats(self):
        """Test getting discovery statistics."""
        discovery = MCPToolDiscovery()

        discovery._tool_definitions = {
            "tool1": Mock(),
            "tool2": Mock(),
            "tool3": Mock(),
        }

        stats = discovery.get_stats()

        assert stats["tools_discovered"] == 3
        assert "tool1" in stats["tool_names"]
        assert "tool2" in stats["tool_names"]
        assert "tool3" in stats["tool_names"]
