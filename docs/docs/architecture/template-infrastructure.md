# Template Infrastructure Architecture

The template infrastructure has been streamlined to provide a clean, efficient system for managing compute templates. This document describes the simplified architecture and its key components.

## Overview

The template system treats templates as configuration data rather than transactional business entities, providing:

- **Single Source of Truth**: `TemplateConfigurationManager` consolidates all template operations
- **Configuration-Driven Discovery**: Provider-specific file hierarchy with priority ordering
- **Direct File Operations**: Simplified approach for better performance
- **Clean Architecture**: No circular dependencies, professional codebase

## Core Components

### TemplateConfigurationManager

The central component that replaces multiple competing template systems:

```python
class TemplateConfigurationManager:
    """
    Template Configuration Manager - Single Source of Truth.

    Consolidates template operations into a coherent architecture that
    treats templates as configuration data rather than transactional entities.
    """

    def __init__(self, 
                 config_manager: ConfigurationManager,
                 scheduler_strategy: SchedulerPort,
                 logger: LoggingPort,
                 event_publisher: Optional[EventPublisherPort] = None):
        self.config_manager = config_manager
        self.scheduler_strategy = scheduler_strategy
        self.logger = logger
        self.event_publisher = event_publisher
```

**Key Responsibilities:**
- Discover template files using provider-specific hierarchy
- Load and parse templates with scheduler strategy integration
- Provide template access with caching
- Handle CRUD operations for template management
- Maintain template metadata and validation
- Publish domain events for template lifecycle operations (optional)

### TemplateRepositoryImpl

Repository pattern implementation for template persistence:

```python
@injectable
class TemplateRepositoryImpl(TemplateRepositoryPort):
    """Template repository implementation using configuration manager."""

    def __init__(self, 
                 config_manager: TemplateConfigurationManager,
                 logger: LoggingPort):
        self.config_manager = config_manager
        self.logger = logger

    async def find_all(self) -> List[Template]:
        """Find all templates."""
        template_dtos = await self.config_manager.load_templates()
        return [self._dto_to_domain(dto) for dto in template_dtos]
```

### TemplateCacheService

Performance optimization through intelligent caching:

```python
@injectable
class TemplateCacheService:
    """Template caching service for performance optimization."""

    def __init__(self, cache_ttl_seconds: int = 300):
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, datetime] = {}
        self._cache_ttl_seconds = cache_ttl_seconds
```

## Template Discovery System

### Provider-Specific File Hierarchy

Templates are discovered using a hierarchical priority system:

1. **Instance files**: `{provider}inst_templates.{ext}` (Priority 1 - Highest)
2. **Type files**: `{provider}type_templates.{ext}` (Priority 2)
3. **Main templates**: `{provider}prov_templates.{ext}` (Priority 3)
4. **Legacy files**: `templates.{ext}` (Priority 4 - Lowest)

### Discovery Process

```python
async def discover_template_files(self, force_refresh: bool = False) -> TemplateDiscoveryResult:
    """
    Discover template files using provider-specific hierarchy.

    Returns:
        TemplateDiscoveryResult with discovered files and metadata
    """
    discovery_result = TemplateDiscoveryResult()

    # Get active providers from configuration
    active_providers = self._get_active_providers()

    for provider in active_providers:
        await self._discover_provider_templates(provider, discovery_result)

    # Discover legacy templates (no provider prefix)
    await self._discover_legacy_templates(discovery_result)

    return discovery_result
```

### Template File Metadata

Each discovered template file includes comprehensive metadata:

```python
@dataclass
class TemplateFileMetadata:
    """Metadata for template files in the hierarchy."""
    path: Path
    provider: str
    file_type: str  # 'instance', 'type', 'main', 'legacy'
    priority: int
    last_modified: datetime
```

## Template Loading and Caching

### Loading Process

Templates are loaded with intelligent caching:

```python
async def load_templates(self, force_refresh: bool = False) -> List[TemplateDTO]:
    """
    Load all templates from discovered files.

    Args:
        force_refresh: Force reload even if cached

    Returns:
        List of TemplateDTO objects
    """
    # Check cache first
    if not force_refresh and self._template_cache and self._cache_timestamp:
        cache_age = (datetime.now() - self._cache_timestamp).total_seconds()
        if cache_age < self._cache_ttl_seconds:
            return list(self._template_cache.values())

    # Discover and load templates
    discovery_result = await self.discover_template_files(force_refresh)
    all_templates = {}

    for file_metadata in discovery_result.get_sorted_files():
        file_templates = await self._load_templates_from_file(file_metadata)

        # Merge templates, respecting priority
        for template in file_templates:
            template_id = template.template_id

            if template_id not in all_templates:
                all_templates[template_id] = template
            else:
                # Check priority for override
                existing_priority = getattr(all_templates[template_id], '_source_priority', 999)
                if file_metadata.priority < existing_priority:
                    all_templates[template_id] = template

    return list(all_templates.values())
```

### Cache Management

The system provides comprehensive cache management:

- **TTL-based expiration**: Configurable cache time-to-live
- **Force refresh**: Ability to bypass cache when needed
- **Cache statistics**: Monitoring and debugging support
- **Automatic invalidation**: Cache cleared on template modifications

## CRUD Operations

### Create/Update Templates

```python
async def save_template(self, template: TemplateDTO) -> None:
    """
    Save template to configuration files.

    This method implements CRUD create/update operations by writing
    templates to the appropriate provider-specific files.
    """
    # Determine target file based on template provider
    provider = getattr(template, '_source_provider', 'aws')
    target_file = await self._determine_target_file(template, provider)

    # Load existing templates from target file
    existing_templates = await self._load_templates_from_file_path(target_file)

    # Update or add the template
    template_found = False
    for i, existing_template in enumerate(existing_templates):
        if existing_template.get('template_id') == template.template_id:
            existing_templates[i] = template.configuration
            template_found = True
            break

    if not template_found:
        existing_templates.append(template.configuration)

    # Write back to file
    await self._write_templates_to_file(target_file, existing_templates)

    # Update cache
    self._template_cache[template.template_id] = template
```

### Delete Templates

```python
async def delete_template(self, template_id: str) -> None:
    """
    Delete template from configuration files.

    This method implements CRUD delete operations by removing
    templates from their source files.
    """
    # Find the template to determine its source file
    template = await self.get_template_by_id(template_id)
    if not template:
        raise ValueError(f"Template {template_id} not found")

    source_file = getattr(template, '_source_file', None)
    if not source_file:
        raise ValueError(f"Cannot determine source file for template {template_id}")

    # Load, modify, and save
    existing_templates = await self._load_templates_from_file_path(Path(source_file))
    existing_templates = [
        t for t in existing_templates 
        if t.get('template_id') != template_id and t.get('templateId') != template_id
    ]

    await self._write_templates_to_file(Path(source_file), existing_templates)

    # Remove from cache
    if template_id in self._template_cache:
        del self._template_cache[template_id]
```

## Event-Driven Architecture Integration

### Domain Event Publishing

The `TemplateConfigurationManager` supports optional domain event publishing for template lifecycle operations. When an `EventPublisherPort` is provided during initialization, the system publishes relevant domain events for:

- **Template Creation**: `TemplateCreatedEvent` when new templates are saved
- **Template Updates**: `TemplateUpdatedEvent` when existing templates are modified
- **Template Deletion**: `TemplateDeletedEvent` when templates are removed
- **Template Validation**: `TemplateValidatedEvent` when templates are validated

#### Event Publishing Implementation

```python
async def save_template(self, template: TemplateDTO) -> None:
    """Save template with optional event publishing."""
    try:
        # Determine if this is create or update
        existing_template = await self.get_template_by_id(template.template_id)
        is_update = existing_template is not None

        # Perform the save operation
        await self._perform_save_operation(template)

        # Publish domain event if event publisher is available
        if self.event_publisher and self.event_publisher.is_enabled():
            if is_update:
                event = TemplateUpdatedEvent(
                    template_id=template.template_id,
                    template_name=template.name or template.template_id,
                    changes=self._calculate_changes(existing_template, template),
                    timestamp=datetime.utcnow()
                )
            else:
                event = TemplateCreatedEvent(
                    template_id=template.template_id,
                    template_name=template.name or template.template_id,
                    template_type=template.provider_api or 'aws',
                    timestamp=datetime.utcnow()
                )

            self.event_publisher.publish(event)
            self.logger.debug(f"Published {event.__class__.__name__} for template {template.template_id}")

    except Exception as e:
        self.logger.error(f"Failed to save template {template.template_id}: {e}")
        raise
```

#### Event Handler Integration

Template events can be handled by dedicated event handlers:

```python
@event_handler("TemplateCreatedEvent")
class TemplateCreatedHandler(BaseEventHandler):
    """Handle template creation events."""

    async def handle(self, event: TemplateCreatedEvent) -> None:
        # Send notifications
        await self.notification_service.notify_template_created(
            template_id=event.template_id,
            template_name=event.template_name
        )

        # Update metrics
        self.metrics_collector.increment_counter("templates_created")

        # Audit logging
        self.audit_logger.log_template_operation(
            operation="CREATE",
            template_id=event.template_id,
            timestamp=event.timestamp
        )

@event_handler("TemplateUpdatedEvent")
class TemplateUpdatedHandler(BaseEventHandler):
    """Handle template update events."""

    async def handle(self, event: TemplateUpdatedEvent) -> None:
        # Invalidate related caches
        await self.cache_service.invalidate_template_cache(event.template_id)

        # Notify dependent systems
        await self.dependency_service.notify_template_changed(
            template_id=event.template_id,
            changes=event.changes
        )
```

#### Configuration for Event Publishing

Event publishing can be configured through the dependency injection system:

```python
def register_template_services(container: DIContainer) -> None:
    """Register template services with optional event publishing."""

    # Register event publisher (optional)
    if container.has_service(EventPublisherPort):
        event_publisher = container.get(EventPublisherPort)
    else:
        event_publisher = None

    # Register template configuration manager with event publishing
    # Note: TemplateConfigurationManager is manually registered as singleton
    # in src/infrastructure/di/port_registrations.py instead of using @injectable
    container.register_factory(
        TemplateConfigurationManager,
        lambda c: TemplateConfigurationManager(
            config_manager=c.get(ConfigurationManager),
            scheduler_strategy=c.get(SchedulerPort),
            logger=c.get(LoggingPort),
            event_publisher=event_publisher  # Optional event publishing
        )
    )
```

#### Benefits of Event-Driven Template Management

- **Decoupled Architecture**: Template operations don't directly depend on side effects
- **Extensible Notifications**: Easy to add new event handlers without modifying core logic
- **Audit Trail**: Comprehensive tracking of template lifecycle events
- **Cache Invalidation**: Automatic cache management through event handlers
- **Metrics Collection**: Automated metrics gathering for template operations
- **Integration Points**: Clean integration with external systems through events

## Integration with CQRS

The template infrastructure integrates seamlessly with the CQRS pattern:

### Query Handlers

```python
@query_handler(ListTemplatesQuery)
class ListTemplatesHandler(BaseQueryHandler[ListTemplatesQuery, List[TemplateDTO]]):
    def __init__(self, config_manager: TemplateConfigurationManager):
        self.config_manager = config_manager

    async def execute_query(self, query: ListTemplatesQuery) -> List[TemplateDTO]:
        templates = await self.config_manager.load_templates(query.force_refresh)

        # Apply filters
        if query.provider_api:
            templates = [t for t in templates if t.provider_api == query.provider_api]

        return templates
```

### Command Handlers

```python
@command_handler(CreateTemplateCommand)
class CreateTemplateHandler(BaseCommandHandler[CreateTemplateCommand, TemplateDTO]):
    def __init__(self, config_manager: TemplateConfigurationManager):
        self.config_manager = config_manager

    async def execute_command(self, command: CreateTemplateCommand) -> TemplateDTO:
        # Create TemplateDTO from command
        template_dto = TemplateDTO(
            template_id=command.template_id,
            name=command.name,
            provider_api=command.provider_api,
            configuration=command.configuration
        )

        # Save using configuration manager
        await self.config_manager.save_template(template_dto)

        return template_dto
```

## Performance Characteristics

### Caching Strategy

- **Memory-based caching**: Fast access to frequently used templates
- **TTL expiration**: Automatic cache invalidation after configured time
- **Selective refresh**: Ability to refresh specific templates or all templates
- **Cache statistics**: Monitoring for performance optimization

### File I/O Optimization

- **Lazy loading**: Templates loaded only when needed
- **Batch operations**: Multiple templates loaded in single file read
- **Efficient parsing**: Direct JSON/YAML parsing without intermediate layers
- **Minimal memory footprint**: Templates stored as lightweight DTOs

## Error Handling

### Comprehensive Error Management

```python
class TemplateConfigurationError(InfrastructureError):
    """Template configuration specific errors."""
    pass

class TemplateNotFoundError(TemplateConfigurationError):
    """Template not found in configuration."""
    pass

class TemplateValidationError(TemplateConfigurationError):
    """Template validation failed."""
    pass
```

### Graceful Degradation

- **File not found**: Continue with available templates
- **Parse errors**: Log warning and skip invalid templates
- **Permission errors**: Fallback to read-only mode
- **Network issues**: Use cached templates when possible

## Monitoring and Observability

### Cache Statistics

```python
def get_cache_stats(self) -> Dict[str, Any]:
    """Get cache statistics for monitoring."""
    return {
        'template_cache_size': len(self._template_cache),
        'file_cache_size': len(self._file_cache),
        'last_discovery': self._last_discovery.discovery_time if self._last_discovery else None,
        'cache_timestamp': self._cache_timestamp,
        'cache_ttl_seconds': self._cache_ttl_seconds
    }
```

### Intelligent Attribute Generation

The template system includes intelligent HostFactory attribute generation through the scheduler strategy:

```python
def _create_hf_attributes(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create HF-compatible attributes object with CPU/RAM specs.

    This method handles the creation of HostFactory attributes with proper
    CPU and RAM specifications based on instance type.
    """
    # Handle both snake_case and camelCase field names
    instance_type = template_data.get('instance_type') or template_data.get('instanceType', 't2.micro')

    # CPU/RAM mapping for common instance types
    cpu_ram_mapping = {
        "t2.micro": {"ncpus": "1", "nram": "1024"},
        "t2.small": {"ncpus": "1", "nram": "2048"},
        "t2.medium": {"ncpus": "2", "nram": "4096"},
        "t3.micro": {"ncpus": "2", "nram": "1024"},
        "t3.small": {"ncpus": "2", "nram": "2048"},
        "t3.medium": {"ncpus": "2", "nram": "4096"},
        "m5.large": {"ncpus": "2", "nram": "8192"},
        "m5.xlarge": {"ncpus": "4", "nram": "16384"},
        "c5.large": {"ncpus": "2", "nram": "4096"},
        "c5.xlarge": {"ncpus": "4", "nram": "8192"},
        "r5.large": {"ncpus": "2", "nram": "16384"},
        "r5.xlarge": {"ncpus": "4", "nram": "32768"},
    }

    # Get specs for instance type, default to t2.micro specs
    specs = cpu_ram_mapping.get(instance_type, {"ncpus": "1", "nram": "1024"})

    # Return HF-compatible attributes format
    return {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", specs["ncpus"]],
        "nram": ["Numeric", specs["nram"]]
    }
```

**Key Features:**
- **Automatic Detection**: Extracts instance type from template configuration
- **Dual Format Support**: Handles both `instance_type` and `instanceType` field names
- **Comprehensive Mapping**: Built-in specifications for common AWS instance types
- **Fallback Behavior**: Defaults to t2.micro specifications for unknown types
- **HostFactory Compliance**: Generates attributes in the exact format expected by IBM Symphony

**Integration Points:**
- **Template Loading**: Attributes generated during template format conversion
- **API Responses**: Ensures all template responses include accurate resource specifications
- **Validation**: Provides consistent attribute format for template validation
- **Caching**: Generated attributes are cached with template data for performance

### Logging Integration

- **Structured logging**: JSON-formatted logs with context
- **Performance metrics**: Template load times and cache hit rates
- **Error tracking**: Detailed error information with stack traces
- **Audit trail**: Template creation, modification, and deletion events

## Migration from Legacy Systems

The new template infrastructure replaces four competing systems:

1. **Domain Repository Pattern** → Configuration-based template access
2. **Configuration Store Pattern** → Direct file operations with caching
3. **Scheduler Strategy Integration** → Provider-specific file naming and parsing
4. **Multi-Provider Strategy** → Hierarchical template file discovery

### Benefits of Consolidation

- **Reduced complexity**: Single source of truth eliminates confusion
- **Better performance**: Direct file operations without abstraction overhead
- **Cleaner architecture**: No circular dependencies or competing patterns
- **Professional codebase**: Optimized for customer delivery
- **Easier maintenance**: Single system to understand and modify

This streamlined template infrastructure provides a robust, efficient foundation for template management while maintaining clean architectural boundaries and supporting comprehensive testing strategies.