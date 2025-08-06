# Open Host Factory Plugin

A cloud provider integration plugin for IBM Spectrum Symphony Host Factory, enabling dynamic provisioning of compute resources with a REST API interface and structured architecture implementation.

## Overview

The Open Host Factory Plugin provides integration between IBM Spectrum Symphony Host Factory and cloud providers, implementing industry-standard patterns including Domain-Driven Design (DDD), Command Query Responsibility Segregation (CQRS), and structured architecture principles.

**Currently Supported Providers:**
- **AWS** - Amazon Web Services (RunInstances, EC2Fleet, SpotFleet, Auto Scaling Groups)

## Key Features

### Core Functionality
- **HostFactory Compatible Output**: Native compatibility with IBM Symphony Host Factory requirements
- **Multi-Provider Architecture**: Extensible provider system supporting multiple cloud platforms
- **REST API Interface**: REST API with OpenAPI/Swagger documentation
- **Configuration-Driven**: Dynamic provider selection and configuration through centralized config system

### Key Architecture Features
- **Clean Architecture**: Domain-driven design with clear separation of concerns
- **CQRS Pattern**: Command Query Responsibility Segregation for scalable operations
- **Event-Driven Architecture**: Domain events with optional event publishing for template operations
- **Dependency Injection**: Comprehensive DI container with automatic dependency resolution
- **Strategy Pattern**: Pluggable provider strategies with runtime selection
- **Resilience Patterns**: Built-in retry mechanisms, circuit breakers, and error handling

### Output Formats and Compatibility
- **Flexible Field Control**: Configurable output fields for different use cases
- **Multiple Output Formats**: JSON, YAML, Table, and List formats
- **Legacy Compatibility**: Support for camelCase field naming conventions
- **Professional Tables**: Rich Unicode table formatting for CLI output

## Quick Start

### Docker Deployment (Recommended)

```bash
# Clone repository
git clone https://gitlab.aws.dev/aws-gfs-acceleration/open-hostfactory-plugin.git
cd open-hostfactory-plugin

# Configure environment
cp .env.example .env
# Edit .env with your configuration

# Start services
docker-compose up -d

# Verify deployment
curl http://localhost:8000/health
```

### Package Installation

```bash
# Install from PyPI
pip install open-hostfactory-plugin

# Verify installation
ohfp --version
ohfp --help
```

### Fast Development Setup with UV (Recommended)

For faster dependency resolution and installation, use [uv](https://github.com/astral-sh/uv):

```bash
# Install uv (if not already installed)
pip install uv

# Clone repository
git clone https://gitlab.aws.dev/aws-gfs-acceleration/open-hostfactory-plugin.git
cd open-hostfactory-plugin

# Fast development setup with uv
make dev-install-uv

# Or manually with uv
uv pip install -e ".[dev]"

# Generate lock files for reproducible builds
make uv-lock

# Sync with lock files (fastest)
make uv-sync-dev
```

### Traditional Development Setup

```bash
# Clone repository
git clone https://gitlab.aws.dev/aws-gfs-acceleration/open-hostfactory-plugin.git
cd open-hostfactory-plugin

# Traditional setup with pip
make dev-install-pip

# Or manually
pip install -e ".[dev]"
```

## Usage Examples

### MCP Server Mode (AI Assistant Integration)

The plugin provides a Model Context Protocol (MCP) server for AI assistant integration:

```bash
# Start MCP server in stdio mode (recommended for AI assistants)
ohfp mcp serve --stdio

# Start MCP server as TCP server (for development/testing)
ohfp mcp serve --port 3000 --host localhost

# Configure logging level
ohfp mcp serve --stdio --log-level DEBUG
```

#### Available MCP Tools

The MCP server exposes all CLI functionality as tools for AI assistants:

- **Provider Management**: `check_provider_health`, `list_providers`, `get_provider_config`, `get_provider_metrics`
- **Template Operations**: `list_templates`, `get_template`, `validate_template`
- **Infrastructure Requests**: `request_machines`, `get_request_status`, `list_return_requests`, `return_machines`

#### Available MCP Resources

Access domain objects via MCP resource URIs:

- `templates://` - Available compute templates
- `requests://` - Provisioning requests
- `machines://` - Compute instances
- `providers://` - Cloud providers

#### AI Assistant Prompts

Pre-built prompts for common infrastructure tasks:

- `provision_infrastructure` - Guide infrastructure provisioning workflows
- `troubleshoot_deployment` - Help diagnose deployment issues
- `infrastructure_best_practices` - Provide deployment best practices

#### Integration Examples

**Claude Desktop Configuration:**
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

**Python MCP Client:**
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

### Command Line Interface

#### Template Management (Full CRUD Operations)

```bash
# List available templates
ohfp templates list
ohfp templates list --long                    # Detailed information
ohfp templates list --format table           # Table format

# Show specific template
ohfp templates show TEMPLATE_ID

# Create new template
ohfp templates create --file template.json
ohfp templates create --file template.yaml --validate-only

# Update existing template
ohfp templates update TEMPLATE_ID --file updated-template.json

# Delete template
ohfp templates delete TEMPLATE_ID
ohfp templates delete TEMPLATE_ID --force    # Force without confirmation

# Validate template configuration
ohfp templates validate --file template.json

# Refresh template cache
ohfp templates refresh
ohfp templates refresh --force               # Force complete refresh
```

#### Machine and Request Management

```bash
# Request machines
ohfp requests create --template-id my-template --count 5

# Check request status
ohfp requests status --request-id req-12345

# List active machines
ohfp machines list

# Return machines
ohfp requests return --request-id req-12345
```

#### Storage Management

```bash
ohfp storage list                    # List available storage strategies
ohfp storage show                    # Show current storage configuration
ohfp storage health                  # Check storage health
ohfp storage validate                # Validate storage configuration
ohfp storage test                    # Test storage connectivity
ohfp storage metrics                 # Show storage performance metrics
```

### REST API

```bash
# Get available templates
curl -X GET "http://localhost:8000/api/v1/templates"

# Create machine request
curl -X POST "http://localhost:8000/api/v1/requests" \
  -H "Content-Type: application/json" \
  -d '{"templateId": "my-template", "maxNumber": 5}'

# Check request status
curl -X GET "http://localhost:8000/api/v1/requests/req-12345"
```

## Architecture

The plugin implements Clean Architecture principles with the following layers:

- **Domain Layer**: Core business logic, entities, and domain services
- **Application Layer**: Use cases, command/query handlers, and application services
- **Infrastructure Layer**: External integrations, persistence, and technical concerns
- **Interface Layer**: REST API, CLI, and external interfaces

### Design Patterns

- **Domain-Driven Design (DDD)**: Rich domain models with clear bounded contexts
- **CQRS**: Separate command and query responsibilities for scalability
- **Ports and Adapters**: Hexagonal architecture for testability and flexibility
- **Strategy Pattern**: Pluggable provider implementations
- **Factory Pattern**: Dynamic object creation based on configuration
- **Repository Pattern**: Data access abstraction with multiple storage strategies
- **Clean Architecture**: Strict layer separation with dependency inversion principles

## Configuration

### Environment Configuration

```bash
# Provider configuration
PROVIDER_TYPE=aws
AWS_REGION=us-east-1
AWS_PROFILE=default

# API configuration
API_HOST=0.0.0.0
API_PORT=8000

# Storage configuration
STORAGE_TYPE=dynamodb
STORAGE_TABLE_PREFIX=hostfactory
```

### Provider Configuration

```yaml
# config/providers.yml
providers:
  - name: aws-primary
    type: aws
    config:
      region: us-east-1
      profile: default
      handlers:
        default: ec2_fleet
        spot_fleet:
          enabled: true
        auto_scaling_group:
          enabled: true
```

## Development

### Prerequisites

- Python 3.11+
- Docker and Docker Compose
- AWS CLI (for AWS provider)

### Development Setup

```bash
# Clone repository
git clone https://gitlab.aws.dev/aws-gfs-acceleration/open-hostfactory-plugin.git
cd open-hostfactory-plugin

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install development dependencies
pip install -r requirements-dev.txt

# Install in development mode
pip install -e .

# Run tests
pytest

# Run linting
make lint

# Run type checking
make type-check
```

### Testing

```bash
# Run all tests
make test

# Run with coverage
make test-coverage

# Run integration tests
make test-integration

# Run performance tests
make test-performance
```

## Documentation

Comprehensive documentation is available at:

- **Architecture Guide**: Understanding the system design and patterns
- **API Reference**: Complete REST API documentation
- **Configuration Guide**: Detailed configuration options
- **Developer Guide**: Contributing and extending the plugin
- **Deployment Guide**: Production deployment scenarios

## HostFactory Integration

The plugin is designed for seamless integration with IBM Spectrum Symphony Host Factory:

- **API Compatibility**: Full compatibility with HostFactory API requirements
- **Attribute Generation**: Automatic CPU and RAM specifications based on AWS instance types
- **Output Format Compliance**: Native support for expected output formats with accurate resource specifications
- **Configuration Integration**: Easy integration with existing HostFactory configurations
- **Monitoring Integration**: Compatible with HostFactory monitoring and logging

### Resource Specifications

The plugin generates HostFactory attributes based on AWS instance types:

```json
{
  "templates": [
    {
      "templateId": "t3-medium-template",
      "maxNumber": 5,
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "2"],
        "nram": ["Numeric", "4096"]
      }
    },
    {
      "templateId": "m5-xlarge-template",
      "maxNumber": 3,
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "4"],
        "nram": ["Numeric", "16384"]
      }
    }
  ]
}
```

**Supported Instance Types**: Common AWS instance types with appropriate CPU and RAM mappings

## Support and Contributing

### Getting Help

- **Documentation**: Comprehensive guides and API reference
- **Issues**: GitHub Issues for bug reports and feature requests
- **Discussions**: Community discussions and questions

### Contributing

We welcome contributions! Please see our Contributing Guide for details on:

- Code style and standards
- Testing requirements
- Pull request process
- Development workflow

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Security

For security concerns, please see our [Security Policy](SECURITY.md) for responsible disclosure procedures.
