# CQRS Pattern Implementation

This document provides the comprehensive technical reference for the Command Query Responsibility Segregation (CQRS) pattern implementation in the Open Host Factory Plugin, including pattern structure, implementation details, and architectural considerations.

## Related Documentation

- **[Developer Guide: CQRS Implementation](../developer_guide/cqrs.md)** - Practical CQRS usage and examples
- **[Architecture: System Diagrams](./system_diagrams.md)** - Visual representations of CQRS flow
- **[Developer Guide: Dependency Injection](../developer_guide/dependency_injection.md)** - DI patterns used with CQRS
- **[Testing: CQRS Testing](../developer_guide/testing.md)** - Testing strategies for commands and queries

## CQRS Overview

CQRS separates read and write operations into distinct models:

- **Commands**: Operations that change system state
- **Queries**: Operations that retrieve system state
- **Handlers**: Dedicated processors for commands and queries
- **Buses**: Infrastructure for routing commands and queries to handlers

## Implementation Structure

### Command Side (Write Operations)

Commands represent intentions to change system state.

#### Command Definitions

**Base Command Structure**
```python
# src/application/dto/commands.py
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class BaseCommand:
    """Base class for all commands."""
    correlation_id: Optional[str] = None
    user_id: Optional[str] = None
```

**Specific Commands**
```python
@dataclass
class CreateRequestCommand(BaseCommand):
    """Command to create a new request."""
    template_id: str
    max_number: int
    attributes: Optional[Dict[str, Any]] = None

@dataclass
class UpdateMachineStatusCommand(BaseCommand):
    """Command to update machine status."""
    machine_id: str
    status: str
    metadata: Optional[Dict[str, Any]] = None
```

#### Command Handlers

**Template Command Handlers**
```python
# src/application/commands/template_handlers.py
from src.domain.template.repository import TemplateRepository
from src.domain.base.ports import LoggingPort

class ValidateTemplateHandler:
    """Handle template validation commands."""

    def __init__(self, 
                 template_repo: TemplateRepository,
                 logger: LoggingPort):
        self._template_repo = template_repo
        self._logger = logger

    async def handle(self, command: ValidateTemplateCommand) -> ValidationResult:
        """Handle template validation."""
        self._logger.info(f"Validating template: {command.template_id}")

        # Retrieve template
        template = await self._template_repo.get_by_id(command.template_id)
        if not template:
            raise TemplateNotFoundError(command.template_id)

        # Perform validation
        validation_result = template.validate_configuration()

        # Log result
        self._logger.info(f"Template validation result: {validation_result.is_valid}")

        return validation_result
```

**Request Command Handlers**
```python
# src/application/commands/request_handlers.py
class CreateRequestHandler:
    """Handle request creation commands."""

    def __init__(self,
                 request_repo: RequestRepository,
                 template_repo: TemplateRepository,
                 provider_context: ProviderContext,
                 logger: LoggingPort):
        self._request_repo = request_repo
        self._template_repo = template_repo
        self._provider_context = provider_context
        self._logger = logger

    async def handle(self, command: CreateRequestCommand) -> RequestId:
        """Handle request creation."""
        self._logger.info(f"Creating request for template: {command.template_id}")

        # Validate template exists
        template = await self._template_repo.get_by_id(command.template_id)
        if not template:
            raise TemplateNotFoundError(command.template_id)

        # Create request aggregate
        request = Request.create(
            template_id=command.template_id,
            max_number=command.max_number,
            attributes=command.attributes or {}
        )

        # Save request
        await self._request_repo.save(request)

        # Publish domain event
        request.publish_event(RequestCreatedEvent(request.id))

        self._logger.info(f"Request created: {request.id}")
        return request.id
```

### Query Side (Read Operations)

Queries represent requests for system state information.

#### Query Definitions

**Base Query Structure**
```python
# src/application/dto/queries.py
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

@dataclass
class BaseQuery:
    """Base class for all queries."""
    correlation_id: Optional[str] = None
    user_id: Optional[str] = None
```

**Specific Queries**
```python
@dataclass
class GetTemplatesQuery(BaseQuery):
    """Query to retrieve templates."""
    filters: Optional[Dict[str, Any]] = None
    limit: Optional[int] = None
    offset: Optional[int] = None

@dataclass
class GetRequestStatusQuery(BaseQuery):
    """Query to get request status."""
    request_id: str
    include_machines: bool = False
```

#### Query Handlers

**Template Query Handlers**
```python
# src/application/queries/template_handlers.py
class GetTemplatesHandler:
    """Handle template retrieval queries."""

    def __init__(self,
                 template_repo: TemplateRepository,
                 logger: LoggingPort):
        self._template_repo = template_repo
        self._logger = logger

    async def handle(self, query: GetTemplatesQuery) -> List[TemplateResponse]:
        """Handle template retrieval."""
        self._logger.info("Retrieving templates")

        # Apply filters
        templates = await self._template_repo.get_all(
            filters=query.filters,
            limit=query.limit,
            offset=query.offset
        )

        # Convert to response DTOs
        responses = [
            TemplateResponse.from_domain(template)
            for template in templates
        ]

        self._logger.info(f"Retrieved {len(responses)} templates")
        return responses
```

**Request Query Handlers**
```python
# src/application/queries/request_handlers.py
class GetRequestStatusHandler:
    """Handle request status queries."""

    def __init__(self,
                 request_repo: RequestRepository,
                 machine_repo: MachineRepository,
                 logger: LoggingPort):
        self._request_repo = request_repo
        self._machine_repo = machine_repo
        self._logger = logger

    async def handle(self, query: GetRequestStatusQuery) -> RequestStatusResponse:
        """Handle request status retrieval."""
        self._logger.info(f"Getting status for request: {query.request_id}")

        # Get request
        request = await self._request_repo.get_by_id(query.request_id)
        if not request:
            raise RequestNotFoundError(query.request_id)

        # Get machines if requested
        machines = []
        if query.include_machines:
            machines = await self._machine_repo.get_by_request_id(query.request_id)

        # Build response
        response = RequestStatusResponse(
            request_id=request.id,
            status=request.status,
            created_at=request.created_at,
            machines=[MachineResponse.from_domain(m) for m in machines]
        )

        return response
```

### CQRS Buses

The buses route commands and queries to their appropriate handlers.

#### Command Bus

```python
# src/application/base/commands.py
from typing import Dict, Type, Any
from src.domain.base.ports import LoggingPort

class CommandBus:
    """Bus for routing commands to handlers."""

    def __init__(self, logger: LoggingPort):
        self._handlers: Dict[Type, Any] = {}
        self._logger = logger

    def register_handler(self, command_type: Type, handler: Any):
        """Register a command handler."""
        self._handlers[command_type] = handler
        self._logger.info(f"Registered handler for {command_type.__name__}")

    async def execute(self, command: Any) -> Any:
        """Execute a command."""
        command_type = type(command)

        if command_type not in self._handlers:
            raise HandlerNotFoundError(f"No handler for {command_type.__name__}")

        handler = self._handlers[command_type]
        self._logger.info(f"Executing command: {command_type.__name__}")

        try:
            result = await handler.handle(command)
            self._logger.info(f"Command executed successfully: {command_type.__name__}")
            return result
        except Exception as e:
            self._logger.error(f"Command execution failed: {command_type.__name__}: {e}")
            raise
```

#### Query Bus

```python
# src/application/base/queries.py
class QueryBus:
    """Bus for routing queries to handlers."""

    def __init__(self, logger: LoggingPort):
        self._handlers: Dict[Type, Any] = {}
        self._logger = logger

    def register_handler(self, query_type: Type, handler: Any):
        """Register a query handler."""
        self._handlers[query_type] = handler
        self._logger.info(f"Registered handler for {query_type.__name__}")

    async def execute(self, query: Any) -> Any:
        """Execute a query."""
        query_type = type(query)

        if query_type not in self._handlers:
            raise HandlerNotFoundError(f"No handler for {query_type.__name__}")

        handler = self._handlers[query_type]
        self._logger.info(f"Executing query: {query_type.__name__}")

        try:
            result = await handler.handle(query)
            self._logger.info(f"Query executed successfully: {query_type.__name__}")
            return result
        except Exception as e:
            self._logger.error(f"Query execution failed: {query_type.__name__}: {e}")
            raise
```

## Application Service Integration

The ApplicationService coordinates CQRS operations.

```python
# src/application/service.py
@injectable
class ApplicationService:
    """Main application service using CQRS."""

    def __init__(self,
                 command_bus: CommandBus,
                 query_bus: QueryBus,
                 logger: LoggingPort):
        self._command_bus = command_bus
        self._query_bus = query_bus
        self._logger = logger

    async def create_request(self, template_id: str, max_number: int) -> str:
        """Create a new request using CQRS."""
        command = CreateRequestCommand(
            template_id=template_id,
            max_number=max_number
        )

        request_id = await self._command_bus.execute(command)
        return request_id

    async def get_templates(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Get templates using CQRS."""
        query = GetTemplatesQuery(filters=filters)

        templates = await self._query_bus.execute(query)
        return [template.to_dict() for template in templates]
```

## Interface Layer Integration

The interface layer uses CQRS through the ApplicationService.

### CLI Integration

```python
# src/interface/request_command_handlers.py
class RequestCommandHandler:
    """Handle CLI request commands using CQRS."""

    def __init__(self, application_service: ApplicationService):
        self._app_service = application_service

    async def handle_create_request(self, args):
        """Handle create request CLI command."""
        try:
            request_id = await self._app_service.create_request(
                template_id=args.template_id,
                max_number=args.count
            )

            print(f"Request created: {request_id}")

        except Exception as e:
            print(f"Error creating request: {e}")
```

### REST API Integration

```python
# src/api/routers/requests.py
@router.post("/requests")
async def create_request(
    request: CreateRequestModel,
    app_service: ApplicationService = Depends(get_application_service)
):
    """Create request via REST API using CQRS."""
    try:
        request_id = await app_service.create_request(
            template_id=request.template_id,
            max_number=request.max_number
        )

        return {"request_id": request_id}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

## Benefits of CQRS Implementation

### Separation of Concerns
- **Read operations** optimized for data retrieval
- **Write operations** optimized for data modification
- **Independent scaling** of read and write sides
- **Different data models** for reads and writes

### Performance Optimization
- **Query optimization**: Specialized query handlers
- **Command optimization**: Focused command processing
- **Caching strategies**: Read-side caching
- **Database optimization**: Separate read/write databases possible

### Maintainability
- **Single responsibility**: Each handler has one purpose
- **Easy testing**: Handlers can be tested independently
- **Clear boundaries**: Commands vs queries separation
- **Extensibility**: Easy to add new commands/queries

### Scalability
- **Independent deployment**: Command and query sides can be deployed separately
- **Load balancing**: Different scaling strategies for reads vs writes
- **Technology choices**: Different technologies for different sides
- **Performance tuning**: Optimize each side independently

## Error Handling in CQRS

### Command Error Handling
```python
class CreateRequestHandler:
    async def handle(self, command: CreateRequestCommand) -> RequestId:
        try:
            # Command processing
            return request_id
        except DomainException as e:
            # Domain-specific error handling
            self._logger.error(f"Domain error: {e}")
            raise
        except InfrastructureException as e:
            # Infrastructure error handling
            self._logger.error(f"Infrastructure error: {e}")
            raise
```

### Query Error Handling
```python
class GetTemplatesHandler:
    async def handle(self, query: GetTemplatesQuery) -> List[TemplateResponse]:
        try:
            # Query processing
            return templates
        except RepositoryException as e:
            # Repository error handling
            self._logger.error(f"Repository error: {e}")
            return []  # Return empty list for queries
```

This CQRS implementation provides clear separation between read and write operations, enabling better performance, maintainability, and scalability while maintaining clean architecture principles.
