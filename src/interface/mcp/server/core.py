"""Core MCP Server implementation for Open Host Factory Plugin."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, Union

from _package import PACKAGE_NAME, __version__
from infrastructure.logging.logger import get_logger


class MCPMessageType(Enum):
    """MCP message types according to the specification."""

    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"


@dataclass
class MCPMessage:
    """MCP message structure."""

    jsonrpc: str = "2.0"
    id: Optional[Union[str, int]] = None
    method: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None


class OpenHFPluginMCPServer:
    """
    Full MCP Server implementation for Open Host Factory Plugin.

    Provides Model Context Protocol server functionality, exposing
    CLI commands as MCP tools and domain objects as MCP resources.
    """

    def __init__(self, app=None) -> None:
        """Initialize MCP server with application instance."""
        self.app = app
        self.logger = get_logger(__name__)
        self.tools: Dict[str, Callable] = {}
        self.resources: Dict[str, Callable] = {}
        self.prompts: Dict[str, Dict[str, Any]] = {}
        self.session_id: Optional[str] = None
        self.client_info: Optional[Dict[str, Any]] = None

        # Register built-in tools and resources
        self._register_core_tools()
        self._register_core_resources()
        self._register_core_prompts()

    def _register_core_tools(self) -> None:
        """Register core MCP tools from CLI handlers."""
        from interface.request_command_handlers import (
            handle_get_request_status,
            handle_get_return_requests,
            handle_request_machines,
            handle_request_return_machines,
        )
        from interface.system_command_handlers import (
            handle_list_providers,
            handle_provider_config,
            handle_provider_health,
            handle_provider_metrics,
        )
        from interface.template_command_handlers import (
            handle_get_template,
            handle_list_templates,
            handle_validate_template,
        )

        # System tools
        self.tools["check_provider_health"] = handle_provider_health
        self.tools["list_providers"] = handle_list_providers
        self.tools["get_provider_config"] = handle_provider_config
        self.tools["get_provider_metrics"] = handle_provider_metrics

        # Template tools
        self.tools["list_templates"] = handle_list_templates
        self.tools["get_template"] = handle_get_template
        self.tools["validate_template"] = handle_validate_template

        # Request tools
        self.tools["get_request_status"] = handle_get_request_status
        self.tools["request_machines"] = handle_request_machines
        self.tools["list_return_requests"] = handle_get_return_requests
        self.tools["return_machines"] = handle_request_return_machines

    def _register_core_resources(self) -> None:
        """Register core MCP resources."""
        self.resources["templates"] = self._get_templates_resource
        self.resources["requests"] = self._get_requests_resource
        self.resources["machines"] = self._get_machines_resource
        self.resources["providers"] = self._get_providers_resource

    def _register_core_prompts(self) -> None:
        """Register core MCP prompts for AI assistants."""
        self.prompts = {
            "provision_infrastructure": {
                "name": "provision_infrastructure",
                "description": "Help provision cloud infrastructure using templates",
                "arguments": [
                    {
                        "name": "template_type",
                        "description": "Type of infrastructure to provision (e.g., 'ec2', 'spot_fleet')",
                        "required": True,
                    },
                    {
                        "name": "instance_count",
                        "description": "Number of instances to provision",
                        "required": False,
                    },
                ],
            },
            "troubleshoot_deployment": {
                "name": "troubleshoot_deployment",
                "description": "Help troubleshoot deployment issues",
                "arguments": [
                    {
                        "name": "request_id",
                        "description": "Request ID to troubleshoot",
                        "required": True,
                    }
                ],
            },
            "infrastructure_best_practices": {
                "name": "infrastructure_best_practices",
                "description": "Provide infrastructure deployment best practices",
                "arguments": [
                    {
                        "name": "provider",
                        "description": "Cloud provider (e.g., 'aws')",
                        "required": False,
                    }
                ],
            },
        }

    async def handle_message(self, message: str) -> str:
        """
        Handle incoming MCP message.

        Args:
            message: JSON-RPC 2.0 message string

        Returns:
            JSON-RPC 2.0 response string
        """
        try:
            # Parse message
            data = json.loads(message)
            mcp_msg = MCPMessage(**data)

            # Handle different message types
            if mcp_msg.method:
                response = await self._handle_request(mcp_msg)
            else:
                response = MCPMessage(
                    id=mcp_msg.id, error={"code": -32600, "message": "Invalid Request"}
                )

            return json.dumps(response.__dict__, default=str)

        except json.JSONDecodeError:
            error_response = MCPMessage(error={"code": -32700, "message": "Parse error"})
            return json.dumps(error_response.__dict__)
        except Exception as e:
            self.logger.error("Error handling MCP message: %s", e)
            error_response = MCPMessage(
                id=getattr(mcp_msg, "id", None) if "mcp_msg" in locals() else None,
                error={"code": -32603, "message": f"Internal error: {str(e)}"},
            )
            return json.dumps(error_response.__dict__)

    async def _handle_request(self, message: MCPMessage) -> MCPMessage:
        """Handle MCP request message."""
        method = message.method
        params = message.params or {}

        try:
            if method == "initialize":
                result = await self._handle_initialize(params)
            elif method == "tools/list":
                result = await self._handle_tools_list(params)
            elif method == "tools/call":
                result = await self._handle_tools_call(params)
            elif method == "resources/list":
                result = await self._handle_resources_list(params)
            elif method == "resources/read":
                result = await self._handle_resources_read(params)
            elif method == "prompts/list":
                result = await self._handle_prompts_list(params)
            elif method == "prompts/get":
                result = await self._handle_prompts_get(params)
            else:
                return MCPMessage(
                    id=message.id,
                    error={"code": -32601, "message": f"Method not found: {method}"},
                )

            return MCPMessage(id=message.id, result=result)

        except Exception as e:
            self.logger.error("Error handling method %s: %s", method, e)
            return MCPMessage(
                id=message.id,
                error={"code": -32603, "message": f"Internal error: {str(e)}"},
            )

    async def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle MCP initialize request."""
        self.client_info = params.get("clientInfo", {})
        self.session_id = params.get("sessionId")

        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"subscribe": True, "listChanged": True},
                "prompts": {"listChanged": True},
            },
            "serverInfo": {
                "name": PACKAGE_NAME,
                "version": __version__,
                "description": "MCP server for Open Host Factory Plugin - Cloud infrastructure provisioning",
            },
        }

    async def _handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/list request."""
        tools_list = []

        for tool_name, tool_func in self.tools.items():
            # Get tool metadata from function docstring and signature
            description = (
                getattr(tool_func, "__doc__", f"Execute {tool_name} operation")
                or f"Execute {tool_name} operation"
            )

            tool_def = {
                "name": tool_name,
                # First line of docstring
                "description": description.strip().split("\n")[0],
                "inputSchema": {
                    "type": "object",
                    "properties": self._get_tool_schema(tool_name),
                    "required": [],
                },
            }
            tools_list.append(tool_def)

        return {"tools": tools_list}

    async def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/call request."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")

        # Convert arguments to args-like object
        args = type("Args", (), arguments)()

        # Call the tool function
        tool_func = self.tools[tool_name]
        result = await tool_func(args, self.app)

        return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}

    async def _handle_resources_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle resources/list request."""
        resources_list = [
            {
                "uri": "templates://",
                "name": "Templates",
                "description": "Available compute templates",
                "mimeType": "application/json",
            },
            {
                "uri": "requests://",
                "name": "Requests",
                "description": "Provisioning requests",
                "mimeType": "application/json",
            },
            {
                "uri": "machines://",
                "name": "Machines",
                "description": "Compute instances",
                "mimeType": "application/json",
            },
            {
                "uri": "providers://",
                "name": "Providers",
                "description": "Cloud providers",
                "mimeType": "application/json",
            },
        ]

        return {"resources": resources_list}

    async def _handle_resources_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle resources/read request."""
        uri = params.get("uri", "")

        if uri.startswith("templates://"):
            content = await self._get_templates_resource(uri)
        elif uri.startswith("requests://"):
            content = await self._get_requests_resource(uri)
        elif uri.startswith("machines://"):
            content = await self._get_machines_resource(uri)
        elif uri.startswith("providers://"):
            content = await self._get_providers_resource(uri)
        else:
            raise ValueError(f"Unknown resource URI: {uri}")

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(content, indent=2, default=str),
                }
            ]
        }

    async def _handle_prompts_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle prompts/list request."""
        prompts_list = [
            {
                "name": prompt_name,
                "description": prompt_data["description"],
                "arguments": prompt_data.get("arguments", []),
            }
            for prompt_name, prompt_data in self.prompts.items()
        ]

        return {"prompts": prompts_list}

    async def _handle_prompts_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle prompts/get request."""
        prompt_name = params.get("name")
        arguments = params.get("arguments", {})

        if prompt_name not in self.prompts:
            raise ValueError(f"Unknown prompt: {prompt_name}")

        # Generate prompt content based on the prompt type
        if prompt_name == "provision_infrastructure":
            content = self._generate_provision_prompt(arguments)
        elif prompt_name == "troubleshoot_deployment":
            content = self._generate_troubleshoot_prompt(arguments)
        elif prompt_name == "infrastructure_best_practices":
            content = self._generate_best_practices_prompt(arguments)
        else:
            content = f"Prompt for {prompt_name} with arguments: {arguments}"

        return {
            "description": self.prompts[prompt_name]["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": content}}],
        }

    def _get_tool_schema(self, tool_name: str) -> Dict[str, Any]:
        """Get JSON schema for tool parameters."""
        # Basic schema - could be improved with actual parameter introspection
        common_props = {
            "template_id": {"type": "string", "description": "Template identifier"},
            "request_id": {"type": "string", "description": "Request identifier"},
            "count": {"type": "integer", "description": "Number of instances"},
            "provider": {"type": "string", "description": "Provider name"},
        }

        if "template" in tool_name:
            return {"template_id": common_props["template_id"]}
        elif "request" in tool_name:
            return {"request_id": common_props["request_id"]}
        elif "machine" in tool_name:
            return {
                "template_id": common_props["template_id"],
                "count": common_props["count"],
            }
        else:
            return {"provider": common_props["provider"]}

    async def _get_templates_resource(self, uri: str) -> Dict[str, Any]:
        """Get templates resource data."""
        # Use the list_templates tool to get data
        args = type("Args", (), {})()
        result = await self.tools["list_templates"](args, self.app)
        return result

    async def _get_requests_resource(self, uri: str) -> Dict[str, Any]:
        """Get requests resource data."""
        args = type("Args", (), {})()
        result = await self.tools["list_return_requests"](args, self.app)
        return result

    async def _get_machines_resource(self, uri: str) -> Dict[str, Any]:
        """Get machines resource data."""
        # For now, return empty list - would need actual machine listing
        return {"machines": [], "message": "Machine listing not yet implemented"}

    async def _get_providers_resource(self, uri: str) -> Dict[str, Any]:
        """Get providers resource data."""
        args = type("Args", (), {})()
        result = await self.tools["list_providers"](args, self.app)
        return result

    def _generate_provision_prompt(self, arguments: Dict[str, Any]) -> str:
        """Generate infrastructure provisioning prompt."""
        template_type = arguments.get("template_type", "ec2")
        instance_count = arguments.get("instance_count", 1)

        return f"""I need to provision {instance_count} {template_type} instance(s) using the Open Host Factory Plugin.

Please help me:
1. List available templates for {template_type}
2. Select the most appropriate template
3. Create a provisioning request
4. Monitor the request status

Use the available MCP tools to accomplish this task."""

    def _generate_troubleshoot_prompt(self, arguments: Dict[str, Any]) -> str:
        """Generate troubleshooting prompt."""
        request_id = arguments.get("request_id", "unknown")

        return f"""I need help troubleshooting a deployment issue with request ID: {request_id}

Please help me:
1. Check the current status of the request
2. Identify any error conditions
3. Suggest remediation steps
4. Check provider health if needed

Use the available MCP tools to diagnose the issue."""

    def _generate_best_practices_prompt(self, arguments: Dict[str, Any]) -> str:
        """Generate best practices prompt."""
        provider = arguments.get("provider", "aws")

        return f"""Please provide infrastructure deployment best practices for {provider} using the Open Host Factory Plugin.

Cover topics like:
1. Template selection and configuration
2. Resource sizing and optimization
3. Security considerations
4. Monitoring and maintenance
5. Cost optimization strategies

Use the available MCP tools to gather current configuration information."""
