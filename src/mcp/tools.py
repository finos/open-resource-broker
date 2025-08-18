"""
MCP tools implementation for direct AI assistant integration.

Provides MCP tools that can be directly integrated into AI assistants
without requiring a separate server process.
"""

from typing import Any, Dict, List, Optional

from sdk.client import OpenHFPluginSDK

from .discovery import MCPToolDefinition, MCPToolDiscovery


class OpenHFPluginMCPTools:
    """
    MCP tools for direct AI assistant integration.

    Provides all SDK methods as MCP tools with automatic discovery
    and structured error handling for AI assistant consumption.
    """

    def __init__(self, sdk: Optional[OpenHFPluginSDK] = None, **sdk_kwargs):
        """
        Initialize MCP tools.

        Args:
            sdk: Optional SDK instance (will create if not provided)
            **sdk_kwargs: Arguments for SDK initialization if sdk not provided
        """
        self.sdk = sdk or OpenHFPluginSDK(**sdk_kwargs)
        self.discovery = MCPToolDiscovery()
        self.tools: Dict[str, MCPToolDefinition] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """
        Initialize MCP tools and discover all available tools.

        Raises:
            SDKError: If SDK initialization fails
            ValueError: If tool discovery fails
        """
        if self._initialized:
            return

        # Initialize SDK if not already initialized
        if not self.sdk.initialized:
            await self.sdk.initialize()

        # Discover all MCP tools from SDK methods
        self.tools = self.discovery.discover_mcp_tools(self.sdk)

        self._initialized = True

    async def cleanup(self) -> None:
        """Clean up resources."""
        if self.sdk:
            await self.sdk.cleanup()
        self._initialized = False
        self.tools.clear()

    # Context manager support
    async def __aenter__(self) -> "OpenHFPluginMCPTools":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.cleanup()

    def list_tools(self) -> List[Dict[str, Any]]:
        """
        List all available MCP tools.

        Returns:
            List of tool definitions in MCP format

        Raises:
            ValueError: If tools not initialized
        """
        if not self._initialized:
            raise ValueError(
                "MCP tools not initialized. Call initialize() or use as context manager."
            )

        return self.discovery.get_tools_list()

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call an MCP tool by name.

        Args:
            name: Tool name to call
            arguments: Tool arguments

        Returns:
            Tool execution result in MCP format

        Raises:
            ValueError: If tool not found or not initialized
            SDKError: If tool execution fails
        """
        if not self._initialized:
            raise ValueError(
                "MCP tools not initialized. Call initialize() or use as context manager."
            )

        if name not in self.tools:
            available_tools = list(self.tools.keys())
            raise ValueError(f"Unknown tool: {name}. Available tools: {available_tools}") from e

        tool_def = self.tools[name]

        try:
            # Get the SDK method
            if not hasattr(self.sdk, tool_def.method_name):
                raise ValueError(f"SDK method {tool_def.method_name} not found") from e

            method = getattr(self.sdk, tool_def.method_name)

            # Execute the method
            result = await method(**arguments)

            # Convert result to MCP-compatible format
            return self._format_result(result, name)

        except Exception as e:
            # Return error in MCP format
            return {
                "error": {
                    "type": type(e).__name__,
                    "message": str(e),
                    "tool": name,
                    "arguments": arguments,
                }
            }

    def get_tool_info(self, name: str) -> Optional[MCPToolDefinition]:
        """
        Get information about a specific tool.

        Args:
            name: Tool name

        Returns:
            Tool definition or None if not found
        """
        if not self._initialized:
            return None

        return self.tools.get(name)

    def get_tools_by_type(self, handler_type: str) -> List[str]:
        """
        Get tools filtered by handler type.

        Args:
            handler_type: 'command' or 'query'

        Returns:
            List of tool names for the specified handler type
        """
        if not self._initialized:
            return []

        tools = []
        for tool_name, tool_def in self.tools.items():
            if tool_def.method_info and tool_def.method_info.handler_type == handler_type:
                tools.append(tool_name)

        return tools

    def _format_result(self, result: Any, tool_name: str) -> Dict[str, Any]:
        """
        Format SDK result for MCP consumption.

        Args:
            result: Result from SDK method execution
            tool_name: Name of the tool that was executed

        Returns:
            MCP-formatted result
        """
        try:
            # Handle different result types
            if hasattr(result, "to_dict"):
                # Object with to_dict method
                return {"success": True, "data": result.to_dict(), "tool": tool_name}
            elif isinstance(result, (list, dict, str, int, float, bool)):
                # Basic JSON-serializable types
                return {"success": True, "data": result, "tool": tool_name}
            else:
                # Try to convert to string
                return {
                    "success": True,
                    "data": str(result),
                    "tool": tool_name,
                    "note": f"Result converted to string (type: {type(result).__name__})",
                }

        except Exception as e:
            # Fallback error handling
            return {
                "error": {
                    "type": "ResultFormattingError",
                    "message": f"Failed to format result: {str(e)}",
                    "tool": tool_name,
                    "result_type": type(result).__name__,
                }
            }

    @property
    def initialized(self) -> bool:
        """Check if MCP tools are initialized."""
        return self._initialized

    def get_stats(self) -> Dict[str, Any]:
        """
        Get MCP tools statistics.

        Returns:
            Dictionary with statistics and information
        """
        if not self._initialized:
            return {
                "initialized": False,
                "tools_discovered": 0,
                "sdk_initialized": self.sdk.initialized if self.sdk else False,
            }

        command_tools = self.get_tools_by_type("command")
        query_tools = self.get_tools_by_type("query")

        return {
            "initialized": True,
            "tools_discovered": len(self.tools),
            "command_tools": len(command_tools),
            "query_tools": len(query_tools),
            "available_tools": list(self.tools.keys()),
            "sdk_stats": self.sdk.get_stats() if self.sdk else {},
        }

    def __repr__(self) -> str:
        """Return string representation of MCP tools instance."""
        status = "initialized" if self._initialized else "not initialized"
        tool_count = len(self.tools) if self._initialized else 0
        return f"OpenHFPluginMCPTools(status='{status}', tools={tool_count})"
