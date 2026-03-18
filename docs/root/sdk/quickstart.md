# Open Resource Broker SDK Quickstart

The Open Resource Broker SDK provides a clean, async-first programmatic interface for cloud resource provisioning operations.

## Key Features

- **Automatic Method Discovery**: All CQRS handlers automatically exposed as SDK methods
- **Zero Code Duplication**: Reuses existing DTOs, domain objects, and CQRS infrastructure
- **Clean Architecture**: Maintains correct layer separation and dependency injection
- **Async/Await Support**: Full async support throughout
- **Type Safety**: Complete type hints and validation

## Installation

```bash
# Install the base package (SDK is included)
pip install orb-py
```

## Basic Usage

### Context Manager (Recommended)

```python
from orb import ORBClient as orb

async with orb(provider="aws") as sdk:
    # List available templates
    result = await sdk.list_templates(active_only=True)
    templates = result.get("templates", result) if isinstance(result, dict) else result
    print(f"Found {len(templates)} templates")

    # Create machines using the orchestrator method
    if templates:
        request = await sdk.request_machines(
            template_id=templates[0]["template_id"],
            count=5
        )
        print(f"Created request: {request['request_id']}")

        # Check status
        status = await sdk.get_request(request_id=request["request_id"])
        print(f"Request status: {status}")
```

### CLI-Style Convenience Methods

The SDK provides CLI-equivalent convenience methods for common operations:

```python
async with orb(provider="aws") as sdk:
    # CLI: orb machines request <template_id> <count>
    request = await sdk.request_machines("template-id", 5)
    
    # CLI: orb templates show <template_id>
    template = await sdk.show_template("template-id")
    
    # CLI: orb providers health
    health = await sdk.health_check()
```

These convenience methods map to the underlying CQRS methods:
- `request_machines(template_id, count)` → `create_request(template_id=template_id, count=count)`
- `show_template(template_id)` → `get_template(template_id=template_id)`
- `health_check()` → `get_provider_health()`

### Manual Initialization

```python
from orb import ORBClient as orb

sdk = orb(provider="aws")
await sdk.initialize()

try:
    # Use SDK methods
    templates = await sdk.list_templates()
    # ... other operations
finally:
    await sdk.cleanup()
```

## Configuration

There are five ways to configure `ORBClient`. They are not mutually exclusive — `config=` and `app_config=` can be combined, and environment variables are always read as the baseline when neither `config=` nor `config_path=` is passed.

| Parameter | Controls | Use when |
|-----------|----------|----------|
| _(none)_ | SDK settings from env vars | Local dev with env vars set |
| `config=` | SDK-level settings (timeout, log_level, region, etc.) | Programmatic SDK tuning |
| `config_path=` | Path to app config JSON on disk | Standard on-disk deployment |
| `app_config=` | Full application config as dict | Lambda, notebooks, CI — no filesystem |
| env vars only | SDK settings via `ORB_*` variables | Container / shell environments |

### Default (no arguments)

When no config arguments are passed, the SDK reads `ORB_*` environment variables and falls back to built-in defaults:

```python
async with orb() as sdk:
    # provider=aws, timeout=300, log_level=INFO (or whatever ORB_* vars are set)
    templates = await sdk.list_templates()
```

### Environment Variables

All SDK-level settings can be set via environment variables:

```bash
export ORB_PROVIDER=aws
export ORB_REGION=us-east-1
export ORB_PROFILE=default
export ORB_TIMEOUT=300
export ORB_RETRY_ATTEMPTS=3
export ORB_LOG_LEVEL=INFO
export ORB_CONFIG_FILE=/path/to/config.json   # app config file path
```

`ORB_CONFIG_FILE` points to the application config JSON (equivalent to `config_path=`). The other variables map directly to `SDKConfig` fields.

### SDK Config Dictionary (`config=`)

Pass SDK-level settings as a dict. These control the SDK's own behaviour — timeout, log level, region override, etc. — not the application config structure.

```python
config = {
    "provider": "aws",
    "region": "us-west-2",
    "timeout": 600,
    "log_level": "DEBUG",
    "retry_attempts": 5,
}

async with orb(config=config) as sdk:
    pass
```

### Config File on Disk (`config_path=`)

Load the full application config from a JSON file. This is the standard mode for deployed services that have a config.json on disk.

```python
async with orb(config_path="/etc/orb/config.json") as sdk:
    pass
```

The file must follow the same structure as the platform's `config.json`. A `ConfigurationError` is raised if the file does not exist.

### In-Memory Application Config (`app_config=`)

For environments without a config file on disk (Lambda functions, Jupyter notebooks, CI pipelines), pass the full application config as a dict. This is equivalent to what would normally be in `config.json`.

```python
app_config = {
    "provider": {
        "type": "aws",
        "providers": [{
            "name": "default",
            "type": "aws",
            "region": "us-east-1"
        }]
    },
    "storage": {"type": "json"}
}

async with orb(app_config=app_config) as sdk:
    templates = await sdk.list_templates()
```

`app_config=` and `config=` can be combined — `app_config` sets the application config structure while `config` tunes SDK behaviour:

```python
async with orb(app_config=app_config, config={"timeout": 600, "log_level": "DEBUG"}) as sdk:
    pass
```

### Per-Client Isolation

Each `ORBClient` instance creates its own isolated DI container. Multiple clients in the same process don't share state:

```python
# Two clients with different regions — fully isolated
async with orb(app_config={"provider": {"type": "aws", "providers": [{"name": "east", "type": "aws", "region": "us-east-1"}]}, "storage": {"type": "json"}}) as east_client:
    async with orb(app_config={"provider": {"type": "aws", "providers": [{"name": "west", "type": "aws", "region": "us-west-2"}]}, "storage": {"type": "json"}}) as west_client:
        # Each client operates independently
        east_templates = await east_client.list_templates()
        west_templates = await west_client.list_templates()
```

For a complete working example, see `docs/root/examples/sdk_usage.py` in the repository.

## CLI vs SDK Equivalents

For users familiar with the CLI, the SDK provides both convenience methods and direct CQRS methods:

| CLI Command | SDK Convenience Method | SDK CQRS Method |
|-------------|----------------------|-----------------|
| `orb machines request <template_id> <count>` | `sdk.request_machines(template_id, count)` | `sdk.create_request(template_id=template_id, count=count)` |
| `orb templates show <template_id>` | `sdk.show_template(template_id)` | `sdk.get_template(template_id=template_id)` |
| `orb providers health` | `sdk.health_check()` | `sdk.get_provider_health()` |
| `orb templates list` | N/A | `sdk.list_templates()` |
| `orb requests status <request_id>` | N/A | `sdk.get_request(request_id=request_id)` |

### Example Usage Comparison

```python
# CLI-style convenience methods (shorter, familiar to CLI users)
async with orb(provider="aws") as sdk:
    template = await sdk.show_template("my-template")
    request = await sdk.request_machines("my-template", 3)
    health = await sdk.health_check()

# CQRS methods (more explicit, full parameter control)
async with orb(provider="aws") as sdk:
    template = await sdk.get_template(template_id="my-template")
    request = await sdk.create_request(
        template_id="my-template",
        count=3,
        timeout=1800
    )
    health = await sdk.get_provider_health()
```

## Method Discovery

The SDK automatically discovers all available methods from the existing CQRS handlers:

```python
async with orb(provider="mock") as sdk:
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

> **Note:** `list_available_methods()` returns only CQRS-discovered methods. The 14 explicit orchestrator-backed methods (`list_templates`, `get_template`, `create_template`, `update_template`, `delete_template`, `validate_template`, `refresh_templates`, `list_machines`, `get_machine`, `request_machines`, `return_machines`, `cancel_request`, `list_requests`, `get_request_status`) are not included in this list.

### Type Safety

The SDK provides `ORBClientProtocol` for IDE autocompletion and type checking:

```python
from orb.sdk import ORBClientProtocol

async def provision_machines(client: ORBClientProtocol, template_id: str, count: int):
    """Type-safe function accepting any ORBClient-compatible object."""
    request = await client.create_request(template_id=template_id, count=count)
    return await client.get_request(request_id=request["created_request_id"])
```

## Common Operations

### Template Management

```python
async with orb(provider="aws") as sdk:
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
        configuration={"machine_types": {"t3.medium": 1}}
    )

    # Update template
    updated_template = await sdk.update_template(
        template_id="my-template",
        name="Updated Template",
        configuration={"machine_types": {"t3.large": 1}}
    )

    # Delete template
    await sdk.delete_template(template_id="old-template")

    # Validate template
    validation_result = await sdk.validate_template(template_id="my-template")
```

### Machine Provisioning

```python
async with orb(provider="aws") as sdk:
    # Create machine request
    request = await sdk.create_request(
        template_id="basic-template",
        count=3,
        timeout=1800
    )

    # Monitor request status
    status = await sdk.get_request(request_id=request["created_request_id"])

    # List active requests
    requests = await sdk.list_active_requests()

    # Return machines when done
    return_request = await sdk.create_return_request(
        machine_ids=["i-1234567890abcdef0"]
    )
```

### Request Management

```python
async with orb(provider="aws") as sdk:
    # List active requests
    requests = await sdk.list_active_requests()

    # Get request status
    status = await sdk.get_request(request_id="req-12345678")
```

### Provider Operations

```python
async with orb(provider="aws") as sdk:
    # Check provider health
    health = await sdk.get_provider_health()

    # List available providers
    providers = await sdk.list_available_providers()

    # Get provider configuration
    config = await sdk.get_provider_config()

    # Get provider metrics
    metrics = await sdk.get_provider_metrics()
```

### System Operations

```python
async with orb(provider="aws") as sdk:
    # Get system status
    status = await sdk.get_system_status()
```

## Error Handling

```python
from orb import ORBClient as orb
from orb.sdk.exceptions import SDKError, ConfigurationError, ProviderError

try:
    async with orb(provider="aws") as sdk:
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
- **ConfigurationError**: Configuration-related errors (invalid config, missing files)
- **ProviderError**: Cloud provider initialization or operation errors
- **HandlerDiscoveryError**: CQRS handler discovery failures
- **MethodExecutionError**: SDK method execution failures

## Advanced Usage

### Custom Middleware

```python
from orb import ORBClient as orb
from orb.sdk import SDKMiddleware

class LoggingMiddleware(SDKMiddleware):
    async def process(self, method_name, args, kwargs, next_handler):
        print(f"Calling {method_name} with kwargs={kwargs}")
        result = await next_handler(args, kwargs)
        print(f"{method_name} returned: {result}")
        return result

async with orb(provider="aws") as sdk:
    sdk.add_middleware(LoggingMiddleware())
    templates = await sdk.list_templates()
```

### Batch Operations

```python
async with orb(provider="aws") as sdk:
    # Create multiple machines in different regions
    results = await sdk.batch([
        sdk.create_request("template-us-east", 2),
        sdk.create_request("template-us-west", 3),
        sdk.create_request("template-eu-west", 1)
    ])

    for result in results:
        print(f"Request ID: {result['created_request_id']}")
```

Failed operations do not raise — the exception instance is returned at that index instead. Always check before accessing result fields:

```python
results = await sdk.batch([
    sdk.create_request("template-us-east", 2),
    sdk.create_request("template-invalid", 1),  # will fail
    sdk.create_request("template-eu-west", 1)
])

for i, result in enumerate(results):
    if isinstance(result, Exception):
        print(f"Operation {i} failed: {result}")
    else:
        print(f"Operation {i} succeeded: {result['created_request_id']}")
```

### Custom Serialization

All SDK methods accept optional serialization parameters:

- `raw_response=True` — returns the raw handler result without dict conversion. Takes precedence over `format`.
- `format="json"` — returns a JSON string instead of a dict.
- `format="yaml"` — returns a YAML string instead of a dict.

> **Note:** `format=` and `raw_response=` only apply to CQRS-discovered methods (e.g. `list_active_requests`, `get_request`). The 14 explicit orchestrator-backed methods (`list_templates`, `request_machines`, `return_machines`, etc.) accept these kwargs but ignore them silently.

```python
async with orb(provider="aws") as sdk:
    # Raw handler result — no dict conversion applied
    raw_data = await sdk.list_active_requests(raw_response=True)

    # JSON string representation
    json_str = await sdk.list_active_requests(format="json")

    # YAML string representation
    yaml_str = await sdk.list_active_requests(format="yaml")

    # raw_response takes precedence — format is ignored here
    raw_data = await sdk.list_active_requests(raw_response=True, format="json")
```

## Performance Considerations

- **Async Operations**: All operations are async for better concurrency
- **Batch Processing**: Use batch operations for multiple requests

## Next Steps

- [Configuration Guide](#configuration) - Detailed configuration options
- [Method Discovery](#method-discovery) - Explore available SDK methods
- [Error Handling](#error-handling) - Handle errors and exceptions
- [Advanced Usage](#advanced-usage) - Middleware, batch operations, and serialization