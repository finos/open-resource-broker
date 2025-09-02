# Separation of Concerns Implementation

This document describes how the Open Host Factory Plugin implements Separation of Concerns (SoC), demonstrating clear boundaries between different responsibilities and how the Single Responsibility Principle (SRP) is applied throughout the architecture.

## Separation of Concerns Overview

Separation of Concerns is a design principle that divides a system into distinct sections, each addressing a separate concern. In the plugin, this is achieved through:

- **Layer separation**: Clear boundaries between domain, application, infrastructure, and interface layers
- **Component isolation**: Each component has a single, well-defined responsibility
- **Interface segregation**: Focused interfaces that separate different concerns
- **Dependency direction**: Dependencies flow in one direction, maintaining separation

## Architectural Layer Separation

### Domain Layer Concerns

The domain layer is concerned only with business logic and rules:

```python
# src/domain/template/aggregate.py
class Template(AggregateRoot):
    """Domain concern: Template business logic and rules."""

    template_id: str
    max_number: int
    attributes: Dict[str, Any]

    def validate_configuration(self) -> bool:
        """Business concern: Template validation rules."""
        # Only contains business validation logic
        # No infrastructure, persistence, or UI concerns
        return (
            self._validate_max_number() and
            self._validate_required_attributes() and
            self._validate_business_constraints()
        )

    def _validate_max_number(self) -> bool:
        """Business rule: max_number constraints."""
        return 1 <= self.max_number <= 1000

    def _validate_required_attributes(self) -> bool:
        """Business rule: required attribute validation."""
        required_attrs = ['vm_type', 'image_id']
        return all(attr in self.attributes for attr in required_attrs)

    def calculate_estimated_cost(self) -> float:
        """Business concern: Cost calculation logic."""
        # Pure business logic - no external dependencies
        base_cost = self.attributes.get('hourly_rate', 0.10)
        return base_cost * self.max_number

# src/domain/request/aggregate.py
class Request(AggregateRoot):
    """Domain concern: Request business logic."""

    def can_be_fulfilled(self, available_capacity: int) -> bool:
        """Business concern: Fulfillment logic."""
        # Pure business logic
        return available_capacity >= self.max_number

    def transition_to_status(self, new_status: RequestStatus) -> None:
        """Business concern: State transition rules."""
        # Business rules for valid state transitions
        valid_transitions = {
            RequestStatus.PENDING: [RequestStatus.PROCESSING, RequestStatus.CANCELLED],
            RequestStatus.PROCESSING: [RequestStatus.COMPLETED, RequestStatus.FAILED],
            RequestStatus.COMPLETED: [],
            RequestStatus.FAILED: [RequestStatus.PENDING],
            RequestStatus.CANCELLED: []
        }

        if new_status not in valid_transitions.get(self.status, []):
            raise InvalidStateTransitionError(f"Cannot transition from {self.status} to {new_status}")

        self.status = new_status
        self.add_domain_event(RequestStatusChangedEvent(self.id, new_status))
```

### Application Layer Concerns

The application layer is concerned with use cases and orchestration:

```python
# src/application/commands/request_handlers.py
class CreateRequestHandler:
    """Application concern: Request creation use case."""

    def __init__(self,
                 request_repo: RequestRepository,
                 template_repo: TemplateRepository,
                 logger: LoggingPort):
        # Application layer dependencies - no infrastructure details
        self._request_repo = request_repo
        self._template_repo = template_repo
        self._logger = logger

    async def handle(self, command: CreateRequestCommand) -> str:
        """Application concern: Orchestrate request creation."""
        self._logger.info(f"Creating request for template: {command.template_id}")

        # Application logic: validate template exists
        template = await self._template_repo.get_by_id(command.template_id)
        if not template:
            raise TemplateNotFoundError(command.template_id)

        # Application logic: validate template can fulfill request
        if not template.can_fulfill_request(command.max_number):
            raise InsufficientCapacityError(f"Template cannot fulfill {command.max_number} instances")

        # Domain logic: create request
        request = Request.create(
            template_id=command.template_id,
            max_number=command.max_number,
            attributes=command.attributes
        )

        # Application logic: persist request
        await self._request_repo.save(request)

        self._logger.info(f"Request created: {request.id}")
        return request.id

# src/application/queries/template_handlers.py
class GetTemplatesHandler:
    """Application concern: Template retrieval use case."""

    def __init__(self,
                 template_repo: TemplateRepository,
                 logger: LoggingPort):
        self._template_repo = template_repo
        self._logger = logger

    async def handle(self, query: GetTemplatesQuery) -> List[TemplateResponse]:
        """Application concern: Orchestrate template retrieval."""
        self._logger.info("Retrieving templates")

        # Application logic: retrieve templates with filters
        templates = await self._template_repo.get_all(
            filters=query.filters,
            limit=query.limit,
            offset=query.offset
        )

        # Application logic: convert to response DTOs
        responses = []
        for template in templates:
            response = TemplateResponse(
                template_id=template.template_id,
                max_number=template.max_number,
                attributes=template.attributes,
                estimated_cost=template.calculate_estimated_cost()  # Uses domain logic
            )
            responses.append(response)

        return responses
```

### Infrastructure Layer Concerns

The infrastructure layer is concerned with external systems and technical implementation:

```python
# src/infrastructure/persistence/dynamodb/template_repository.py
class DynamoDBTemplateRepository(TemplateRepository):
    """Infrastructure concern: DynamoDB persistence implementation."""

    def __init__(self, table_name: str, region: str, logger: LoggingPort):
        # Infrastructure concerns: AWS configuration
        self._table_name = table_name
        self._region = region
        self._logger = logger
        self._dynamodb = boto3.resource('dynamodb', region_name=region)
        self._table = self._dynamodb.Table(table_name)

    async def get_by_id(self, template_id: str) -> Optional[Template]:
        """Infrastructure concern: DynamoDB data retrieval."""
        try:
            # Infrastructure logic: DynamoDB operations
            response = self._table.get_item(Key={'template_id': template_id})

            if 'Item' not in response:
                return None

            # Infrastructure concern: Data transformation
            return self._item_to_domain_object(response['Item'])

        except ClientError as e:
            # Infrastructure concern: AWS error handling
            self._logger.error(f"DynamoDB error retrieving template {template_id}: {e}")
            raise RepositoryError(f"Failed to retrieve template: {e}")

    def _item_to_domain_object(self, item: Dict[str, Any]) -> Template:
        """Infrastructure concern: DynamoDB to domain object conversion."""
        return Template(
            template_id=item['template_id'],
            max_number=int(item['max_number']),
            attributes=item.get('attributes', {})
        )

# src/providers/aws/infrastructure/aws_client.py
class AWSClient:
    """Infrastructure concern: AWS service client management."""

    def __init__(self, config: AWSProviderConfig, logger: LoggingPort):
        # Infrastructure concerns: AWS configuration and client management
        self._config = config
        self._logger = logger
        self._clients: Dict[str, Any] = {}
        self._session = self._create_session()

    def get_client(self, service_name: str) -> Any:
        """Infrastructure concern: AWS client creation and caching."""
        if service_name not in self._clients:
            # Infrastructure logic: AWS client creation
            self._clients[service_name] = self._session.client(
                service_name,
                region_name=self._config.region,
                config=self._get_client_config()
            )
            self._logger.debug(f"Created AWS {service_name} client")

        return self._clients[service_name]

    def _create_session(self) -> boto3.Session:
        """Infrastructure concern: AWS session management."""
        if self._config.profile:
            return boto3.Session(profile_name=self._config.profile)
        else:
            return boto3.Session()
```

### Interface Layer Concerns

The interface layer is concerned with external communication and presentation:

```python
# src/api/routers/templates.py
@router.get("/templates")
async def get_templates(
    limit: Optional[int] = Query(None, ge=1, le=100),
    offset: Optional[int] = Query(None, ge=0),
    provider_type: Optional[str] = Query(None),
    app_service: ApplicationService = Depends(get_application_service)
) -> List[TemplateResponse]:
    """Interface concern: HTTP API endpoint for template retrieval."""

    try:
        # Interface concern: HTTP parameter validation and conversion
        filters = {}
        if provider_type:
            filters['provider_type'] = provider_type

        # Interface concern: Call application layer
        query = GetTemplatesQuery(
            filters=filters,
            limit=limit,
            offset=offset
        )

        templates = await app_service.get_templates(query)

        # Interface concern: HTTP response formatting
        return [TemplateResponse.model_validate(template) for template in templates]

    except TemplateNotFoundError as e:
        # Interface concern: HTTP error handling
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        # Interface concern: HTTP validation error handling
        raise HTTPException(status_code=400, detail=str(e))

# src/cli/formatters.py
class TemplateFormatter:
    """Interface concern: CLI output formatting."""

    def __init__(self, field_mapper: FieldMapper):
        self._field_mapper = field_mapper

    def format_templates(self, 
                        templates: List[Dict[str, Any]], 
                        output_format: str,
                        long_format: bool = False) -> str:
        """Interface concern: CLI output formatting logic."""

        if output_format == "table":
            return self._format_as_table(templates, long_format)
        elif output_format == "json":
            return self._format_as_json(templates, long_format)
        elif output_format == "yaml":
            return self._format_as_yaml(templates, long_format)
        else:
            return self._format_as_list(templates, long_format)

    def _format_as_table(self, templates: List[Dict[str, Any]], long_format: bool) -> str:
        """Interface concern: Table formatting logic."""
        # CLI-specific formatting logic
        # No business logic or infrastructure concerns
        pass
```

## Component Responsibility Separation

### Configuration Management Separation

```python
# src/config/manager.py
class ConfigurationManager:
    """Single concern: Configuration management."""

    def __init__(self, config_path: Optional[str] = None):
        # Only concerned with configuration loading and management
        self._config_data = {}
        self._config_path = config_path
        self._load_configuration()

    def get(self, key: str, default: Any = None) -> Any:
        """Single concern: Configuration value retrieval."""
        # Only handles configuration access logic
        pass

    def _load_configuration(self) -> None:
        """Single concern: Configuration loading."""
        # Only handles file loading and parsing
        pass

# src/config/validation/validator.py
class ConfigurationValidator:
    """Single concern: Configuration validation."""

    def validate_provider_config(self, config: Dict[str, Any]) -> ValidationResult:
        """Single concern: Provider configuration validation."""
        # Only handles validation logic
        pass

    def validate_storage_config(self, config: Dict[str, Any]) -> ValidationResult:
        """Single concern: Storage configuration validation."""
        # Only handles storage validation
        pass

# src/config/schemas/provider_schema.py
class ProviderConfigSchema:
    """Single concern: Provider configuration schema definition."""

    # Only defines configuration structure
    # No validation or loading logic
    pass
```

### Logging Responsibility Separation

```python
# src/infrastructure/logging/logger.py
def get_logger(name: str) -> logging.Logger:
    """Single concern: Logger creation."""
    # Only handles logger setup and configuration
    pass

# src/infrastructure/adapters/logging_adapter.py
class LoggingAdapter(LoggingPort):
    """Single concern: Logging port implementation."""

    def info(self, message: str, **kwargs) -> None:
        """Single concern: Info level logging."""
        # Only handles logging operation
        pass

# src/infrastructure/logging/formatters.py
class StructuredFormatter(logging.Formatter):
    """Single concern: Log message formatting."""

    def format(self, record: logging.LogRecord) -> str:
        """Single concern: Format log records."""
        # Only handles log formatting
        pass
```

### Error Handling Separation

```python
# src/domain/base/exceptions.py
class DomainException(Exception):
    """Domain concern: Domain-specific exceptions."""
    pass

class TemplateValidationError(DomainException):
    """Domain concern: Template validation errors."""
    pass

# src/infrastructure/error/exceptions.py
class InfrastructureException(Exception):
    """Infrastructure concern: Infrastructure-specific exceptions."""
    pass

class RepositoryError(InfrastructureException):
    """Infrastructure concern: Repository operation errors."""
    pass

# src/infrastructure/error/exception_handler.py
class ExceptionHandler:
    """Single concern: Exception handling and conversion."""

    def handle_domain_exception(self, ex: DomainException) -> ErrorResponse:
        """Single concern: Domain exception handling."""
        # Only handles domain exception conversion
        pass

    def handle_infrastructure_exception(self, ex: InfrastructureException) -> ErrorResponse:
        """Single concern: Infrastructure exception handling."""
        # Only handles infrastructure exception conversion
        pass
```

## Interface Segregation for Separation

### Port Segregation

```python
# Separate ports for different concerns

# src/domain/base/ports/logging_port.py
class LoggingPort(ABC):
    """Single concern: Logging operations."""

    @abstractmethod
    def info(self, message: str) -> None:
        pass

# src/domain/base/ports/configuration_port.py
class ConfigurationPort(ABC):
    """Single concern: Configuration access."""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        pass

# src/domain/base/ports/container_port.py
class ContainerPort(ABC):
    """Single concern: Dependency injection."""

    @abstractmethod
    def get(self, interface: Type[T]) -> T:
        pass

# Components depend only on their specific concerns
class ApplicationService:
    def __init__(self,
                 logger: LoggingPort,        # Only logging concern
                 config: ConfigurationPort,  # Only configuration concern
                 container: ContainerPort):  # Only DI concern
        # Each dependency addresses a single concern
        pass
```

### Repository Interface Segregation

```python
# Separate interfaces for different data access concerns

# src/domain/base/repository_ports.py
class Reader(ABC, Generic[T]):
    """Single concern: Read operations."""

    @abstractmethod
    async def get_by_id(self, entity_id: str) -> Optional[T]:
        pass

    @abstractmethod
    async def get_all(self) -> List[T]:
        pass

class Writer(ABC, Generic[T]):
    """Single concern: Write operations."""

    @abstractmethod
    async def save(self, entity: T) -> None:
        pass

    @abstractmethod
    async def delete(self, entity_id: str) -> bool:
        pass

class Searcher(ABC, Generic[T]):
    """Single concern: Search operations."""

    @abstractmethod
    async def find_by_criteria(self, criteria: Dict[str, Any]) -> List[T]:
        pass

# Components depend only on needed concerns
class TemplateQueryHandler:
    def __init__(self, reader: Reader[Template]):  # Only needs read access
        self._reader = reader

class TemplateCommandHandler:
    def __init__(self, writer: Writer[Template]):  # Only needs write access
        self._writer = writer

class TemplateSearchHandler:
    def __init__(self, searcher: Searcher[Template]):  # Only needs search access
        self._searcher = searcher
```

## Benefits of Separation of Concerns

### Maintainability
- **Isolated changes**: Changes to one concern don't affect others
- **Clear boundaries**: Easy to understand what each component does
- **Focused testing**: Each concern can be tested independently

### Flexibility
- **Independent evolution**: Different concerns can evolve at different rates
- **Technology substitution**: Infrastructure concerns can be replaced without affecting business logic
- **Parallel development**: Different teams can work on different concerns

### Reusability
- **Component reuse**: Well-separated components can be reused in different contexts
- **Interface reuse**: Clean interfaces can be implemented by different components
- **Logic reuse**: Business logic is independent of infrastructure and can be reused

### Testability
- **Unit testing**: Each concern can be unit tested in isolation
- **Mock substitution**: Interfaces enable easy mocking of dependencies
- **Integration testing**: Different concerns can be tested together systematically

### Code Quality
- **Single responsibility**: Each component has one reason to change
- **Reduced complexity**: Separation reduces overall system complexity
- **Better abstraction**: Clear separation leads to better abstractions

The Separation of Concerns implementation in the Open Host Factory Plugin creates a well-structured, maintainable system where each component has a clear, single responsibility and dependencies flow in the correct direction according to Clean Architecture principles.
