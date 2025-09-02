# SOLID Principles Implementation

This document describes how the Open Host Factory Plugin implements the SOLID principles of object-oriented design, demonstrating practical applications of these fundamental design principles.

## SOLID Principles Overview

The SOLID principles are:

1. **S**ingle Responsibility Principle (SRP)
2. **O**pen/Closed Principle (OCP)
3. **L**iskov Substitution Principle (LSP)
4. **I**nterface Segregation Principle (ISP)
5. **D**ependency Inversion Principle (DIP)

## Dependency Inversion Principle (DIP)

The Dependency Inversion Principle states:

1. **High-level modules should not depend on low-level modules. Both should depend on abstractions.**
2. **Abstractions should not depend on details. Details should depend on abstractions.**

### DIP Implementation in the Plugin

The plugin implements DIP through:

- **Abstract interfaces (ports)**: Define contracts for external dependencies
- **Concrete implementations (adapters)**: Implement the abstract interfaces
- **Dependency injection**: Inject abstractions into high-level modules
- **Inversion of control**: Framework manages dependency creation and injection

### High-Level Modules Depending on Abstractions

#### Application Service Dependencies

The ApplicationService (high-level module) depends only on abstractions:

```python
# src/application/service.py
@injectable
class ApplicationService:
    """High-level module - depends only on abstractions."""

    def __init__(self,
                 provider_type: str,
                 command_bus: CommandBus,           # Abstraction
                 query_bus: QueryBus,               # Abstraction
                 logger: LoggingPort,               # Abstraction
                 container: ContainerPort,          # Abstraction
                 config: ConfigurationPort,         # Abstraction
                 error_handler: ErrorHandlingPort,  # Abstraction
                 provider_context: ProviderContext): # Abstraction
        """All dependencies are abstractions, not concrete implementations."""

        # High-level module doesn't know about:
        # - Specific logging implementation (Python logging, structured logging, etc.)
        # - Specific configuration format (YAML, JSON, environment variables)
        # - Specific DI container implementation
        # - Specific provider implementation (AWS, Azure, etc.)

        self._provider_type = provider_type
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger
        self._container = container
        self._config = config
        self._error_handler = error_handler
        self._provider_context = provider_context
```

### Abstractions (Ports)

The plugin defines abstractions in the domain layer:

```python
# src/domain/ports/logging_port.py
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

class LoggingPort(ABC):
    """Abstraction for logging functionality."""

    @abstractmethod
    def info(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log info message."""
        pass

    @abstractmethod
    def error(self, message: str, exception: Optional[Exception] = None) -> None:
        """Log error message."""
        pass

    @abstractmethod
    def debug(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log debug message."""
        pass
```

### Concrete Implementations (Adapters)

Concrete implementations are provided in the infrastructure layer:

```python
# src/infrastructure/adapters/python_logging_adapter.py
import logging
from typing import Any, Dict, Optional
from src.domain.ports.logging_port import LoggingPort

class PythonLoggingAdapter(LoggingPort):
    """Concrete implementation using Python's logging module."""

    def __init__(self, logger_name: str = __name__):
        self._logger = logging.getLogger(logger_name)

    def info(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log info message using Python logging."""
        self._logger.info(message, extra=extra)

    def error(self, message: str, exception: Optional[Exception] = None) -> None:
        """Log error message using Python logging."""
        if exception:
            self._logger.error(f"{message}: {str(exception)}", exc_info=True)
        else:
            self._logger.error(message)

    def debug(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log debug message using Python logging."""
        self._logger.debug(message, extra=extra)
```

### Benefits of DIP Implementation

1. **Flexibility**: Easy to swap implementations without changing high-level modules
2. **Testability**: Easy to mock dependencies for unit testing
3. **Maintainability**: Changes to low-level modules don't affect high-level modules
4. **Extensibility**: New implementations can be added without modifying existing code

### DIP in Practice: Provider Strategy

The provider strategy pattern demonstrates DIP:

```python
# High-level module depends on abstraction
class ProviderContext:
    def __init__(self, strategy: ProviderStrategy):  # Abstraction
        self._strategy = strategy

    def execute_request(self, request: MachineRequest) -> MachineResponse:
        return self._strategy.handle_request(request)

# Abstraction
class ProviderStrategy(ABC):
    @abstractmethod
    def handle_request(self, request: MachineRequest) -> MachineResponse:
        pass

# Concrete implementations
class AWSProviderStrategy(ProviderStrategy):
    def handle_request(self, request: MachineRequest) -> MachineResponse:
        # AWS-specific implementation
        pass

class AzureProviderStrategy(ProviderStrategy):
    def handle_request(self, request: MachineRequest) -> MachineResponse:
        # Azure-specific implementation
        pass
```

## Other SOLID Principles

### Single Responsibility Principle (SRP)

Each class has a single reason to change:

```python
# Good: Single responsibility
class MachineRequestValidator:
    """Only responsible for validating machine requests."""

    def validate(self, request: MachineRequest) -> ValidationResult:
        # Validation logic only
        pass

class MachineRequestProcessor:
    """Only responsible for processing machine requests."""

    def process(self, request: MachineRequest) -> MachineResponse:
        # Processing logic only
        pass
```

### Open/Closed Principle (OCP)

Classes are open for extension but closed for modification:

```python
# Base class closed for modification
class BaseHandler(ABC):
    @abstractmethod
    def handle(self, request: Any) -> Any:
        pass

# Extended through inheritance, not modification
class MachineRequestHandler(BaseHandler):
    def handle(self, request: MachineRequest) -> MachineResponse:
        # Specific implementation
        pass
```

### Liskov Substitution Principle (LSP)

Derived classes must be substitutable for their base classes:

```python
# Base class
class ProviderStrategy(ABC):
    @abstractmethod
    def handle_request(self, request: MachineRequest) -> MachineResponse:
        pass

# Derived classes are fully substitutable
class AWSProviderStrategy(ProviderStrategy):
    def handle_request(self, request: MachineRequest) -> MachineResponse:
        # AWS implementation - fully compatible with base contract
        return MachineResponse(...)

class AzureProviderStrategy(ProviderStrategy):
    def handle_request(self, request: MachineRequest) -> MachineResponse:
        # Azure implementation - fully compatible with base contract
        return MachineResponse(...)
```

### Interface Segregation Principle (ISP)

Clients should not be forced to depend on interfaces they don't use:

```python
# Good: Segregated interfaces
class ReadablePort(ABC):
    @abstractmethod
    def read(self) -> Any:
        pass

class WritablePort(ABC):
    @abstractmethod
    def write(self, data: Any) -> None:
        pass

# Clients depend only on what they need
class Reader:
    def __init__(self, readable: ReadablePort):  # Only needs read capability
        self._readable = readable

class Writer:
    def __init__(self, writable: WritablePort):  # Only needs write capability
        self._writable = writable
```

## Related Documentation

- [Architecture: Dependency Injection](../architecture/dependency_injection.md) - Comprehensive DI technical reference
- [Developer Guide: Dependency Injection](../developer_guide/dependency_injection.md) - Practical DI implementation
- [Architecture: Clean Architecture](../architecture/clean_architecture.md) - Overall architectural principles
