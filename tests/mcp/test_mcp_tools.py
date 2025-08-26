"""Unit tests for MCP tools following existing test patterns."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from mcp.discovery import MCPToolDiscovery
from mcp.tools import OpenHFPluginMCPTools
from sdk.client import OpenHFPluginSDK


class TestOpenHFPluginMCPTools:
    """Test cases for OpenHFPluginMCPTools following existing test patterns."""

    def test_initialization_with_default_sdk(self):
        """Test MCP tools initialization with default SDK."""
        tools = OpenHFPluginMCPTools()

        assert tools.sdk is not None
        assert isinstance(tools.sdk, OpenHFPluginSDK)
        assert isinstance(tools.discovery, MCPToolDiscovery)
        assert not tools.initialized
        assert tools.tools == {}

    def test_initialization_with_custom_sdk(self):
        """Test MCP tools initialization with custom SDK."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        tools = OpenHFPluginMCPTools(sdk=mock_sdk)

        assert tools.sdk is mock_sdk
        assert not tools.initialized

    def test_initialization_with_sdk_kwargs(self):
        """Test MCP tools initialization with SDK kwargs."""
        tools = OpenHFPluginMCPTools(provider="mock", timeout=600)

        assert tools.sdk.provider == "mock"
        # The timeout goes into custom_config, not the main config field
        assert tools.sdk.config.custom_config["timeout"] == 600

    @pytest.mark.asyncio
    async def test_initialize_success(self):
        """Test successful MCP tools initialization."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_sdk.initialized = False
        mock_sdk.initialize = AsyncMock(return_value=True)
        mock_sdk.list_available_methods = Mock(return_value=["test_method"])
        mock_sdk.get_method_info = Mock(return_value=Mock())

        tools = OpenHFPluginMCPTools(sdk=mock_sdk)

        with patch.object(tools.discovery, "discover_mcp_tools") as mock_discover:
            mock_discover.return_value = {"test_method": Mock()}

            await tools.initialize()

            assert tools.initialized
            mock_sdk.initialize.assert_called_once()
            mock_discover.assert_called_once_with(mock_sdk)

    @pytest.mark.asyncio
    async def test_initialize_already_initialized(self):
        """Test initialization when already initialized."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        tools = OpenHFPluginMCPTools(sdk=mock_sdk)
        tools._initialized = True

        await tools.initialize()

        # Should not call SDK initialize if already initialized
        mock_sdk.initialize.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_with_initialized_sdk(self):
        """Test initialization with already initialized SDK."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_sdk.initialized = True

        tools = OpenHFPluginMCPTools(sdk=mock_sdk)

        with patch.object(tools.discovery, "discover_mcp_tools") as mock_discover:
            mock_discover.return_value = {}

            await tools.initialize()

            # Should not call SDK initialize if SDK already initialized
            mock_sdk.initialize.assert_not_called()
            mock_discover.assert_called_once_with(mock_sdk)

    @pytest.mark.asyncio
    async def test_cleanup(self):
        """Test MCP tools cleanup."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_sdk.cleanup = AsyncMock()

        tools = OpenHFPluginMCPTools(sdk=mock_sdk)
        tools._initialized = True
        tools.tools = {"test": Mock()}

        await tools.cleanup()

        assert not tools.initialized
        assert tools.tools == {}
        mock_sdk.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test MCP tools as async context manager."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_sdk.initialized = False
        mock_sdk.initialize = AsyncMock(return_value=True)
        mock_sdk.cleanup = AsyncMock()

        tools = OpenHFPluginMCPTools(sdk=mock_sdk)

        with patch.object(tools, "initialize", new_callable=AsyncMock) as mock_init:
            with patch.object(tools, "cleanup", new_callable=AsyncMock) as mock_cleanup:
                async with tools as context_tools:
                    assert context_tools is tools

                mock_init.assert_called_once()
                mock_cleanup.assert_called_once()

    def test_list_tools_not_initialized(self):
        """Test list_tools raises error when not initialized."""
        tools = OpenHFPluginMCPTools()

        with pytest.raises(ValueError, match="MCP tools not initialized"):
            tools.list_tools()

    def test_list_tools_success(self):
        """Test successful tools listing."""
        tools = OpenHFPluginMCPTools()
        tools._initialized = True

        mock_tools_list = [{"name": "test_tool", "description": "Test tool", "inputSchema": {}}]

        with patch.object(tools.discovery, "get_tools_list", return_value=mock_tools_list):
            result = tools.list_tools()

            assert result == mock_tools_list

    @pytest.mark.asyncio
    async def test_call_tool_not_initialized(self):
        """Test call_tool raises error when not initialized."""
        tools = OpenHFPluginMCPTools()

        with pytest.raises(ValueError, match="MCP tools not initialized"):
            await tools.call_tool("test_tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_unknown_tool(self):
        """Test call_tool raises error for unknown tool."""
        tools = OpenHFPluginMCPTools()
        tools._initialized = True
        tools.tools = {"known_tool": Mock()}

        with pytest.raises(ValueError, match="Unknown tool: unknown_tool"):
            await tools.call_tool("unknown_tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        """Test successful tool call."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_method = AsyncMock(return_value={"result": "success"})
        mock_sdk.test_method = mock_method

        mock_tool_def = Mock()
        mock_tool_def.method_name = "test_method"

        tools = OpenHFPluginMCPTools(sdk=mock_sdk)
        tools._initialized = True
        tools.tools = {"test_tool": mock_tool_def}

        result = await tools.call_tool("test_tool", {"arg1": "value1"})

        assert result["success"] is True
        assert result["tool"] == "test_tool"
        mock_method.assert_called_once_with(arg1="value1")

    @pytest.mark.asyncio
    async def test_call_tool_method_not_found(self):
        """Test call_tool when SDK method not found."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)

        mock_tool_def = Mock()
        mock_tool_def.method_name = "nonexistent_method"

        tools = OpenHFPluginMCPTools(sdk=mock_sdk)
        tools._initialized = True
        tools.tools = {"test_tool": mock_tool_def}

        result = await tools.call_tool("test_tool", {})

        assert "error" in result
        assert "SDK method nonexistent_method not found" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_call_tool_execution_error(self):
        """Test call_tool when method execution fails."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_method = AsyncMock(side_effect=Exception("Execution failed"))
        mock_sdk.test_method = mock_method

        mock_tool_def = Mock()
        mock_tool_def.method_name = "test_method"

        tools = OpenHFPluginMCPTools(sdk=mock_sdk)
        tools._initialized = True
        tools.tools = {"test_tool": mock_tool_def}

        result = await tools.call_tool("test_tool", {})

        assert "error" in result
        assert result["error"]["message"] == "Execution failed"
        assert result["error"]["tool"] == "test_tool"

    def test_get_tool_info_not_initialized(self):
        """Test get_tool_info when not initialized."""
        tools = OpenHFPluginMCPTools()

        result = tools.get_tool_info("test_tool")

        assert result is None

    def test_get_tool_info_success(self):
        """Test successful get_tool_info."""
        mock_tool_def = Mock()

        tools = OpenHFPluginMCPTools()
        tools._initialized = True
        tools.tools = {"test_tool": mock_tool_def}

        result = tools.get_tool_info("test_tool")

        assert result is mock_tool_def

    def test_get_tools_by_type_not_initialized(self):
        """Test get_tools_by_type when not initialized."""
        tools = OpenHFPluginMCPTools()

        result = tools.get_tools_by_type("query")

        assert result == []

    def test_get_tools_by_type_success(self):
        """Test successful get_tools_by_type."""
        mock_method_info_query = Mock()
        mock_method_info_query.handler_type = "query"

        mock_method_info_command = Mock()
        mock_method_info_command.handler_type = "command"

        mock_tool_def_query = Mock()
        mock_tool_def_query.method_info = mock_method_info_query

        mock_tool_def_command = Mock()
        mock_tool_def_command.method_info = mock_method_info_command

        tools = OpenHFPluginMCPTools()
        tools._initialized = True
        tools.tools = {
            "query_tool": mock_tool_def_query,
            "command_tool": mock_tool_def_command,
        }

        query_tools = tools.get_tools_by_type("query")
        command_tools = tools.get_tools_by_type("command")

        assert query_tools == ["query_tool"]
        assert command_tools == ["command_tool"]

    def test_get_stats_not_initialized(self):
        """Test get_stats when not initialized."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_sdk.initialized = False

        tools = OpenHFPluginMCPTools(sdk=mock_sdk)

        stats = tools.get_stats()

        assert stats["initialized"] is False
        assert stats["tools_discovered"] == 0
        assert stats["sdk_initialized"] is False

    def test_get_stats_initialized(self):
        """Test get_stats when initialized."""
        mock_sdk = Mock(spec=OpenHFPluginSDK)
        mock_sdk.get_stats = Mock(return_value={"sdk_stat": "value"})

        tools = OpenHFPluginMCPTools(sdk=mock_sdk)
        tools._initialized = True
        tools.tools = {"tool1": Mock(), "tool2": Mock()}

        with patch.object(tools, "get_tools_by_type") as mock_get_by_type:
            mock_get_by_type.side_effect = lambda t: (["tool1"] if t == "query" else ["tool2"])

            stats = tools.get_stats()

            assert stats["initialized"] is True
            assert stats["tools_discovered"] == 2
            assert stats["command_tools"] == 1
            assert stats["query_tools"] == 1
            assert stats["available_tools"] == ["tool1", "tool2"]
            assert stats["sdk_stats"] == {"sdk_stat": "value"}

    def test_repr(self):
        """Test string representation."""
        tools = OpenHFPluginMCPTools()

        repr_str = repr(tools)

        assert "OpenHFPluginMCPTools" in repr_str
        assert "not initialized" in repr_str
        assert "tools=0" in repr_str

        # Test initialized state
        tools._initialized = True
        tools.tools = {"tool1": Mock(), "tool2": Mock()}

        repr_str = repr(tools)

        assert "initialized" in repr_str
        assert "tools=2" in repr_str
