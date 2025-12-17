# Source Code Architecture

This directory contains the complete source code for the Open Resource Broker, implementing Clean Architecture principles with Domain-Driven Design (DDD) and Command Query Responsibility Segregation (CQRS) patterns.

## Architecture Overview

The codebase follows Clean Architecture with four distinct layers:

```
src/
├── domain/          # Domain Layer - Core business logic
├── application/     # Application Layer - Use cases and CQRS handlers
├── infrastructure/  # Infrastructure Layer - External integrations
└── interface/       # Interface Layer - CLI and external interfaces
```

## Layer Responsibilities

### Domain Layer (`domain/`)
- **Purpose**: Core business logic and domain models
- **Dependencies**: None (dependency-free)
- **Contains**: Aggregates, Value Objects, Domain Events, Ports (interfaces)
- **Key Principles**:
  - No dependencies on other layers
  - Pure business logic
  - Domain-driven design patterns

### Application Layer (`application/`)
- **Purpose**: Use cases, CQRS handlers, and application services
- **Dependencies**: Domain layer only
- **Contains**: Command/Query handlers, DTOs, Application services
- **Key Patterns**:
  - CQRS (Command Query Responsibility Segregation)
  - Handler pattern with automatic discovery
  - Event-driven architecture

### Infrastructure Layer (`infrastructure/`)
- **Purpose**: External integrations and technical implementations
- **Dependencies**: Domain and Application layers
- **Contains**: Repositories, External APIs, Persistence, Configuration
- **Key Patterns**:
  - Port/Adapter pattern
  - Dependency injection
  - Strategy pattern for providers

### Interface Layer (`interface/`)
- **Purpose**: External interfaces (CLI, REST API)
- **Dependencies**: All layers (orchestration layer)
- **Contains**: CLI handlers, API controllers, External interfaces
- **Key Patterns**:
  - Command pattern for CLI
  - Adapter pattern for external systems

## Key Design Patterns

### CQRS Implementation
- **Commands**: Modify state, handled by CommandHandlers
- **Queries**: Read data, handled by QueryHandlers
- **Events**: Domain events for side effects
- **Automatic Discovery**: Handlers registered via decorators

### Dependency Injection
- **Container**: Comprehensive DI container with automatic registration
- **Ports**: Abstract interfaces for external dependencies
- **Adapters**: Concrete implementations of ports
- **Registration**: Automatic service discovery and registration

### Clean Architecture Benefits
- **Testability**: Easy to unit test with clear boundaries
- **Maintainability**: Clear separation of concerns
- **Flexibility**: Easy to swap implementations
- **Scalability**: Well-organized for team development

## Getting Started

### Development Setup
1. **Install Dependencies**: `pip install -r requirements-dev.txt`
2. **Run Tests**: `pytest tests/`
3. **Code Quality**: `make lint` and `make type-check`
4. **Documentation**: See individual layer READMEs

### Key Entry Points
- **CLI**: `src/cli/main.py` - Command-line interface
- **Bootstrap**: `src/bootstrap.py` - Application initialization
- **Configuration**: `src/config/` - Configuration management

## Code Standards

### Import Conventions
```python
# Layer imports (allowed)
from src.domain.* import *           # Domain can import domain
from src.application.* import *      # Application can import domain
from src.infrastructure.* import *   # Infrastructure can import domain/application
from src.interface.* import *        # Interface can import all layers

# Anti-patterns (forbidden)
from src.infrastructure.* import *   # Domain cannot import infrastructure
from src.interface.* import *        # Domain/Application cannot import interface
```

### CQRS Handler Patterns
```python
# Command Handler
@command_handler(MyCommand)
class MyCommandHandler(BaseCommandHandler[MyCommand, MyResponse]):
    async def execute_command(self, command: MyCommand) -> MyResponse:
        # Implementation

# Query Handler
@query_handler(MyQuery)
class MyQueryHandler(BaseQueryHandler[MyQuery, MyResult]):
    async def execute_query(self, query: MyQuery) -> MyResult:
        # Implementation
```

### Dependency Injection
```python
# Service with DI
@injectable
class MyService:
    def __init__(self, repository: MyRepositoryPort):
        self.repository = repository

# Port definition
class MyRepositoryPort(ABC):
    @abstractmethod
    async def save(self, entity: MyEntity) -> None:
        pass
```

## Testing Strategy

- **Unit Tests**: Test individual components in isolation
- **Integration Tests**: Test layer interactions
- **End-to-End Tests**: Test complete workflows
- **Architecture Tests**: Verify architectural constraints

## Documentation

Each layer contains its own README with specific details:
- [Domain Layer README](domain/README.md)
- [Application Layer README](application/README.md)
- [Infrastructure Layer README](infrastructure/README.md)

## Contributing

1. Follow Clean Architecture principles
2. Maintain layer boundaries
3. Use CQRS patterns for handlers
4. Write comprehensive tests
5. Document public APIs

For detailed contribution guidelines, see the main project README.