# Factory Pattern Implementation

This document describes the implementation of various Factory patterns in the Open Host Factory Plugin, including Simple Factory, Factory Method, and Abstract Factory patterns for creating objects based on configuration and runtime conditions.

## Factory Pattern Overview

The plugin uses factory patterns to:

- **Encapsulate object creation**: Hide complex creation logic behind simple interfaces
- **Configuration-driven creation**: Create objects based on configuration parameters
- **Runtime selection**: Choose implementations at runtime based on conditions
- **Dependency management**: Handle complex dependency injection scenarios

## Provider Strategy Factory

The Provider Strategy Factory creates appropriate provider strategies based on configuration.

### Factory Implementation

```python
# src/infrastructure/factories/provider_strategy_factory.py
from typing import Dict, Any, List
from src.domain.base.ports import LoggingPort, ConfigurationPort
from src.providers.base.strategy.provider_strategy import ProviderStrategy
from src.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from src.providers.aws.configuration.config import AWSProviderConfig

class ProviderStrategyFactory:
    """Factory for creating provider strategies from configuration."""

    def __init__(self, 
                 config_manager: ConfigurationPort,
                 logger: LoggingPort):
        self._config_manager = config_manager
        self._logger = logger
        self._strategy_registry: Dict[str, callable] = {}
        self._register_strategy_creators()

    def _register_strategy_creators(self):
        """Register strategy creation functions."""
        self._strategy_registry = {
            "aws": self._create_aws_strategy,
            # Future providers can be added here
            # "azure": self._create_azure_strategy,
            # "gcp": self._create_gcp_strategy,
        }

    def create_strategy(self, provider_type: str, config_override: Dict[str, Any] = None) -> ProviderStrategy:
        """Create provider strategy based on type and configuration."""
        self._logger.info(f"Creating provider strategy: {provider_type}")

        if provider_type not in self._strategy_registry:
            available_types = list(self._strategy_registry.keys())
            raise ValueError(f"Unsupported provider type: {provider_type}. Available: {available_types}")

        creator_func = self._strategy_registry[provider_type]
        strategy = creator_func(config_override)

        self._logger.info(f"Created provider strategy: {provider_type}")
        return strategy

    def _create_aws_strategy(self, config_override: Dict[str, Any] = None) -> AWSProviderStrategy:
        """Create AWS provider strategy with configuration."""
        # Get base configuration
        aws_config_data = self._config_manager.get_section("aws")

        # Apply overrides if provided
        if config_override:
            aws_config_data.update(config_override)

        # Validate required configuration
        self._validate_aws_config(aws_config_data)

        # Create configuration object
        aws_config = AWSProviderConfig(**aws_config_data)

        # Create and return strategy
        return AWSProviderStrategy(
            config=aws_config,
            logger=self._logger
        )

    def _validate_aws_config(self, config: Dict[str, Any]) -> None:
        """Validate AWS configuration parameters."""
        required_fields = ["region"]
        for field in required_fields:
            if field not in config:
                raise ValueError(f"Missing required AWS configuration field: {field}")

    def get_available_providers(self) -> List[str]:
        """Get list of available provider types."""
        return list(self._strategy_registry.keys())

    def get_provider_info(self) -> Dict[str, Any]:
        """Get information about available providers."""
        return {
            "available_providers": self.get_available_providers(),
            "default_provider": self._config_manager.get("provider.default", "aws")
        }
```

### Factory Registration and Usage

```python
# src/infrastructure/di/provider_services.py
def create_provider_strategy_factory(container: DIContainer) -> ProviderStrategyFactory:
    """Factory function for provider strategy factory."""
    return ProviderStrategyFactory(
        config_manager=container.get(ConfigurationPort),
        logger=container.get(LoggingPort)
    )

# Registration in DI container
container.register_factory(ProviderStrategyFactory, create_provider_strategy_factory)

# Usage
strategy_factory = container.get(ProviderStrategyFactory)
aws_strategy = strategy_factory.create_strategy("aws")
```

## AWS Handler Factory

The AWS Handler Factory creates appropriate handlers for different provisioning methods.

### Handler Factory Implementation

```python
# src/providers/aws/infrastructure/aws_handler_factory.py
from typing import Dict, Type, Any
from src.domain.base.dependency_injection import injectable
from src.domain.base.ports import LoggingPort, ConfigurationPort
from src.providers.aws.infrastructure.aws_client import AWSClient
from src.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from src.domain.template.aggregate import Template

@injectable
class AWSHandlerFactory:
    """Factory for creating AWS handlers based on template requirements."""

    def __init__(self,
                 aws_client: AWSClient,
                 logger: LoggingPort,
                 config: ConfigurationPort):
        self._aws_client = aws_client
        self._logger = logger
        self._config = config
        self._handler_registry: Dict[str, Type[AWSHandler]] = {}
        self._handler_cache: Dict[str, AWSHandler] = {}
        self._register_handlers()

    def _register_handlers(self):
        """Register available handler implementations."""
        from src.providers.aws.infrastructure.handlers.ec2_fleet_handler import EC2FleetHandler
        from src.providers.aws.infrastructure.handlers.spot_fleet_handler import SpotFleetHandler
        from src.providers.aws.infrastructure.handlers.asg_handler import ASGHandler
        from src.providers.aws.infrastructure.handlers.run_instances_handler import RunInstancesHandler

        self._handler_registry = {
            "ec2_fleet": EC2FleetHandler,
            "spot_fleet": SpotFleetHandler,
            "auto_scaling_group": ASGHandler,
            "run_instances": RunInstancesHandler
        }

        self._logger.info(f"Registered {len(self._handler_registry)} handler types")

    def create_handler(self, handler_type: str) -> AWSHandler:
        """Create handler for specified type."""
        self._logger.debug(f"Creating handler for type: {handler_type}")

        # Check if handler type is supported
        if handler_type not in self._handler_registry:
            available_types = list(self._handler_registry.keys())
            raise ValueError(f"Unsupported handler type: {handler_type}. Available: {available_types}")

        # Check cache first
        if handler_type in self._handler_cache:
            return self._handler_cache[handler_type]

        # Create new handler instance
        handler_class = self._handler_registry[handler_type]
        handler = handler_class(
            aws_client=self._aws_client,
            logger=self._logger,
            config=self._config
        )

        # Cache for reuse
        self._handler_cache[handler_type] = handler

        self._logger.debug(f"Created handler: {handler_type}")
        return handler

    def create_handler_for_template(self, template: Template) -> AWSHandler:
        """Create appropriate handler based on template configuration."""
        self._logger.debug(f"Creating handler for template: {template.template_id}")

        # Determine handler type from template
        handler_type = self._determine_handler_type(template)

        # Create and return handler
        return self.create_handler(handler_type)

    def _determine_handler_type(self, template: Template) -> str:
        """Determine appropriate handler type based on template attributes."""
        attributes = template.attributes

        # Check for spot instance preference
        if attributes.get("use_spot_instances", False):
            return "spot_fleet"

        # Check for auto scaling preference
        if attributes.get("use_auto_scaling", False):
            return "auto_scaling_group"

        # Check for fleet preference (default)
        if attributes.get("use_fleet", True):
            return "ec2_fleet"

        # Fallback to run instances
        return "run_instances"

    def get_available_handlers(self) -> List[str]:
        """Get list of available handler types."""
        return list(self._handler_registry.keys())

    def get_handler_info(self) -> Dict[str, Any]:
        """Get information about available handlers."""
        return {
            "available_handlers": self.get_available_handlers(),
            "cached_handlers": list(self._handler_cache.keys()),
            "default_handler": self._config.get("aws.handlers.default", "ec2_fleet")
        }
```

## Repository Factory

The Repository Factory creates appropriate repository implementations based on storage configuration.

### Repository Factory Implementation

```python
# src/infrastructure/utilities/factories/repository_factory.py
from typing import Dict, Type, Any
from src.domain.base.ports import LoggingPort, ConfigurationPort
from src.domain.template.repository import TemplateRepository
from src.domain.request.repository import RequestRepository
from src.domain.machine.repository import MachineRepository

class RepositoryFactory:
    """Factory for creating repository implementations based on storage type."""

    def __init__(self, 
                 config: ConfigurationPort,
                 logger: LoggingPort):
        self._config = config
        self._logger = logger
        self._repository_registry: Dict[str, Dict[str, Type]] = {}
        self._register_repository_implementations()

    def _register_repository_implementations(self):
        """Register available repository implementations."""
        # Import implementations
        from src.infrastructure.persistence.dynamodb.template_repository import DynamoDBTemplateRepository
        from src.infrastructure.persistence.dynamodb.request_repository import DynamoDBRequestRepository
        from src.infrastructure.persistence.dynamodb.machine_repository import DynamoDBMachineRepository
        from src.infrastructure.persistence.memory.template_repository import InMemoryTemplateRepository
        from src.infrastructure.persistence.memory.request_repository import InMemoryRequestRepository
        from src.infrastructure.persistence.memory.machine_repository import InMemoryMachineRepository

        self._repository_registry = {
            "dynamodb": {
                "template": DynamoDBTemplateRepository,
                "request": DynamoDBRequestRepository,
                "machine": DynamoDBMachineRepository
            },
            "memory": {
                "template": InMemoryTemplateRepository,
                "request": InMemoryRequestRepository,
                "machine": InMemoryMachineRepository
            }
        }

        self._logger.info(f"Registered repository implementations for storage types: {list(self._repository_registry.keys())}")

    def create_template_repository(self) -> TemplateRepository:
        """Create template repository based on configuration."""
        storage_type = self._get_storage_type()
        return self._create_repository("template", storage_type)

    def create_request_repository(self) -> RequestRepository:
        """Create request repository based on configuration."""
        storage_type = self._get_storage_type()
        return self._create_repository("request", storage_type)

    def create_machine_repository(self) -> MachineRepository:
        """Create machine repository based on configuration."""
        storage_type = self._get_storage_type()
        return self._create_repository("machine", storage_type)

    def _create_repository(self, repo_type: str, storage_type: str) -> Any:
        """Create repository of specified type and storage implementation."""
        self._logger.debug(f"Creating {repo_type} repository with {storage_type} storage")

        # Validate storage type
        if storage_type not in self._repository_registry:
            available_types = list(self._repository_registry.keys())
            raise ValueError(f"Unsupported storage type: {storage_type}. Available: {available_types}")

        # Validate repository type
        storage_implementations = self._repository_registry[storage_type]
        if repo_type not in storage_implementations:
            available_repos = list(storage_implementations.keys())
            raise ValueError(f"Repository type {repo_type} not available for storage {storage_type}. Available: {available_repos}")

        # Get repository class
        repo_class = storage_implementations[repo_type]

        # Create repository with appropriate configuration
        if storage_type == "dynamodb":
            return self._create_dynamodb_repository(repo_class)
        elif storage_type == "memory":
            return self._create_memory_repository(repo_class)
        else:
            raise ValueError(f"Unknown storage type: {storage_type}")

    def _create_dynamodb_repository(self, repo_class: Type) -> Any:
        """Create DynamoDB repository with configuration."""
        dynamodb_config = self._config.get_section("storage.dynamodb")

        return repo_class(
            table_name=dynamodb_config.get("table_name"),
            region=dynamodb_config.get("region"),
            profile=dynamodb_config.get("profile"),
            logger=self._logger
        )

    def _create_memory_repository(self, repo_class: Type) -> Any:
        """Create in-memory repository."""
        return repo_class(logger=self._logger)

    def _get_storage_type(self) -> str:
        """Get configured storage type."""
        return self._config.get("storage.type", "memory")

    def get_available_storage_types(self) -> List[str]:
        """Get list of available storage types."""
        return list(self._repository_registry.keys())
```

## Abstract Factory Pattern

The Abstract Factory pattern is used for creating families of related objects.

### Provider Component Factory

```python
# src/providers/base/factory/provider_component_factory.py
from abc import ABC, abstractmethod
from typing import Any
from src.domain.base.ports import LoggingPort, ConfigurationPort

class ProviderComponentFactory(ABC):
    """Abstract factory for creating provider-specific components."""

    @abstractmethod
    def create_client(self) -> Any:
        """Create provider-specific client."""
        pass

    @abstractmethod
    def create_instance_manager(self) -> Any:
        """Create provider-specific instance manager."""
        pass

    @abstractmethod
    def create_resource_manager(self) -> Any:
        """Create provider-specific resource manager."""
        pass

    @abstractmethod
    def create_template_adapter(self) -> Any:
        """Create provider-specific template adapter."""
        pass

class AWSComponentFactory(ProviderComponentFactory):
    """Concrete factory for AWS components."""

    def __init__(self, 
                 config: ConfigurationPort,
                 logger: LoggingPort):
        self._config = config
        self._logger = logger

    def create_client(self) -> AWSClient:
        """Create AWS client."""
        from src.providers.aws.infrastructure.aws_client import AWSClient
        aws_config = self._config.get_section("aws")
        return AWSClient(aws_config, self._logger)

    def create_instance_manager(self) -> AWSInstanceManager:
        """Create AWS instance manager."""
        from src.providers.aws.managers.aws_instance_manager import AWSInstanceManager
        client = self.create_client()
        return AWSInstanceManager(client, self._config, self._logger)

    def create_resource_manager(self) -> AWSResourceManagerImpl:
        """Create AWS resource manager."""
        from src.providers.aws.managers.aws_resource_manager import AWSResourceManagerImpl
        client = self.create_client()
        return AWSResourceManagerImpl(client, self._config, self._logger)

    def create_template_adapter(self) -> AWSTemplateAdapter:
        """Create AWS template adapter."""
        from src.providers.aws.infrastructure.adapters.template_adapter import AWSTemplateAdapter
        client = self.create_client()
        return AWSTemplateAdapter(client, self._logger, self._config)
```

## Configuration-Driven Factory Selection

Factories are selected and configured based on application configuration:

### Factory Configuration

```yaml
# config/factories.yml
storage:
  type: dynamodb
  dynamodb:
    table_name: hostfactory-data
    region: us-east-1
    profile: default

providers:
  default: aws
  aws:
    region: us-east-1
    profile: default
    handlers:
      default: ec2_fleet

repositories:
  caching: true
  cache_ttl: 300
```

### Factory Registration

```python
# src/infrastructure/di/factory_services.py
def register_factory_services(container: DIContainer) -> None:
    """Register factory services in DI container."""

    # Register provider strategy factory
    container.register_factory(
        ProviderStrategyFactory,
        lambda c: ProviderStrategyFactory(
            config_manager=c.get(ConfigurationPort),
            logger=c.get(LoggingPort)
        )
    )

    # Register repository factory
    container.register_singleton(
        RepositoryFactory,
        lambda c: RepositoryFactory(
            config=c.get(ConfigurationPort),
            logger=c.get(LoggingPort)
        )
    )

    # Register AWS handler factory (if AWS provider is configured)
    config = container.get(ConfigurationPort)
    if config.get("providers.default") == "aws":
        container.register_singleton(AWSHandlerFactory)
```

## Benefits of Factory Pattern Implementation

### Encapsulation of Creation Logic
- Complex object creation is hidden behind simple interfaces
- Creation parameters are managed centrally
- Dependencies are handled automatically

### Configuration-Driven Behavior
- Object types selected based on configuration
- Runtime behavior modification without code changes
- Easy switching between implementations

### Extensibility
- New implementations can be added easily
- Factory registration system supports plugins
- Abstract factories enable provider families

### Testability
- Factories can create mock objects for testing
- Different configurations for different test scenarios
- Easy isolation of creation logic

### Maintainability
- Creation logic centralized in factories
- Clear separation between creation and usage
- Consistent object creation patterns

This comprehensive factory pattern implementation provides flexible, configurable, and maintainable object creation throughout the Open Host Factory Plugin.
