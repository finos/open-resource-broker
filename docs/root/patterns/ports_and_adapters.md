# Ports and Adapters Pattern Implementation

This document describes the implementation of the Ports and Adapters pattern (also known as Hexagonal Architecture) in the Open Host Factory Plugin, which enables clean separation between business logic and external concerns.

## Ports and Adapters Overview

The Ports and Adapters pattern isolates the core business logic from external dependencies by:

- **Ports**: Abstract interfaces defining contracts for external interactions
- **Adapters**: Concrete implementations of ports for specific technologies
- **Inversion of Control**: Business logic depends on abstractions, not implementations
- **Testability**: Easy mocking and testing through interface substitution

## Port Definitions

Ports are abstract interfaces defined in the domain layer that specify contracts for external interactions.

### Core Ports

#### Logging Port

```python
# src/domain/base/ports/logging_port.py
from abc import ABC, abstractmethod

class LoggingPort(ABC):
    """Abstract interface for logging operations."""

    @abstractmethod
    def info(self, message: str, **kwargs) -> None:
        """Log informational message."""
        pass

    @abstractmethod
    def error(self, message: str, **kwargs) -> None:
        """Log error message."""
        pass

    @abstractmethod
    def warning(self, message: str, **kwargs) -> None:
        """Log warning message."""
        pass

    @abstractmethod
    def debug(self, message: str, **kwargs) -> None:
        """Log debug message."""
        pass
```

#### Configuration Port

```python
# src/domain/base/ports/configuration_port.py
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

class ConfigurationPort(ABC):
    """Abstract interface for configuration access."""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key."""
        pass

    @abstractmethod
    def get_section(self, section: str) -> Dict[str, Any]:
        """Get entire configuration section."""
        pass

    @abstractmethod
    def has_key(self, key: str) -> bool:
        """Check if configuration key exists."""
        pass

    @abstractmethod
    def get_provider_config(self) -> Dict[str, Any]:
        """Get provider-specific configuration."""
        pass
```

#### Container Port

```python
# src/domain/base/ports/container_port.py
from abc import ABC, abstractmethod
from typing import Type, TypeVar, Any

T = TypeVar('T')

class ContainerPort(ABC):
    """Abstract interface for dependency injection container."""

    @abstractmethod
    def get(self, interface: Type[T]) -> T:
        """Resolve dependency by interface type."""
        pass

    @abstractmethod
    def register_singleton(self, interface: Type, implementation: Type) -> None:
        """Register singleton service."""
        pass

    @abstractmethod
    def register_transient(self, interface: Type, implementation: Type) -> None:
        """Register transient service."""
        pass
```

### Domain-Specific Ports

#### Template Repository Port

```python
# src/domain/template/repository.py
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from .aggregate import Template

class TemplateRepository(ABC):
    """Abstract interface for template data access."""

    @abstractmethod
    async def get_all(self, 
                     filters: Optional[Dict[str, Any]] = None,
                     limit: Optional[int] = None,
                     offset: Optional[int] = None) -> List[Template]:
        """Retrieve all templates with optional filtering."""
        pass

    @abstractmethod
    async def get_by_id(self, template_id: str) -> Optional[Template]:
        """Retrieve template by ID."""
        pass

    @abstractmethod
    async def save(self, template: Template) -> None:
        """Save template."""
        pass

    @abstractmethod
    async def delete(self, template_id: str) -> bool:
        """Delete template by ID."""
        pass
```

#### Provider Strategy Port

```python
# src/domain/base/provider_interfaces.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from src.domain.request.aggregate import Request
from src.domain.machine.aggregate import Machine

class ProviderStrategy(ABC):
    """Abstract interface for cloud provider strategies."""

    @abstractmethod
    async def provision_instances(self, request: Request) -> List[Machine]:
        """Provision compute instances."""
        pass

    @abstractmethod
    async def terminate_instances(self, instance_ids: List[str]) -> bool:
        """Terminate compute instances."""
        pass

    @abstractmethod
    async def get_instance_status(self, instance_ids: List[str]) -> Dict[str, str]:
        """Get status of compute instances."""
        pass

    @abstractmethod
    async def validate_template(self, template_config: Dict[str, Any]) -> bool:
        """Validate template configuration."""
        pass
```

## Adapter Implementations

Adapters are concrete implementations of ports that handle specific technologies or external systems.

### Infrastructure Adapters

#### Logging Adapter

```python
# src/infrastructure/adapters/logging_adapter.py
from src.domain.base.ports import LoggingPort
from src.infrastructure.logging.logger import get_logger
import logging

class LoggingAdapter(LoggingPort):
    """Concrete logging implementation using Python logging."""

    def __init__(self, name: str = __name__):
        self._logger = get_logger(name)

    def info(self, message: str, **kwargs) -> None:
        """Log informational message."""
        self._logger.info(message, extra=kwargs)

    def error(self, message: str, **kwargs) -> None:
        """Log error message."""
        self._logger.error(message, extra=kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """Log warning message."""
        self._logger.warning(message, extra=kwargs)

    def debug(self, message: str, **kwargs) -> None:
        """Log debug message."""
        self._logger.debug(message, extra=kwargs)
```

#### Configuration Adapter

```python
# src/infrastructure/adapters/configuration_adapter.py
from src.domain.base.ports import ConfigurationPort
from src.config.manager import ConfigurationManager
from typing import Any, Dict

class ConfigurationAdapter(ConfigurationPort):
    """Concrete configuration implementation using ConfigurationManager."""

    def __init__(self, config_manager: ConfigurationManager):
        self._config_manager = config_manager

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key."""
        return self._config_manager.get(key, default)

    def get_section(self, section: str) -> Dict[str, Any]:
        """Get entire configuration section."""
        return self._config_manager.get_section(section)

    def has_key(self, key: str) -> bool:
        """Check if configuration key exists."""
        return self._config_manager.has_key(key)

    def get_provider_config(self) -> Dict[str, Any]:
        """Get provider-specific configuration."""
        return self._config_manager.get_provider_config()
```

#### Container Adapter

```python
# src/infrastructure/adapters/container_adapter.py
from src.domain.base.ports import ContainerPort
from src.infrastructure.di.container import DIContainer
from typing import Type, TypeVar

T = TypeVar('T')

class ContainerAdapter(ContainerPort):
    """Concrete container implementation using DIContainer."""

    def __init__(self, di_container: DIContainer):
        self._container = di_container

    def get(self, interface: Type[T]) -> T:
        """Resolve dependency by interface type."""
        return self._container.get(interface)

    def register_singleton(self, interface: Type, implementation: Type) -> None:
        """Register singleton service."""
        self._container.register_singleton(interface, implementation)

    def register_transient(self, interface: Type, implementation: Type) -> None:
        """Register transient service."""
        self._container.register_transient(interface, implementation)
```

### Provider Adapters

#### AWS Template Adapter

```python
# src/providers/aws/infrastructure/adapters/template_adapter.py
from src.domain.base.dependency_injection import injectable
from src.domain.base.ports import LoggingPort, ConfigurationPort
from src.providers.aws.infrastructure.aws_client import AWSClient
from typing import Dict, Any, List

@injectable
class AWSTemplateAdapter:
    """AWS-specific template operations adapter."""

    def __init__(self, 
                 aws_client: AWSClient, 
                 logger: LoggingPort, 
                 config: ConfigurationPort):
        self._aws_client = aws_client
        self._logger = logger
        self._config = config

    async def validate_template(self, template_config: Dict[str, Any]) -> bool:
        """Validate AWS-specific template configuration."""
        self._logger.info(f"Validating AWS template configuration")

        # AWS-specific validation logic
        required_fields = ['image_id', 'vm_type', 'subnet_ids']
        for field in required_fields:
            if field not in template_config:
                self._logger.error(f"Missing required field: {field}")
                return False

        # Validate AMI exists
        ami_id = template_config.get('image_id')
        if not await self._validate_ami_exists(ami_id):
            return False

        return True

    async def _validate_ami_exists(self, ami_id: str) -> bool:
        """Validate that AMI exists in AWS."""
        try:
            ec2_client = self._aws_client.get_client('ec2')
            response = ec2_client.describe_images(ImageIds=[ami_id])
            return len(response['Images']) > 0
        except Exception as e:
            self._logger.error(f"Error validating AMI {ami_id}: {e}")
            return False
```

#### AWS Provider Strategy Adapter

```python
# src/providers/aws/strategy/aws_provider_strategy.py
from src.domain.base.dependency_injection import injectable
from src.domain.base.ports import LoggingPort
from src.domain.base.provider_interfaces import ProviderStrategy
from src.providers.aws.configuration.config import AWSProviderConfig
from src.providers.aws.infrastructure.aws_client import AWSClient
from typing import List, Dict, Any

@injectable
class AWSProviderStrategy(ProviderStrategy):
    """AWS implementation of provider strategy."""

    def __init__(self, 
                 config: AWSProviderConfig, 
                 logger: LoggingPort):
        self._config = config
        self._logger = logger
        self._aws_client = AWSClient(config, logger)

    async def provision_instances(self, request: Request) -> List[Machine]:
        """Provision AWS compute instances."""
        self._logger.info(f"Provisioning {request.max_number} instances for template {request.template_id}")

        # AWS-specific provisioning logic
        ec2_client = self._aws_client.get_client('ec2')

        # Build launch parameters
        launch_params = self._build_launch_parameters(request)

        # Launch instances
        response = ec2_client.run_instances(**launch_params)

        # Convert to domain objects
        machines = []
        for instance in response['Instances']:
            machine = Machine(
                machine_id=instance['InstanceId'],
                instance_type=instance['InstanceType'],
                status=instance['State']['Name'],
                request_id=request.id
            )
            machines.append(machine)

        self._logger.info(f"Provisioned {len(machines)} instances")
        return machines

    def _build_launch_parameters(self, request: Request) -> Dict[str, Any]:
        """Build AWS-specific launch parameters."""
        # Implementation details for AWS parameter building
        pass
```

### Repository Adapters

#### DynamoDB Template Repository

```python
# src/infrastructure/persistence/dynamodb/template_repository.py
from src.domain.template.repository import TemplateRepository
from src.domain.template.aggregate import Template
from src.domain.base.ports import LoggingPort
from typing import List, Optional, Dict, Any
import boto3

class DynamoDBTemplateRepository(TemplateRepository):
    """DynamoDB implementation of template repository."""

    def __init__(self, 
                 table_name: str,
                 region: str,
                 logger: LoggingPort):
        self._table_name = table_name
        self._region = region
        self._logger = logger
        self._dynamodb = boto3.resource('dynamodb', region_name=region)
        self._table = self._dynamodb.Table(table_name)

    async def get_all(self, 
                     filters: Optional[Dict[str, Any]] = None,
                     limit: Optional[int] = None,
                     offset: Optional[int] = None) -> List[Template]:
        """Retrieve all templates from DynamoDB."""
        self._logger.info("Retrieving templates from DynamoDB")

        try:
            # Build scan parameters
            scan_params = {}
            if limit:
                scan_params['Limit'] = limit

            # Apply filters if provided
            if filters:
                scan_params.update(self._build_filter_expression(filters))

            # Perform scan
            response = self._table.scan(**scan_params)

            # Convert to domain objects
            templates = []
            for item in response['Items']:
                template = self._item_to_template(item)
                templates.append(template)

            self._logger.info(f"Retrieved {len(templates)} templates")
            return templates

        except Exception as e:
            self._logger.error(f"Error retrieving templates: {e}")
            raise

    async def get_by_id(self, template_id: str) -> Optional[Template]:
        """Retrieve template by ID from DynamoDB."""
        self._logger.info(f"Retrieving template {template_id}")

        try:
            response = self._table.get_item(Key={'template_id': template_id})

            if 'Item' not in response:
                return None

            template = self._item_to_template(response['Item'])
            return template

        except Exception as e:
            self._logger.error(f"Error retrieving template {template_id}: {e}")
            raise

    def _item_to_template(self, item: Dict[str, Any]) -> Template:
        """Convert DynamoDB item to Template domain object."""
        return Template(
            template_id=item['template_id'],
            max_number=item['max_number'],
            attributes=item.get('attributes', {})
        )
```

## Benefits of Ports and Adapters

### Technology Independence

**Business Logic Isolation**
- Domain logic doesn't depend on specific technologies
- Can switch from DynamoDB to PostgreSQL without changing business rules
- Can switch from AWS to Azure without changing core functionality

**Framework Flexibility**
- Infrastructure can use different frameworks
- Easy migration between technologies
- Reduced vendor lock-in

### Testability

**Unit Testing with Mocks**
```python
def test_application_service():
    """Test application service with mocked ports."""
    # Create mocks
    mock_logger = Mock(spec=LoggingPort)
    mock_config = Mock(spec=ConfigurationPort)
    mock_template_repo = Mock(spec=TemplateRepository)

    # Create service with mocked dependencies
    service = ApplicationService(
        logger=mock_logger,
        config=mock_config,
        template_repo=mock_template_repo
    )

    # Test behavior
    # Verify interactions with mocks
```

**Integration Testing**
```python
def test_with_real_adapters():
    """Test with real adapter implementations."""
    # Use real adapters with test configuration
    logger = LoggingAdapter("test")
    config = ConfigurationAdapter(test_config_manager)
    template_repo = InMemoryTemplateRepository()

    # Test with real implementations
    service = ApplicationService(logger, config, template_repo)
    # Test actual behavior
```

### Maintainability

**Clear Boundaries**
- Well-defined interfaces between layers
- Easy to understand responsibilities
- Reduced coupling between components

**Easy Extension**
- New adapters can be added without changing business logic
- Multiple implementations of same port
- Configuration-driven adapter selection

## Configuration-Driven Adapter Selection

The system can select adapters based on configuration:

```python
# src/infrastructure/di/adapter_registration.py
def register_adapters(container: DIContainer, config: ConfigurationPort) -> None:
    """Register adapters based on configuration."""

    # Storage adapter selection
    storage_type = config.get("storage.type", "memory")
    if storage_type == "dynamodb":
        container.register_singleton(
            TemplateRepository,
            DynamoDBTemplateRepository
        )
    elif storage_type == "postgresql":
        container.register_singleton(
            TemplateRepository,
            PostgreSQLTemplateRepository
        )
    else:
        container.register_singleton(
            TemplateRepository,
            InMemoryTemplateRepository
        )

    # Provider adapter selection
    provider_type = config.get("provider.type", "mock")
    if provider_type == "aws":
        container.register_singleton(
            ProviderStrategy,
            AWSProviderStrategy
        )
    else:
        container.register_singleton(
            ProviderStrategy,
            MockProviderStrategy
        )
```

## Error Handling in Adapters

Adapters handle technology-specific errors and translate them to domain exceptions:

```python
class DynamoDBTemplateRepository(TemplateRepository):
    async def get_by_id(self, template_id: str) -> Optional[Template]:
        try:
            # DynamoDB operations
            pass
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ResourceNotFoundException':
                return None
            elif error_code == 'ValidationException':
                raise TemplateValidationError(f"Invalid template ID: {template_id}")
            else:
                raise TemplateRepositoryError(f"DynamoDB error: {e}")
        except Exception as e:
            raise TemplateRepositoryError(f"Unexpected error: {e}")
```

## Adapter Lifecycle Management

Adapters are managed by the dependency injection container:

```python
# Singleton adapters (shared instances)
container.register_singleton(LoggingPort, LoggingAdapter)
container.register_singleton(ConfigurationPort, ConfigurationAdapter)

# Transient adapters (new instance per request)
container.register_transient(TemplateRepository, DynamoDBTemplateRepository)

# Factory-created adapters (complex creation logic)
container.register_factory(
    ProviderStrategy,
    lambda c: create_provider_strategy(c.get(ConfigurationPort))
)
```

This Ports and Adapters implementation provides clean separation between business logic and external concerns, enabling high testability, maintainability, and flexibility in the Open Host Factory Plugin.
