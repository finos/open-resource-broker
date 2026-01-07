# Open Resource Broker

[![Test Matrix](https://github.com/awslabs/open-resource-broker/workflows/Test%20Matrix/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/test-matrix.yml)
[![Quality Checks](https://github.com/awslabs/open-resource-broker/workflows/Quality%20Checks/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/ci-quality.yml)
[![Security Scanning](https://github.com/awslabs/open-resource-broker/workflows/Security%20Scanning/badge.svg)](https://github.com/awslabs/open-resource-broker/actions/workflows/security-code.yml)
[![Latest Release](https://img.shields.io/github/v/release/awslabs/open-resource-broker)](https://github.com/awslabs/open-resource-broker/releases)
[![PyPI Version](https://img.shields.io/pypi/v/open-resource-broker)](https://pypi.org/project/open-resource-broker/)
[![Python Versions](https://img.shields.io/pypi/pyversions/open-resource-broker)](https://pypi.org/project/open-resource-broker/)
[![License](https://img.shields.io/github/license/awslabs/open-resource-broker)](LICENSE)

**Health & Performance:**
[![Success Rate](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/fgogolli/ec3393a523fa3a6b6ff89a0636de3085/raw/success-rate.json)](https://github.com/awslabs/open-resource-broker/actions/workflows/health-monitoring.yml)
[![Avg Duration](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/fgogolli/ec3393a523fa3a6b6ff89a0636de3085/raw/avg-duration.json)](https://github.com/awslabs/open-resource-broker/actions/workflows/health-monitoring.yml)

**Code Quality:**
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/fgogolli/50bc37df3c178a0846dbd3682a71d50a/raw/coverage.json)](https://github.com/awslabs/open-resource-broker/actions/workflows/advanced-metrics.yml)
[![Lines of Code](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/fgogolli/50bc37df3c178a0846dbd3682a71d50a/raw/lines-of-code.json)](https://github.com/awslabs/open-resource-broker/actions/workflows/advanced-metrics.yml)
[![Comments](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/fgogolli/50bc37df3c178a0846dbd3682a71d50a/raw/comments.json)](https://github.com/awslabs/open-resource-broker/actions/workflows/advanced-metrics.yml)
[![Test Duration](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/fgogolli/50bc37df3c178a0846dbd3682a71d50a/raw/test-duration.json)](https://github.com/awslabs/open-resource-broker/actions/workflows/advanced-metrics.yml)

A cloud provider integration plugin for IBM Spectrum Symphony Host Factory, enabling dynamic provisioning of compute resources with a REST API interface and structured architecture implementation.

## Overview

The Open Resource Broker provides integration between IBM Spectrum Symphony Host Factory and cloud providers, implementing industry-standard patterns including Domain-Driven Design (DDD), Command Query Responsibility Segregation (CQRS), and structured architecture principles.

**Currently Supported Providers:**
- **AWS** - Amazon Web Services (RunInstances, EC2Fleet, SpotFleet, Auto Scaling Groups)
  - Context field support for EC2Fleet, SpotFleet, and Auto Scaling Groups

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
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker

# Configure environment
cp .env.example .env
# Edit .env with your configuration

# Start services
docker-compose up -d

# Verify deployment
curl http://localhost:8000/health
```

## Installation

### Package Installation (Recommended)

```bash
# Install from PyPI
pip install open-resource-broker

# Verify installation
orb --version
orb --help
```

### System-Wide Installation (Production)

```bash
# Auto-detects best location (no sudo needed if not available)
make install-system

# Or install to custom directory (requires sudo if needed)
ORB_INSTALL_DIR=/opt/orb make install-system

# Installation will output the actual location and next steps
# Add to PATH as instructed by the installer
orb --version
```

### Local Development Installation

```bash
# Clone repository
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker

# Install local development environment
make dev-install

# Or full development workflow (recommended)
make dev
```

### Installation Comparison

| Method | Location | Use Case | Command |
|--------|----------|----------|---------|
| **PyPI** | System Python | End users | `pip install open-resource-broker` |
| **System** | `/usr/local/orb/` or `~/.local/orb/` | Production deployment | `make install-system` |
| **Local** | `./.venv/` | Development | `make dev-install` |

### Fast Development Setup with UV (Advanced)

For faster dependency resolution and installation, use [uv](https://github.com/astral-sh/uv):

```bash
# Install uv (if not already installed)
pip install uv

# Clone repository
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker

# Fast development setup with uv
make dev-install

# Generate lock files for reproducible builds
make uv-lock

# Sync with lock files (fastest)
make uv-sync-dev
```

# Or manually
pip install -e ".[dev]"
```

## Usage Examples

### MCP Server Mode (AI Assistant Integration)

The plugin provides a Model Context Protocol (MCP) server for AI assistant integration:

```bash
# Start MCP server in stdio mode (recommended for AI assistants)
orb mcp serve --stdio

# Start MCP server as TCP server (for development/testing)
orb mcp serve --port 3000 --host localhost

# Configure logging level
orb mcp serve --stdio --log-level DEBUG
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
    "open-resource-broker": {
      "command": "orb",
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
        command="orb",
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
orb templates list
orb templates list --long                    # Detailed information
orb templates list --format table           # Table format

# Show specific template
orb templates show TEMPLATE_ID

# Create new template
orb templates create --file template.json
orb templates create --file template.yaml --validate-only

# Update existing template
orb templates update TEMPLATE_ID --file updated-template.json

# Delete template
orb templates delete TEMPLATE_ID
orb templates delete TEMPLATE_ID --force    # Force without confirmation

# Validate template configuration
orb templates validate --file template.json

# Refresh template cache
orb templates refresh
orb templates refresh --force               # Force complete refresh
```

#### Machine and Request Management

```bash
# Request machines
orb requests create --template-id my-template --count 5

# Check request status
orb requests status --request-id req-12345

# List active machines
orb machines list

# Return machines
orb requests return --request-id req-12345
```

#### Storage Management

```bash
orb storage list                    # List available storage strategies
orb storage show                    # Show current storage configuration
orb storage health                  # Check storage health
orb storage validate                # Validate storage configuration
orb storage test                    # Test storage connectivity
orb storage metrics                 # Show storage performance metrics
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

# Scheduler directory configuration
# HostFactory scheduler
HF_PROVIDER_WORKDIR=/path/to/working/directory
HF_PROVIDER_CONFDIR=/path/to/config/directory
HF_PROVIDER_LOGDIR=/path/to/logs/directory

# Default scheduler
DEFAULT_PROVIDER_WORKDIR=/path/to/working/directory
DEFAULT_PROVIDER_CONFDIR=/path/to/config/directory
DEFAULT_PROVIDER_LOGDIR=/path/to/logs/directory
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
    template_defaults:
```

## Development

### Prerequisites

- Python 3.10+ (tested on 3.10, 3.11, 3.12, 3.13, 3.14)
- Docker and Docker Compose
- AWS CLI (for AWS provider)

### Development Setup

```bash
# Clone repository
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install development dependencies
pip install -r requirements-dev.txt

# Install in development mode
pip install -e .

# Run tests
make test

# Format code (Ruff replaces Black + isort)
make format

# Check code quality
make lint

# Run before committing (replaces pre-commit hooks)
make pre-commit
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

### Release Workflow

The project uses semantic-release for automated version management:

```bash
# Create a new release
git commit -m "release: add new features and bug fixes"
git push origin main
```

**Release Process:**
- Uses conventional commits for version calculation
- `feat:` → minor version bump
- `fix:` → patch version bump  
- `BREAKING CHANGE:` → major version bump
- Commit with "release:" prefix triggers semantic-release
- Automatically publishes to PyPI, builds containers, and deploys documentation

See [Release Management Guide](docs/root/developer_guide/releases.md) for complete documentation.

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
