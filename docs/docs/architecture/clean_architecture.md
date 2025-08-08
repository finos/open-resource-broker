# Clean Architecture Implementation

This document describes how the Open Host Factory Plugin implements Clean Architecture principles, ensuring separation of concerns, testability, and maintainability.

## Clean Architecture Principles

Clean Architecture organizes code into layers with clear dependency rules:

1. **Dependencies point inward**: Outer layers depend on inner layers, never the reverse
2. **Business logic is isolated**: Core business rules are independent of external concerns
3. **Interfaces define contracts**: Abstract interfaces separate layers
4. **Framework independence**: Business logic doesn't depend on frameworks

## Layer Implementation

### Domain Layer (Innermost)

The domain layer contains the core business logic and has no external dependencies.

#### Location
- `src/domain/`

#### Components

**Entities (Aggregates)**
```python
# src/domain/template/aggregate.py
class Template(BaseModel):
    """Template configuration representing VM template."""
    template_id: str
    max_number: int
    attributes: Dict[str, Any]

    def validate_configuration(self) -> bool:
        """Business rule: validate template configuration."""
        # Core business logic here
```

**Value Objects**
```python
# src/domain/machine/machine_status.py
class MachineStatus(Enum):
    """Machine status value object."""
    PENDING = "pending"
    RUNNING = "running"
    TERMINATED = "terminated"
```

**Domain Services**
```python
# src/domain/template/ami_resolver.py
class AMIResolver:
    """Domain service for AMI resolution logic."""

    def resolve_ami_id(self, ami_reference: str) -> str:
        """Business logic for AMI resolution."""
```

**Repository Interfaces**
```python
# src/domain/template/repository.py
class TemplateRepository(ABC):
    """Abstract repository interface."""

    @abstractmethod
    async def get_by_id(self, template_id: str) -> Optional[Template]:
        """Get template by ID."""
```

#### Characteristics
- **No external dependencies**: Pure business logic
- **Framework independent**: No FastAPI, SQLAlchemy, or AWS SDK dependencies
- **Testable**: Easy unit testing without external systems
- **Stable**: Changes rarely, only when business rules change

### Application Layer

The application layer orchestrates domain objects and implements use cases.

#### Location
- `src/application/`

#### Components

**Application Services**
```python
# src/application/service.py
@injectable
class ApplicationService:
    """Main application orchestrator."""

    def __init__(self, 
                 command_bus: CommandBus,
                 query_bus: QueryBus,
                 provider_context: ProviderContext):
        # Dependencies injected, not created
```

**Command Handlers (CQRS)**
```python
# src/application/commands/template_handlers.py
class GetTemplatesHandler:
    """Handle template retrieval commands."""

    def handle(self, command: GetTemplatesCommand) -> List[Template]:
        # Coordinate domain objects
```

**Query Handlers (CQRS)**
```python
# src/application/queries/handlers.py
class TemplateQueryHandler:
    """Handle template queries."""

    def handle(self, query: TemplateQuery) -> TemplateResponse:
        # Process queries using domain objects
```

**Data Transfer Objects**
```python
# src/application/dto/commands.py
class CreateRequestCommand:
    """Command for creating requests."""
    template_id: str
    max_number: int
```

#### Characteristics
- **Depends on domain layer**: Uses domain entities and services
- **Independent of infrastructure**: No database or external service dependencies
- **Use case focused**: Each handler represents a specific use case
- **Testable**: Can be tested with mock repositories

### Infrastructure Layer

The infrastructure layer implements external concerns and technical details.

#### Location
- `src/infrastructure/`
- `src/providers/`

#### Components

**Repository Implementations**
```python
# src/infrastructure/persistence/json/template_repository.py
class JSONTemplateRepository(TemplateRepository):
    """JSON implementation of template repository."""

    async def get_by_id(self, template_id: str) -> Optional[Template]:
        # JSON-specific implementation
```

**External Service Adapters**
```python
# src/providers/aws/managers/aws_instance_manager.py
@injectable
class AWSInstanceManager:
    """AWS-specific instance management."""

    def __init__(self, aws_client: AWSClient, logger: LoggingPort):
        # Infrastructure dependencies
```

**Dependency Injection Container**
```python
# src/infrastructure/di/container.py
class DIContainer:
    """Dependency injection container."""

    def register_singleton(self, interface: Type, implementation: Type):
        # DI container implementation
```

**Configuration Management**
```python
# src/infrastructure/config/manager.py
class ConfigurationManager:
    """Configuration management implementation."""
```

#### Characteristics
- **Implements interfaces**: Implements domain and application interfaces
- **External dependencies**: Database, cloud services, frameworks
- **Technology specific**: Contains technology-specific code
- **Replaceable**: Can be replaced without affecting business logic

### Interface Layer (Outermost)

The interface layer provides external access points to the system.

#### Location
- `src/interface/`
- `src/api/`
- `src/cli/`

#### Components

**CLI Interface**
```python
# src/cli/main.py
def main():
    """CLI entry point."""
    # Parse arguments
    # Call application services
    # Format output
```

**REST API Interface**
```python
# src/api/routers/templates.py
@router.get("/templates")
async def get_templates():
    """REST API endpoint."""
    # HTTP-specific handling
    # Call application services
    # Return JSON response
```

**Interface Command Handlers**
```python
# src/interface/template_command_handlers.py
class TemplateCommandHandler:
    """Handle CLI template commands."""

    def handle_list_templates(self, args):
        # CLI-specific processing
        # Call application layer
```

#### Characteristics
- **External facing**: Direct interaction with users/systems
- **Framework dependent**: Uses FastAPI, Click, etc.
- **Format specific**: Handles JSON, CLI output, etc.
- **Thin layer**: Minimal logic, delegates to application layer

## Dependency Rule Implementation

### Dependency Direction
```
Interface Layer  ->  Application Layer  ->  Domain Layer
      ^                      ^                 ^ 
Infrastructure Layer ---- ->                |
      ^                                    |
External Systems ---------------------- -> 
```

### Dependency Inversion Examples

**Repository Pattern**
```python
# Domain layer defines interface
class TemplateRepository(ABC):
    @abstractmethod
    async def get_by_id(self, template_id: str) -> Optional[Template]:
        pass

# Infrastructure layer implements interface
class JSONTemplateRepository(TemplateRepository):
    async def get_by_id(self, template_id: str) -> Optional[Template]:
        # JSON implementation
```

**Provider Strategy Pattern**
```python
# Domain layer defines interface
class ProviderStrategy(ABC):
    @abstractmethod
    async def provision_instances(self, request: Request) -> List[Machine]:
        pass

# Infrastructure layer implements interface
class AWSProviderStrategy(ProviderStrategy):
    async def provision_instances(self, request: Request) -> List[Machine]:
        # AWS implementation
```

## Benefits of Clean Architecture

### Testability

**Unit Testing Domain Logic**
```python
def test_template_validation():
    """Test domain logic without external dependencies."""
    template = Template(template_id="test", max_number=5, attributes={})
    assert template.validate_configuration() == True
```

**Integration Testing Application Layer**
```python
def test_create_request_handler():
    """Test application logic with mock repositories."""
    mock_repo = Mock(spec=RequestRepository)
    handler = CreateRequestHandler(mock_repo)
    # Test without real database
```

### Framework Independence

**Business Logic Isolation**
- Domain logic doesn't depend on FastAPI, SQLAlchemy, or AWS SDK
- Can switch from FastAPI to Flask without changing business logic
- Can switch from JSON to SQL storage without changing domain

**Technology Flexibility**
- Infrastructure implementations can be replaced
- New providers can be added without changing core logic
- Different storage backends can be used

### Maintainability

**Clear Boundaries**
- Each layer has specific responsibilities
- Changes in one layer don't affect others
- Easy to understand and modify

**Separation of Concerns**
- Business logic separated from technical concerns
- External dependencies isolated in infrastructure layer
- Interface concerns separated from business logic

## Implementation Patterns

### Dependency Injection

**Constructor Injection**
```python
@injectable
class ApplicationService:
    def __init__(self, 
                 template_repo: TemplateRepository,      # Abstraction, not implementation
                 logger: LoggingPort):                   # Abstraction, not implementation
        self._template_repo = template_repo
        self._logger = logger
```

**Interface Segregation**
```python
# Small, focused interfaces
class LoggingPort(ABC):
    @abstractmethod
    def info(self, message: str) -> None:
        pass

class ConfigurationPort(ABC):
    @abstractmethod
    def get(self, key: str) -> Any:
        pass
```

### CQRS Implementation

**Command Side (Write)**
```python
class CreateRequestCommand:
    template_id: str
    max_number: int

class CreateRequestHandler:
    def handle(self, command: CreateRequestCommand) -> RequestId:
        # Handle state changes
```

**Query Side (Read)**
```python
class GetTemplatesQuery:
    filters: Optional[Dict[str, Any]] = None

class GetTemplatesHandler:
    def handle(self, query: GetTemplatesQuery) -> List[Template]:
        # Handle data retrieval
```

### Repository Pattern

**Abstract Repository**
```python
class TemplateRepository(ABC):
    @abstractmethod
    async def get_all(self) -> List[Template]:
        pass

    @abstractmethod
    async def get_by_id(self, template_id: str) -> Optional[Template]:
        pass
```

**Concrete Implementation**
```python
class JSONTemplateRepository(TemplateRepository):
    def __init__(self, file_path: str, logger: LoggingPort):
        self._file_path = file_path
        self._logger = logger

    async def get_all(self) -> List[Template]:
        # JSON-specific implementation
```

## Testing Strategy

### Layer-Specific Testing

**Domain Layer Testing**
- Pure unit tests
- No external dependencies
- Fast execution
- High coverage

**Application Layer Testing**
- Unit tests with mocked repositories
- Integration tests with real repositories
- Use case validation

**Infrastructure Layer Testing**
- Integration tests with real external systems
- Contract tests for interfaces
- Performance tests

**Interface Layer Testing**
- End-to-end tests
- API contract tests
- CLI behavior tests

### Test Pyramid Implementation

```
    E2E Tests (Interface Layer)
         /\
        /  \
   Integration Tests (Application + Infrastructure)
      /\    /\
     /  \  /  \
Unit Tests (Domain + Application)
```

This Clean Architecture implementation ensures that the Open Host Factory Plugin maintains clear separation of concerns, high testability, and flexibility for future changes while adhering to established software engineering principles.
