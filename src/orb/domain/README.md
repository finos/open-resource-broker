# Domain Layer

The Domain Layer contains the core business logic and domain models. This is the heart of the application, implementing Domain-Driven Design (DDD) principles with rich domain models, business rules, and domain events.

## Architecture

```
domain/
├── base/              # Base domain classes and ports
├── machine/           # Machine aggregate and related concepts
├── template/          # Template aggregate and related concepts  
├── request/           # Request aggregate and related concepts
└── provider/          # Provider domain concepts
```

## Domain-Driven Design Principles

### Aggregates
Self-contained business entities that maintain consistency:

```python
class Machine(Aggregate):
    def __init__(self, machine_id: MachineId, template_id: TemplateId):
        super().__init__(machine_id)
        self._template_id = template_id
        self._status = MachineStatus.PENDING
        self._created_at = datetime.utcnow()

    def start(self) -> None:
        """Start the machine - business rule enforcement."""
        if self._status != MachineStatus.PENDING:
            raise DomainError("Can only start pending machines")

        self._status = MachineStatus.RUNNING
        self._add_domain_event(MachineStartedEvent(self.id))

    def stop(self) -> None:
        """Stop the machine - business rule enforcement."""
        if self._status not in [MachineStatus.RUNNING, MachineStatus.PENDING]:
            raise DomainError("Can only stop running or pending machines")

        self._status = MachineStatus.STOPPED
        self._add_domain_event(MachineStoppedEvent(self.id))
```

### Value Objects
Immutable objects that represent concepts without identity:

```python
@dataclass(frozen=True)
class MachineId:
    value: str

    def __post_init__(self):
        if not self.value or len(self.value) < 3:
            raise ValueError("Machine ID must be at least 3 characters")

@dataclass(frozen=True)
class InstanceConfiguration:
    instance_type: str
    cpu_count: int
    memory_gb: int

    def __post_init__(self):
        if self.cpu_count <= 0 or self.memory_gb <= 0:
            raise ValueError("CPU and memory must be positive")
```

### Domain Events
Events that represent something important that happened in the domain:

```python
@dataclass(frozen=True)
class MachineStartedEvent(DomainEvent):
    machine_id: MachineId
    started_at: datetime

    @property
    def event_type(self) -> str:
        return "MachineStarted"

@dataclass(frozen=True)
class TemplateValidatedEvent(DomainEvent):
    template_id: TemplateId
    validation_result: ValidationResult

    @property
    def event_type(self) -> str:
        return "TemplateValidated"
```

## Core Domain Concepts

### Machine Domain
Represents compute resources and their lifecycle:

- **Machine Aggregate**: Core machine entity with status management
- **MachineStatus**: Enumeration of valid machine states
- **MachineConfiguration**: Value object for machine settings
- **Machine Events**: Domain events for machine lifecycle

### Template Domain  
Represents machine templates and configurations:

- **Template Aggregate**: Template entity with validation rules
- **TemplateConfiguration**: Complex configuration value object
- **AMI Resolution**: Business logic for AMI selection
- **Template Events**: Domain events for template operations

### Request Domain
Represents machine provisioning requests:

- **Request Aggregate**: Request entity with workflow management
- **RequestStatus**: Enumeration of request states
- **Resource Allocation**: Business rules for resource assignment
- **Request Events**: Domain events for request lifecycle

### Provider Domain
Represents cloud provider abstractions:

- **Provider Strategy**: Abstract provider interface
- **Provider Configuration**: Provider-specific settings
- **Provider Capabilities**: What each provider supports
- **Provider Events**: Domain events for provider operations

## Ports (Interfaces)

The domain defines ports (interfaces) that infrastructure implements:

### Repository Ports
```python
class MachineRepositoryPort(ABC):
    @abstractmethod
    async def save(self, machine: Machine) -> None:
        """Save machine to persistence."""
        pass

    @abstractmethod
    async def find_by_id(self, machine_id: MachineId) -> Optional[Machine]:
        """Find machine by ID."""
        pass

    @abstractmethod
    async def find_by_status(self, status: MachineStatus) -> List[Machine]:
        """Find machines by status."""
        pass
```

### External Service Ports
```python
class CloudProviderPort(ABC):
    @abstractmethod
    async def provision_instance(self, config: InstanceConfiguration) -> ProvisionResult:
        """Provision cloud instance."""
        pass

    @abstractmethod
    async def terminate_instance(self, instance_id: str) -> None:
        """Terminate cloud instance."""
        pass
```

### Infrastructure Ports
```python
class LoggingPort(ABC):
    @abstractmethod
    def info(self, message: str) -> None:
        """Log info message."""
        pass

    @abstractmethod
    def error(self, message: str, exception: Optional[Exception] = None) -> None:
        """Log error message."""
        pass
```

## Business Rules

### Domain Services
Complex business logic that doesn't belong to a single aggregate:

```python
class MachineAllocationService:
    def __init__(self, machine_repo: MachineRepositoryPort):
        self.machine_repo = machine_repo

    async def can_allocate_machine(self, template: Template) -> bool:
        """Business rule: Check if machine can be allocated."""
        active_machines = await self.machine_repo.find_by_status(MachineStatus.RUNNING)

        # Business rule: Maximum 100 active machines
        if len(active_machines) >= 100:
            return False

        # Business rule: Check template resource requirements
        if template.requires_gpu and not self._gpu_available():
            return False

        return True

    def _gpu_available(self) -> bool:
        """Check if GPU resources are available."""
        # Implementation of GPU availability check
        return True
```

### Validation Rules
```python
class TemplateValidationService:
    def validate_template(self, template: Template) -> ValidationResult:
        """Validate template according to business rules."""
        errors = []

        # Business rule: Template must have valid instance type
        if not self._is_valid_instance_type(template.instance_type):
            errors.append("Invalid instance type")

        # Business rule: Memory must be sufficient for instance type
        if template.memory_gb < self._minimum_memory_for_type(template.instance_type):
            errors.append("Insufficient memory for instance type")

        return ValidationResult(is_valid=len(errors) == 0, errors=errors)
```

## Domain Events

### Event Publishing
Aggregates collect domain events that are published after successful operations:

```python
class Aggregate:
    def __init__(self, aggregate_id: Any):
        self.id = aggregate_id
        self._domain_events: List[DomainEvent] = []

    def _add_domain_event(self, event: DomainEvent) -> None:
        """Add domain event to be published."""
        self._domain_events.append(event)

    def get_domain_events(self) -> List[DomainEvent]:
        """Get all domain events."""
        return self._domain_events.copy()

    def clear_domain_events(self) -> None:
        """Clear domain events after publishing."""
        self._domain_events.clear()
```

### Event Types
```python
# Machine Events
class MachineCreatedEvent(DomainEvent):
    machine_id: MachineId
    template_id: TemplateId

class MachineStatusChangedEvent(DomainEvent):
    machine_id: MachineId
    old_status: MachineStatus
    new_status: MachineStatus

# Template Events  
class TemplateCreatedEvent(DomainEvent):
    template_id: TemplateId
    template_name: str

class TemplateValidatedEvent(DomainEvent):
    template_id: TemplateId
    is_valid: bool
    validation_errors: List[str]

# Request Events
class RequestSubmittedEvent(DomainEvent):
    request_id: RequestId
    template_id: TemplateId
    machine_count: int
```

## Exception Handling

### Domain Exceptions
```python
class DomainError(Exception):
    """Base class for domain errors."""
    pass

class BusinessRuleViolationError(DomainError):
    """Raised when business rules are violated."""
    pass

class InvalidStateTransitionError(DomainError):
    """Raised when invalid state transitions are attempted."""
    pass

class ResourceNotFoundError(DomainError):
    """Raised when required resources are not found."""
    pass
```

## Dependencies

### Zero External Dependencies
The domain layer has **NO dependencies** on other layers:
- No infrastructure imports
- No application imports  
- No interface imports
- Only standard library and domain code

### Dependency Inversion
The domain defines interfaces (ports) that other layers implement:
```python
# Domain defines the interface
class NotificationPort(ABC):
    @abstractmethod
    async def send_notification(self, message: str) -> None:
        pass

# Infrastructure implements the interface
class EmailNotificationAdapter(NotificationPort):
    async def send_notification(self, message: str) -> None:
        # Send email implementation
        pass
```

## Testing

### Unit Testing Domain Logic
```python
def test_machine_start():
    # Arrange
    machine = Machine(MachineId("test-123"), TemplateId("template-456"))

    # Act
    machine.start()

    # Assert
    assert machine.status == MachineStatus.RUNNING
    events = machine.get_domain_events()
    assert len(events) == 1
    assert isinstance(events[0], MachineStartedEvent)

def test_business_rule_violation():
    # Arrange
    machine = Machine(MachineId("test-123"), TemplateId("template-456"))
    machine.start()

    # Act & Assert
    with pytest.raises(DomainError):
        machine.start()  # Cannot start already running machine
```

### Domain Service Testing
```python
async def test_allocation_service():
    # Arrange
    mock_repo = Mock(spec=MachineRepositoryPort)
    mock_repo.find_by_status.return_value = []  # No active machines

    service = MachineAllocationService(mock_repo)
    template = Template(TemplateId("test"), instance_type="t3.micro")

    # Act
    can_allocate = await service.can_allocate_machine(template)

    # Assert
    assert can_allocate is True
```

## Best Practices

### Aggregate Design
1. **Keep Aggregates Small**: Focus on consistency boundaries
2. **Enforce Invariants**: Business rules enforced within aggregates
3. **Use Value Objects**: Immutable objects for concepts without identity
4. **Publish Events**: Use domain events for side effects

### Business Logic
1. **Rich Domain Models**: Logic in domain objects, not services
2. **Explicit Business Rules**: Clear, testable business rule implementation
3. **Domain Services**: For logic that spans multiple aggregates
4. **Validation**: Input validation and business rule enforcement

### Event Design
1. **Past Tense**: Events represent things that happened
2. **Immutable**: Events should be immutable data structures
3. **Specific**: Events should be specific to domain concepts
4. **Complete**: Include all relevant data in events

This Domain Layer provides a solid foundation for the business logic, ensuring that core business rules are properly encapsulated and that the domain remains independent of external concerns.
