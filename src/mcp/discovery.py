"""
MCP tool discovery from SDK methods.

Automatically discovers SDK methods and creates MCP tool definitions
with appropriate JSON schemas for AI assistant integration.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sdk.client import OpenHFPluginSDK
from sdk.discovery import MethodInfo


@dataclass
class MCPToolDefinition:
    """Definition of an MCP tool."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    method_name: str
    method_info: Optional[MethodInfo] = None


class MCPToolDiscovery:
    """
    Discovers SDK methods and creates MCP tool definitions.

    Follows the same discovery patterns as SDKMethodDiscovery
    but creates MCP-compatible tool definitions with JSON schemas.
    """

    def __init__(self):
        """Initialize the instance."""
        self._tool_definitions: Dict[str, MCPToolDefinition] = {}

    def discover_mcp_tools(self, sdk: OpenHFPluginSDK) -> Dict[str, MCPToolDefinition]:
        """
        Auto-discover MCP tools from SDK methods.

        Args:
            sdk: Initialized SDK instance

        Returns:
            Dict mapping tool names to MCP tool definitions
        """
        if not sdk.initialized:
            raise ValueError("SDK must be initialized before discovering MCP tools") from e

        tools = {}

        # Get all available SDK methods
        for method_name in sdk.list_available_methods():
            method_info = sdk.get_method_info(method_name)

            # Create MCP tool definition
            tool_def = MCPToolDefinition(
                name=method_name,
                description=self._generate_description(method_name, method_info),
                input_schema=self._generate_schema(method_name, method_info),
                method_name=method_name,
                method_info=method_info,
            )

            tools[method_name] = tool_def

        self._tool_definitions = tools
        return tools

    def get_tool_definition(self, tool_name: str) -> Optional[MCPToolDefinition]:
        """Get MCP tool definition by name."""
        return self._tool_definitions.get(tool_name)

    def list_tool_names(self) -> List[str]:
        """List all discovered tool names."""
        return list(self._tool_definitions.keys())

    def get_tools_list(self) -> List[Dict[str, Any]]:
        """
        Get MCP tools list in the format expected by MCP protocol.

        Returns:
            List of tool definitions for MCP list_tools response
        """
        return [
            {
                "name": tool_def.name,
                "description": tool_def.description,
                "inputSchema": tool_def.input_schema,
            }
            for tool_def in self._tool_definitions.values()
        ]

    def _generate_description(self, method_name: str, method_info: Optional[MethodInfo]) -> str:
        """
        Generate MCP tool description from method info.

        Args:
            method_name: Name of the SDK method
            method_info: Method information from SDK discovery

        Returns:
            Human-readable description for the MCP tool
        """
        if method_info and method_info.description:
            return method_info.description

        # Fallback: generate description from method name
        words = method_name.replace("_", " ").title()
        return f"{words} - Execute {method_name} operation"

    def _generate_schema(
        self, method_name: str, method_info: Optional[MethodInfo]
    ) -> Dict[str, Any]:
        """
        Generate JSON schema for MCP tool from method signature.

        Args:
            method_name: Name of the SDK method
            method_info: Method information from SDK discovery

        Returns:
            JSON schema for the MCP tool input
        """
        if not method_info or not method_info.parameters:
            # Default schema for methods without parameter info
            return {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
                "description": f"Parameters for {method_name} operation",
            }

        # Convert method parameters to JSON schema
        properties = {}
        required = []

        for param_name, param_info in method_info.parameters.items():
            # Convert parameter info to JSON schema property
            prop_schema = self._convert_param_to_schema(param_name, param_info)
            properties[param_name] = prop_schema

            # Add to required if parameter is required
            if param_info.get("required", False):
                required.append(param_name)

        schema = {
            "type": "object",
            "properties": properties,
            "description": f"Parameters for {method_name} operation",
        }

        if required:
            schema["required"] = required

        return schema

    def _convert_param_to_schema(
        self, param_name: str, param_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Convert parameter information to JSON schema property.

        Args:
            param_name: Parameter name
            param_info: Parameter information from method discovery

        Returns:
            JSON schema property definition
        """
        param_type = param_info.get("type", "Any")
        description = param_info.get("description", f"Parameter {param_name}")

        # Basic type mapping
        if param_type == str or "str" in str(param_type):
            return {"type": "string", "description": description}
        elif param_type == int or "int" in str(param_type):
            return {"type": "integer", "description": description}
        elif param_type == float or "float" in str(param_type):
            return {"type": "number", "description": description}
        elif param_type == bool or "bool" in str(param_type):
            return {"type": "boolean", "description": description}
        elif "List" in str(param_type) or "list" in str(param_type):
            return {
                "type": "array",
                "description": description,
                "items": {"type": "string"},  # Default to string items
            }
        elif "Dict" in str(param_type) or "dict" in str(param_type):
            return {
                "type": "object",
                "description": description,
                "additionalProperties": True,
            }
        else:
            # Default to string for unknown types
            return {"type": "string", "description": description}

    def get_stats(self) -> Dict[str, Any]:
        """Get discovery statistics."""
        return {
            "tools_discovered": len(self._tool_definitions),
            "tool_names": list(self._tool_definitions.keys()),
        }
