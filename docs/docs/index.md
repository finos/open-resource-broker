# Open Host Factory Plugin

Welcome to the Open Host Factory Plugin documentation! This plugin provides integration between IBM Spectrum Symphony Host Factory and cloud providers, enabling dynamic provisioning of compute resources with a REST API interface.

## Documentation Navigation

### Getting Started
- **[Quick Start Guide](getting_started/quick_start.md)** - Get up and running in minutes
- **[Installation Guide](user_guide/installation.md)** - Detailed installation instructions
- **[Configuration Guide](user_guide/configuration.md)** - System configuration options

### Architecture & Design
- **[System Overview](architecture/system_overview.md)** - High-level architecture overview
- **[Clean Architecture](architecture/clean_architecture.md)** - Architectural principles and patterns
- **[System Diagrams](architecture/system_diagrams.md)** - Visual architecture representations

### Developer Resources
- **[Developer Guide](developer_guide/architecture.md)** - Development-focused guidance
- **[API Reference](api/readme.md)** - REST API documentation
- **[Testing Guide](testing/readme.md)** - Testing strategies and examples

### Deployment & Operations
- **[Deployment Guide](deployment/readme.md)** - Complete deployment documentation
- **[Operational Tools](operational/tools.md)** - Monitoring and maintenance tools

## Quick Start

### Docker Deployment (Recommended)

The fastest way to get started:

```bash
# Clone repository
git clone <repository-url>
cd open-hostfactory-plugin

# Copy environment template
cp .env.example .env

# Start with Docker Compose
docker-compose up -d

# Access API documentation
open http://localhost:8000/docs
```

### Package Installation

```bash
# Install from PyPI
pip install open-hostfactory-plugin

# Verify installation
ohfp --help
```

## Features

- **Multi-Cloud Support**: Currently supports AWS with extensible architecture
- **REST API**: REST API with OpenAPI documentation
- **Docker Ready**: Suitable for production containerization
- **Authentication**: Multiple authentication strategies (JWT, AWS IAM, Cognito)
- **Monitoring**: Built-in health checks and metrics
- **Clean Architecture**: Domain-Driven Design with CQRS patterns

## Architecture

The plugin follows Domain-Driven Design (DDD) principles with a clean architecture approach:

- **Domain Layer**: Pure business logic and entities
- **Application Layer**: Use cases and application services  
- **Infrastructure Layer**: Technical implementations
- **API Layer**: REST API endpoints and CLI interface

## Supported Providers

### Amazon Web Services (AWS)
- **EC2 Instances**: Direct instance provisioning
- **Auto Scaling Groups**: Managed scaling groups
- **Spot Fleet**: Cost-optimized spot instances
- **Fleet API**: EC2 Fleet provisioning

## Getting Started

Choose your preferred deployment method:

### [Docker Deployment](deployment/docker.md)
Complete containerization with Docker Compose, security hardening, and production deployment.

### [Cloud Deployment](deployment/readme.md)
Deploy to Kubernetes, AWS ECS, Google Cloud Run, or other cloud platforms.

### [Traditional Installation](user_guide/installation.md)
Direct installation on servers with systemd service configuration.

## Documentation Structure

- **[User Guide](user_guide/installation.md)**: End-user documentation and API reference
- **[Deployment Guide](deployment/readme.md)**: Complete deployment documentation
- **[Developer Guide](developer_guide/architecture.md)**: Development and architecture documentation
- **[API Reference](api-reference.md)**: Technical API documentation

## Support

- **Documentation**: Comprehensive guides and examples
- **Testing**: Complete test suite with Docker integration
- **Security**: Production security best practices
- **Performance**: Optimized for high-throughput scenarios

## License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.
