# Application Layer

The Application Layer implements use cases and orchestrates domain operations through CQRS (Command Query Responsibility Segregation) patterns. This layer contains the business workflows and application-specific logic.

## Architecture

```
application/
├── base/              # Base classes for handlers
├── commands/          # Command handlers (write operations)
├── queries/           # Query handlers (read operations)  
├── events/            # Event handlers (side effects)
├── dto/               # Data Transfer Objects
├── decorators.py      # CQRS decorators
└── interfaces/        # Handler interfaces
```

## CQRS Implementation

### Command Handlers
Handle write operations that modify system state:

```python
@command_handler(CreateMachineCommand)
class CreateMachineHandler(BaseCommandHandler[CreateMachineCommand, MachineDTO]):
    async def execute_command(self, command: CreateMachineCommand) -> MachineDTO:
        # Validate command
        await self.validate_command(command)

        # Execute business logic
        machine = await self.machine_service.create_machine(command.template_id)

        # Publish events
        await self.publish_events([MachineCreatedEvent(machine.id)])

        return MachineDTO.from_domain(machine)
```

### Query Handlers
Handle read operations that retrieve data:

```python
@query_handler(ListMachinesQuery)
class ListMachinesHandler(BaseQueryHandler[ListMachinesQuery, List[MachineDTO]]):
    async def execute_query(self, query: ListMachinesQuery) -> List[MachineDTO]:
        machines = await self.machine_repository.find_all()
        return [MachineDTO.from_domain(m) for m in machines]
```

### Event Handlers
Handle domain events for side effects:

```python
@event_handler("MachineCreatedEvent")
class MachineCreatedHandler(BaseEventHandler[MachineCreatedEvent]):
    async def handle(self, event: MachineCreatedEvent) -> None:
        # Log machine creation
        self.logger.info(f"Machine created: {event.machine_id}")

        # Send notifications
        await self.notification_service.notify_machine_created(event)
```

## Handler Discovery System

Handlers are automatically discovered and registered using decorators:

### CQRS Decorators
- `@command_handler(CommandType)` - Registers command handlers
- `@query_handler(QueryType)` - Registers query handlers  
- `@event_handler("EventName")` - Registers event handlers

### Registration Process
1. **Discovery**: Decorators mark handlers during import
2. **Registration**: Handler Discovery System registers in DI container
3. **Resolution**: Handlers resolved automatically when needed

### Important Notes
- **Use ONLY CQRS decorators** on handlers (not `@injectable`)
- **Handler Discovery System** automatically handles DI registration
- **One decorator per handler** - don't mix CQRS and `@injectable`

## Base Classes

### BaseCommandHandler
Provides common command handling functionality:
- Command validation
- Event publishing
- Error handling
- Monitoring and logging

### BaseQueryHandler  
Provides common query handling functionality:
- Caching support
- Result formatting
- Error handling
- Performance monitoring

### BaseEventHandler
Provides common event handling functionality:
- Async event processing
- Error recovery
- Event logging
- Retry mechanisms

## Data Transfer Objects (DTOs)

### Purpose
- **API Contracts**: Define input/output structures
- **Type Safety**: Strong typing with Pydantic validation
- **Serialization**: Automatic JSON/camelCase conversion

### Example DTO
```python
class MachineDTO(BaseDTO):
    machine_id: str = Field(description="Unique machine identifier")
    status: str = Field(description="Current machine status")
    template_id: str = Field(description="Template used for machine")
    created_at: datetime = Field(description="Creation timestamp")

    @classmethod
    def from_domain(cls, machine: Machine) -> 'MachineDTO':
        return cls(
            machine_id=machine.id,
            status=machine.status.value,
            template_id=machine.template_id,
            created_at=machine.created_at
        )
```

## Error Handling

### Standardized Error Management
All handlers use `BaseHandler.handle_with_error_management()`:

```python
async def handle(self, command: MyCommand) -> MyResponse:
    return await self.handle_with_error_management(
        lambda: self.execute_command(command),
        context=f"command_handling_{self.__class__.__name__}"
    )
```

### Error Types
- **ValidationError**: Invalid input data
- **BusinessRuleError**: Domain rule violations  
- **InfrastructureError**: External system failures
- **NotFoundError**: Resource not found

## Dependencies

### Allowed Dependencies
- **Domain Layer**: Can import domain entities, value objects, ports
- **No Infrastructure**: Cannot directly import infrastructure implementations
- **No Interface**: Cannot import CLI or API layer components

### Dependency Injection
Services are injected through constructor parameters:

```python
class MyCommandHandler(BaseCommandHandler):
    def __init__(self, 
                 repository: MyRepositoryPort,
                 service: MyDomainService,
                 logger: LoggingPort):
        super().__init__(logger)
        self.repository = repository
        self.service = service
```

## Testing

### Unit Testing
Test handlers in isolation with mocked dependencies:

```python
@pytest.fixture
def handler(mock_repository, mock_logger):
    return MyCommandHandler(mock_repository, mock_logger)

async def test_command_execution(handler):
    command = MyCommand(data="test")
    result = await handler.handle(command)
    assert result.success is True
```

### Integration Testing
Test handler interactions with real dependencies:

```python
async def test_command_integration(container):
    handler = container.get(MyCommandHandler)
    command = MyCommand(data="test")
    result = await handler.handle(command)
    # Verify database changes, events published, etc.
```

## Performance Considerations

### Async Operations
- All handlers are async for better concurrency
- Use `await` for I/O operations
- Avoid blocking operations in handlers

### Caching
- Query handlers support caching via `get_cache_key()`
- Implement caching for expensive read operations
- Cache invalidation on relevant commands

### Monitoring
- All handlers include performance monitoring
- Metrics collected automatically
- Error rates and response times tracked

## Best Practices

### Handler Design
1. **Single Responsibility**: One handler per command/query/event
2. **Async First**: All operations should be async
3. **Error Handling**: Use standardized error management
4. **Validation**: Validate inputs before processing
5. **Events**: Publish events for side effects

### Code Organization
1. **Group by Feature**: Related handlers in same module
2. **Clear Naming**: Handler names match their purpose
3. **Consistent Patterns**: Follow established conventions
4. **Documentation**: Document complex business logic

### Testing Strategy
1. **Unit Tests**: Test handler logic in isolation
2. **Integration Tests**: Test with real dependencies
3. **Contract Tests**: Verify DTO contracts
4. **Performance Tests**: Test under load

## Common Patterns

### Command Validation
```python
async def validate_command(self, command: MyCommand) -> None:
    if not command.required_field:
        raise ValidationError("Required field missing")

    if not await self.business_rule_service.is_valid(command):
        raise BusinessRuleError("Business rule violation")
```

### Event Publishing
```python
async def execute_command(self, command: MyCommand) -> MyResponse:
    # Execute business logic
    result = await self.domain_service.process(command)

    # Publish domain events
    events = [MyDomainEvent(result.id, result.data)]
    await self.publish_events(events)

    return MyResponse.from_domain(result)
```

### Query Caching
```python
def get_cache_key(self, query: MyQuery) -> Optional[str]:
    return f"my_query_{query.filter_id}_{query.page}"

def is_cacheable(self, query: MyQuery, result: MyResult) -> bool:
    return len(result.items) > 0  # Only cache non-empty results
```

This Application Layer provides a robust, scalable foundation for implementing business use cases while maintaining clean architectural boundaries and following CQRS best practices.
