# Dependency Injection - Developer Guide

## Prerequisites

Before working with the DI system, you should understand:
- Basic dependency injection concepts
- Python decorators and type hints
- [Clean Architecture principles](../architecture/clean_architecture.md)

## Overview

This guide provides practical implementation guidance for using the dependency injection system in the Open Host Factory Plugin. For comprehensive technical details, see the [Architecture Reference](../architecture/dependency_injection.md).

The plugin uses a comprehensive dependency injection system that follows Clean Architecture principles, with DI abstractions moved to the domain layer while maintaining backward compatibility.

## Next Steps

After mastering DI basics:
1. **[CQRS Implementation](./cqrs.md)** - Learn command and query patterns
2. **[Testing with DI](../developer_guide/testing.md)** - Testing strategies for DI components
3. **[Advanced Patterns](../architecture/dependency_injection.md)** - Advanced DI patterns and techniques

## Architecture Overview

### Clean Architecture Compliance

The DI system follows correct dependency direction:

```python
# [[]] Clean Architecture compliant
from src.domain.base.dependency_injection import injectable

@injectable
class ApplicationService:
    def __init__(self, logger: LoggingPort, config: ConfigurationPort):
        self.logger = logger
        self.config = config
```

**Dependency Direction:** Domain <- Application <- Infrastructure

## Domain DI Layer

**Location:** `src/domain/base/dependency_injection.py`

### Available Decorators

The domain DI layer provides these decorators:

#### Core Decorators
- `@injectable` - Mark a class for automatic dependency injection
- `@singleton` - Mark a class as singleton (single instance)

#### CQRS Decorators  
- `@command_handler(command_type)` - Register CQRS command handler
- `@query_handler(query_type)` - Register CQRS query handler
- `@event_handler(event_type)` - Register domain event handler

#### Advanced Decorators
- `@requires(*dependencies)` - Specify explicit dependencies
- `@factory(factory_function)` - Use custom factory function
- `@lazy` - Enable lazy initialization

### Utility Functions

- `is_injectable(cls)` - Check if class is injectable
- `get_injectable_metadata(cls)` - Get injectable metadata
- `optional_dependency(type)` - Create optional dependency

## Infrastructure DI Container

**Location:** `src/infrastructure/di/container.py`

The `DIContainer` class implements domain contracts:

### Core Methods
- `get(dependency_type)` - Resolve dependency
- `register(registration)` - Register with full configuration
- `register_singleton(cls)` - Register as singleton
- `register_factory(cls, factory)` - Register with factory
- `is_registered(cls)` - Check if registered

### Improved Methods
- `get_optional(dependency_type)` - Optional resolution (returns None if not found)
- `get_all(dependency_type)` - Get all instances of a type
- `register_instance(cls, instance)` - Register pre-created instance
- `unregister(dependency_type)` - Remove registration
- `clear()` - Clear all registrations

### CQRS Methods
- `register_command_handler(command_type, handler_type)`
- `register_query_handler(query_type, handler_type)`
- `register_event_handler(event_type, handler_type)`
- `get_command_handler(command_type)`
- `get_query_handler(query_type)`
- `get_event_handlers(event_type)`

## Usage Examples

### Basic Dependency Injection

```python
from src.domain.base.dependency_injection import injectable

@injectable
class ApplicationService:
    def __init__(self, logger: LoggingPort, config: ConfigurationPort):
        self.logger = logger
        self.config = config

# Container automatically resolves dependencies
from src.infrastructure.di.container import get_container
container = get_container()
app_service = container.get(ApplicationService)
```

### Singleton Pattern

```python
from src.domain.base.dependency_injection import injectable, singleton

@singleton
@injectable
class ConfigurationService:
    def __init__(self):
        self.config = self.load_config()

    def load_config(self):
        # Load configuration logic
        return {}

# Only one instance created
config1 = container.get(ConfigurationService)
config2 = container.get(ConfigurationService)
assert config1 is config2  # True
```

### CQRS Integration

The system includes actual command and query handlers:

#### Command Handlers (Actual Examples)

```python
# From src/application/commands/request_handlers.py
@injectable
class CreateMachineRequestHandler:
    def __init__(self, repository, logger):
        self.repository = repository
        self.logger = logger

# From src/application/commands/template_handlers.py  
@injectable
class CreateTemplateHandler:
    def handle(self, command):
        # Handle template creation
        pass

# From src/application/commands/system_handlers.py
@injectable
class MigrateProviderConfigHandler:
    def handle(self, command):
        # Handle provider config migration
        pass
```

#### Query Handlers (Actual Examples)

```python
# From src/application/queries/handlers.py
@injectable
class GetRequestHandler:
    def handle(self, query):
        # Handle request retrieval
        pass

# From src/application/queries/system_handlers.py
@injectable
class GetProviderConfigHandler:
    def handle(self, query):
        # Handle provider config retrieval
        pass

# From src/application/queries/specialized_handlers.py
@injectable
class GetActiveMachineCountHandler:
    def handle(self, query):
        # Handle active machine count query
        pass
```

### Actual Commands and Queries

The system defines these actual commands and queries:

```python
# From src/application/dto/commands.py
class CreateRequestCommand(Command, BaseModel):
    template_id: str
    count: int
    # ... other fields

class UpdateRequestStatusCommand(Command, BaseModel):
    request_id: str
    status: str
    # ... other fields

# From src/application/dto/queries.py  
class GetRequestQuery(Query, BaseModel):
    request_id: str
    # ... other fields

class ListActiveRequestsQuery(Query, BaseModel):
    limit: Optional[int] = None
    # ... other fields
```

### Container Operations

```python
from src.infrastructure.di.container import get_container

container = get_container()

# Register dependencies
container.register_singleton(ApplicationService)

# Resolve dependencies
app_service = container.get(ApplicationService)

# Optional resolution (returns None if not registered)
optional_service = container.get_optional(SomeOptionalService)

# Check if registered
if container.is_registered(ApplicationService):
    service = container.get(ApplicationService)

# Get all instances of a type
all_handlers = container.get_all(CommandHandler)
```

## Registry Pattern Integration

The DI system integrates with registry patterns for strategy-based component selection:

### Scheduler Port with Registry

The scheduler port demonstrates registry integration for configuration-driven strategy selection:

```python
# Automatic registration using registry pattern
def create_scheduler_port(container):
    from src.infrastructure.registry.scheduler_registry import get_scheduler_registry
    from src.config.manager import get_config_manager

    config_manager = get_config_manager()
    scheduler_config = config_manager.get_scheduler_config()
    scheduler_type = scheduler_config.get('strategy', 'hostfactory')

    registry = get_scheduler_registry()
    return registry.get_active_strategy(scheduler_type, scheduler_config)

# Usage - transparent to consumers
from src.domain.base.ports import SchedulerPort

scheduler = container.get(SchedulerPort)  # Automatically resolves via registry
```

### Benefits of Registry Integration

- **Configuration-Driven**: Strategy selection based on configuration
- **Lazy Loading**: Strategies created only when needed
- **Type Safety**: Maintains appropriate port/adapter abstraction
- **Testability**: Easy to mock or substitute strategies
- **Separation of Concerns**: Registry handles strategy selection, DI handles dependency resolution

## Current Injectable Classes

Based on actual codebase analysis:

### Application Layer
- `ApplicationService` - Main application orchestrator [[]] Injectable
- Command handlers in `src/application/commands/`:
  - `CreateMachineRequestHandler`
  - `CreateTemplateHandler`
  - `MigrateProviderConfigHandler`
  - And many more...
- Query handlers in `src/application/queries/`:
  - `GetRequestHandler`
  - `GetProviderConfigHandler`
  - `GetActiveMachineCountHandler`
  - And many more...

### Provider Layer  
- `AWSInstanceManager` - AWS instance management [[]] Injectable
- `AWSOperations` - AWS operations wrapper [[]] Injectable
- Various AWS adapters and handlers throughout `src/providers/aws/`

### Infrastructure Layer
- Various infrastructure services and adapters
- `TemplateConfigurationManager` - **Manually registered** (not using @injectable decorator)

### Manual Registration Pattern

Some services are registered manually in the DI container instead of using the `@injectable` decorator:

```python
# Example: TemplateConfigurationManager registration
# Location: src/infrastructure/di/port_registrations.py
def _register_template_configuration_services(container: DIContainer) -> None:
    """Register template configuration services."""

    # Factory-based singleton registration with complex initialization
    container.register_singleton(
        TemplateConfigurationManager,
        create_template_configuration_manager
    )
```

**When to use manual registration:**
- Configuration-driven services that need complex initialization
- Services that require specific factory patterns
- Services transitioning from @injectable pattern
- Services with conditional registration based on configuration

## Migration Guide

### For Existing Code
All existing code continues to work with backward compatibility:

```python
# Existing imports still work
from src.application.service import ApplicationService
from src.infrastructure.di.container import get_container

# Classes that were injectable remain injectable
container = get_container()
service = container.get(ApplicationService)
```

### For New Code
Use domain DI imports for new classes:

```python
from src.domain.base.dependency_injection import injectable

@injectable
class NewService:
    def __init__(self, dependency: SomeDependency):
        self.dependency = dependency
```

## Testing with DI

```python
from src.infrastructure.di.container import DIContainer

def test_service():
    # Create test container
    container = DIContainer()

    # Register test dependencies
    mock_dependency = MockDependency()
    container.register_instance(SomeDependency, mock_dependency)

    # Test service
    service = container.get(ServiceUnderTest)
    assert service.dependency is mock_dependency
```

## Best Practices

1. **Use Constructor Injection** - Prefer constructor injection for required dependencies
2. **Type Annotations** - Always provide type annotations for dependencies:
   ```python
   def __init__(self, repository: UserRepository):  # [[]] Good
   def __init__(self, repository):                  # [[]] Bad
   ```
3. **Interface Dependencies** - Depend on interfaces/ports, not concrete implementations
4. **Singleton Sparingly** - Only use `@singleton` for truly shared state
5. **Test with Mocks** - Use dependency injection for easier testing

## Performance

The DI system is optimized for performance:
- **Decorator overhead:** < 0.0001s per instance
- **Container resolution:** < 0.001s per dependency
- **Singleton caching:** Near-zero overhead for cached instances

## Troubleshooting

### Common Issues

#### Missing Type Annotations
```python
# Problem
@injectable
class Service:
    def __init__(self, dependency):  # No type annotation
        pass

# Solution  
@injectable
class Service:
    def __init__(self, dependency: DependencyType):
        pass
```

#### Unregistered Dependencies
```python
# Problem
service = container.get(UnregisteredService)  # Raises error

# Solution
container.register_singleton(UnregisteredService)
service = container.get(UnregisteredService)
```

#### Circular Dependencies
Use lazy loading or break the dependency cycle by introducing an interface.

## Summary

The DI architecture provides:

[[]] **Clean Architecture Compliance** - Correct dependency direction  
[[]] **Improved DI Features** - Singleton, CQRS, optional dependencies  
[[]] **Backward Compatibility** - All existing code continues to work  
[[]] **Performance Optimized** - Minimal overhead with intelligent caching  
[[]] **Type Safe** - Full generic type support  
[[]] **Testing Friendly** - Easy mocking and testing patterns

This architecture establishes a solid foundation for scalable, maintainable dependency injection throughout the application while maintaining Clean Architecture principles.
