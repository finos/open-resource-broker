# Dependency Injection Implementation

This document provides the comprehensive technical reference for the dependency injection system implemented in the Open Host Factory Plugin, including container management, service registration, dependency resolution patterns, and Clean Architecture compliance.

## Related Documentation

- **[Developer Guide: DI Implementation](../developer_guide/dependency_injection.md)** - Practical implementation guidance and examples
- **[Engineering: SOLID Principles](../engineering/solid_principles.md)** - Design principles including Dependency Inversion Principle
- **[Architecture: Clean Architecture](./clean_architecture.md)** - Overall architectural principles and layer structure
- **[Testing: DI Testing](../developer_guide/testing.md)** - Testing strategies for DI components

## Dependency Injection Overview

The plugin implements a comprehensive dependency injection (DI) system that:

- **Manages object lifecycles**: Singleton and transient instances
- **Resolves dependencies automatically**: Constructor injection with automatic resolution
- **Supports interface segregation**: Depend on abstractions, not implementations
- **Enables testability**: Easy mocking and testing through interface injection

## DI Container Implementation

### Core Container

The DI container is implemented in `src/infrastructure/di/container.py`:

```python
class DIContainer:
    """Comprehensive dependency injection container."""

    def __init__(self):
        self._singletons: Dict[Type, Any] = {}
        self._factories: Dict[Type, Callable] = {}
        self._transients: Dict[Type, Type] = {}
        self._instances: Dict[Type, Any] = {}

    def register_singleton(self, interface: Type, implementation: Optional[Type] = None):
        """Register a singleton service."""
        if implementation is None:
            implementation = interface

        self._singletons[interface] = implementation

    def register_factory(self, interface: Type, factory: Callable):
        """Register a factory function."""
        self._factories[interface] = factory

    def register_transient(self, interface: Type, implementation: Type):
        """Register a transient service."""
        self._transients[interface] = implementation

    def get(self, interface: Type) -> Any:
        """Resolve a dependency."""
        # Check for existing instance
        if interface in self._instances:
            return self._instances[interface]

        # Check for factory
        if interface in self._factories:
            instance = self._factories[interface](self)
            return instance

        # Check for singleton
        if interface in self._singletons:
            implementation = self._singletons[interface]
            instance = self._create_instance(implementation)
            self._instances[interface] = instance
            return instance

        # Check for transient
        if interface in self._transients:
            implementation = self._transients[interface]
            return self._create_instance(implementation)

        # Try to create directly if it has @injectable decorator
        if hasattr(interface, '__injectable__'):
            return self._create_instance(interface)

        raise DependencyNotFoundError(f"No registration found for {interface}")

    def _create_instance(self, implementation: Type) -> Any:
        """Create instance with dependency injection."""
        # Get constructor signature
        signature = inspect.signature(implementation.__init__)
        parameters = signature.parameters

        # Resolve constructor dependencies
        kwargs = {}
        for param_name, param in parameters.items():
            if param_name == 'self':
                continue

            param_type = param.annotation
            if param_type != inspect.Parameter.empty:
                kwargs[param_name] = self.get(param_type)

        return implementation(**kwargs)
```

### Injectable Decorator

The `@injectable` decorator marks classes for dependency injection:

```python
# src/domain/base/dependency_injection.py
def injectable(cls):
    """Decorator to mark classes as injectable."""
    cls.__injectable__ = True
    return cls

# Usage example
@injectable
class ApplicationService:
    def __init__(self, 
                 command_bus: CommandBus,
                 query_bus: QueryBus,
                 logger: LoggingPort):
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger
```

## Service Registration

Services are registered in dedicated modules within `src/infrastructure/di/` following a specific dependency order to ensure correct initialization.

### Service Registration Orchestration

The service registration process is orchestrated by `src/infrastructure/di/services.py` with the following dependency-aware order:

```python
def register_all_services(container: Optional[DIContainer] = None) -> DIContainer:
    """Register all services in dependency order."""

    # 1. Register scheduler strategies first (needed by port adapters)
    from src.infrastructure.scheduler.registration import register_all_scheduler_types
    register_all_scheduler_types()

    # 2. Register core services (includes SchedulerPort registration)
    register_core_services(container)

    # 3. Register port adapters (uses SchedulerPort from core services)
    from src.infrastructure.di.port_registrations import register_port_adapters
    register_port_adapters(container)

    # 4. Setup CQRS infrastructure (handlers and buses)
    from src.infrastructure.di.container import _setup_cqrs_infrastructure
    _setup_cqrs_infrastructure(container)

    # 5. Register provider services (needed by infrastructure services)
    register_provider_services(container)

    # 6. Register infrastructure services
    register_infrastructure_services(container)

    # 7. Register server services (conditionally based on config)
    register_server_services(container)

    return container
```

This registration order ensures that:
- **Scheduler strategies** are available before port adapters need them
- **Core services** (including SchedulerPort) are registered before dependent services
- **Port adapters** can properly resolve their dependencies from core services
- **CQRS infrastructure** is set up with all necessary dependencies available
- **Provider and infrastructure services** have access to all foundational components
- **Server services** are registered last as they depend on all other layers

### Registration Dependencies

The dependency graph for service registration follows this pattern:

```
Scheduler Strategies (Registry)
    ↓
Core Services (SchedulerPort, LoggingPort, ConfigurationPort)
    ↓
Port Adapters (Uses SchedulerPort from core services)
    ↓
CQRS Infrastructure (CommandBus, QueryBus, Handler Discovery)
    ↓
Provider Services (AWS, Strategy Factories)
    ↓
Infrastructure Services (Repositories, Templates)
    ↓
Server Services (FastAPI, REST API Handlers)
```

### Core Services Registration

```python
# src/infrastructure/di/core_services.py
def register_core_services(container: DIContainer) -> None:
    """Register core application services."""

    # Register logging port
    container.register_singleton(
        LoggingPort,
        lambda c: LoggingAdapter()
    )

    # Register configuration port
    container.register_singleton(
        ConfigurationPort,
        lambda c: ConfigurationAdapter(get_config_manager())
    )

    # Register error handling port
    container.register_singleton(
        ErrorHandlingPort,
        lambda c: ErrorHandlingAdapter(c.get(LoggingPort))
    )

    # Register container port
    container.register_singleton(
        ContainerPort,
        lambda c: ContainerAdapter(c)
    )

    # Register CQRS buses
    container.register_singleton(
        CommandBus,
        lambda c: CommandBus(
            logger=c.get(LoggingPort),
            event_publisher=c.get(EventPublisherPort)
        )
    )

    container.register_singleton(
        QueryBus,
        lambda c: QueryBus(logger=c.get(LoggingPort))
    )

    # Register application service
    container.register_singleton(ApplicationService)
```

### Provider Services Registration

```python
# src/infrastructure/di/provider_services.py
def register_provider_services(container: DIContainer) -> None:
    """Register provider-specific services."""

    # Register provider strategy factory
    container.register_factory(ProviderStrategyFactory, create_provider_strategy_factory)

    # Register provider context
    container.register_factory(ProviderContext, create_configured_provider_context)

    # Register AWS services if AWS provider is configured
    config_manager = container.get(ConfigurationManager)
    if _is_aws_provider_configured(config_manager):
        _register_aws_services(container)

def _register_aws_services(container: DIContainer) -> None:
    """Register AWS-specific services."""

    # Register AWS client
    container.register_singleton(AWSClient)

    # Register AWS operations
    container.register_singleton(AWSOperations)

    # Register AWS handler factory
    container.register_singleton(AWSHandlerFactory)

    # Register AWS adapters
    container.register_singleton(AWSTemplateAdapter)
    container.register_singleton(AWSMachineAdapter)
    container.register_singleton(AWSProvisioningAdapter)
    container.register_singleton(AWSRequestAdapter)
    container.register_singleton(AWSResourceManagerAdapter)

    # Register AWS provider strategy and adapter
    container.register_singleton(AWSProviderAdapter)
    container.register_singleton(AWSProviderStrategy)

    # Register AWS managers
    container.register_singleton(AWSInstanceManager)
    container.register_singleton(AWSResourceManagerImpl)
```

### Command and Query Handler Registration

```python
# src/infrastructure/di/command_handler_services.py
def register_command_handlers(container: DIContainer) -> None:
    """Register command handlers."""

    # Template command handlers
    container.register_singleton(ValidateTemplateHandler)
    container.register_singleton(ConvertTemplateHandler)

    # Request command handlers
    container.register_singleton(CreateRequestHandler)
    container.register_singleton(UpdateRequestStatusHandler)
    container.register_singleton(CompleteRequestHandler)

    # Machine command handlers
    container.register_singleton(ConvertMachineStatusCommandHandler)
    container.register_singleton(ConvertBatchMachineStatusCommandHandler)
    container.register_singleton(UpdateMachineStatusCommandHandler)
    container.register_singleton(CleanupMachineResourcesCommandHandler)

# src/infrastructure/di/query_handler_services.py
def register_query_handlers(container: DIContainer) -> None:
    """Register query handlers."""

    # Template query handlers
    container.register_singleton(GetTemplatesHandler)
    container.register_singleton(GetTemplateByIdHandler)

    # Request query handlers
    container.register_singleton(GetRequestsHandler)
    container.register_singleton(GetRequestByIdHandler)
    container.register_singleton(GetRequestStatusHandler)

    # Machine query handlers
    container.register_singleton(GetMachinesHandler)
    container.register_singleton(GetMachinesByRequestHandler)

    # System query handlers
    container.register_singleton(GetProviderConfigHandler)
    container.register_singleton(ValidateProviderConfigHandler)
```

## Port and Adapter Pattern

The DI system supports the ports and adapters pattern through interface segregation:

### Port Definitions

```python
# src/domain/base/ports/logging_port.py
class LoggingPort(ABC):
    """Abstract logging interface."""

    @abstractmethod
    def info(self, message: str) -> None:
        pass

    @abstractmethod
    def error(self, message: str) -> None:
        pass

    @abstractmethod
    def warning(self, message: str) -> None:
        pass

# src/domain/base/ports/configuration_port.py
class ConfigurationPort(ABC):
    """Abstract configuration interface."""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        pass

    @abstractmethod
    def get_section(self, section: str) -> Dict[str, Any]:
        pass
```

### Adapter Implementations

```python
# src/infrastructure/adapters/logging_adapter.py
class LoggingAdapter(LoggingPort):
    """Concrete logging implementation."""

    def __init__(self):
        self._logger = get_logger(__name__)

    def info(self, message: str) -> None:
        self._logger.info(message)

    def error(self, message: str) -> None:
        self._logger.error(message)

    def warning(self, message: str) -> None:
        self._logger.warning(message)

# src/infrastructure/adapters/configuration_adapter.py
class ConfigurationAdapter(ConfigurationPort):
    """Concrete configuration implementation."""

    def __init__(self, config_manager: ConfigurationManager):
        self._config_manager = config_manager

    def get(self, key: str, default: Any = None) -> Any:
        return self._config_manager.get(key, default)

    def get_section(self, section: str) -> Dict[str, Any]:
        return self._config_manager.get_section(section)
```

## Factory Pattern Integration

The DI container supports factory patterns for complex object creation:

### Provider Strategy Factory

```python
# src/infrastructure/factories/provider_strategy_factory.py
class ProviderStrategyFactory:
    """Factory for creating provider strategies."""

    def __init__(self, 
                 config_manager: ConfigurationPort,
                 logger: LoggingPort):
        self._config_manager = config_manager
        self._logger = logger

    def create_strategy(self, provider_type: str) -> ProviderStrategy:
        """Create provider strategy based on type."""
        if provider_type == "aws":
            return self._create_aws_strategy()
        else:
            raise UnsupportedProviderError(f"Provider type not supported: {provider_type}")

    def _create_aws_strategy(self) -> AWSProviderStrategy:
        """Create AWS provider strategy."""
        aws_config = self._config_manager.get_section("aws")
        return AWSProviderStrategy(
            config=AWSProviderConfig(**aws_config),
            logger=self._logger
        )

# Factory registration
def create_provider_strategy_factory(container: DIContainer) -> ProviderStrategyFactory:
    """Factory function for provider strategy factory."""
    return ProviderStrategyFactory(
        config_manager=container.get(ConfigurationPort),
        logger=container.get(LoggingPort)
    )
```

## Lifecycle Management

The DI container manages different object lifecycles:

### Singleton Lifecycle

```python
# Registered once, same instance returned for all requests
container.register_singleton(ApplicationService)

# Usage - same instance every time
service1 = container.get(ApplicationService)
service2 = container.get(ApplicationService)
assert service1 is service2  # True
```

### Transient Lifecycle

```python
# New instance created for each request
container.register_transient(RequestHandler, RequestHandlerImpl)

# Usage - different instances
handler1 = container.get(RequestHandler)
handler2 = container.get(RequestHandler)
assert handler1 is not handler2  # True
```

### Factory Lifecycle

```python
# Factory function called for each request
container.register_factory(
    DatabaseConnection,
    lambda c: create_database_connection(c.get(ConfigurationPort))
)
```

## Testing with Dependency Injection

The DI system enables easy testing through dependency substitution:

### Unit Testing

```python
def test_application_service():
    """Test application service with mocked dependencies."""
    # Create test container
    container = DIContainer()

    # Register mocks
    mock_command_bus = Mock(spec=CommandBus)
    mock_query_bus = Mock(spec=QueryBus)
    mock_logger = Mock(spec=LoggingPort)

    container.register_instance(CommandBus, mock_command_bus)
    container.register_instance(QueryBus, mock_query_bus)
    container.register_instance(LoggingPort, mock_logger)

    # Register service under test
    container.register_singleton(ApplicationService)

    # Get service with injected mocks
    service = container.get(ApplicationService)

    # Test service behavior
    # Mocks can be verified for interactions
```

### Integration Testing

```python
def test_with_real_dependencies():
    """Test with real dependencies but test configuration."""
    container = DIContainer()

    # Register real implementations with test configuration
    container.register_singleton(
        ConfigurationPort,
        lambda c: TestConfigurationAdapter()
    )

    # Register other services normally
    register_core_services(container)
    register_provider_services(container)

    # Test with real implementations
    service = container.get(ApplicationService)
    # Test actual behavior
```

## Configuration-Driven Registration

Services can be registered based on configuration:

```python
def register_storage_services(container: DIContainer) -> None:
    """Register storage services based on configuration."""
    config = container.get(ConfigurationPort)
    storage_type = config.get("storage.type", "memory")

    if storage_type == "dynamodb":
        container.register_singleton(
            TemplateRepository,
            DynamoDBTemplateRepository
        )
    elif storage_type == "memory":
        container.register_singleton(
            TemplateRepository,
            InMemoryTemplateRepository
        )
    else:
        raise ConfigurationError(f"Unsupported storage type: {storage_type}")
```

## Registry Pattern Integration

The DI system integrates with the registry pattern for strategy-based component selection:

### Scheduler Port Registration

The scheduler port uses the registry pattern for configuration-driven strategy selection:

```python
# src/infrastructure/di/port_registrations.py
def register_port_adapters(container):
    """Register all port adapters in the DI container."""

    # Register scheduler port adapter using registry pattern
    def create_scheduler_port(c):
        from src.infrastructure.registry.scheduler_registry import get_scheduler_registry
        from src.config.manager import get_config_manager

        config_manager = get_config_manager()
        scheduler_config = config_manager.get_scheduler_config()
        scheduler_type = scheduler_config.get('strategy', 'hostfactory')

        registry = get_scheduler_registry()
        return registry.get_active_strategy(scheduler_type, scheduler_config)

    container.register_singleton(
        SchedulerPort,
        create_scheduler_port
    )
```

This pattern provides several benefits:

- **Configuration-Driven**: Scheduler strategy selected based on configuration
- **Registry Integration**: Leverages the scheduler registry for strategy management
- **Lazy Loading**: Strategy created only when needed
- **Type Safety**: Maintains appropriate port/adapter abstraction
- **Testability**: Easy to mock or substitute strategies for testing

### Registry Factory Pattern

The registry pattern enables dynamic strategy creation:

```python
# Registry resolves strategy based on configuration
registry = get_scheduler_registry()
strategy = registry.get_active_strategy(scheduler_type, config)

# Strategy implements the port interface
assert isinstance(strategy, SchedulerPort)
```

This approach separates strategy selection logic from dependency injection, maintaining clean separation of concerns while enabling flexible configuration-driven behavior.

## Error Handling

The DI system provides comprehensive error handling:

```python
class DependencyInjectionError(Exception):
    """Base exception for DI errors."""
    pass

class DependencyNotFoundError(DependencyInjectionError):
    """Raised when a dependency cannot be resolved."""
    pass

class CircularDependencyError(DependencyInjectionError):
    """Raised when circular dependencies are detected."""
    pass

class RegistrationError(DependencyInjectionError):
    """Raised when service registration fails."""
    pass
```

## Benefits of DI Implementation

### Loose Coupling
- Components depend on abstractions, not implementations
- Easy to swap implementations
- Reduced coupling between components

### Testability
- Easy mocking of dependencies
- Isolated unit testing
- Integration testing with different configurations

### Maintainability
- Clear dependency relationships
- Single responsibility principle enforcement
- Easy to understand and modify

### Flexibility
- Runtime behavior modification through configuration
- Easy addition of new implementations
- Support for different deployment scenarios

This comprehensive dependency injection system provides the foundation for clean, testable, and maintainable code throughout the Open Host Factory Plugin.
