"""
OpenHFPlugin MCP (Model Context Protocol) Integration.

This package provides MCP integration for AI assistants and agents,
supporting both direct tool integration and standalone server modes.

Key Features:
- Automatic tool discovery from SDK methods
- Direct integration mode for AI assistants
- Standalone server mode with JSON-RPC protocol
- Zero code duplication - leverages existing SDK infrastructure
- Full MCP protocol compliance

Usage:
    # Direct tool integration
    from orbsdk.mcp import OpenHFPluginMCPTools

    tools = OpenHFPluginMCPTools()
    await tools.initialize()
    result = await tools.call_tool("list_templates", {"active_only": True})

    # Standalone server mode
    from orbsdk.mcp import OpenHFPluginMCPServer

    server = OpenHFPluginMCPServer()
    await server.start_stdio()
"""

from .discovery import MCPToolDiscovery
from .tools import OpenHFPluginMCPTools

__all__: list[str] = ["MCPToolDiscovery", "OpenHFPluginMCPTools"]
