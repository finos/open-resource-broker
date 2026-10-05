<p align="center">
  <img alt="Open Resource Broker" src="assets/orb-logo-horizontal.svg" width="480">
</p>

<p align="center">
  <strong>Unified API for orchestrating and provisioning compute capacity</strong>
</p>

---

Welcome to the Open Resource Broker documentation. ORB lets you define what compute capacity you need in a template, request it, track it, and return it — through a CLI, REST API, Python SDK, or MCP server.

## Getting Started

- **[Quick Start Guide](getting_started/quick_start.md)** — get up and running in minutes
- **[Installation Guide](user_guide/installation.md)** — detailed installation instructions
- **[Configuration Guide](user_guide/configuration.md)** — system configuration options

## Architecture & Design

- **[System Overview](architecture/system_overview.md)** — high-level architecture overview
- **[Clean Architecture](architecture/clean_architecture.md)** — architectural principles and patterns
- **[System Diagrams](architecture/system_diagrams.md)** — visual architecture representations

## Workflows

- **[End-to-End Workflow Guide](workflows/end-to-end.md)** — complete lifecycle across CLI, SDK, REST API, and MCP

## Developer Resources

- **[Developer Guide](developer_guide/architecture.md)** — development-focused guidance
- **[SDK Quickstart](sdk/quickstart.md)** — programmatic access via Python SDK
- **[SDK API Reference](sdk/api-reference.md)** — parameter mapping across all interfaces
- **[API Reference](api/readme.md)** — REST API documentation
- **[Testing Guide](testing/readme.md)** — testing strategies and examples

## Examples

- **[SDK Usage](examples/sdk_usage.py)** — full SDK lifecycle: templates, requests, batch, middleware, discovery
- **[Provider Strategy](examples/provider_strategy_usage_examples.py)** — custom providers, load balancing, fallback, composition
- **[Config: Single Provider](examples/single-provider.json)** — minimal single-provider config
- **[Config: Multi Provider](examples/multi-provider.json)** — multi-provider setup
- **[Config: Production](examples/production-config.json)** — production-ready config

## Deployment & Operations

- **[Deployment Guide](deployment/readme.md)** — complete deployment documentation
- **[HostFactory Integration](hostfactory/integration_guide.md)** — IBM Spectrum Symphony integration
- **[Operational Tools](operational/tools.md)** — monitoring and maintenance tools

## Features

- **AWS Provider** — [EC2 Instances](providers/aws/run-instances.md), [Auto Scaling Groups](providers/aws/asg.md), [Spot Fleet](providers/aws/spot-fleet.md), [EC2 Fleet](providers/aws/ec2-fleet.md), [Lambda MicroVMs](providers/aws/microvm.md)
- **Azure Provider** — Virtual Machine Scale Sets, single VMs, and CycleCloud cluster nodes
- **GCP Provider** — Managed Instance Groups and single Compute Engine VMs
- **Kubernetes Provider** — [Pods, Deployments, StatefulSets, and Jobs](providers/k8s/index.md)
- **Scheduler integrations** — default (standalone), [IBM Spectrum Symphony (HostFactory)](hostfactory/integration_guide.md), and [Slurm](slurm/integration_guide.md); HTC-Grid and OpenGRIS Scaler integrate through the default scheduler
- **CLI** — primary interface for all operations
- **REST API** — HTTP endpoints for service integration
- **Python SDK** — async-first programmatic access
- **MCP Server** — AI assistant integration
- **Clean Architecture** — Domain-Driven Design with CQRS patterns
- **Extensible** — Strategy/Registry pattern for adding providers and schedulers

## License

This project is licensed under the Apache License 2.0 — see the LICENSE file for details.
