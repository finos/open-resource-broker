# DRY Principle Implementation

This document describes how the Open Host Factory Plugin implements the DRY (Don't Repeat Yourself) principle, showing examples of code reuse, abstraction, and configuration-driven behavior that eliminate duplication throughout the codebase.

## DRY Principle Overview

The DRY principle states that "Every piece of knowledge must have a single, unambiguous, authoritative representation within a system." This means:

- **Avoid code duplication**: Don't repeat the same logic in multiple places
- **Single source of truth**: Each piece of knowledge should exist in exactly one place
- **Abstraction over repetition**: Use abstractions to eliminate repeated patterns
- **Configuration over code**: Use configuration to drive behavior instead of duplicating code

## Code Reuse Through Base Classes

### Base Entity and Aggregate Root

Instead of duplicating common entity behavior, the plugin uses base classes:

```python
# src/domain/base/entity.py
class Entity(BaseModel):
    """Base entity with common functionality."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    def __eq__(self, other) -> bool:
        """Common equality logic - not repeated in each entity."""
        if not isinstance(other, self.__class__):
            return False
        return self.get_identity() == other.get_identity()

    def __hash__(self) -> int:
        """Common hash logic - not repeated in each entity."""
        return hash(self.get_identity())

    @abstractmethod
    def get_identity(self) -> Any:
        """Each entity defines its identity."""
        pass

class AggregateRoot(Entity):
    """Base aggregate root with event handling."""

    def __init__(self, **data):
        super().__init__(**data)
        self._domain_events: List[DomainEvent] = []

    def add_domain_event(self, event: DomainEvent) -> None:
        """Common event handling - not repeated in each aggregate."""
        self._domain_events.append(event)

    def get_domain_events(self) -> List[DomainEvent]:
        """Common event retrieval - not repeated in each aggregate."""
        return self._domain_events.copy()

    def clear_domain_events(self) -> None:
        """Common event clearing - not repeated in each aggregate."""
        self._domain_events.clear()

# Domain entities inherit common behavior
class Template(AggregateRoot):
    """Template entity - inherits common functionality."""

    template_id: str
    max_number: int
    attributes: Dict[str, Any]

    def get_identity(self) -> str:
        """Only defines identity - other behavior inherited."""
        return self.template_id

class Request(AggregateRoot):
    """Request entity - inherits same common functionality."""

    request_id: str
    template_id: str
    status: RequestStatus

    def get_identity(self) -> str:
        """Only defines identity - other behavior inherited."""
        return self.request_id
```

### Base Repository Pattern

Common repository operations are abstracted to avoid duplication:

```python
# src/domain/base/repository.py
class Repository(ABC, Generic[T]):
    """Base repository with common operations."""

    @abstractmethod
    async def get_by_id(self, entity_id: str) -> Optional[T]:
        pass

    @abstractmethod
    async def save(self, entity: T) -> None:
        pass

    @abstractmethod
    async def delete(self, entity_id: str) -> bool:
        pass

    # Common validation logic - not repeated in each repository
    def _validate_entity_id(self, entity_id: str) -> None:
        """Common validation - used by all repositories."""
        if not entity_id or not entity_id.strip():
            raise ValueError("Entity ID cannot be empty")

    # Common error handling - not repeated in each repository
    def _handle_not_found(self, entity_id: str, entity_type: str) -> None:
        """Common not found handling."""
        raise EntityNotFoundError(f"{entity_type} with ID {entity_id} not found")

# Specific repositories inherit common behavior
class TemplateRepository(Repository[Template]):
    """Template repository - inherits common operations."""
    pass

class RequestRepository(Repository[Request]):
    """Request repository - inherits same common operations."""
    pass
```

## Configuration-Driven Behavior

Instead of duplicating similar code for different configurations, the plugin uses configuration to drive behavior:

### Provider Configuration

```python
# Single provider strategy implementation handles multiple configurations
class AWSProviderStrategy:
    """Single implementation handles multiple AWS configurations."""

    def __init__(self, config: AWSProviderConfig, logger: LoggingPort):
        self._config = config
        self._logger = logger

    async def provision_instances(self, request: Request) -> List[Machine]:
        """Single method handles different provisioning types via configuration."""

        # Configuration drives behavior instead of separate methods
        provisioning_type = self._config.get_provisioning_type(request.template)

        if provisioning_type == "ec2_fleet":
            return await self._provision_with_ec2_fleet(request)
        elif provisioning_type == "spot_fleet":
            return await self._provision_with_spot_fleet(request)
        elif provisioning_type == "auto_scaling":
            return await self._provision_with_auto_scaling(request)
        else:
            return await self._provision_with_run_instances(request)

# Configuration defines behavior instead of code duplication
# config/aws_provider.yml
provisioning:
  default_type: ec2_fleet
  template_mappings:
    high_performance: ec2_fleet
    cost_optimized: spot_fleet
    scalable: auto_scaling

  ec2_fleet:
    target_capacity_type: on-demand
    allocation_strategy: diversified

  spot_fleet:
    target_capacity: 10
    allocation_strategy: lowestPrice
    spot_price: 0.10
```

### Handler Factory Configuration

```python
# Single factory handles multiple handler types via configuration
class AWSHandlerFactory:
    """Single factory implementation for all handler types."""

    def __init__(self, config: ConfigurationPort):
        self._config = config
        self._handler_configs = self._load_handler_configurations()

    def create_handler(self, handler_type: str) -> AWSHandler:
        """Single creation method handles all types via configuration."""

        # Configuration drives handler creation instead of separate factory methods
        handler_config = self._handler_configs.get(handler_type)
        if not handler_config:
            raise ValueError(f"Unknown handler type: {handler_type}")

        # Common creation logic with configuration-driven parameters
        return self._create_configured_handler(handler_type, handler_config)

    def _create_configured_handler(self, handler_type: str, config: Dict[str, Any]) -> AWSHandler:
        """Common handler creation logic - not duplicated for each type."""

        handler_class = self._get_handler_class(handler_type)

        # Common initialization parameters from configuration
        return handler_class(
            aws_client=self._aws_client,
            logger=self._logger,
            config=config,
            retry_config=config.get('retry', {}),
            timeout_config=config.get('timeout', {})
        )

# Configuration defines handler behavior
# config/handlers.yml
handlers:
  ec2_fleet:
    class: EC2FleetHandler
    retry:
      max_attempts: 3
      backoff_multiplier: 2
    timeout:
      provision: 300
      terminate: 60

  spot_fleet:
    class: SpotFleetHandler
    retry:
      max_attempts: 5
      backoff_multiplier: 1.5
    timeout:
      provision: 600
      terminate: 120
```

## Shared Utility Functions

Common operations are extracted into reusable utility functions:

### AWS Operations Utilities

```python
# src/providers/aws/utilities/aws_operations.py
class AWSOperations:
    """Shared AWS operations - eliminates duplication across handlers."""

    @staticmethod
    def build_tags(base_tags: Dict[str, str], additional_tags: Dict[str, str] = None) -> List[Dict[str, str]]:
        """Common tag building logic - used by all handlers."""
        tags = base_tags.copy()
        if additional_tags:
            tags.update(additional_tags)

        return [{"Key": k, "Value": v} for k, v in tags.items()]

    @staticmethod
    def parse_instance_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Common response parsing - used by all handlers."""
        instances = []

        for reservation in response.get('Reservations', []):
            for instance in reservation.get('Instances', []):
                instances.append({
                    'instance_id': instance['InstanceId'],
                    'state': instance['State']['Name'],
                    'instance_type': instance['InstanceType'],
                    'launch_time': instance.get('LaunchTime'),
                    'private_ip': instance.get('PrivateIpAddress'),
                    'public_ip': instance.get('PublicIpAddress')
                })

        return instances

    @staticmethod
    def validate_instance_ids(instance_ids: List[str]) -> None:
        """Common validation - used by all handlers."""
        if not instance_ids:
            raise ValueError("Instance IDs list cannot be empty")

        for instance_id in instance_ids:
            if not instance_id.startswith('i-'):
                raise ValueError(f"Invalid instance ID format: {instance_id}")

# Handlers use shared utilities instead of duplicating logic
class EC2FleetHandler(AWSHandler):
    async def provision_instances(self, request: Request) -> List[Machine]:
        # Uses shared utility instead of duplicating tag logic
        tags = AWSOperations.build_tags(
            base_tags={"RequestId": request.id, "Handler": "EC2Fleet"},
            additional_tags=request.template.attributes.get("tags", {})
        )

        # Launch instances with common tags
        response = await self._launch_ec2_fleet(request, tags)

        # Uses shared utility instead of duplicating parsing logic
        instances = AWSOperations.parse_instance_response(response)

        return self._convert_to_machines(instances, request)

class SpotFleetHandler(AWSHandler):
    async def provision_instances(self, request: Request) -> List[Machine]:
        # Same shared utilities - no duplication
        tags = AWSOperations.build_tags(
            base_tags={"RequestId": request.id, "Handler": "SpotFleet"},
            additional_tags=request.template.attributes.get("tags", {})
        )

        response = await self._launch_spot_fleet(request, tags)
        instances = AWSOperations.parse_instance_response(response)

        return self._convert_to_machines(instances, request)
```

### Configuration Utilities

```python
# src/config/utilities/config_utils.py
class ConfigurationUtils:
    """Shared configuration utilities - eliminates duplication."""

    @staticmethod
    def merge_configurations(base_config: Dict[str, Any], 
                           override_config: Dict[str, Any]) -> Dict[str, Any]:
        """Common configuration merging - used throughout the system."""
        merged = base_config.copy()

        for key, value in override_config.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = ConfigurationUtils.merge_configurations(merged[key], value)
            else:
                merged[key] = value

        return merged

    @staticmethod
    def validate_required_fields(config: Dict[str, Any], 
                                required_fields: List[str], 
                                config_name: str) -> None:
        """Common validation - used by all configuration classes."""
        missing_fields = []

        for field in required_fields:
            if field not in config:
                missing_fields.append(field)

        if missing_fields:
            raise ConfigurationError(
                f"Missing required fields in {config_name}: {missing_fields}"
            )

    @staticmethod
    def get_nested_value(config: Dict[str, Any], 
                        key_path: str, 
                        default: Any = None) -> Any:
        """Common nested value retrieval - used throughout configuration system."""
        keys = key_path.split('.')
        value = config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

# Configuration classes use shared utilities
class AWSProviderConfig:
    def __init__(self, config_data: Dict[str, Any]):
        # Uses shared validation instead of duplicating
        ConfigurationUtils.validate_required_fields(
            config_data, 
            ['region'], 
            'AWS Provider'
        )

        # Uses shared merging for defaults
        self._config = ConfigurationUtils.merge_configurations(
            self._get_default_config(),
            config_data
        )

    def get_handler_config(self, handler_type: str) -> Dict[str, Any]:
        # Uses shared nested value retrieval
        return ConfigurationUtils.get_nested_value(
            self._config,
            f'handlers.{handler_type}',
            {}
        )
```

## Template-Based Code Generation

Instead of duplicating similar patterns, the plugin uses templates and generators:

### Command and Query Handler Templates

```python
# src/application/base/handler_template.py
class CommandHandlerTemplate:
    """Template for command handlers - eliminates duplication."""

    def __init__(self, 
                 repository: Repository,
                 logger: LoggingPort,
                 validator: Validator = None):
        self._repository = repository
        self._logger = logger
        self._validator = validator

    async def handle_command(self, 
                           command: Any,
                           entity_factory: Callable,
                           success_message: str) -> Any:
        """Template method - common command handling pattern."""

        # Common validation pattern
        if self._validator:
            self._validator.validate(command)

        # Common logging pattern
        self._logger.info(f"Handling command: {command.__class__.__name__}")

        try:
            # Entity creation using factory
            entity = entity_factory(command)

            # Common persistence pattern
            await self._repository.save(entity)

            # Common success logging
            self._logger.info(success_message.format(entity_id=entity.get_identity()))

            return entity.get_identity()

        except Exception as e:
            # Common error handling pattern
            self._logger.error(f"Command handling failed: {e}")
            raise

# Specific handlers use template instead of duplicating patterns
class CreateTemplateHandler:
    def __init__(self, template_repo: TemplateRepository, logger: LoggingPort):
        self._template = CommandHandlerTemplate(template_repo, logger)

    async def handle(self, command: CreateTemplateCommand) -> str:
        # Uses template instead of duplicating command handling logic
        return await self._template.handle_command(
            command=command,
            entity_factory=lambda cmd: Template(
                template_id=cmd.template_id,
                max_number=cmd.max_number,
                attributes=cmd.attributes
            ),
            success_message="Template created: {entity_id}"
        )

class CreateRequestHandler:
    def __init__(self, request_repo: RequestRepository, logger: LoggingPort):
        self._template = CommandHandlerTemplate(request_repo, logger)

    async def handle(self, command: CreateRequestCommand) -> str:
        # Same template - no duplication
        return await self._template.handle_command(
            command=command,
            entity_factory=lambda cmd: Request(
                request_id=generate_id(),
                template_id=cmd.template_id,
                max_number=cmd.max_number,
                status=RequestStatus.PENDING
            ),
            success_message="Request created: {entity_id}"
        )
```

## Shared Constants and Enumerations

Common values are defined once and reused:

```python
# src/domain/base/constants.py
class SystemConstants:
    """System-wide constants - single source of truth."""

    # Default values used throughout the system
    DEFAULT_TIMEOUT = 300
    DEFAULT_RETRY_ATTEMPTS = 3
    DEFAULT_BATCH_SIZE = 50

    # Common field names
    TEMPLATE_ID_FIELD = "template_id"
    REQUEST_ID_FIELD = "request_id"
    MACHINE_ID_FIELD = "machine_id"

    # Common status values
    PENDING_STATUS = "pending"
    RUNNING_STATUS = "running"
    COMPLETED_STATUS = "completed"
    FAILED_STATUS = "failed"

# src/domain/base/field_mappings.py
class FieldMappings:
    """Common field mappings - eliminates duplication across formatters."""

    # HostFactory standard field mappings
    HF_STANDARD_FIELDS = {
        "templateId": "template_id",
        "maxNumber": "max_number",
        "requestId": "request_id",
        "machineId": "machine_id",
        "instanceType": "instance_type"
    }

    # AWS-specific field mappings
    AWS_FIELD_MAPPINGS = {
        "InstanceId": "machine_id",
        "InstanceType": "instance_type",
        "State": "status",
        "LaunchTime": "created_at",
        "PrivateIpAddress": "private_ip"
    }

    @classmethod
    def get_mapped_field(cls, source_field: str, mapping_type: str) -> str:
        """Common field mapping logic - used by all formatters."""
        mappings = getattr(cls, f"{mapping_type.upper()}_FIELD_MAPPINGS", {})
        return mappings.get(source_field, source_field)

# Usage throughout the system
class TemplateFormatter:
    def format_for_hostfactory(self, template: Template) -> Dict[str, Any]:
        # Uses shared mappings instead of duplicating mapping logic
        result = {}
        for hf_field, internal_field in FieldMappings.HF_STANDARD_FIELDS.items():
            if hasattr(template, internal_field):
                result[hf_field] = getattr(template, internal_field)
        return result

class MachineFormatter:
    def format_aws_response(self, aws_data: Dict[str, Any]) -> Dict[str, Any]:
        # Same shared mappings - no duplication
        result = {}
        for aws_field, internal_field in FieldMappings.AWS_FIELD_MAPPINGS.items():
            if aws_field in aws_data:
                result[internal_field] = aws_data[aws_field]
        return result
```

## Benefits of DRY Implementation

### Maintainability
- **Single point of change**: Modifications only need to be made in one place
- **Consistency**: Shared logic ensures consistent behavior across the system
- **Reduced bugs**: Fixes in shared code benefit all users of that code

### Development Efficiency
- **Faster development**: Reusable components speed up feature development
- **Less testing**: Shared components only need to be tested once
- **Easier refactoring**: Changes to shared logic automatically propagate

### Code Quality
- **Reduced complexity**: Less code overall means less complexity
- **Better abstraction**: Common patterns are properly abstracted
- **Improved readability**: Less repetitive code is easier to understand

### System Reliability
- **Consistent behavior**: Shared implementations ensure consistent system behavior
- **Centralized improvements**: Performance improvements benefit the entire system
- **Reduced maintenance burden**: Less code to maintain and update

The DRY principle implementation in the Open Host Factory Plugin creates a maintainable, efficient, and reliable codebase by eliminating duplication through abstraction, configuration-driven behavior, and shared utilities.
