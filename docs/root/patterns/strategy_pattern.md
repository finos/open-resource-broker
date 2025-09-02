# Strategy Pattern Implementation

This document describes the implementation of the Strategy pattern in the Open Host Factory Plugin, which enables pluggable provider implementations and runtime behavior selection.

## Strategy Pattern Overview

The Strategy pattern allows the plugin to:

- **Encapsulate algorithms**: Different provider implementations as separate strategies
- **Runtime selection**: Choose provider strategy based on configuration
- **Easy extension**: Add new providers without modifying existing code
- **Consistent interface**: All providers implement the same contract

## Provider Strategy Implementation

### Base Strategy Interface

The core provider strategy interface defines the contract for all provider implementations:

```python
# src/providers/base/strategy/provider_strategy.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from src.domain.request.aggregate import Request
from src.domain.machine.aggregate import Machine
from src.domain.template.aggregate import Template

class ProviderStrategy(ABC):
    """Abstract base class for provider strategies."""

    @abstractmethod
    async def provision_instances(self, request: Request) -> List[Machine]:
        """Provision compute instances based on request."""
        pass

    @abstractmethod
    async def terminate_instances(self, instance_ids: List[str]) -> bool:
        """Terminate specified compute instances."""
        pass

    @abstractmethod
    async def get_instance_status(self, instance_ids: List[str]) -> Dict[str, str]:
        """Get current status of specified instances."""
        pass

    @abstractmethod
    async def validate_template(self, template: Template) -> bool:
        """Validate template configuration for this provider."""
        pass

    @abstractmethod
    async def get_available_templates(self) -> List[Template]:
        """Get list of available templates for this provider."""
        pass

    @abstractmethod
    def get_provider_info(self) -> Dict[str, Any]:
        """Get provider-specific information."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check provider health and connectivity."""
        pass
```

### AWS Provider Strategy

The AWS provider strategy implements the provider interface for Amazon Web Services:

```python
# src/providers/aws/strategy/aws_provider_strategy.py
from src.domain.base.dependency_injection import injectable
from src.domain.base.ports import LoggingPort
from src.providers.base.strategy.provider_strategy import ProviderStrategy
from src.providers.aws.configuration.config import AWSProviderConfig
from src.providers.aws.infrastructure.aws_client import AWSClient
from src.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

@injectable
class AWSProviderStrategy(ProviderStrategy):
    """AWS implementation of the provider strategy."""

    def __init__(self, config: AWSProviderConfig, logger: LoggingPort):
        self._config = config
        self._logger = logger
        self._aws_client = AWSClient(config, logger)
        self._handler_factory = AWSHandlerFactory(self._aws_client, logger, config)
        self._initialized = False

    async def provision_instances(self, request: Request) -> List[Machine]:
        """Provision AWS compute instances."""
        self._logger.info(f"Provisioning instances for request {request.id}")

        # Get appropriate handler based on template configuration
        handler = self._handler_factory.create_handler_for_template(request.template)

        # Provision instances using handler
        machines = await handler.provision_instances(request)

        self._logger.info(f"Provisioned {len(machines)} instances")
        return machines

    async def terminate_instances(self, instance_ids: List[str]) -> bool:
        """Terminate AWS compute instances."""
        self._logger.info(f"Terminating {len(instance_ids)} instances")

        try:
            ec2_client = self._aws_client.get_client('ec2')
            response = ec2_client.terminate_instances(InstanceIds=instance_ids)

            # Check termination status
            terminating_instances = response.get('TerminatingInstances', [])
            success = len(terminating_instances) == len(instance_ids)

            if success:
                self._logger.info("All instances terminated successfully")
            else:
                self._logger.warning("Some instances failed to terminate")

            return success

        except Exception as e:
            self._logger.error(f"Error terminating instances: {e}")
            return False

    async def get_instance_status(self, instance_ids: List[str]) -> Dict[str, str]:
        """Get AWS instance status."""
        self._logger.info(f"Getting status for {len(instance_ids)} instances")

        try:
            ec2_client = self._aws_client.get_client('ec2')
            response = ec2_client.describe_instances(InstanceIds=instance_ids)

            status_map = {}
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    instance_id = instance['InstanceId']
                    state = instance['State']['Name']
                    status_map[instance_id] = state

            return status_map

        except Exception as e:
            self._logger.error(f"Error getting instance status: {e}")
            return {}

    async def validate_template(self, template: Template) -> bool:
        """Validate AWS template configuration."""
        self._logger.info(f"Validating template {template.template_id}")

        # Use template adapter for validation
        template_adapter = self._get_template_adapter()
        return await template_adapter.validate_template(template.to_dict())

    async def get_available_templates(self) -> List[Template]:
        """Get available AWS templates."""
        self._logger.info("Retrieving available AWS templates")

        # Implementation would retrieve templates from configuration
        # or discover them from AWS resources
        templates = []

        # Example template configurations
        default_templates = self._config.get_default_templates()
        for template_config in default_templates:
            template = Template.from_dict(template_config)
            templates.append(template)

        return templates

    def get_provider_info(self) -> Dict[str, Any]:
        """Get AWS provider information."""
        return {
            "provider_type": "aws",
            "region": self._config.region,
            "profile": self._config.profile,
            "initialized": self._initialized,
            "supported_handlers": [
                "ec2_fleet",
                "spot_fleet", 
                "auto_scaling_group",
                "run_instances"
            ]
        }

    async def health_check(self) -> bool:
        """Check AWS provider health."""
        try:
            # Test AWS connectivity
            sts_client = self._aws_client.get_client('sts')
            response = sts_client.get_caller_identity()

            self._logger.info(f"AWS health check passed for account {response.get('Account')}")
            return True

        except Exception as e:
            self._logger.error(f"AWS health check failed: {e}")
            return False
```

### Provider Context (Strategy Manager)

The provider context manages strategy selection and execution:

```python
# src/providers/base/strategy/provider_context.py
from typing import Dict, List, Optional, Any
from src.domain.base.ports import LoggingPort
from src.providers.base.strategy.provider_strategy import ProviderStrategy

class ProviderContext:
    """Context for managing provider strategies."""

    def __init__(self, logger: LoggingPort):
        self._logger = logger
        self._strategies: Dict[str, ProviderStrategy] = {}
        self._current_strategy: Optional[str] = None
        self._default_strategy: Optional[str] = None

    def register_strategy(self, name: str, strategy: ProviderStrategy) -> None:
        """Register a provider strategy."""
        self._strategies[name] = strategy
        self._logger.info(f"Registered provider strategy: {name}")

        # Set as default if first strategy
        if self._default_strategy is None:
            self._default_strategy = name
            self._current_strategy = name

    def set_current_strategy(self, name: str) -> bool:
        """Set the current active strategy."""
        if name not in self._strategies:
            self._logger.error(f"Strategy not found: {name}")
            return False

        self._current_strategy = name
        self._logger.info(f"Switched to strategy: {name}")
        return True

    def get_current_strategy(self) -> Optional[ProviderStrategy]:
        """Get the current active strategy."""
        if self._current_strategy is None:
            return None

        return self._strategies.get(self._current_strategy)

    def get_available_strategies(self) -> List[str]:
        """Get list of available strategy names."""
        return list(self._strategies.keys())

    async def provision_instances(self, request: Request) -> List[Machine]:
        """Provision instances using current strategy."""
        strategy = self.get_current_strategy()
        if strategy is None:
            raise RuntimeError("No provider strategy available")

        return await strategy.provision_instances(request)

    async def terminate_instances(self, instance_ids: List[str]) -> bool:
        """Terminate instances using current strategy."""
        strategy = self.get_current_strategy()
        if strategy is None:
            raise RuntimeError("No provider strategy available")

        return await strategy.terminate_instances(instance_ids)

    def get_provider_info(self) -> Dict[str, Any]:
        """Get information about current provider."""
        strategy = self.get_current_strategy()
        if strategy is None:
            return {"error": "No provider strategy available"}

        info = strategy.get_provider_info()
        info["current_strategy"] = self._current_strategy
        info["available_strategies"] = self.get_available_strategies()

        return info
```

## Strategy Factory Pattern

The strategy factory creates appropriate strategies based on configuration:

```python
# src/infrastructure/factories/provider_strategy_factory.py
from src.domain.base.ports import LoggingPort, ConfigurationPort
from src.providers.base.strategy.provider_strategy import ProviderStrategy
from src.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from src.providers.aws.configuration.config import AWSProviderConfig

class ProviderStrategyFactory:
    """Factory for creating provider strategies."""

    def __init__(self, 
                 config_manager: ConfigurationPort,
                 logger: LoggingPort):
        self._config_manager = config_manager
        self._logger = logger

    def create_strategy(self, provider_type: str) -> ProviderStrategy:
        """Create provider strategy based on type."""
        self._logger.info(f"Creating provider strategy: {provider_type}")

        if provider_type.lower() == "aws":
            return self._create_aws_strategy()
        else:
            raise ValueError(f"Unsupported provider type: {provider_type}")

    def _create_aws_strategy(self) -> AWSProviderStrategy:
        """Create AWS provider strategy."""
        # Get AWS configuration
        aws_config_data = self._config_manager.get_section("aws")
        aws_config = AWSProviderConfig(**aws_config_data)

        # Create strategy
        strategy = AWSProviderStrategy(
            config=aws_config,
            logger=self._logger
        )

        return strategy

    def get_available_providers(self) -> List[str]:
        """Get list of available provider types."""
        return ["aws"]  # Can be extended for other providers
```

## Configuration-Driven Strategy Selection

Strategies are selected based on configuration:

```yaml
# config/providers.yml
providers:
  - name: aws-primary
    type: aws
    config:
      region: us-east-1
      profile: default
      handlers:
        default: ec2_fleet
        ec2_fleet:
          enabled: true
        spot_fleet:
          enabled: true
          max_spot_price: 0.10
        auto_scaling_group:
          enabled: true
          min_size: 1
          max_size: 10
```

```python
# Strategy registration based on configuration
def register_provider_strategies(container: DIContainer) -> None:
    """Register provider strategies based on configuration."""
    config = container.get(ConfigurationPort)
    logger = container.get(LoggingPort)

    # Get provider configuration
    provider_configs = config.get("providers", [])

    # Create provider context
    provider_context = ProviderContext(logger)

    # Register strategies
    strategy_factory = ProviderStrategyFactory(config, logger)

    for provider_config in provider_configs:
        provider_name = provider_config["name"]
        provider_type = provider_config["type"]

        # Create strategy
        strategy = strategy_factory.create_strategy(provider_type)

        # Register with context
        provider_context.register_strategy(provider_name, strategy)

    # Register context in container
    container.register_instance(ProviderContext, provider_context)
```

## Handler Strategy Pattern (Sub-strategies)

Within the AWS provider, different handlers implement sub-strategies for different provisioning methods:

### Handler Strategy Interface

```python
# src/providers/aws/infrastructure/handlers/base_handler.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from src.domain.request.aggregate import Request
from src.domain.machine.aggregate import Machine

@injectable
class AWSHandler(ABC):
    """Abstract base class for AWS handlers."""

    def __init__(self, 
                 aws_client: AWSClient,
                 logger: LoggingPort,
                 config: ConfigurationPort):
        self._aws_client = aws_client
        self._logger = logger
        self._config = config

    @abstractmethod
    async def provision_instances(self, request: Request) -> List[Machine]:
        """Provision instances using this handler."""
        pass

    @abstractmethod
    async def terminate_instances(self, instance_ids: List[str]) -> bool:
        """Terminate instances using this handler."""
        pass

    @abstractmethod
    def get_handler_info(self) -> Dict[str, Any]:
        """Get handler-specific information."""
        pass
```

### Concrete Handler Strategies

```python
# src/providers/aws/infrastructure/handlers/ec2_fleet_handler.py
@injectable
class EC2FleetHandler(AWSHandler):
    """Handler for EC2 Fleet provisioning."""

    async def provision_instances(self, request: Request) -> List[Machine]:
        """Provision instances using EC2 Fleet."""
        self._logger.info(f"Provisioning instances using EC2 Fleet")

        # Build fleet configuration
        fleet_config = self._build_fleet_config(request)

        # Create fleet
        ec2_client = self._aws_client.get_client('ec2')
        response = ec2_client.create_fleet(**fleet_config)

        # Process response and create machine objects
        machines = self._process_fleet_response(response, request)

        return machines

# src/providers/aws/infrastructure/handlers/spot_fleet_handler.py
@injectable
class SpotFleetHandler(AWSHandler):
    """Handler for Spot Fleet provisioning."""

    async def provision_instances(self, request: Request) -> List[Machine]:
        """Provision instances using Spot Fleet."""
        self._logger.info(f"Provisioning instances using Spot Fleet")

        # Build spot fleet configuration
        spot_config = self._build_spot_fleet_config(request)

        # Create spot fleet
        ec2_client = self._aws_client.get_client('ec2')
        response = ec2_client.request_spot_fleet(**spot_config)

        # Process response and create machine objects
        machines = self._process_spot_fleet_response(response, request)

        return machines
```

### Handler Factory (Strategy Factory)

```python
# src/providers/aws/infrastructure/aws_handler_factory.py
@injectable
class AWSHandlerFactory:
    """Factory for creating AWS handlers based on strategy."""

    def __init__(self,
                 aws_client: AWSClient,
                 logger: LoggingPort,
                 config: ConfigurationPort):
        self._aws_client = aws_client
        self._logger = logger
        self._config = config
        self._handlers: Dict[str, Type[AWSHandler]] = {}
        self._register_handlers()

    def _register_handlers(self):
        """Register available handler types."""
        from .handlers.ec2_fleet_handler import EC2FleetHandler
        from .handlers.spot_fleet_handler import SpotFleetHandler
        from .handlers.asg_handler import ASGHandler
        from .handlers.run_instances_handler import RunInstancesHandler

        self._handlers = {
            "ec2_fleet": EC2FleetHandler,
            "spot_fleet": SpotFleetHandler,
            "auto_scaling_group": ASGHandler,
            "run_instances": RunInstancesHandler
        }

    def create_handler(self, handler_type: str) -> AWSHandler:
        """Create handler based on type."""
        if handler_type not in self._handlers:
            raise ValueError(f"Unknown handler type: {handler_type}")

        handler_class = self._handlers[handler_type]
        return handler_class(self._aws_client, self._logger, self._config)

    def create_handler_for_template(self, template: Template) -> AWSHandler:
        """Create appropriate handler based on template configuration."""
        # Determine handler type from template
        handler_type = self._determine_handler_type(template)
        return self.create_handler(handler_type)

    def _determine_handler_type(self, template: Template) -> str:
        """Determine appropriate handler type for template."""
        # Logic to determine handler based on template attributes
        attributes = template.attributes

        if attributes.get("use_spot_instances", False):
            return "spot_fleet"
        elif attributes.get("use_auto_scaling", False):
            return "auto_scaling_group"
        elif attributes.get("use_fleet", True):
            return "ec2_fleet"
        else:
            return "run_instances"
```

## Benefits of Strategy Pattern Implementation

### Extensibility
- Easy to add new cloud providers
- New provisioning methods can be added as strategies
- Configuration-driven strategy selection

### Maintainability
- Each strategy is self-contained
- Clear separation of provider-specific logic
- Easy to modify individual strategies

### Testability
- Strategies can be tested independently
- Easy mocking of specific strategies
- Integration testing with different strategies

### Runtime Flexibility
- Strategy selection based on configuration
- Dynamic strategy switching possible
- Multiple strategies can coexist

This Strategy pattern implementation provides a flexible and extensible foundation for supporting multiple cloud providers and provisioning methods in the Open Host Factory Plugin.
