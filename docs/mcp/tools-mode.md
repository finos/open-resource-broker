# MCP Tools Mode - Direct Integration

MCP Tools Mode provides direct integration with AI assistants without requiring a separate server process. This is the simplest way to integrate Host Factory operations into AI workflows.

## Overview

MCP Tools Mode automatically discovers all SDK methods and exposes them as MCP tools with appropriate JSON schemas for AI assistant consumption.

## Key Features

- **Automatic Tool Discovery**: All 51+ CQRS handlers automatically exposed as MCP tools
- **Direct Integration**: No separate server process required
- **JSON Schema Generation**: Automatic schema generation for tool parameters
- **Error Handling**: Comprehensive error handling for AI assistant consumption
- **Type Safety**: Full type validation and conversion

## Usage

### Direct Integration in AI Assistants

```python
from ohfpsdk.mcp import OpenHFPluginMCPTools

# Initialize MCP tools
async with OpenHFPluginMCPTools(provider="aws") as tools:
    # List all available tools
    available_tools = tools.list_tools()
    print(f"Available tools: {len(available_tools)}")

    # Call a tool
    result = await tools.call_tool("list_templates", {
        "active_only": True
    })

    if result.get("success"):
        templates = result["data"]
        print(f"Found {len(templates)} templates")
    else:
        print(f"Error: {result.get('error', {}).get('message')}")
```

### CLI Testing and Discovery

```bash
# List all available MCP tools
ohfp mcp tools list

# List only query tools
ohfp mcp tools list --type query

# List only command tools  
ohfp mcp tools list --type command

# Get information about a specific tool
ohfp mcp tools info list_templates

# Call a tool directly for testing
ohfp mcp tools call list_templates --args '{"active_only": true}'

# Call a tool with arguments from file
ohfp mcp tools call create_request --file request_args.json

# Validate MCP configuration
ohfp mcp validate
```

## Tool Discovery

MCP tools are automatically discovered from SDK methods:

```python
async with OpenHFPluginMCPTools() as tools:
    # Get tools by type
    query_tools = tools.get_tools_by_type("query")
    command_tools = tools.get_tools_by_type("command")

    print(f"Query tools: {query_tools}")
    print(f"Command tools: {command_tools}")

    # Get detailed tool information
    tool_info = tools.get_tool_info("list_templates")
    print(f"Tool schema: {tool_info.input_schema}")
```

## Available Tools

All SDK methods are automatically exposed as MCP tools:

### Template Operations
- `list_templates` - List available machine templates
- `get_template` - Get specific template details
- `validate_template` - Validate template configuration

### Machine Operations
- `create_request` - Create new machine provisioning request
- `get_request_status` - Get status of provisioning request
- `list_machines` - List provisioned machines
- `create_return_request` - Return/terminate machines

### Provider Operations
- `get_provider_health` - Check provider health status
- `list_providers` - List available providers
- `get_provider_metrics` - Get provider performance metrics

### System Operations
- `get_system_status` - Get system status information
- `validate_configuration` - Validate system configuration

## Tool Schema Format

Each tool includes a JSON schema for parameter validation:

```json
{
  "name": "list_templates",
  "description": "List Templates - Query operation",
  "inputSchema": {
    "type": "object",
    "properties": {
      "active_only": {
        "type": "boolean",
        "description": "Only return active templates"
      },
      "provider_api": {
        "type": "string", 
        "description": "Filter by provider API type"
      }
    },
    "required": []
  }
}
```

## Response Format

Tool responses follow a consistent format:

### Success Response
```json
{
  "success": true,
  "data": {
    "templates": [
      {
        "templateId": "basic-template",
        "name": "Basic Template",
        "description": "Basic machine template",
        "providerType": "aws"
      }
    ]
  },
  "tool": "list_templates"
}
```

### Error Response
```json
{
  "error": {
    "type": "SDKError",
    "message": "Failed to list templates: Provider not available",
    "tool": "list_templates",
    "arguments": {"active_only": true}
  }
}
```

## Configuration

### Environment Variables
```bash
export OHFP_PROVIDER=aws
export OHFP_REGION=us-east-1
export OHFP_PROFILE=default
```

### Configuration File
```python
# Load MCP tools with custom configuration
tools = OpenHFPluginMCPTools(
    provider="aws",
    config={
        "region": "us-west-2",
        "timeout": 600
    }
)
```

## Error Handling

MCP tools provide comprehensive error handling:

```python
async with OpenHFPluginMCPTools() as tools:
    try:
        result = await tools.call_tool("create_request", {
            "template_id": "invalid-template",
            "machine_count": 5
        })

        if "error" in result:
            error = result["error"]
            print(f"Tool error: {error['message']}")
            print(f"Error type: {error['type']}")
        else:
            print(f"Success: {result['data']}")

    except ValueError as e:
        print(f"Validation error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")
```

## Performance Considerations

- **Initialization**: Tools initialize once and can be reused
- **Context Manager**: Use async context manager for automatic cleanup
- **Caching**: Tool definitions are cached after discovery
- **Concurrent Calls**: Multiple tools can be called concurrently

## Integration Examples

### Claude Desktop Integration
```json
{
  "mcpServers": {
    "hostfactory": {
      "command": "python",
      "args": ["-c", "
        from ohfpsdk.mcp import OpenHFPluginMCPTools
        import asyncio
        import json

        async def main():
            async with OpenHFPluginMCPTools() as tools:
                # Your integration logic here
                pass

        asyncio.run(main())
      "]
    }
  }
}
```

### Custom AI Assistant Integration
```python
class HostFactoryAssistant:
    def __init__(self):
        self.mcp_tools = None

    async def initialize(self):
        self.mcp_tools = OpenHFPluginMCPTools()
        await self.mcp_tools.initialize()

    async def handle_request(self, tool_name: str, args: dict):
        if not self.mcp_tools:
            await self.initialize()

        return await self.mcp_tools.call_tool(tool_name, args)

    async def cleanup(self):
        if self.mcp_tools:
            await self.mcp_tools.cleanup()
```

## Troubleshooting

### Common Issues

1. **Tools not discovered**: Ensure SDK is properly initialized
2. **Tool execution fails**: Check provider configuration and credentials
3. **Schema validation errors**: Verify tool arguments match schema
4. **Performance issues**: Use context manager for resource cleanup

### Debug Mode
```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

async with OpenHFPluginMCPTools() as tools:
    # Debug information
    stats = tools.get_stats()
    print(f"Debug stats: {stats}")
```

## Next Steps

- [MCP Server Mode](server-mode.md) - Standalone server for multiple clients
- [CLI Reference](cli-reference.md) - Complete CLI command reference
- [Integration Examples](examples/) - More integration examples
