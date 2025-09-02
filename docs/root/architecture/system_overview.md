# System Architecture Overview

*Last updated: 2025-07-12 12:07:07 (Auto-generated)*

This document provides a high-level overview of the Open Host Factory Plugin's system architecture, focusing on the overall structure, key components, and architectural decisions.

> **Related Documentation:**
> - [Developer Guide: Architecture](../developer_guide/architecture.md) - Development-focused architectural guidance
> - [Architecture: Clean Architecture](./clean_architecture.md) - Detailed Clean Architecture implementation

## Current Architecture Metrics

### Code Organization
- **Total Files**: 324 Python files
- **Total Lines of Code**: 62,571 lines
- **Average File Size**: 193 lines per file

### File Size Distribution
- **Small files** (< 100 lines): 108 files
- **Medium files** (100-300 lines): 146 files  
- **Large files** (300-600 lines): 63 files
- **Extra large files** (> 600 lines): 7 files

### Layer Distribution
- **Bootstrap.py Layer**: 1 files, 246 lines
- **Run.py Layer**: 1 files, 31 lines
- **Interface Layer**: 6 files, 1,593 lines
- **Config Layer**: 20 files, 3,075 lines
- **Providers Layer**: 53 files, 13,905 lines
- **Cli Layer**: 3 files, 1,152 lines
- **Api Layer**: 21 files, 3,088 lines
- **Application Layer**: 50 files, 8,173 lines
- **Monitoring Layer**: 2 files, 762 lines
- **Infrastructure Layer**: 122 files, 24,755 lines
- **Domain Layer**: 45 files, 5,791 lines

## Architecture Principles

The plugin implements Clean Architecture principles with clear separation of concerns across four distinct layers:

1. **Domain Layer**: Core business logic and entities
2. **Application Layer**: Use cases and application services  
3. **Infrastructure Layer**: External integrations and technical concerns
4. **Interface Layer**: External interfaces (CLI, REST API)

## Implemented Patterns

### CQRS (Command Query Responsibility Segregation)
**Status**: Implemented

- Command Handlers: 8
- Query Handlers: 0

### Clean Architecture
**Status**: Implemented

- Layers: domain, application, infrastructure, interface

### Dependency Injection
**Status**: Implemented

- DI Files: 5

### Strategy Pattern
**Status**: Implemented

- Strategy Files: 18


## Layer Structure

### Domain Layer (`src/domain/`)

The domain layer contains the core business logic and is independent of external concerns.

**Current Statistics:**
- Files: 5791 lines across 45 files
- Key Modules: template.aggregate, template.value_objects, template.ami_resolver, template.exceptions, template.repository...

#### Core Aggregates
- **Template**: Represents VM template configurations
- **Machine**: Represents provisioned compute instances  
- **Request**: Represents provisioning requests

### Application Layer (`src/application/`)

Contains use cases and application services that orchestrate domain objects.

**Current Statistics:**
- Files: 8173 lines across 50 files
- Key Modules: service, decorators, dto.system, dto.responses, dto.queries...

#### CQRS Implementation
- Command handlers for write operations
- Query handlers for read operations
- Event handlers for domain events

### Infrastructure Layer (`src/infrastructure/`)

Implements technical concerns and external integrations.

**Current Statistics:**
- Files: 24755 lines across 122 files
- Key Modules: lifecycle, mocking.dry_run_context, di.services, di.port_registrations, di.buses...

#### Key Components
- Dependency injection container
- Persistence implementations
- External service integrations
- Configuration management

### Interface Layer (`src/interface/`)

Provides external interfaces and entry points.

**Current Statistics:**
- Files: 1593 lines across 6 files
- Key Modules: request_command_handlers, serve_command_handler, template_command_handlers, system_command_handlers, command_handlers...

## Architecture Quality

### Large Files Requiring Attention
- `infrastructure/error/exception_handler.py`: 1060 lines
- `infrastructure/di/container.py`: 1037 lines
- `config/loader.py`: 735 lines
- `providers/base/strategy/composite_strategy.py`: 637 lines
- `providers/base/strategy/fallback_strategy.py`: 635 lines
- `infrastructure/persistence/json/template.py`: 623 lines
- `providers/aws/infrastructure/handlers/spot_fleet_handler.py`: 605 lines

### Recommendations

1. **File Size Management**: Consider splitting files larger than 600 lines
2. **Layer Isolation**: Ensure dependencies flow inward (Interface → Infrastructure → Application → Domain)
3. **CQRS Compliance**: Verify all handlers inherit from appropriate base classes
4. **Pattern Consistency**: Maintain consistent implementation of architectural patterns

---

*This document is automatically generated from the current codebase state.*
*For manual updates, edit the source files and regenerate this documentation.*
