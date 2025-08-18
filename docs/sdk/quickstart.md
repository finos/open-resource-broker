# OpenHFPlugin SDK Quickstart

The OpenHFPlugin SDK (ohfpsdk) provides a clean, async-first programmatic interface for cloud resource provisioning operations.

## Key Features

- **Automatic Method Discovery**: All CQRS handlers automatically exposed as SDK methods
- **Zero Code Duplication**: Reuses existing DTOs, domain objects, and CQRS infrastructure
- **Clean Architecture**: Maintains correct layer separation and dependency injection
- **Async/Await Support**: Full async support throughout
- **Type Safety**: Complete type hints and validation

## Installation

```bash
# Install the base package
pip install open-hostfactory-plugin

# Or install with SDK support
pip install open-hostfactory-plugin[sdk]
```

## Basic Usage

### Context Manager (Recommended)

```python
from ohfpsdk import OHFPSDK

async with OHFPSDK(provider="aws") as sdk:
    # List available templates
    templates = await sdk.list_templates(active_only=True)
    print(f"Found {len(templates)} templates")

    # Create machines
    if templates:
        request = await sdk.create_request(
            template_id=templates[0].template_id,
            machine_count=5
        )
        print(f"Created request: {request.id}")

        # Check status
        status = await sdk.get_request_status(request_id=request.id)
        print(f"Request status: {status}")
```

### Manual Initialization

```python
from ohfpsdk import OHFPSDK

sdk = OHFPSDK(provider="aws")
await sdk.initialize()

try:
    # Use SDK methods
    templates = await sdk.list_templates()
    # ... other operations
finally:
    await sdk.cleanup()
```

## Configuration

### Environment Variables

```bash
export OHFP_PROVIDER=aws
export OHFP_REGION=us-east-1
export OHFP_PROFILE=default
export OHFP_TIMEOUT=300
export OHFP_LOG_LEVEL=INFO
```

### Configuration Dictionary

```python
config = {
    "provider": "aws",
    "region": "us-west-2", 
    "timeout": 600,
    "log_level": "DEBUG"
}

async with OHFPSDK(config=config) as sdk:
    # Use SDK with custom configuration
    pass
```

### Configuration File

```python
# Load from JSON file
async with OHFPSDK(config_path="config.json") as sdk:
    pass
```

## Method Discovery

The SDK automatically discovers all available methods from the existing CQRS handlers:

```python
async with OHFPSDK(provider="mock") as sdk:
    # List all available methods
    methods = sdk.list_available_methods()
    print(f"Available methods: {methods}")

    # Get information about a specific method
    method_info = sdk.get_method_info("list_templates")
    print(f"Method info: {method_info}")

    # Get methods by type
    query_methods = sdk.get_methods_by_type("query")
    command_methods = sdk.get_methods_by_type("command")

    # Get SDK statistics
    stats = sdk.get_stats()
    print(f"SDK stats: {stats}")
```

## Common Operations

### Template Management

```python
async with OHFPSDK(provider="aws") as sdk:
    # List all templates
    templates = await sdk.list_templates()

    # List only active templates
    active_templates = await sdk.list_templates(active_only=True)

    # Get specific template
    template = await sdk.get_template(template_id="my-template")

    # Create template
    new_template = await sdk.create_template(
        template_id="new-template",
        name="New Template",
        provider_api="aws",
        image_id="ami-12345678",
        instance_type="t3.medium"
    )

    # Update template
    updated_template = await sdk.update_template(
        template_id="my-template",
        name="Updated Template",
        instance_type="t3.large"
    )

    # Delete template
    await sdk.delete_template(template_id="old-template")

    # Validate template
    validation_result = await sdk.validate_template(template_id="my-template")
```

### Machine Provisioning

```python
async with OHFPSDK(provider="aws") as sdk:
    # Create machine request
    request = await sdk.create_request(
        template_id="basic-template",
        machine_count=3,
        timeout=1800
    )

    # Monitor request status
    status = await sdk.get_request_status(request_id=request.id)

    # List machines
    machines = await sdk.list_machines(status="running")

    # Get machine details
    machine = await sdk.get_machine(machine_id="i-1234567890abcdef0")

    # Return machines when done
    return_request = await sdk.create_return_request(
        machine_ids=["i-1234567890abcdef0"]
    )
```

### Request Management

```python
async with OHFPSDK(provider="aws") as sdk:
    # List requests
    requests = await sdk.list_requests(status="pending")

    # Get request details
    request = await sdk.get_request(request_id="req-12345678")

    # Cancel request
    await sdk.cancel_request(request_id="req-12345678")
```

### Provider Operations

```python
async with OHFPSDK(provider="aws") as sdk:
    # Check provider health
    health = await sdk.get_provider_health()

    # List available providers
    providers = await sdk.list_providers()

    # Get provider configuration
    config = await sdk.get_provider_config()

    # Get provider metrics
    metrics = await sdk.get_provider_metrics()
```

### System Operations

```python
async with OHFPSDK(provider="aws") as sdk:
    # Get system status
    status = await sdk.get_system_status()

    # Run health check
    health = await sdk.check_system_health(detailed=True)

    # Get system metrics
    metrics = await sdk.get_system_metrics()
```

## Error Handling

```python
from ohfpsdk import OHFPSDK, SDKError, ConfigurationError, ProviderError

try:
    async with OHFPSDK(provider="aws") as sdk:
        templates = await sdk.list_templates()
except ConfigurationError as e:
    print(f"Configuration error: {e}")
except ProviderError as e:
    print(f"Provider error: {e}")
except SDKError as e:
    print(f"SDK error: {e}")
```

### Error Types

- **SDKError**: Base class for all SDK errors
- **ConfigurationError**: Configuration-related errors
- **ProviderError**: Cloud provider-related errors
- **ValidationError**: Input validation errors
- **ResourceNotFoundError**: Resource not found errors
- **AuthenticationError**: Authentication-related errors
- **NetworkError**: Network-related errors

## Advanced Usage

### Custom Middleware

```python
from ohfpsdk import OHFPSDK, SDKMiddleware

class LoggingMiddleware(SDKMiddleware):
    async def process(self, method_name, args, kwargs, next_handler):
        print(f"Calling {method_name} with args={args}, kwargs={kwargs}")
        result = await next_handler(args, kwargs)
        print(f"{method_name} returned: {result}")
        return result

async with OHFPSDK(provider="aws") as sdk:
    sdk.add_middleware(LoggingMiddleware())
    templates = await sdk.list_templates()
```

### Batch Operations

```python
async with OHFPSDK(provider="aws") as sdk:
    # Create multiple machines in different regions
    results = await sdk.batch([
        sdk.create_request("template-us-east", 2),
        sdk.create_request("template-us-west", 3),
        sdk.create_request("template-eu-west", 1)
    ])

    for result in results:
        print(f"Request ID: {result.id}")
```

### Custom Serialization

```python
async with OHFPSDK(provider="aws") as sdk:
    # Get raw response data
    raw_data = await sdk.list_templates(raw_response=True)

    # Custom serialization format
    yaml_data = await sdk.list_templates(format="yaml")
```

## Performance Considerations

- **Connection Pooling**: The SDK uses connection pooling for better performance
- **Caching**: Query results are cached when appropriate
- **Async Operations**: All operations are async for better concurrency
- **Batch Processing**: Use batch operations for multiple requests

## Next Steps

- [API Reference](api-reference.md) - Complete method documentation
- [Configuration Guide](configuration.md) - Detailed configuration options
- [Examples](examples/) - More usage examples
- [Integration Guide](integration.md) - Integrating with existing applications