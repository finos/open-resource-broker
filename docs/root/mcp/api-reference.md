# MCP API Reference

This document provides a complete reference for the Open Host Factory Plugin MCP server API.

## Protocol Information

- **Protocol**: Model Context Protocol (MCP)
- **Version**: 2024-11-05
- **Transport**: JSON-RPC 2.0
- **Server Name**: open-hostfactory-plugin
- **Server Version**: 1.0.0

## Server Capabilities

```json
{
  "capabilities": {
    "tools": {
      "listChanged": true
    },
    "resources": {
      "subscribe": true,
      "listChanged": true
    },
    "prompts": {
      "listChanged": true
    }
  }
}
```

## Methods

### initialize

Initialize the MCP session.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "clientInfo": {
      "name": "client-name",
      "version": "1.0.0"
    }
  }
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": {"listChanged": true},
      "resources": {"subscribe": true, "listChanged": true},
      "prompts": {"listChanged": true}
    },
    "serverInfo": {
      "name": "open-hostfactory-plugin",
      "version": "1.0.0",
      "description": "MCP server for Open Host Factory Plugin - Cloud infrastructure provisioning"
    }
  }
}
```

### tools/list

List available tools.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": {}
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "check_provider_health",
        "description": "Handle provider health operations.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "provider": {
              "type": "string",
              "description": "Provider name"
            }
          },
          "required": []
        }
      }
    ]
  }
}
```

### tools/call

Call a specific tool.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "list_providers",
    "arguments": {}
  }
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"providers\": [{\"name\": \"aws\", \"status\": \"active\"}], \"count\": 1}"
      }
    ]
  }
}
```

### resources/list

List available resources.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "resources/list",
  "params": {}
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "resources": [
      {
        "uri": "templates://",
        "name": "Templates",
        "description": "Available compute templates",
        "mimeType": "application/json"
      },
      {
        "uri": "requests://",
        "name": "Requests",
        "description": "Provisioning requests",
        "mimeType": "application/json"
      },
      {
        "uri": "machines://",
        "name": "Machines",
        "description": "Compute instances",
        "mimeType": "application/json"
      },
      {
        "uri": "providers://",
        "name": "Providers",
        "description": "Cloud providers",
        "mimeType": "application/json"
      }
    ]
  }
}
```

### resources/read

Read a specific resource.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "resources/read",
  "params": {
    "uri": "templates://"
  }
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "contents": [
      {
        "uri": "templates://",
        "mimeType": "application/json",
        "text": "{\"templates\": [...], \"count\": 9}"
      }
    ]
  }
}
```

### prompts/list

List available prompts.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "prompts/list",
  "params": {}
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "prompts": [
      {
        "name": "provision_infrastructure",
        "description": "Help provision cloud infrastructure using templates",
        "arguments": [
          {
            "name": "template_type",
            "description": "Type of infrastructure to provision (e.g., 'ec2', 'spot_fleet')",
            "required": true
          },
          {
            "name": "instance_count",
            "description": "Number of instances to provision",
            "required": false
          }
        ]
      }
    ]
  }
}
```

### prompts/get

Get a specific prompt.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "prompts/get",
  "params": {
    "name": "provision_infrastructure",
    "arguments": {
      "template_type": "ec2",
      "instance_count": 3
    }
  }
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "description": "Help provision cloud infrastructure using templates",
    "messages": [
      {
        "role": "user",
        "content": {
          "type": "text",
          "text": "I need to provision 3 ec2 instance(s) using the Open Host Factory Plugin..."
        }
      }
    ]
  }
}
```

## Tools Reference

### Provider Management Tools

#### check_provider_health
Check cloud provider health status.

**Parameters**:
- `provider` (optional): Specific provider name

**Returns**: Provider health information

#### list_providers
List available cloud providers.

**Parameters**: None

**Returns**: List of configured providers

#### get_provider_config
Get provider configuration details.

**Parameters**:
- `provider` (optional): Specific provider name

**Returns**: Provider configuration

#### get_provider_metrics
Get provider performance metrics.

**Parameters**:
- `provider` (optional): Specific provider name

**Returns**: Provider metrics

### Template Operations Tools

#### list_templates
List available compute templates.

**Parameters**:
- `template_id` (optional): Specific template ID

**Returns**: List of templates

#### get_template
Get specific template details.

**Parameters**:
- `template_id` (required): Template identifier

**Returns**: Template details

#### validate_template
Validate template configuration.

**Parameters**:
- `template_id` (required): Template identifier

**Returns**: Validation results

### Infrastructure Request Tools

#### request_machines
Request new compute instances.

**Parameters**:
- `template_id` (required): Template to use
- `count` (required): Number of instances

**Returns**: Request details

#### get_request_status
Check provisioning request status.

**Parameters**:
- `request_id` (required): Request identifier

**Returns**: Request status

#### list_return_requests
List machine return requests.

**Parameters**: None

**Returns**: List of return requests

#### return_machines
Return compute instances.

**Parameters**:
- `template_id` (required): Template identifier
- `count` (required): Number of instances to return

**Returns**: Return request details

## Resources Reference

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

## Prompts Reference

### provision_infrastructure
Guide infrastructure provisioning workflows.

**Arguments**:
- `template_type` (required): Infrastructure type
- `instance_count` (optional): Number of instances

**Generated Prompt**: Provides step-by-step guidance for provisioning infrastructure

### troubleshoot_deployment
Help diagnose deployment issues.

**Arguments**:
- `request_id` (required): Request ID to troubleshoot

**Generated Prompt**: Provides troubleshooting steps for deployment issues

### infrastructure_best_practices
Provide deployment best practices.

**Arguments**:
- `provider` (optional): Cloud provider

**Generated Prompt**: Provides best practices for infrastructure deployment

## Error Codes

### Standard JSON-RPC Errors
- `-32700`: Parse error
- `-32600`: Invalid Request
- `-32601`: Method not found
- `-32602`: Invalid params
- `-32603`: Internal error

### Application-Specific Errors
- `1001`: Unknown tool
- `1002`: Tool execution failed
- `1003`: Unknown resource URI
- `1004`: Resource access failed
- `1005`: Unknown prompt
- `1006`: Prompt generation failed

## Rate Limiting

The MCP server does not implement rate limiting by default. Clients should implement appropriate throttling based on their use case.

## Versioning

The MCP server follows semantic versioning. Breaking changes will increment the major version number.

Current version: `1.0.0`
