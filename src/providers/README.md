# Provider Layer - Cloud-Agnostic Extensions

The provider layer contains cloud-specific implementations that extend the core system for different cloud platforms. This layer maintains the provider-agnostic design of the domain layer while providing concrete implementations for cloud services.

**File Count**: 48 files implementing comprehensive cloud provider support

## Architecture Overview

### Cloud-Agnostic Design Philosophy
This Host Factory Plugin is built as an **open-source, cloud-agnostic solution** based on open standards:

- **Open Source Foundation**: Built with open standards and extensible architecture
- **Provider-Agnostic Core**: Domain layer uses generic `provider_config` instead of cloud-specific fields
- **Clean Extension Points**: Standardized interfaces for adding new cloud providers
- **Vendor Neutral**: No vendor lock-in, supports multiple cloud platforms
- **Open Standards Compliance**: Follows industry-standard patterns and interfaces

### Dependency Flow
```
Provider Layer → Infrastructure Layer → Application Layer → Domain Layer
```

## Package Structure

### `base/` - Base Provider Components
Common base classes and interfaces for all cloud providers.

**Key Components:**
- Base provider interfaces
- Common provider utilities
- Shared provider patterns

### `aws/` - Reference Implementation
Complete reference implementation demonstrating the provider pattern.

**Supported Services:**
- **EC2 Fleet**: Advanced fleet management with mixed instance types
- **Auto Scaling Groups**: Scalable instance groups with automatic scaling
- **Spot Fleet**: Cost-optimized spot instance management
- **RunInstances**: Direct instance provisioning

**Key Features:**
- Full cloud SDK integration
- Comprehensive error handling and resilience
- Cost optimization strategies
- Multi-region support

## Provider Implementation Architecture

### Clean Architecture Compliance
Each provider follows the same clean architecture principles:

```
Provider Implementation
├── domain/          # Provider domain extensions
├── application/     # Provider application services  
├── infrastructure/  # Provider infrastructure implementations
├── managers/        # Provider resource managers
├── configuration/   # Provider configuration
├── utilities/       # Provider utilities
└── resilience/      # Provider resilience patterns
```

### Key Components

#### `domain/` - Provider Domain Extensions
Provider-specific domain extensions that don't pollute the core domain.

**Components:**
- Provider-specific value objects
- Provider domain events
- Provider business rules

#### `application/` - Provider Application Services
Provider-specific application logic and use cases.

**Components:**
- Provider command handlers
- Provider query handlers
- Provider workflow orchestration

#### `infrastructure/` - Provider Infrastructure
Technical implementations for cloud services.

**Key Features:**
- **Client Management**: Cloud SDK client lifecycle
- **Session Management**: Credential and authentication handling
- **Region Management**: Multi-region support
- **Service Integration**: Cloud service API integration

#### `managers/` - Provider Resource Managers
High-level managers for cloud resource operations.

**Key Managers:**
- **Resource Manager**: Overall resource management
- **Instance Manager**: Virtual machine instance management
- **Service Handlers**: Specific cloud service handlers

#### `configuration/` - Provider Configuration
Provider-specific configuration management.

**Features:**
- Cloud credential configuration
- Region and availability zone settings
- Service-specific configurations
- Cost optimization settings

#### `utilities/` - Provider Utilities
Provider-specific utility functions and helpers.

**Key Utilities:**
- **Consolidated Operations**: Integrated operation patterns
- Instance management utilities
- Cost calculation utilities
- Resource tagging utilities

#### `resilience/` - Provider Resilience
Provider-specific resilience patterns and error handling.

**Features:**
- Cloud API retry strategies
- Provider-specific error classification
- Service-specific circuit breakers
- API throttling handling

## Handler Implementation Pattern

### Integrated Operations Approach
All cloud service handlers use consolidated operation utilities to eliminate code duplication:

```python
class CloudOperations:
    """Consolidated cloud operations utility eliminating duplication."""

    def __init__(self, cloud_client, logger):
        self._cloud_client = cloud_client
        self._logger = logger

    async def terminate_instances(self, instance_ids: List[str]) -> Dict[str, Any]:
        """Integrated instance termination across all handlers."""
        try:
            response = await self._cloud_client.terminate_instances(
                instance_ids=instance_ids
            )

            self._logger.info(f"Terminated {len(instance_ids)} instances")
            return self._standardize_termination_response(response)

        except Exception as e:
            self._logger.error(f"Failed to terminate instances: {str(e)}")
            raise CloudOperationError(f"Instance termination failed: {str(e)}") from e
```

### Service Handler Implementations

#### Fleet Handler Pattern
```python
class FleetHandler:
    """Handler for fleet operations."""

    def __init__(self, cloud_client, cloud_operations):
        self._cloud_client = cloud_client
        self._cloud_ops = cloud_operations
        self._logger = get_logger(__name__)

    async def create_fleet(self, fleet_config: FleetConfig) -> List[str]:
        """Create fleet and return instance IDs."""
        try:
            response = await self._cloud_client.create_fleet(
                launch_templates=fleet_config.launch_templates,
                target_capacity=fleet_config.target_capacity,
                fleet_type='instant'
            )

            instance_ids = self._extract_instance_ids(response)
            self._logger.info(f"Created fleet with {len(instance_ids)} instances")

            return instance_ids

        except Exception as e:
            self._logger.error(f"Fleet creation failed: {str(e)}")
            raise CloudFleetError(f"Failed to create fleet: {str(e)}") from e

    async def terminate_fleet_instances(self, instance_ids: List[str]) -> Dict[str, Any]:
        """Terminate fleet instances using integrated operations."""
        return await self._cloud_ops.terminate_instances(instance_ids)
```

#### Auto Scaling Handler Pattern
```python
class AutoScalingHandler:
    """Handler for auto scaling operations."""

    def __init__(self, scaling_client, compute_client, cloud_operations):
        self._scaling_client = scaling_client
        self._compute_client = compute_client
        self._cloud_ops = cloud_operations
        self._logger = get_logger(__name__)

    async def create_auto_scaling_group(self, asg_config: ASGConfig) -> str:
        """Create auto scaling group."""
        try:
            await self._scaling_client.create_auto_scaling_group(
                name=asg_config.name,
                launch_template=asg_config.launch_template,
                min_size=asg_config.min_size,
                max_size=asg_config.max_size,
                desired_capacity=asg_config.desired_capacity,
                subnets=asg_config.subnet_ids
            )

            self._logger.info(f"Created auto scaling group: {asg_config.name}")
            return asg_config.name

        except Exception as e:
            self._logger.error(f"Auto scaling group creation failed: {str(e)}")
            raise CloudASGError(f"Failed to create auto scaling group: {str(e)}") from e

    async def terminate_asg_instances(self, instance_ids: List[str]) -> Dict[str, Any]:
        """Terminate auto scaling group instances using integrated operations."""
        return await self._cloud_ops.terminate_instances(instance_ids)
```

## Configuration Integration

### Provider Configuration Pattern
```python
@dataclass
class CloudProviderConfig:
    """Generic cloud provider configuration."""
    region: str = "default-region"
    profile: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    session_token: Optional[str] = None
    max_retries: int = 3
    timeout: int = 30

    # Service-specific configurations
    compute: Optional[ComputeConfig] = None
    auto_scaling: Optional[AutoScalingConfig] = None
    spot_fleet: Optional[SpotFleetConfig] = None
```

### Provider Registration Pattern
```python
def register_cloud_provider(container: DIContainer, config: CloudProviderConfig) -> None:
    """Register cloud provider services with DI container."""

    # Register cloud client
    container.register_singleton(
        CloudClient, 
        lambda: CloudClient(config)
    )

    # Register cloud operations utility
    container.register_singleton(
        CloudOperations,
        lambda: CloudOperations(
            container.resolve(CloudClient),
            get_logger("cloud.operations")
        )
    )

    # Register resource managers
    container.register_singleton(
        CloudResourceManager,
        lambda: CloudResourceManager(
            container.resolve(CloudClient),
            container.resolve(CloudOperations)
        )
    )

    # Register provider
    container.register_singleton(
        ProviderInterface,
        lambda: CloudProvider(config)
    )
```

## Error Handling

### Provider-Specific Exception Hierarchy
```python
class CloudProviderError(Exception):
    """Base cloud provider exception."""
    pass

class CloudOperationError(CloudProviderError):
    """Cloud operation failed."""
    pass

class CloudFleetError(CloudProviderError):
    """Cloud fleet operation failed."""
    pass

class CloudASGError(CloudProviderError):
    """Cloud auto scaling group operation failed."""
    pass

class CloudSpotFleetError(CloudProviderError):
    """Cloud spot fleet operation failed."""
    pass
```

### Error Classification Pattern
```python
class CloudErrorClassifier:
    """Classify cloud errors for appropriate handling."""

    TRANSIENT_ERRORS = [
        'RequestLimitExceeded',
        'Throttling',
        'ServiceUnavailable',
        'InternalError'
    ]

    PERMANENT_ERRORS = [
        'InvalidParameterValue',
        'UnauthorizedOperation',
        'InvalidResourceID'
    ]

    def is_transient_error(self, error: Exception) -> bool:
        """Check if error is transient and should be retried."""
        if hasattr(error, 'response'):
            error_code = error.response.get('Error', {}).get('Code', '')
            return error_code in self.TRANSIENT_ERRORS
        return False
```

## Cost Optimization

### Spot Instance Integration Pattern
```python
class SpotFleetHandler:
    """Handler for cost-optimized spot fleet operations."""

    async def create_spot_fleet(self, spot_config: SpotFleetConfig) -> str:
        """Create spot fleet for cost optimization."""
        try:
            response = await self._cloud_client.request_spot_fleet(
                fleet_config={
                    'iam_fleet_role': spot_config.iam_fleet_role,
                    'allocation_strategy': 'diversified',
                    'target_capacity': spot_config.target_capacity,
                    'max_spot_price': spot_config.max_spot_price,
                    'launch_specifications': spot_config.launch_specifications,
                    'fleet_type': 'request'
                }
            )

            fleet_id = response['fleet_id']
            self._logger.info(f"Created spot fleet: {fleet_id}")

            return fleet_id

        except Exception as e:
            self._logger.error(f"Spot fleet creation failed: {str(e)}")
            raise CloudSpotFleetError(f"Failed to create spot fleet: {str(e)}") from e
```

## Multi-Region Support

### Region Management Pattern
```python
class CloudRegionManager:
    """Manage cloud operations across multiple regions."""

    def __init__(self, primary_region: str, fallback_regions: List[str]):
        self._primary_region = primary_region
        self._fallback_regions = fallback_regions
        self._region_clients: Dict[str, CloudClient] = {}

    async def provision_with_fallback(self, 
                                    provision_request: ProvisionRequest) -> List[str]:
        """Provision resources with region fallback."""
        regions_to_try = [self._primary_region] + self._fallback_regions

        for region in regions_to_try:
            try:
                client = await self._get_region_client(region)
                return await client.provision_instances(provision_request)

            except Exception as e:
                self._logger.warning(f"Provisioning failed in {region}: {str(e)}")
                if region == regions_to_try[-1]:
                    raise  # Re-raise if all regions failed
                continue
```

## Testing Provider Implementations

### Mock Cloud Services Pattern
```python
class MockCloudClient:
    """Mock cloud client for testing."""

    def __init__(self):
        self._instances: Dict[str, Dict[str, Any]] = {}
        self._fleets: Dict[str, Dict[str, Any]] = {}

    async def run_instances(self, **kwargs) -> Dict[str, Any]:
        """Mock compute instance creation operation."""
        instance_count = kwargs.get('instance_count', 1)
        instances = []

        for i in range(instance_count):
            instance_id = f"instance-{uuid.uuid4().hex[:8]}"
            instance = {
                'instance_id': instance_id,
                'state': {'name': 'running'},
                'instance_type': kwargs.get('instance_type', 'standard.small')
            }
            instances.append(instance)
            self._instances[instance_id] = instance

        return {'instances': instances}
```

## Provider Extension Guidelines

### Adding New Cloud Providers

#### Provider Structure Template
```
providers/
└── new_provider/
    ├── domain/          # Provider domain extensions
    ├── application/     # Provider application services
    ├── infrastructure/  # Provider infrastructure
    ├── managers/        # Provider resource managers
    ├── configuration/   # Provider configuration
    ├── utilities/       # Provider utilities
    └── resilience/      # Provider resilience patterns
```

#### Provider Interface Implementation
```python
class NewCloudProvider(ProviderInterface):
    """New cloud provider implementation."""

    @property
    def provider_type(self) -> str:
        return "new_cloud"

    async def provision_resources(self, request: ProvisionRequest) -> List[str]:
        """Provision cloud resources."""
        # Provider-specific implementation
        pass

    async def terminate_resources(self, resource_ids: List[str]) -> Dict[str, Any]:
        """Terminate cloud resources."""
        # Provider-specific implementation
        pass
```

## Best Practices

### Provider Development Guidelines
1. **Maintain Cloud Agnosticism**: Keep domain layer cloud-agnostic
2. **Use Consistent Interfaces**: Implement standard provider interfaces
3. **Handle Provider-Specific Errors**: Classify and handle cloud-specific errors
4. **Optimize for Cost**: Implement cost optimization strategies
5. **Support Multi-Region**: Design for region failover and distribution
6. **Comprehensive Testing**: Mock cloud services for testing
7. **Monitor Performance**: Track provider-specific metrics

### Configuration Management
- Use provider-specific configuration sections
- Support multiple credential methods
- Validate configuration at startup
- Support environment-specific settings

### Error Handling
- Classify transient vs permanent errors
- Implement appropriate retry strategies
- Provide meaningful error messages
- Log provider-specific error details

### Open Source Contribution
- Follow open source development practices
- Maintain comprehensive documentation
- Provide clear extension examples
- Support community contributions

---

This provider layer enables seamless multi-cloud support while maintaining the clean architecture and cloud-agnostic design of the core system. Built on open standards and designed for extensibility, it provides a solid foundation for supporting any cloud platform.
