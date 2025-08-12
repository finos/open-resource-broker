# API Reference

This document provides a comprehensive API reference for the Open Host Factory Plugin, covering CLI commands, SDK methods, and REST API endpoints.

## Command Line Interface (CLI)

The Open Host Factory Plugin provides a resource-action command structure for all operations.

### Global Options

These options can be used with any command:

```bash
--config FILE         Configuration file path
--log-level LEVEL     Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
--format FORMAT       Output format (json, yaml, table, list)
--output FILE         Output file (default: stdout)
--quiet               Suppress non-essential output
--verbose             Enable verbose output
--dry-run             Show what would be done without executing
--scheduler STRATEGY  Override scheduler strategy (default, hostfactory, hf)
--version             Show version and exit
```

### Template Management

#### List Templates

```bash
ohfp templates list [--provider-api PROVIDER] [--long] [--format FORMAT]
```

**Options:**
- `--provider-api`: Filter templates by provider API type
- `--long`: Include detailed configuration fields
- `--format`: Output format (json, yaml, table, list)

**Example:**
```bash
ohfp templates list --format table
ohfp templates list --format json
```

**Response:**
```json
{
  "success": true,
  "templates": [
    {
      "template_id": "aws-basic",
      "name": "Basic AWS Template",
      "provider_api": "aws",
      "image_id": "ami-12345678",
      "instance_type": "t3.medium"
    }
  ],
  "total_count": 1,
  "message": "Retrieved 1 templates successfully"
}
```

#### Show Template

```bash
ohfp templates show TEMPLATE_ID [--format FORMAT]
```

**Options:**
- `--format`: Output format (json, yaml, table, list)

**Example:**
```bash
ohfp templates show aws-basic
```

**Response:**
```json
{
  "success": true,
  "template": {
    "template_id": "aws-basic",
    "name": "Basic AWS Template",
    "provider_api": "aws",
    "image_id": "ami-12345678",
    "instance_type": "t3.medium",
    "key_name": "my-keypair",
    "security_group_ids": ["sg-12345678"],
    "subnet_ids": ["subnet-12345678"],
    "tags": {
      "Environment": "production"
    }
  },
  "message": "Retrieved template aws-basic successfully"
}
```

#### Create Template

```bash
ohfp templates create --file FILE [--validate-only]
```

**Options:**
- `--file`: Template configuration file (JSON or YAML)
- `--validate-only`: Only validate, do not create

**Example:**
```bash
ohfp templates create --file new-template.json
```

**Response:**
```json
{
  "success": true,
  "message": "Template created successfully",
  "template_id": "new-aws-template"
}
```

#### Update Template

```bash
ohfp templates update TEMPLATE_ID --file FILE
```

**Options:**
- `--file`: Updated template configuration file (JSON or YAML)

**Example:**
```bash
ohfp templates update aws-basic --file updated-template.json
```

**Response:**
```json
{
  "success": true,
  "message": "Template updated successfully",
  "template_id": "aws-basic"
}
```

#### Delete Template

```bash
ohfp templates delete TEMPLATE_ID [--force]
```

**Options:**
- `--force`: Force deletion without confirmation

**Example:**
```bash
ohfp templates delete old-template
```

**Response:**
```json
{
  "success": true,
  "message": "Template deleted successfully",
  "template_id": "old-template"
}
```

#### Validate Template

```bash
ohfp templates validate --file FILE
```

**Options:**
- `--file`: Template file to validate

**Example:**
```bash
ohfp templates validate --file template.json
```

**Response:**
```json
{
  "success": true,
  "valid": true,
  "validation_errors": [],
  "validation_warnings": [],
  "template_id": "my-template",
  "message": "Validation completed"
}
```

#### Refresh Templates

```bash
ohfp templates refresh [--force]
```

**Options:**
- `--force`: Force complete refresh

**Example:**
```bash
ohfp templates refresh
```

**Response:**
```json
{
  "success": true,
  "message": "Templates refreshed successfully",
  "template_count": 5,
  "cache_stats": {
    "cache_hits": 0,
    "cache_misses": 5,
    "files_loaded": 3
  }
}
```

### Machine Management

#### List Machines

```bash
ohfp machines list [--status STATUS] [--template-id TEMPLATE_ID] [--format FORMAT]
```

**Options:**
- `--status`: Filter by machine status
- `--template-id`: Filter by template ID
- `--format`: Output format (json, yaml, table, list)

**Example:**
```bash
ohfp machines list --format table
```

#### Show Machine

```bash
ohfp machines show MACHINE_ID [--format FORMAT]
```

**Options:**
- `--format`: Output format (json, yaml, table, list)

**Example:**
```bash
ohfp machines show i-1234567890abcdef0
```

#### Request Machines

```bash
ohfp machines request TEMPLATE_ID COUNT [--wait] [--timeout SECONDS]
```

**Options:**
- `--wait`: Wait for machines to be ready
- `--timeout`: Wait timeout in seconds (default: 300)

**Example:**
```bash
ohfp machines request aws-basic 5
```

**Response:**
```json
{
  "success": true,
  "request_id": "req-12345678-1234-1234-1234-123456789012",
  "message": "Request submitted successfully",
  "machines": [
    {
      "machine_id": "i-1234567890abcdef0",
      "status": "pending"
    }
  ]
}
```

#### Return Machines

```bash
ohfp machines return MACHINE_ID [MACHINE_ID ...] [--force]
```

**Options:**
- `--force`: Force return without confirmation

**Example:**
```bash
ohfp machines return i-1234567890abcdef0
```

**Response:**
```json
{
  "success": true,
  "message": "Return request submitted successfully",
  "request_id": "ret-12345678-1234-1234-1234-123456789012"
}
```

#### Check Machine Status

```bash
ohfp machines status MACHINE_ID [MACHINE_ID ...]
```

**Example:**
```bash
ohfp machines status i-1234567890abcdef0
```

**Response:**
```json
{
  "success": true,
  "machines": [
    {
      "machine_id": "i-1234567890abcdef0",
      "status": "running",
      "ip_address": "10.0.1.100"
    }
  ]
}
```

### Request Management

#### List Requests

```bash
ohfp requests list [--status STATUS] [--template-id TEMPLATE_ID] [--format FORMAT]
```

**Options:**
- `--status`: Filter by request status (pending, running, completed, failed)
- `--template-id`: Filter by template ID
- `--format`: Output format (json, yaml, table, list)

**Example:**
```bash
ohfp requests list --status pending
```

#### Show Request

```bash
ohfp requests show REQUEST_ID [--format FORMAT]
```

**Options:**
- `--format`: Output format (json, yaml, table, list)

**Example:**
```bash
ohfp requests show req-12345678-1234-1234-1234-123456789012
```

#### Cancel Request

```bash
ohfp requests cancel REQUEST_ID [--force]
```

**Options:**
- `--force`: Force cancellation

**Example:**
```bash
ohfp requests cancel req-12345678-1234-1234-1234-123456789012
```

#### Check Request Status

```bash
ohfp requests status REQUEST_ID [REQUEST_ID ...]
```

**Example:**
```bash
ohfp requests status req-12345678-1234-1234-1234-123456789012
```

**Response:**
```json
{
  "success": true,
  "request_id": "req-12345678-1234-1234-1234-123456789012",
  "status": "completed",
  "machines": [
    {
      "machine_id": "i-1234567890abcdef0",
      "status": "running",
      "ip_address": "10.0.1.100"
    }
  ]
}
```

### System Operations

#### System Status

```bash
ohfp system status [--format FORMAT]
```

**Options:**
- `--format`: Output format (json, yaml, table, list)

**Example:**
```bash
ohfp system status
```

#### System Health

```bash
ohfp system health [--detailed]
```

**Options:**
- `--detailed`: Show detailed health information

**Example:**
```bash
ohfp system health
```

#### System Metrics

```bash
ohfp system metrics
```

**Example:**
```bash
ohfp system metrics
```

## SDK Interface

The OpenHFPlugin SDK provides a clean, async-first programmatic interface for cloud resource provisioning operations.

### Basic Usage

```python
from ohfpsdk import OHFPSDK

async with OHFPSDK(provider="aws") as sdk:
    # List available templates
    templates = await sdk.list_templates(active_only=True)
    print(f"Found {len(templates)} templates")

    # Create machines
    if templates:
        request = await sdk.create_request(
            template_id=templates[0].template_id,
            machine_count=5
        )
        print(f"Created request: {request.id}")

        # Check status
        status = await sdk.get_request_status(request_id=request.id)
        print(f"Request status: {status}")
```

### Configuration

```python
# Environment variables
# OHFP_PROVIDER=aws
# OHFP_REGION=us-east-1
# OHFP_PROFILE=default
# OHFP_TIMEOUT=300
# OHFP_LOG_LEVEL=INFO

# Configuration dictionary
config = {
    "provider": "aws",
    "region": "us-west-2", 
    "timeout": 600,
    "log_level": "DEBUG"
}

async with OHFPSDK(config=config) as sdk:
    # Use SDK with custom configuration
    pass

# Configuration file
async with OHFPSDK(config_path="config.json") as sdk:
    pass
```

### Common Operations

#### Template Management

```python
# List all templates
templates = await sdk.list_templates()

# List only active templates
active_templates = await sdk.list_templates(active_only=True)

# Get specific template
template = await sdk.get_template(template_id="my-template")
```

#### Machine Provisioning

```python
# Create machine request
request = await sdk.create_request(
    template_id="basic-template",
    machine_count=3,
    timeout=1800
)

# Monitor request status
status = await sdk.get_request_status(request_id=request.id)

# Return machines when done
await sdk.create_return_request(machine_ids=[...])
```

#### Provider Operations

```python
# Check provider health
health = await sdk.get_provider_health()

# List available providers
providers = await sdk.list_providers()
```

### Error Handling

```python
from ohfpsdk import OHFPSDK, SDKError, ConfigurationError, ProviderError

try:
    async with OHFPSDK(provider="aws") as sdk:
        templates = await sdk.list_templates()
except ConfigurationError as e:
    print(f"Configuration error: {e}")
except ProviderError as e:
    print(f"Provider error: {e}")
except SDKError as e:
    print(f"SDK error: {e}")
```

## MCP Integration

The Open Host Factory Plugin provides direct MCP (Model Context Protocol) integration for AI assistants.

### Available MCP Tools

- **Provider Management**: `check_provider_health`, `list_providers`, `get_provider_config`, `get_provider_metrics`
- **Template Operations**: `list_templates`, `get_template`, `validate_template`
- **Infrastructure Requests**: `request_machines`, `get_request_status`, `list_return_requests`, `return_machines`

### MCP Resources

- `templates://` - Available compute templates
- `requests://` - Provisioning requests
- `machines://` - Compute instances
- `providers://` - Cloud providers

### Integration Example

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
```

## REST API

The Open Host Factory Plugin also provides a REST API for integration with other systems.

### Template Endpoints

#### List Templates

**GET** `/api/v1/templates`

**Query Parameters:**
- `provider_api` (optional): Filter templates by provider API
- `force_refresh` (optional): Force reload from configuration files

**Response:**
```json
{
  "templates": [
    {
      "template_id": "aws-basic",
      "name": "Basic AWS Template",
      "provider_api": "aws",
      "image_id": "ami-12345678",
      "instance_type": "t3.medium",
      "key_name": "my-keypair",
      "security_group_ids": ["sg-12345678"],
      "subnet_ids": ["subnet-12345678"],
      "tags": {
        "Environment": "production"
      }
    }
  ],
  "total_count": 1,
  "timestamp": "2025-01-15T10:30:00Z"
}
```

#### Get Template

**GET** `/api/v1/templates/{template_id}`

**Path Parameters:**
- `template_id`: Template identifier

**Query Parameters:**
- `include_config` (optional): Include full template configuration

**Response:**
```json
{
  "template": {
    "template_id": "aws-basic",
    "name": "Basic AWS Template",
    "provider_api": "aws",
    "image_id": "ami-12345678",
    "instance_type": "t3.medium",
    "key_name": "my-keypair",
    "security_group_ids": ["sg-12345678"],
    "subnet_ids": ["subnet-12345678"],
    "user_data": "#!/bin/bash\necho 'Hello World'",
    "tags": {
      "Environment": "production",
      "Project": "hostfactory"
    },
    "version": "1.0"
  },
  "timestamp": "2025-01-15T10:30:00Z"
}
```

#### Create Template

**POST** `/api/v1/templates`

**Request Body:**
```json
{
  "template_id": "new-aws-template",
  "name": "New AWS Template",
  "provider_api": "aws",
  "image_id": "ami-87654321",
  "instance_type": "t3.large",
  "key_name": "production-keypair",
  "security_group_ids": ["sg-87654321"],
  "subnet_ids": ["subnet-87654321"],
  "user_data": "#!/bin/bash\nyum update -y",
  "tags": {
    "Environment": "production",
    "Team": "infrastructure"
  },
  "version": "1.0"
}
```

**Response (201 Created):**
```json
{
  "message": "Template created successfully",
  "template_id": "new-aws-template",
  "timestamp": "2025-01-15T10:30:00Z"
}
```

#### Update Template

**PUT** `/api/v1/templates/{template_id}`

**Path Parameters:**
- `template_id`: Template identifier

**Request Body:**
```json
{
  "name": "Updated AWS Template",
  "instance_type": "t3.xlarge",
  "tags": {
    "Environment": "production",
    "Team": "infrastructure",
    "Updated": "2025-01-15"
  }
}
```

**Response (200 OK):**
```json
{
  "message": "Template updated successfully",
  "template_id": "aws-basic",
  "timestamp": "2025-01-15T10:30:00Z"
}
```

#### Delete Template

**DELETE** `/api/v1/templates/{template_id}`

**Path Parameters:**
- `template_id`: Template identifier

**Response (200 OK):**
```json
{
  "message": "Template deleted successfully",
  "template_id": "old-template",
  "timestamp": "2025-01-15T10:30:00Z"
}
```

### Machine Endpoints

#### Request Machines

**POST** `/api/v1/machines`

**Request Body:**
```json
{
  "template_id": "basic-template",
  "count": 5,
  "wait": false,
  "timeout": 300
}
```

**Response (202 Accepted):**
```json
{
  "request_id": "req-12345678-1234-1234-1234-123456789012",
  "status": "submitted",
  "message": "Request submitted successfully"
}
```

#### Get Machine Status

**GET** `/api/v1/machines/{machine_id}`

**Path Parameters:**
- `machine_id`: Machine identifier

**Response (200 OK):**
```json
{
  "machine_id": "i-1234567890abcdef0",
  "status": "running",
  "ip_address": "10.0.1.100",
  "template_id": "basic-template",
  "created_at": "2025-01-15T10:30:00Z"
}
```

### Request Endpoints

#### Get Request Status

**GET** `/api/v1/requests/{request_id}`

**Path Parameters:**
- `request_id`: Request identifier

**Response (200 OK):**
```json
{
  "request_id": "req-12345678-1234-1234-1234-123456789012",
  "status": "completed",
  "template_id": "basic-template",
  "count": 5,
  "created_at": "2025-01-15T10:30:00Z",
  "completed_at": "2025-01-15T10:35:00Z",
  "machines": [
    {
      "machine_id": "i-1234567890abcdef0",
      "status": "running",
      "ip_address": "10.0.1.100"
    }
  ]
}
```

## Error Handling

All interfaces (CLI, SDK, MCP, REST API) use consistent error handling patterns:

### Error Response Format

```json
{
  "success": false,
  "error": "Error message",
  "error_code": "ERROR_CODE",
  "details": {
    "field": "template_id",
    "value": "invalid-value",
    "expected": "valid-pattern"
  }
}
```

### Common Error Codes

| Code | Description |
|------|-------------|
| `TEMPLATE_NOT_FOUND` | Template not found |
| `INVALID_TEMPLATE` | Invalid template configuration |
| `PROVIDER_ERROR` | Provider operation failed |
| `REQUEST_NOT_FOUND` | Request not found |
| `MACHINE_NOT_FOUND` | Machine not found |
| `CONFIGURATION_ERROR` | Configuration error |
| `VALIDATION_ERROR` | Validation error |
| `INTERNAL_ERROR` | Internal server error |

## Configuration

### Configuration Sources

The system uses a hierarchical configuration system with the following priority (highest to lowest):

1. Command-line arguments
2. Environment variables
3. Configuration file
4. Default values

### Environment Variables

```bash
# Provider configuration
OHFP_PROVIDER=aws
OHFP_REGION=us-east-1
OHFP_PROFILE=default

# API configuration
OHFP_API_HOST=0.0.0.0
OHFP_API_PORT=8000

# Storage configuration
OHFP_STORAGE_TYPE=json
OHFP_STORAGE_PATH=data

# Logging configuration
OHFP_LOG_LEVEL=INFO
OHFP_LOG_FILE=logs/app.log
```

### Configuration File

```json
{
  "provider": {
    "type": "aws",
    "region": "us-east-1",
    "profile": "default"
  },
  "api": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "storage": {
    "type": "json",
    "path": "data"
  },
  "logging": {
    "level": "INFO",
    "file": "logs/app.log"
  }
}
```
