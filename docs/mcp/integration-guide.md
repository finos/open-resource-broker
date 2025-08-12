# MCP Integration Guide

This guide explains how to integrate the Open Host Factory Plugin with AI assistants using the Model Context Protocol (MCP).

## Overview

The Open Host Factory Plugin provides direct MCP integration through its tools implementation, allowing AI assistants to:

1. Discover available cloud infrastructure operations
2. Execute infrastructure provisioning commands
3. Access resource information
4. Provide infrastructure guidance

## Integration Methods

### Direct Tools Integration

The Open Host Factory Plugin implements direct MCP tools integration without requiring a separate server process:

```python
from ohfpsdk.mcp import OpenHFPluginMCPTools

async with OpenHFPluginMCPTools(provider="aws") as mcp_tools:
    # List available tools
    tools = mcp_tools.list_tools()

    # Call a specific tool
    result = await mcp_tools.call_tool(
        "list_templates", 
        {"active_only": True}
    )
```

### CLI-Based Integration

For AI assistants that support external tool execution, you can use the CLI-based MCP integration:

```bash
# Start MCP server in stdio mode (recommended for AI assistants)
ohfp mcp serve --stdio

# Start MCP server as TCP server (for development/testing)
ohfp mcp serve --port 3000 --host localhost
```

## Available MCP Tools

The MCP integration exposes the following tools:

### Provider Management Tools

- `check_provider_health`: Check cloud provider health status
- `list_providers`: List available cloud providers
- `get_provider_config`: Get provider configuration details
- `get_provider_metrics`: Get provider performance metrics

### Template Operations Tools

- `list_templates`: List available compute templates
- `get_template`: Get specific template details
- `validate_template`: Validate template configuration

### Infrastructure Request Tools

- `request_machines`: Request new compute instances
- `get_request_status`: Check provisioning request status
- `list_return_requests`: List machine return requests
- `return_machines`: Return compute instances

## MCP Resources

The MCP integration provides access to the following resources:

### templates://

Access available compute templates.

**URI Pattern**: `templates://[template-id]`

**Content Type**: `application/json`

**Structure**:
```json
{
  "templates": [
    {
      "id": "template-id",
      "provider": "provider-name",
      "instances": 2,
      "type": "instant|maintain|request"
    }
  ],
  "count": 1
}
```

### requests://

Access provisioning requests.

**URI Pattern**: `requests://[request-id]`

**Content Type**: `application/json`

**Structure**:
```json
{
  "requests": [
    {
      "id": "request-id",
      "status": "pending|running|completed|failed",
      "template": "template-id",
      "count": 3,
      "created": "2024-01-01T00:00:00Z"
    }
  ],
  "count": 1
}
```

### machines://

Access compute instances.

**URI Pattern**: `machines://[machine-id]`

**Content Type**: `application/json`

**Structure**:
```json
{
  "machines": [
    {
      "id": "machine-id",
      "status": "running|stopped|terminated",
      "template": "template-id",
      "provider": "provider-name",
      "created": "2024-01-01T00:00:00Z"
    }
  ],
  "count": 1
}
```

### providers://

Access cloud providers.

**URI Pattern**: `providers://[provider-name]`

**Content Type**: `application/json`

**Structure**:
```json
{
  "providers": [
    {
      "name": "provider-name",
      "type": "cloud",
      "status": "active|inactive|error",
      "capabilities": ["ec2", "spot_fleet", "auto_scaling"],
      "region": "us-east-1"
    }
  ],
  "count": 1
}
```

## AI Assistant Integration Examples

### Claude Desktop Configuration

```json
{
  "mcpServers": {
    "open-hostfactory": {
      "command": "ohfp",
      "args": ["mcp", "serve", "--stdio"]
    }
  }
}
```

### Python MCP Client

```python
import asyncio
from mcp import ClientSession, StdioServerParameters

async def use_hostfactory():
    server_params = StdioServerParameters(
        command="ohfp", 
        args=["mcp", "serve", "--stdio"]
    )

    async with ClientSession(server_params) as session:
        # List available tools
        tools = await session.list_tools()

        # Request infrastructure
        result = await session.call_tool(
            "request_machines",
            {"template_id": "EC2FleetInstant", "count": 3}
        )

        print(f"Request result: {result}")
```

### OpenAI Function Calling

```python
import openai
import json
import subprocess

# Define the function schema
functions = [
    {
        "name": "request_machines",
        "description": "Request new compute instances",
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {
                    "type": "string",
                    "description": "Template to use"
                },
                "count": {
                    "type": "integer",
                    "description": "Number of instances"
                }
            },
            "required": ["template_id", "count"]
        }
    }
]

# Call the OpenAI API with function calling
response = openai.ChatCompletion.create(
    model="gpt-4",
    messages=[
        {"role": "user", "content": "I need 3 EC2 instances for my project."}
    ],
    functions=functions,
    function_call="auto"
)

# Extract the function call
function_call = response.choices[0].message.function_call
if function_call and function_call.name == "request_machines":
    args = json.loads(function_call.arguments)

    # Execute the MCP tool via CLI
    result = subprocess.run(
        ["ohfp", "mcp", "call", "request_machines", 
         "--args", json.dumps(args)],
        capture_output=True, text=True
    )

    print(f"Machines requested: {result.stdout}")
```

## Error Handling

The MCP integration provides standardized error handling:

```json
{
  "error": true,
  "error_type": "validation_error",
  "message": "Invalid template ID: template-id-not-found",
  "details": {
    "available_templates": ["aws-basic", "aws-spot"]
  }
}
```

## Best Practices

1. **Tool Discovery**: Always use tool discovery to get the latest available tools
2. **Error Handling**: Implement proper error handling for all tool calls
3. **Resource Access**: Use resource URIs for efficient data access
4. **Caching**: Cache resource data when appropriate to reduce API calls
5. **Validation**: Validate inputs before making tool calls

## Troubleshooting

### Common Issues

1. **Tool Not Found**: Ensure the tool name is correct and the MCP server is running
2. **Invalid Arguments**: Check the tool's parameter requirements
3. **Connection Issues**: Verify the MCP server is running and accessible
4. **Permission Issues**: Ensure the user has the necessary permissions

### Debugging

For debugging MCP integration issues:

```bash
# Enable debug logging
ohfp mcp serve --stdio --log-level DEBUG

# Test a specific tool call
ohfp mcp call list_templates --args '{"active_only": true}'
```