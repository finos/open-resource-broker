# Infrastructure Layer

The Infrastructure Layer provides concrete implementations of domain ports and handles all external integrations. This layer implements the technical details while keeping the domain and application layers clean and focused on business logic.

## Architecture

```
infrastructure/
├── di/                # Dependency Injection container and registrations
├── persistence/       # Data persistence implementations
├── providers/         # Cloud provider integrations
├── scheduler/         # Job scheduling and workflow management
├── template/          # Template infrastructure components
├── utilities/         # Infrastructure utilities and factories
├── error/            # Error handling and exception management
└── mocking/          # Test doubles and mocking utilities
```

## Port/Adapter Pattern

The infrastructure layer implements the Port/Adapter pattern, where:
- **Ports**: Interfaces defined in the domain layer
- **Adapters**: Concrete implementations in the infrastructure layer

### Repository Adapters
Implement domain repository ports:

```python
class SQLMachineRepository(MachineRepositoryPort):
    def __init__(self, connection: DatabaseConnection):
        self.connection = connection

    async def save(self, machine: Machine) -> None:
        """Save machine to SQL database."""
        query = """
            INSERT INTO machines (id, template_id, status, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
        """
        await self.connection.execute(query, [
            machine.id.value,
            machine.template_id.value,
            machine.status.value,
            machine.created_at
        ])

    async def find_by_id(self, machine_id: MachineId) -> Optional[Machine]:
        """Find machine by ID from SQL database."""
        query = "SELECT * FROM machines WHERE id = ?"
        row = await self.connection.fetch_one(query, [machine_id.value])

        if not row:
            return None

        return Machine.from_persistence(
            machine_id=MachineId(row['id']),
            template_id=TemplateId(row['template_id']),
            status=MachineStatus(row['status']),
            created_at=row['created_at']
        )
```

### External Service Adapters
Implement external service ports:

```python
class AWSProviderAdapter(CloudProviderPort):
    def __init__(self, ec2_client: EC2Client, logger: LoggingPort):
        self.ec2_client = ec2_client
        self.logger = logger

    async def provision_instance(self, config: InstanceConfiguration) -> ProvisionResult:
        """Provision EC2 instance."""
        try:
            response = await self.ec2_client.run_instances(
                ImageId=config.ami_id,
                InstanceType=config.instance_type,
                MinCount=1,
                MaxCount=1,
                SecurityGroupIds=config.security_groups,
                SubnetId=config.subnet_id
            )

            instance_id = response['Instances'][0]['InstanceId']
            self.logger.info(f"Provisioned EC2 instance: {instance_id}")

            return ProvisionResult(
                instance_id=instance_id,
                status="pending",
                provider_data=response['Instances'][0]
            )

        except Exception as e:
            self.logger.error(f"Failed to provision instance: {e}")
            raise InfrastructureError(f"Provisioning failed: {e}")
```

## Dependency Injection

### Container Configuration
Comprehensive DI container with automatic service registration:

```python
class DIContainer:
    def __init__(self):
        self._services: Dict[Type, Any] = {}
        self._singletons: Dict[Type, Any] = {}
        self._factories: Dict[Type, Callable] = {}

    def register_singleton(self, interface: Type[T], implementation: Type[T]) -> None:
        """Register singleton service."""
        self._services[interface] = implementation
        self._singletons[interface] = None

    def register_transient(self, interface: Type[T], implementation: Type[T]) -> None:
        """Register transient service."""
        self._services[interface] = implementation

    def get(self, service_type: Type[T]) -> T:
        """Resolve service from container."""
        if service_type in self._singletons:
            if self._singletons[service_type] is None:
                self._singletons[service_type] = self._create_instance(service_type)
            return self._singletons[service_type]

        return self._create_instance(service_type)
```

### Service Registration
Automatic registration of infrastructure services:

```python
def register_infrastructure_services(container: DIContainer) -> None:
    """Register all infrastructure services."""

    # Repository implementations
    container.register_singleton(MachineRepositoryPort, SQLMachineRepository)
    container.register_singleton(TemplateRepositoryPort, SQLTemplateRepository)
    container.register_singleton(RequestRepositoryPort, SQLRequestRepository)

    # External service adapters
    container.register_singleton(CloudProviderPort, AWSProviderAdapter)
    container.register_singleton(NotificationPort, EmailNotificationAdapter)
    container.register_singleton(LoggingPort, StructuredLoggingAdapter)

    # Infrastructure services
    container.register_singleton(DatabaseConnection, SQLiteConnection)
    container.register_singleton(ConfigurationManager, YAMLConfigurationManager)
    container.register_transient(EventPublisher, AsyncEventPublisher)
```

## Persistence Layer

### Storage Strategies
Multiple storage implementations with strategy pattern:

```python
class StorageStrategy(ABC):
    @abstractmethod
    async def save(self, key: str, data: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    async def load(self, key: str) -> Optional[Dict[str, Any]]:
        pass

class JSONStorageStrategy(StorageStrategy):
    def __init__(self, file_path: str):
        self.file_path = file_path

    async def save(self, key: str, data: Dict[str, Any]) -> None:
        """Save data to JSON file."""
        # Implementation

class DynamoDBStorageStrategy(StorageStrategy):
    def __init__(self, table_name: str, dynamodb_client: DynamoDBClient):
        self.table_name = table_name
        self.dynamodb_client = dynamodb_client

    async def save(self, key: str, data: Dict[str, Any]) -> None:
        """Save data to DynamoDB."""
        # Implementation
```

### Repository Base Classes
Common repository functionality:

```python
class BaseRepository(Generic[T]):
    def __init__(self, storage: StorageStrategy, serializer: SerializationStrategy):
        self.storage = storage
        self.serializer = serializer

    async def save(self, entity: T) -> None:
        """Save entity using storage strategy."""
        data = self.serializer.serialize(entity)
        await self.storage.save(entity.id, data)

    async def find_by_id(self, entity_id: str) -> Optional[T]:
        """Find entity by ID using storage strategy."""
        data = await self.storage.load(entity_id)
        if not data:
            return None

        return self.serializer.deserialize(data)
```

## Provider Integration

### AWS Provider Strategy
Comprehensive AWS integration:

```python
class AWSProviderStrategy(ProviderStrategyPort):
    def __init__(self, 
                 ec2_client: EC2Client,
                 ssm_client: SSMClient,
                 config: AWSConfiguration):
        self.ec2_client = ec2_client
        self.ssm_client = ssm_client
        self.config = config

    async def provision_machines(self, request: ProvisionRequest) -> ProvisionResult:
        """Provision machines using AWS EC2."""
        template = await self._resolve_template(request.template_id)

        # Resolve AMI
        ami_id = await self._resolve_ami(template.ami_specification)

        # Create launch configuration
        launch_config = self._create_launch_configuration(template, ami_id)

        # Provision instances
        instances = await self._provision_instances(launch_config, request.count)

        return ProvisionResult(
            request_id=request.id,
            instances=instances,
            provider="aws"
        )

    async def _resolve_ami(self, ami_spec: AMISpecification) -> str:
        """Resolve AMI ID from specification."""
        if ami_spec.ami_id:
            return ami_spec.ami_id

        # Query AWS for latest AMI matching criteria
        response = await self.ec2_client.describe_images(
            Filters=[
                {'Name': 'name', 'Values': [ami_spec.name_pattern]},
                {'Name': 'state', 'Values': ['available']},
                {'Name': 'architecture', 'Values': [ami_spec.architecture]}
            ],
            Owners=[ami_spec.owner_id]
        )

        if not response['Images']:
            raise InfrastructureError(f"No AMI found matching: {ami_spec}")

        # Return most recent AMI
        latest_ami = max(response['Images'], key=lambda x: x['CreationDate'])
        return latest_ami['ImageId']
```

### Multi-Provider Support
Strategy pattern for multiple cloud providers:

```python
class ProviderFactory:
    def __init__(self, container: DIContainer):
        self.container = container
        self._strategies: Dict[str, Type[ProviderStrategyPort]] = {
            'aws': AWSProviderStrategy,
            'azure': AzureProviderStrategy,
            'gcp': GCPProviderStrategy
        }

    def create_provider(self, provider_type: str) -> ProviderStrategyPort:
        """Create provider strategy instance."""
        if provider_type not in self._strategies:
            raise ValueError(f"Unsupported provider: {provider_type}")

        strategy_class = self._strategies[provider_type]
        return self.container.get(strategy_class)
```

## Error Handling

### Infrastructure Exceptions
Standardized error handling for infrastructure concerns:

```python
class InfrastructureError(Exception):
    """Base infrastructure error."""
    pass

class PersistenceError(InfrastructureError):
    """Database/storage related errors."""
    pass

class ExternalServiceError(InfrastructureError):
    """External service integration errors."""
    pass

class ConfigurationError(InfrastructureError):
    """Configuration related errors."""
    pass
```

### Error Response Handling
```python
class InfrastructureErrorResponse:
    @staticmethod
    def from_exception(error: Exception, context: str) -> Dict[str, Any]:
        """Create standardized error response."""
        return {
            'error': True,
            'error_type': error.__class__.__name__,
            'message': str(error),
            'context': context,
            'timestamp': datetime.utcnow().isoformat()
        }
```

## Configuration Management

### Configuration Strategy
Flexible configuration management:

```python
class ConfigurationManager:
    def __init__(self, config_sources: List[ConfigurationSource]):
        self.config_sources = config_sources
        self._cache: Dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with fallback chain."""
        if key in self._cache:
            return self._cache[key]

        for source in self.config_sources:
            value = source.get(key)
            if value is not None:
                self._cache[key] = value
                return value

        return default

    def get_provider_config(self) -> ProviderConfiguration:
        """Get provider-specific configuration."""
        return ProviderConfiguration(
            provider_type=self.get('PROVIDER_TYPE', 'aws'),
            region=self.get('AWS_REGION', 'us-east-1'),
            profile=self.get('AWS_PROFILE', 'default'),
            dry_run=self.get('DRY_RUN', False)
        )
```

## Template Infrastructure

### Template Infrastructure Components
The template system provides comprehensive infrastructure support with three core components:

#### Template DTOs (Data Transfer Objects)
Infrastructure layer DTOs for template data transfer and persistence:

```python
class TemplateDTO(BaseModel):
    """Infrastructure DTO for template data transfer and persistence."""
    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        populate_by_name=True
    )

    # Core template identification
    template_id: str = Field(description="Unique template identifier")
    name: Optional[str] = Field(default=None, description="Human-readable template name")
    description: Optional[str] = Field(default=None, description="Template description")

    # Provider configuration
    provider_api: str = Field(description="Provider API type (aws, azure, etc.)")
    provider_type: Optional[str] = Field(default=None, description="Provider type")
    provider_name: Optional[str] = Field(default=None, description="Provider instance name")

    # Template configuration data
    configuration: Dict[str, Any] = Field(default_factory=dict, description="Template configuration")

    # Metadata and status
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Template metadata")
    tags: Dict[str, str] = Field(default_factory=dict, description="Template tags")
    is_active: bool = Field(default=True, description="Whether template is active")

    # Timestamps
    created_at: Optional[datetime] = Field(default=None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(default=None, description="Last update timestamp")

    # File metadata
    source_file: Optional[str] = Field(default=None, description="Source file path")
    file_priority: Optional[int] = Field(default=None, description="File priority in hierarchy")

    def to_domain(self) -> 'Template':
        """Convert infrastructure DTO to domain Template aggregate."""
        from src.domain.template.aggregate import Template

        return Template(
            template_id=self.template_id,
            name=self.name,
            description=self.description,
            provider_api=self.provider_api,
            provider_type=self.provider_type,
            provider_name=self.provider_name,
            configuration=self.configuration,
            metadata=self.metadata,
            tags=self.tags,
            is_active=self.is_active,
            created_at=self.created_at,
            updated_at=self.updated_at
        )

    @classmethod
    def from_domain(cls, template: 'Template') -> 'TemplateDTO':
        """Create infrastructure DTO from domain Template aggregate."""
        return cls(
            template_id=template.template_id,
            name=template.name,
            description=template.description,
            provider_api=template.provider_api or 'aws',
            provider_type=template.provider_type,
            provider_name=template.provider_name,
            configuration=template.configuration if hasattr(template, 'configuration') else {},
            metadata=template.metadata if hasattr(template, 'metadata') else {},
            tags=template.tags if hasattr(template, 'tags') else {},
            is_active=template.is_active if hasattr(template, 'is_active') else True,
            created_at=template.created_at if hasattr(template, 'created_at') else None,
            updated_at=template.updated_at if hasattr(template, 'updated_at') else None
        )
```

#### Template Cache Entry DTO
DTO for template cache entries with metadata and expiration tracking:

```python
class TemplateCacheEntryDTO(BaseModel):
    """DTO for template cache entries with metadata."""
    model_config = ConfigDict(frozen=True)

    template: TemplateDTO = Field(description="Cached template data")
    cached_at: datetime = Field(description="Cache timestamp")
    ttl_seconds: int = Field(description="Time to live in seconds")
    hit_count: int = Field(default=0, description="Number of cache hits")

    @property
    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        age_seconds = (datetime.now() - self.cached_at).total_seconds()
        return age_seconds > self.ttl_seconds

    @property
    def age_seconds(self) -> float:
        """Get age of cache entry in seconds."""
        return (datetime.now() - self.cached_at).total_seconds()
```

#### Template Validation Result DTO
DTO for template validation results with comprehensive error and warning tracking:

```python
class TemplateValidationResultDTO(BaseModel):
    """DTO for template validation results."""
    model_config = ConfigDict(frozen=True)

    template_id: str = Field(description="Template identifier")
    is_valid: bool = Field(description="Whether template is valid")
    errors: List[str] = Field(default_factory=list, description="Validation errors")
    warnings: List[str] = Field(default_factory=list, description="Validation warnings")
    supported_features: List[str] = Field(default_factory=list, description="Supported features")
    validation_time: datetime = Field(default_factory=datetime.now, description="Validation timestamp")
    provider_instance: Optional[str] = Field(default=None, description="Provider instance validated against")
```

#### Template Configuration Manager
The template system has been streamlined to focus on core components with a single source of truth. The manager now uses infrastructure DTOs to avoid direct domain dependencies, following Clean Architecture principles:

```python
class TemplateConfigurationManager:
    """
    Template Configuration Manager - Single Source of Truth.

    Consolidates template operations into a coherent architecture that
    treats templates as configuration data rather than transactional entities.

    Registration: Manually registered in DI container (port_registrations.py)
    using factory pattern for complex initialization with dependencies.

    Architecture Improvements:
    - Uses TemplateDTO instead of direct domain Template imports
    - Follows Dependency Inversion Principle (DIP)
    - Maintains clean separation between infrastructure and domain layers
    - Provides configuration-driven template discovery and management
    """

    def __init__(self, 
                 config_manager: ConfigurationManager,
                 scheduler_strategy: SchedulerPort,
                 logger: LoggingPort):
        self.config_manager = config_manager
        self.scheduler_strategy = scheduler_strategy
        self.logger = logger

    async def discover_template_files(self, force_refresh: bool = False) -> TemplateDiscoveryResult:
        """
        Discover template files using provider-specific hierarchy.

        Provider-specific file hierarchy (priority order):
        1. Instance files: {provider}inst_templates.{ext}
        2. Type files: {provider}type_templates.{ext}  
        3. Main templates: {provider}prov_templates.{ext}
        4. Legacy files: templates.{ext}
        """
        # Implementation

    async def load_templates(self, force_refresh: bool = False) -> List[TemplateDTO]:
        """Load all templates from discovered files with caching."""
        # Implementation

    async def save_template(self, template: TemplateDTO) -> None:
        """Save template to configuration files."""
        # Implementation
```

### Template Repository Implementation
Repository pattern implementation for template persistence with full AggregateRepository compliance:

```python
class TemplateRepositoryImpl(TemplateRepository):
    """Template repository implementation for configuration-based template management."""

    def __init__(self, 
                 template_manager: TemplateConfigurationManager,
                 logger: LoggingPort):
        """Initialize repository with template configuration manager."""
        self._template_manager = template_manager
        self._logger = logger

    # Abstract methods from AggregateRepository
    def save(self, aggregate: Template) -> None:
        """Save a template aggregate."""
        self._logger.debug(f"Saving template: {aggregate.template_id}")
        self._template_manager.save_template(aggregate)

    def find_by_id(self, aggregate_id: str) -> Optional[Template]:
        """Find template by aggregate ID (required by AggregateRepository)."""
        self._logger.debug(f"Finding template by ID: {aggregate_id}")
        return self._template_manager.get_template(aggregate_id)

    def delete(self, aggregate_id: str) -> None:
        """Delete template by aggregate ID."""
        self._logger.debug(f"Deleting template: {aggregate_id}")
        self._template_manager.delete_template(aggregate_id)

    # Abstract methods from TemplateRepository
    def find_by_template_id(self, template_id: str) -> Optional[Template]:
        """Find template by template ID (required by TemplateRepository)."""
        # Delegate to the main find_by_id method to avoid duplication
        return self.find_by_id(template_id)

    def find_by_provider_api(self, provider_api: str) -> List[Template]:
        """Find templates by provider API type."""
        self._logger.debug(f"Finding templates by provider API: {provider_api}")
        return self._template_manager.get_templates_by_provider(provider_api)

    def find_active_templates(self) -> List[Template]:
        """Find all active templates."""
        self._logger.debug("Finding all active templates")
        return self._template_manager.get_all_templates()

    def search_templates(self, criteria: Dict[str, Any]) -> List[Template]:
        """Search templates by criteria."""
        self._logger.debug(f"Searching templates with criteria: {criteria}")

        all_templates = self._template_manager.get_all_templates()

        filtered_templates = []
        for template in all_templates:
            matches = True

            for key, value in criteria.items():
                template_value = getattr(template, key, None)
                if template_value != value:
                    matches = False
                    break

            if matches:
                filtered_templates.append(template)

        return filtered_templates

    # Convenience methods
    def get_by_id(self, template_id: str) -> Optional[Template]:
        """Get template by ID (convenience method, delegates to find_by_id)."""
        return self.find_by_id(template_id)

    def get_all(self) -> List[Template]:
        """Get all templates."""
        return self.find_active_templates()

    def exists(self, template_id: str) -> bool:
        """Check if template exists."""
        return self._template_manager.get_template(template_id) is not None

    def validate_template(self, template: Template) -> List[str]:
        """Validate template configuration."""
        validation_result = self._template_manager.validate_template(template)
        return validation_result.errors if not validation_result.is_valid else []
```

#### Key Architecture Improvements

1. **Full AggregateRepository Compliance**: Implements all required abstract methods from both `AggregateRepository` and `TemplateRepository` interfaces
2. **Clean Dependency Injection**: Removed `@injectable` decorator as this class is manually registered in the DI container using factory pattern
3. **Method Delegation**: `find_by_template_id` delegates to `find_by_id` to avoid code duplication and maintain single source of truth
4. **Comprehensive Interface**: Provides both required abstract methods and convenience methods for ease of use
5. **Appropriate Logging**: Structured logging with context for all operations
6. **Template Validation**: Built-in template validation support through configuration manager

### Template Caching Service
Performance optimization through intelligent caching:

```python
@injectable
class TemplateCacheService:
    """Template caching service for performance optimization."""

    def __init__(self, cache_ttl_seconds: int = 300):
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, datetime] = {}
        self._cache_ttl_seconds = cache_ttl_seconds

    async def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""
        if key not in self._cache:
            return None

        timestamp = self._cache_timestamps.get(key)
        if timestamp and (datetime.now() - timestamp).total_seconds() > self._cache_ttl_seconds:
            # Cache expired
            del self._cache[key]
            del self._cache_timestamps[key]
            return None

        return self._cache[key]

    async def set(self, key: str, value: Any) -> None:
        """Set cached value with timestamp."""
        self._cache[key] = value
        self._cache_timestamps[key] = datetime.now()
```

### Key Features
- **Configuration-driven discovery**: Provider-specific file hierarchy with priority ordering
- **Direct file operations**: Simplified approach for better performance
- **Hierarchical priority system**: Template resolution based on file type priority
- **Clean dependency injection**: No circular dependencies
- **Professional codebase**: Optimized for customer delivery

## Utilities and Factories

### Factory Pattern Implementation
```python
class RepositoryFactory:
    def __init__(self, container: DIContainer):
        self.container = container

    def create_machine_repository(self) -> MachineRepositoryPort:
        """Create machine repository based on configuration."""
        storage_type = self.container.get(ConfigurationManager).get('STORAGE_TYPE')

        if storage_type == 'sql':
            return self.container.get(SQLMachineRepository)
        elif storage_type == 'json':
            return self.container.get(JSONMachineRepository)
        else:
            raise ConfigurationError(f"Unsupported storage type: {storage_type}")
```

## Testing Support

### Test Doubles
Infrastructure provides test doubles for external dependencies:

```python
class MockCloudProvider(CloudProviderPort):
    def __init__(self):
        self.provisioned_instances: List[str] = []
        self.terminated_instances: List[str] = []

    async def provision_instance(self, config: InstanceConfiguration) -> ProvisionResult:
        """Mock instance provisioning."""
        instance_id = f"mock-{uuid.uuid4().hex[:8]}"
        self.provisioned_instances.append(instance_id)

        return ProvisionResult(
            instance_id=instance_id,
            status="running",
            provider_data={"mock": True}
        )
```

### Integration Test Support
```python
class TestInfrastructureContainer:
    @staticmethod
    def create() -> DIContainer:
        """Create container configured for testing."""
        container = DIContainer()

        # Use in-memory implementations for testing
        container.register_singleton(MachineRepositoryPort, InMemoryMachineRepository)
        container.register_singleton(CloudProviderPort, MockCloudProvider)
        container.register_singleton(LoggingPort, TestLoggingAdapter)

        return container
```

## Performance Considerations

### Connection Pooling
```python
class DatabaseConnectionPool:
    def __init__(self, connection_string: str, pool_size: int = 10):
        self.connection_string = connection_string
        self.pool_size = pool_size
        self._pool: Queue = Queue(maxsize=pool_size)
        self._initialize_pool()

    async def get_connection(self) -> DatabaseConnection:
        """Get connection from pool."""
        return await self._pool.get()

    async def return_connection(self, connection: DatabaseConnection) -> None:
        """Return connection to pool."""
        await self._pool.put(connection)
```

### Caching Strategies
```python
class CachingRepository(Generic[T]):
    def __init__(self, 
                 base_repository: Repository[T],
                 cache: CacheStrategy,
                 ttl_seconds: int = 300):
        self.base_repository = base_repository
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    async def find_by_id(self, entity_id: str) -> Optional[T]:
        """Find with caching."""
        cache_key = f"{self.__class__.__name__}:{entity_id}"

        # Try cache first
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        # Fallback to repository
        entity = await self.base_repository.find_by_id(entity_id)
        if entity:
            await self.cache.set(cache_key, entity, ttl=self.ttl_seconds)

        return entity
```

This Infrastructure Layer provides robust, scalable implementations of all external integrations while maintaining clean architectural boundaries and supporting comprehensive testing strategies.
