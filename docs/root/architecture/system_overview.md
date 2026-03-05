# System Architecture Overview

This document provides a high-level overview of the Open Resource Broker's system architecture, focusing on the overall structure, key components, and architectural decisions.

> **Related Documentation:**
> - [Developer Guide: Architecture](../developer_guide/architecture.md) - Development-focused architectural guidance
> - [Architecture: Clean Architecture](./clean_architecture.md) - Detailed Clean Architecture implementation

## Architecture Principles

The plugin implements Clean Architecture principles with clear separation of concerns across four distinct layers:

1. **Domain Layer**: Core business logic and entities
2. **Application Layer**: Use cases and application services
3. **Infrastructure Layer**: External integrations and technical concerns
4. **Interface Layer**: External interfaces (CLI, REST API)

## Implemented Patterns

### CQRS (Command Query Responsibility Segregation)
**Status**: Implemented

- Command Handlers: write operations (mutate state, return void)
- Query Handlers: read operations (return data, no side effects)

### Clean Architecture
**Status**: Implemented

- Layers: domain, application, infrastructure, interface

### Dependency Injection
**Status**: Implemented

### Strategy Pattern
**Status**: Implemented

## Layer Structure

### Domain Layer (`src/domain/`)

The domain layer contains the core business logic and is independent of external concerns.

#### Core Aggregates
- **Template**: Represents VM template configurations
- **Machine**: Represents provisioned compute instances
- **Request**: Represents provisioning requests

### Application Layer (`src/application/`)

Contains use cases and application services that orchestrate domain objects.

#### CQRS Implementation
- Command handlers for write operations
- Query handlers for read operations
- Event handlers for domain events

### Infrastructure Layer (`src/infrastructure/`)

Implements technical concerns and external integrations.

#### Key Components
- Dependency injection container
- Persistence implementations
- External service integrations
- Configuration management

### Interface Layer (`src/interface/`)

Provides external interfaces and entry points.

## Architecture Quality

### Recommendations

1. **Layer Isolation**: Ensure dependencies flow inward (Interface → Infrastructure → Application → Domain)
2. **CQRS Compliance**: Verify all handlers inherit from appropriate base classes
3. **Pattern Consistency**: Maintain consistent implementation of architectural patterns
