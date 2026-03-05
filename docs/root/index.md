# Open Resource Broker

Welcome to the Open Resource Broker documentation. ORB integrates with IBM Spectrum Symphony Host Factory and cloud providers to enable dynamic provisioning of compute resources via a CLI and optional REST API.

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

```bash
pip install orb-py
orb init
orb templates generate
orb templates list
orb machines request <template-id> 3
```

See the [Quick Start Guide](getting_started/quick_start.md) for a full walkthrough.

## Features

- **AWS Provider**: EC2 Instances, Auto Scaling Groups, Spot Fleet, EC2 Fleet
- **CLI**: Primary interface for all operations
- **Optional REST API**: Enable with `pip install "orb-py[api]"`
- **Clean Architecture**: Domain-Driven Design with CQRS patterns
- **Extensible**: Strategy/Registry pattern for adding providers and schedulers

## Supported Providers

### Amazon Web Services (AWS)
- **EC2 Instances**: Direct instance provisioning via RunInstances
- **Auto Scaling Groups**: Managed scaling groups
- **Spot Fleet**: Cost-optimized spot instances
- **EC2 Fleet**: Fleet provisioning with mixed instance types

## Documentation Structure

- **[User Guide](user_guide/installation.md)**: End-user documentation
- **[Deployment Guide](deployment/readme.md)**: Deployment documentation
- **[Developer Guide](developer_guide/architecture.md)**: Architecture and development
- **[API Reference](api/readme.md)**: REST API documentation (optional feature)

## License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.
