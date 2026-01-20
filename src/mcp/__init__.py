"""
Open Resource Broker MCP (Model Context Protocol) Integration.

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
    from orb_py.mcp import OpenResourceBrokerMCPTools

    tools = OpenResourceBrokerMCPTools()
    await tools.initialize()
    result = await tools.call_tool("list_templates", {"active_only": True})

    # Standalone server mode
    from orb_py.mcp import OpenResourceBrokerMCPServer

    server = OpenResourceBrokerMCPServer()
    await server.start_stdio()
"""

from .discovery import MCPToolDiscovery
from .tools import OpenResourceBrokerMCPTools

__all__: list[str] = ["MCPToolDiscovery", "OpenResourceBrokerMCPTools"]
