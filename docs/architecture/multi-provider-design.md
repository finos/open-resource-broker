# Multi-Provider Architecture Design

## Overview

The Open Host Factory Plugin implements a sophisticated multi-provider architecture that enables dynamic provisioning of compute resources across multiple cloud providers and provider instances. This document describes the design, implementation, and usage patterns of the multi-provider system.

## Architecture Components

### CQRS Implementation Status

The system implements CQRS (Command Query Responsibility Segregation) architecture:

**Completed CQRS Components:**
- `CommandBus` and `QueryBus` infrastructure in `src/infrastructure/di/buses.py`
- Query DTOs: `ListTemplatesQuery`, `GetTemplateQuery`, `ValidateTemplateQuery`
- Command DTOs: `CreateTemplateCommand`, `UpdateTemplateCommand`, `DeleteTemplateCommand`, `ValidateTemplateCommand`
- Template list endpoint using `QueryBus`

**Implementation Status:**
- Template API endpoints (GET, POST, PUT, DELETE) - using CQRS handlers
- Machine management endpoints - using CQRS pattern
- Request processing endpoints - using CQRS pattern
- Provider management endpoints - using CQRS pattern

**Architecture Features:**
- All API endpoints using CQRS buses for command/query separation
- Consistent async/await patterns across all handlers
- Proper separation of read and write operations
- Optimized query handling with caching support

### 1. Domain Model Extensions

#### Template Aggregate
The `Template` aggregate has been extended with multi-provider fields:

```python
class Template:
    template_id: str
    provider_type: Optional[str]      # NEW: Provider type (aws, azure, gcp)
    provider_name: Optional[str]      # NEW: Provider instance name (aws-us-east-1)
    provider_api: Optional[str]       # NEW: Specific API to use (EC2Fleet, SpotFleet)
    # ... existing fields
```

#### Request Aggregate
The `Request` aggregate now tracks provider selection:

```python
class Request:
    provider_type: str
    provider_instance: Optional[str]  # NEW: Selected provider instance
    # ... existing fields
```

### 2. Provider Selection Service

The `ProviderSelectionService` implements intelligent provider selection using multiple strategies:

#### Selection Strategies
1. **Explicit Selection**: Template specifies exact provider instance
2. **Load Balanced Selection**: Distribute across provider instances by type
3. **Capability-Based Selection**: Select based on API requirements
4. **Default Selection**: Use configuration defaults

#### Selection Algorithm
```python
def select_provider_for_template(template: Template) -> ProviderSelectionResult:
    if template.provider_name:
        return explicit_selection(template.provider_name)
    elif template.provider_type:
        return load_balanced_selection(template.provider_type)
    elif template.provider_api:
        return capability_based_selection(template.provider_api)
    else:
        return default_selection()
```

### 3. Provider Capability Service

The `ProviderCapabilityService` validates template requirements against provider capabilities:

#### Validation Levels
- **STRICT**: All warnings become errors
- **LENIENT**: Warnings allowed, only critical errors fail
- **BASIC**: Only critical validation, minimal checks

#### Capability Validation
```python
def validate_template_requirements(
    template: Template, 
    provider_instance: str, 
    level: ValidationLevel
) -> ValidationResult:
    # Validate API support
    # Check instance limits
    # Verify pricing model support
    # Validate fleet type compatibility
```

### 4. Template Repository Architecture

The template system implements a repository pattern that provides compliance with Clean Architecture principles:

#### Template Repository Implementation
The `TemplateRepositoryImpl` class provides a complete implementation of both `AggregateRepository` and `TemplateRepository` interfaces:

```python
class TemplateRepositoryImpl(TemplateRepository):
    """Template repository implementation for configuration-based template management."""

    # Abstract methods from AggregateRepository
    def save(self, aggregate: Template) -> None:
        """Save a template aggregate."""

    def find_by_id(self, aggregate_id: str) -> Optional[Template]:
        """Find template by aggregate ID."""

    def delete(self, aggregate_id: str) -> None:
        """Delete template by aggregate ID."""

    # Abstract methods from TemplateRepository
    def find_by_template_id(self, template_id: str) -> Optional[Template]:
        """Find template by template ID (delegates to find_by_id)."""

    def find_by_provider_api(self, provider_api: str) -> List[Template]:
        """Find templates by provider API type."""

    def find_active_templates(self) -> List[Template]:
        """Find all active templates."""

    def search_templates(self, criteria: Dict[str, Any]) -> List[Template]:
        """Search templates by criteria."""
```

#### Key Architecture Improvements
1. **Full Interface Compliance**: Implements all required abstract methods from both base interfaces
2. **Method Delegation**: Avoids code duplication by delegating `find_by_template_id` to `find_by_id`
3. **Clean Dependency Injection**: Uses factory pattern registration instead of decorator-based DI
4. **Comprehensive Functionality**: Provides both required methods and convenience methods

#### Provider-Specific Template Loading
The `ProviderTemplateStrategy` implements hierarchical template loading:

##### File Priority Order (Highest to Lowest)
1. Provider instance files: `{provider-instance}_templates.json`
2. Provider type files: `{provider-type}prov_templates.json`
3. Main templates file: `templates.json`
4. Legacy templates file: `awsprov_templates.json`

##### Template Override Behavior
Templates with the same `template_id` in higher priority files override those in lower priority files.

## Configuration Schema

### Provider Configuration
```yaml
providers:
  selection_policy: "WEIGHTED_ROUND_ROBIN"
  default_provider_type: "aws"
  default_provider_instance: "aws-us-east-1"
  providers:
    - name: "aws-us-east-1"
      type: "aws"
      enabled: true
      priority: 1
      weight: 10
      capabilities: ["EC2Fleet", "SpotFleet", "RunInstances", "ASG"]
    - name: "aws-us-west-2"
      type: "aws"
      enabled: true
      priority: 2
      weight: 5
      capabilities: ["EC2Fleet", "RunInstances"]
```

### Template Examples

#### Explicit Provider Selection
```json
{
  "template_id": "explicit-aws-east",
  "provider_name": "aws-us-east-1",
  "provider_api": "EC2Fleet",
  "image_id": "ami-12345",
  "subnet_ids": ["subnet-123"],
  "max_instances": 5
}
```

#### Provider Type Selection (Load Balanced)
```json
{
  "template_id": "load-balanced-aws",
  "provider_type": "aws",
  "provider_api": "SpotFleet",
  "image_id": "ami-67890",
  "subnet_ids": ["subnet-456"],
  "max_instances": 10
}
```

#### API-Based Selection
```json
{
  "template_id": "api-based-selection",
  "provider_api": "RunInstances",
  "image_id": "ami-abcdef",
  "subnet_ids": ["subnet-789"],
  "max_instances": 3
}
```

## Provider Selection Algorithms

### Weighted Round Robin
Distributes requests across provider instances based on configured weights:

```python
def weighted_round_robin_selection(providers: List[ProviderInstance]) -> str:
    total_weight = sum(p.weight for p in providers)
    random_value = random.randint(1, total_weight)

    current_weight = 0
    for provider in providers:
        current_weight += provider.weight
        if random_value <= current_weight:
            return provider.name
```

### Priority-Based Selection
Selects highest priority available provider:

```python
def priority_based_selection(providers: List[ProviderInstance]) -> str:
    enabled_providers = [p for p in providers if p.enabled]
    return min(enabled_providers, key=lambda p: p.priority).name
```

## Template File Organization

### Directory Structure
```
config/
- templates.json                    # Main templates
- awsprov_templates.json           # AWS provider type templates
- azureprov_templates.json         # Azure provider type templates
- aws-us-east-1_templates.json    # AWS US East instance templates
- aws-us-west-2_templates.json    # AWS US West instance templates
- azure-east-us_templates.json    # Azure East US instance templates
```

### Template Inheritance
Templates inherit and override properties based on file priority:

```json
// templates.json (base)
{
  "template_id": "web-server",
  "image_id": "ami-base",
  "instance_type": "t2.micro",
  "max_instances": 2
}

// awsprov_templates.json (provider override)
{
  "template_id": "web-server",
  "provider_type": "aws",
  "provider_api": "EC2Fleet",
  "instance_type": "t3.small",
  "max_instances": 5
}

// aws-us-east-1_templates.json (instance override)
{
  "template_id": "web-server",
  "provider_name": "aws-us-east-1",
  "image_id": "ami-east-optimized",
  "max_instances": 10
}
```

Final resolved template:
```json
{
  "template_id": "web-server",
  "provider_name": "aws-us-east-1",
  "provider_type": "aws",
  "provider_api": "EC2Fleet",
  "image_id": "ami-east-optimized",
  "instance_type": "t3.small",
  "max_instances": 10
}
```

## API Integration

### REST API Endpoints

#### Provider Information
```http
GET /api/v1/providers
GET /api/v1/providers/{provider-instance}/capabilities
GET /api/v1/providers/{provider-instance}/templates
```

#### Template Management
```http
GET /api/v1/templates?provider_type=aws
GET /api/v1/templates?provider_name=aws-us-east-1
POST /api/v1/templates/validate
```

#### Request Processing
```http
POST /api/v1/requests
{
  "templateId": "web-server",
  "maxNumber": 5,
  "providerPreference": {
    "type": "aws",
    "instance": "aws-us-east-1"
  }
}
```

### CLI Commands

#### Provider Management
```bash
# List available providers
ohfp providers list

# Show provider capabilities
ohfp providers show aws-us-east-1

# Validate provider configuration
ohfp providers validate
```

#### Template Operations
```bash
# List templates by provider
ohfp templates list --provider-type aws
ohfp templates list --provider-name aws-us-east-1

# Show template source information
ohfp templates show web-server --source-info

# Validate template against provider
ohfp templates validate web-server --provider aws-us-east-1
```

## Error Handling and Validation

### Provider Selection Errors
- **No enabled providers**: When no providers are available
- **Provider not found**: When explicit provider doesn't exist
- **Provider disabled**: When selected provider is disabled
- **No compatible providers**: When no providers support required API

### Template Validation Errors
- **API not supported**: Provider doesn't support required API
- **Instance limit exceeded**: Request exceeds provider limits
- **Pricing model mismatch**: Provider doesn't support pricing model
- **Fleet type incompatible**: Provider doesn't support fleet type

### Error Response Format
```json
{
  "error": {
    "code": "PROVIDER_NOT_FOUND",
    "message": "Provider instance 'aws-invalid' not found in configuration",
    "details": {
      "requested_provider": "aws-invalid",
      "available_providers": ["aws-us-east-1", "aws-us-west-2"]
    }
  }
}
```

## Performance Considerations

### Template Caching
- Templates are cached in memory with file modification time tracking
- Cache is automatically refreshed when template files change
- Manual cache refresh available via API and CLI

### Provider Selection Optimization
- Provider configurations are cached at startup
- Selection algorithms use pre-computed weights and priorities
- Capability validation results are cached per provider-API combination

### File I/O Optimization
- Template files are loaded once and cached
- Only modified files are reloaded
- Batch operations minimize file system calls

## Monitoring and Observability

### Metrics
- Provider selection distribution
- Template validation success/failure rates
- File loading performance
- Cache hit/miss ratios

### Logging
- Provider selection decisions with reasoning
- Template override chains
- Validation failures with details
- Performance timing information

### Health Checks
- Provider availability status
- Template file accessibility
- Configuration validation status
- Cache consistency checks

## Migration Guide

### From Single Provider
1. Update configuration to include provider instances
2. Migrate templates to provider-specific files (optional)
3. Update API calls to include provider preferences (optional)
4. Test provider selection behavior

### Template Migration
```bash
# Migrate existing templates to provider-specific files
ohfp templates migrate --from templates.json --to-provider aws-us-east-1

# Validate migrated templates
ohfp templates validate --all --provider aws-us-east-1
```

## Best Practices

### Configuration
- Use meaningful provider instance names
- Set appropriate weights for load balancing
- Enable only necessary providers
- Regular validation of provider configurations

### Template Organization
- Use provider-specific files for customizations
- Keep common templates in main file
- Document template inheritance chains
- Regular cleanup of unused templates

### Monitoring
- Monitor provider selection distribution
- Track validation failure patterns
- Alert on provider availability issues
- Regular performance reviews

## Future Enhancements

### Planned Features
- Dynamic provider discovery
- Cross-provider failover
- Improved scheduling algorithms
- Provider cost optimization
- Multi-region template synchronization

### Extension Points
- Custom selection strategies
- Provider-specific validation rules
- Template transformation pipelines
- External provider registries
