# Adding a Provider

This guide walks through adding a new cloud provider (such as Azure, GCP, or OCI) to the Open Resource Broker. It is aimed at developers who are already familiar with the overall architecture and want a concrete checklist of required steps and extension points.

## Prerequisites

Before reading this guide, review:

- [Clean Architecture](../architecture/clean_architecture.md) — layer boundaries and dependency rules
- [Strategy Pattern](../patterns/strategy_pattern.md) — how provider strategies are structured
- [Ports and Adapters](../patterns/ports_and_adapters.md) — how registries decouple providers from shared infrastructure

## Overview

ORB's provider system is built around an extension-point model: all provider-specific behaviour is registered through a set of dedicated registries at startup. Shared infrastructure (the CLI, the scheduler, the REST API, the DI container) never imports provider packages directly; instead it queries registries that were populated during bootstrap.

The goal of this model is that adding a new provider should touch exactly:

- `src/orb/providers/<name>/` — your provider package (all provider logic lives here)
- Three glue points in shared code: an enum entry, a registration call, and a bootstrap block

The AWS provider is the canonical reference implementation. Refer to `src/orb/providers/aws/registration.py` when you need to see a working example of every registration call described below.

### Provider package layout

Mirror the AWS structure:

```
src/orb/providers/<name>/
    __init__.py
    registration.py          # All registration functions for this provider
    strategy/
        <name>_provider_strategy.py
    cli/
        <name>_cli_spec.py
    configuration/
        config.py
        template_extension.py
    domain/
        template/
            <name>_template_aggregate.py
            <name>_template_dto_config.py
    scheduler/
        hostfactory_field_mapping.py
    auth/
        <strategy>_auth_strategy.py
    defaults_loader.py
```

## Mandatory steps

Complete these steps in order. Each one has a corresponding section in the extension points reference below.

### 1. Create the provider package

Create `src/orb/providers/<name>/` following the layout above. The strategy class must extend `BaseProviderStrategy` from `orb.providers.base.strategy.base_provider_strategy`.

### 2. Add an enum entry

Add your provider to `ProviderType` in `src/orb/domain/base/provider_interfaces.py`:

```python
class ProviderType(str, Enum):
    AWS = "aws"
    AZURE = "azure"   # new entry
```

Use a short, lowercase string that matches the string keys used in all registry calls.

### 3. Add a registration call in `providers/registration.py`

Add your provider to the two functions in `src/orb/providers/registration.py`:

```python
def register_all_provider_cli_specs() -> None:
    from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
    from orb.providers.azure.cli.azure_cli_spec import AzureCLISpec

    if CLISpecRegistry.get("azure") is None:
        CLISpecRegistry.register("azure", AzureCLISpec())


def register_all_provider_types() -> None:
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()

    from orb.providers.aws.registration import register_aws_provider
    register_aws_provider(registry)

    from orb.providers.azure.registration import register_azure_provider
    register_azure_provider(registry)
```

### 4. Add a bootstrap block in `bootstrap/provider_services.py`

Add a `find_spec`-guarded block to `_register_provider_utility_services` in `src/orb/bootstrap/provider_services.py`. Use the same pattern as the AWS block immediately above it:

```python
if importlib.util.find_spec("orb.providers.azure"):
    try:
        from orb.providers.azure.registration import register_azure_services_with_di
        register_azure_services_with_di(container)
    except Exception as e:
        logger.warning("Failed to register Azure utility services: %s", str(e))
```

The `find_spec` guard means the rest of ORB still runs when the provider package is not installed (useful for minimal deployments and test environments that exclude a specific provider's SDK).

### 5. Add SDK dependencies

Add your cloud provider SDK to `pyproject.toml` and regenerate the lockfile:

```toml
[project.optional-dependencies]
azure = ["azure-mgmt-compute>=30.0", "azure-identity>=1.15"]
```

Then run `uv lock` to update `uv.lock`.

---

## Extension points reference

Each registry is a class-level singleton. Register during startup; never register lazily inside a request handler.

### ProviderRegistry

**Location:** `src/orb/providers/registry/provider_registry.py`

**What it does:** The central strategy factory. When a request arrives for a named provider, `ProviderRegistry` calls your `strategy_factory` to create the strategy instance and `config_factory` to parse configuration data.

**When to register:** In your `register_<name>_provider(registry)` function called from `providers/registration.py`.

**How to register:**

```python
# src/orb/providers/azure/registration.py

def register_azure_provider(registry=None) -> None:
    from orb.providers.registry import get_provider_registry
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy

    if registry is None:
        registry = get_provider_registry()

    registry.register_provider(
        provider_type="azure",
        strategy_factory=create_azure_strategy,
        config_factory=create_azure_config,
        resolver_factory=create_azure_resolver,   # return None if not needed
        validator_factory=create_azure_validator, # return None if not needed
        strategy_class=AzureProviderStrategy,
        default_api=_load_azure_default_api(),    # reads from azure_defaults.json
    )
```

**AWS reference:** `register_aws_provider` in `src/orb/providers/aws/registration.py`.

---

### CLISpecRegistry + ProviderCLISpecPort

**Location:** `src/orb/domain/base/ports/provider_cli_spec_port.py`

**What it does:** Supplies provider-specific CLI argument definitions, input validation logic, field extraction from parsed arguments, and display formatting. The shared `cli/args.py` and `interface/init_command_handler.py` iterate over `CLISpecRegistry.all()` rather than hard-coding provider flags.

**When to register:** In `register_all_provider_cli_specs()` in `providers/registration.py`. This function is called before application context exists, so keep it lightweight — no network calls, no DI container access.

**How to register:**

```python
from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
from orb.providers.azure.cli.azure_cli_spec import AzureCLISpec

CLISpecRegistry.register("azure", AzureCLISpec())
```

Implement `ProviderCLISpecPort` on your spec class:

```python
class AzureCLISpec:
    def add_arguments(self, parser) -> None:
        parser.add_argument("--azure-subscription-id", help="Azure subscription ID")
        parser.add_argument("--azure-resource-group", help="Azure resource group")

    def validate(self, args) -> list[str]:
        errors = []
        if not getattr(args, "azure_subscription_id", None):
            errors.append("--azure-subscription-id is required for Azure providers")
        return errors

    def extract_fields(self, args) -> dict:
        return {
            "subscription_id": args.azure_subscription_id,
            "resource_group": getattr(args, "azure_resource_group", None),
        }
```

**AWS reference:** `src/orb/providers/aws/cli/aws_cli_spec.py`.

---

### TemplateExtensionRegistry

**Location:** `src/orb/domain/template/extensions.py`

**What it does:** Holds a typed Pydantic model class for each provider's template configuration. When `TemplateDTO.from_domain` serialises a template, it calls `TemplateExtensionRegistry.get_extension_class(provider_type)` to obtain and populate the `provider_config` field. This replaces ad-hoc `metadata` dict keys and `getattr(template, "validate_<provider>")` dynamic dispatch.

**When to register:** In `register_<name>_extensions()`, called from `initialize_<name>_provider()` during startup.

**How to register:**

```python
from orb.domain.template.extensions import TemplateExtensionRegistry
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig

TemplateExtensionRegistry.register_extension("azure", AzureTemplateExtensionConfig)
```

`AzureTemplateExtensionConfig` should be a Pydantic `BaseModel` with `get_provider_type()` returning `"azure"` and a `to_template_defaults()` method returning a `dict` of default values:

```python
from pydantic import BaseModel, Field

class AzureTemplateExtensionConfig(BaseModel):
    vm_size: str = Field("Standard_D2s_v3", description="Azure VM size")
    location: str = Field("eastus", description="Azure region")
    resource_group: str = Field("", description="Azure resource group")

    def get_provider_type(self) -> str:
        return "azure"

    def to_template_defaults(self) -> dict:
        return self.model_dump()
```

**AWS reference:** `src/orb/providers/aws/configuration/template_extension.py` and the `register_aws_extensions` function in `src/orb/providers/aws/registration.py`.

---

### AuthRegistry

**Location:** `src/orb/infrastructure/auth/registry.py`

**What it does:** Maps auth strategy names (strings like `"iam"`, `"cognito"`) to auth strategy classes. The REST server calls `AuthRegistry.get_strategy(name, **config)` rather than dispatching through `if/elif` chains. Register all auth strategies your provider supports.

**When to register:** In `register_<name>_auth_strategies()`, called from `initialize_<name>_provider()` during startup.

**How to register:**

```python
from orb.infrastructure.auth.registry import get_auth_registry

registry = get_auth_registry()

if not registry.is_registered("azure_ad"):
    from orb.providers.azure.auth.azure_ad_strategy import AzureADAuthStrategy
    registry.register_strategy("azure_ad", AzureADAuthStrategy)

if not registry.is_registered("managed_identity"):
    from orb.providers.azure.auth.managed_identity_strategy import ManagedIdentityAuthStrategy
    registry.register_strategy("managed_identity", ManagedIdentityAuthStrategy)
```

**AWS reference:** `register_aws_auth_strategies` in `src/orb/providers/aws/registration.py`.

---

### FieldMappingRegistry

**Location:** `src/orb/infrastructure/scheduler/hostfactory/field_mapping_registry.py`

**What it does:** Holds a per-provider `FieldMappingPort` adapter. The HostFactory scheduler calls `FieldMappingRegistry.get(provider_type)` to translate IBM Spectrum Symphony camelCase field names to the provider's snake_case equivalents, apply provider-specific defaults, and resolve CPU/RAM values from a provider-specific instance type catalogue.

**When to register:** In `initialize_<name>_provider()`, after other registrations.

**How to register:**

```python
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
from orb.providers.azure.scheduler.hostfactory_field_mapping import AzureFieldMapping

FieldMappingRegistry.register("azure", AzureFieldMapping())
```

Implement `FieldMappingPort` in your mapping class. The two critical methods are `map_fields(raw: dict) -> dict` (camelCase-to-snake_case translation + provider defaults) and `resolve_cpu_ram(vm_size: str) -> tuple[int, int]` (returns `(cpu_count, ram_mb)` from your provider's instance catalogue).

**AWS reference:** `src/orb/providers/aws/scheduler/hostfactory_field_mapping.py` and `register_aws_provider` in `src/orb/providers/aws/registration.py` (the `FieldMappingRegistry.register` call near the end of `initialize_aws_provider`).

---

### DefaultsLoaderRegistry

**Location:** `src/orb/providers/registry/defaults_loader_registry.py`

**What it does:** Holds a per-provider `ProviderDefaultsLoaderPort` that loads a provider's defaults JSON file. The template defaults service calls this registry to populate provider-specific default values rather than hard-coding file paths for each provider.

**When to register:** In `initialize_<name>_provider()`.

**How to register:**

```python
from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry
from orb.providers.azure.defaults_loader import AzureDefaultsLoader

DefaultsLoaderRegistry.register("azure", AzureDefaultsLoader())
```

`AzureDefaultsLoader` implements `ProviderDefaultsLoaderPort` and typically reads from a JSON file bundled alongside your provider package (e.g. `src/orb/providers/azure/config/azure_defaults.json`).

**AWS reference:** `src/orb/providers/aws/defaults_loader.py` and the `DefaultsLoaderRegistry.register` call in `initialize_aws_provider`.

---

### TemplateAdapterPort

**Location:** `src/orb/domain/base/ports/template_adapter_port.py`

**What it does:** Resolves provider-specific template fields that require a live API call (for example, looking up an AMI by name on AWS, or resolving an image reference on Azure). Registered in the DI container as a singleton, not in a class-level registry.

**When to register:** In `register_<name>_services_with_di(container)`, called from the `find_spec`-guarded block in `bootstrap/provider_services.py`.

**How to register:**

```python
from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort

def create_azure_template_adapter(c):
    from orb.providers.azure.infrastructure.adapters.template_adapter import AzureTemplateAdapter
    from orb.domain.base.ports import LoggingPort, ConfigurationPort
    return AzureTemplateAdapter(
        logger=c.get(LoggingPort),
        config=c.get(ConfigurationPort),
    )

container.register_singleton(TemplateAdapterPort, create_azure_template_adapter)
```

**AWS reference:** `register_aws_services_with_di` in `src/orb/providers/aws/registration.py`.

---

### TemplateExampleGeneratorPort

**Location:** `src/orb/domain/base/ports/template_example_generator_port.py`

**What it does:** Generates example template JSON for the `orb template generate` command. ORB resolves this port from the DI container and calls it to produce provider-appropriate example output. No live API connection is required; the generator uses handler class metadata only.

**When to register:** In `register_<name>_services_with_di(container)`, alongside the template adapter.

**How to register:**

```python
from orb.domain.base.ports.template_example_generator_port import TemplateExampleGeneratorPort

def create_azure_example_generator(c):
    from orb.providers.azure.adapters.template_example_generator_adapter import (
        AzureTemplateExampleGeneratorAdapter,
    )
    return AzureTemplateExampleGeneratorAdapter()

container.register_singleton(TemplateExampleGeneratorPort, create_azure_example_generator)
```

**AWS reference:** The `TemplateExampleGeneratorPort` block inside `register_aws_services_with_di`.

---

## OperationOutcome contract

Every strategy method that performs a cloud operation returns `OperationOutcome`, a discriminated union defined in `src/orb/domain/base/operation_outcome.py`:

```python
OperationOutcome = Accepted | Completed | RequiresFollowUp | Failed
```

Choose the correct variant based on what the cloud API actually tells you:

| Variant | Use when |
|---|---|
| `Accepted` | The provider acknowledged the request but resources are not yet in their final state. Include a provider-side tracking ID in `request_id` and the in-flight resource IDs in `pending_resource_ids`. The orchestration layer will poll `get_status` until a terminal outcome is returned. |
| `Completed` | All resources have reached their terminal state in this call. Include the final resource IDs in `resource_ids`. |
| `RequiresFollowUp` | The provider acknowledged the request but a domain-level follow-up action is needed beyond simple polling (for example, a webhook registration or a secondary API call). Populate a `FollowUpContext` describing what to do next. |
| `Failed` | The operation failed. Set `recoverable=True` for transient failures (throttles, temporary capacity shortages) and `False` for hard failures (invalid configuration, permission denied). |

**AWS example — `acquire` always returns `Accepted`:**

```python
async def acquire(self, request: Request) -> OperationOutcome:
    result = await self.execute_operation(operation)
    if not result.success:
        return Failed(error=result.error_message or "acquire failed", recoverable=False)
    return Accepted(
        request_id=str(request.request_id),
        pending_resource_ids=result.data.get("resource_ids", []),
    )
```

EC2 Fleet, SpotFleet, and RunInstances all accept the request immediately and let instances transition through `pending → running` asynchronously. The correct outcome is always `Accepted`.

**Azure ARM example — `return_machines` with multi-step async teardown:**

Azure resource deletion may trigger a long-running ARM operation that requires a separate status poll URL:

```python
async def return_machines(
    self, machine_ids: list[str], request: Request
) -> OperationOutcome:
    response = await self._arm_client.begin_delete(resource_ids=machine_ids)
    if response.needs_follow_up:
        return RequiresFollowUp(
            context=AzureArmFollowUpContext(
                operation_url=response.poll_url,
                resource_ids=machine_ids,
                follow_up_kind="arm_async_delete",
            )
        )
    if response.done:
        return Completed(resource_ids=machine_ids)
    return Accepted(
        request_id=response.operation_id,
        pending_resource_ids=machine_ids,
    )
```

Always dispatch on `OperationOutcome` exhaustively using `match` + `assert_never` in calling code so pyright catches any future variant additions at compile time.

---

## Anti-patterns

The following patterns must not appear in new provider code. Each one creates a coupling that prevents new providers from being added without editing shared infrastructure.

### Do not edit `cli/args.py` for provider-specific flags

`cli/args.py` iterates `CLISpecRegistry.all()`. Adding flags for a specific provider here leaks provider knowledge into shared code and means all users see flags that may not apply to their provider.

```python
# Wrong — in src/orb/cli/args.py
parser.add_argument("--azure-subscription-id", ...)

# Right — in src/orb/providers/azure/cli/azure_cli_spec.py
class AzureCLISpec:
    def add_arguments(self, parser) -> None:
        parser.add_argument("--azure-subscription-id", ...)
```

### Do not add `if provider_type == "x"` branches in shared services

Branching on provider type in shared services (template defaults service, provisioning orchestration service, scheduler) means every new provider requires editing code it should not know about.

```python
# Wrong — in any shared service
if provider_type == "azure":
    apply_azure_defaults(template)
elif provider_type == "aws":
    apply_aws_defaults(template)

# Right — register a DefaultsLoader and let the registry dispatch
loader = DefaultsLoaderRegistry.get(provider_type)
if loader:
    defaults = loader.load_defaults()
```

### Do not use `getattr(template, f"validate_{provider_type}")` dynamic dispatch

String-keyed `getattr` dispatch is invisible to the type checker. Renames silently break at runtime and there is no way to enumerate valid provider types statically.

```python
# Wrong
if hasattr(template, f"validate_{provider_type}"):
    getattr(template, f"validate_{provider_type}")()

# Right — use TemplateExtensionRegistry for unconditional dispatch
extension_class = TemplateExtensionRegistry.get_extension_class(provider_type)
if extension_class:
    extension_class.model_validate(template.provider_config or {})
```

### Do not add provider-specific fields to the shared `TemplateAggregate`

The domain template aggregate is provider-agnostic. Adding an `azure_resource_group` field to `domain/template/template_aggregate.py` forces all providers to handle a field they do not own and breaks the provider isolation guarantee.

```python
# Wrong — in src/orb/domain/template/template_aggregate.py
azure_resource_group: str | None = None

# Right — in AzureTemplateExtensionConfig (a Pydantic model inside the Azure package)
class AzureTemplateExtensionConfig(BaseModel):
    resource_group: str = ""
```

### Do not add provider-specific fields to the shared `TemplateDTO`

`TemplateDTO` is the serialisation boundary between the application and API layers. Provider-specific fields belong in the `provider_config: BaseModel | None` extension field populated by `TemplateExtensionRegistry`, not as top-level DTO attributes.

```python
# Wrong — in src/orb/application/dto/template_dto.py
azure_vm_size: str | None = None

# Right — TemplateDTO.provider_config carries AzureTemplateExtensionConfig
# automatically when the extension is registered
```

### Do not add provider strings to domain value objects

`domain/base/value_objects.py` and similar domain files must not contain string literals for specific providers. Use `ProviderType` where a typed enum is appropriate, and registries for everything else.

```python
# Wrong — in src/orb/domain/base/value_objects.py
KNOWN_PROVIDERS = ["aws", "azure", "gcp"]

# Right — ProviderRegistry.registered_providers() returns this list dynamically
```

### Do not add provider-specific imports to shared infrastructure

Shared infrastructure files (`infrastructure/scheduler/`, `api/server.py`, etc.) must not import from `providers/<name>/`. Doing so creates a hard dependency that prevents the package from being imported in environments where that provider's SDK is not installed.

```python
# Wrong — in src/orb/infrastructure/scheduler/hostfactory/hostfactory_strategy.py
from orb.providers.aws.utilities.ec2.instances import derive_cpu_ram_from_instance_type

# Right — FieldMappingRegistry.get(provider_type).resolve_cpu_ram(vm_size)
```

---

## Test layout

Mirror the source layout under `tests/providers/<name>/`:

```
tests/providers/<name>/
    conftest.py                  # shared fixtures for this provider
    unit/                        # pure unit tests, no cloud calls, no mocks of cloud SDK
        test_<name>_strategy.py
        test_<name>_cli_spec.py
        test_<name>_template_extension.py
        test_<name>_field_mapping.py
    moto/                        # mocked integration tests (use a mock SDK equivalent)
        conftest.py
        test_<name>_acquire.py
        test_<name>_return.py
    live/                        # real-cloud tests, gated by --live flag
        conftest.py              # skips all tests unless --live is passed
        test_<name>_connectivity.py
        test_<name>_roundtrip.py
    contract/                    # contract tests verifying OperationOutcome variants
        test_outcome_variants.py
```

Use the per-provider `conftest.py` to define fixtures that supply mock clients, provider configs, and sample request/template domain objects. Keep `moto/` and `live/` sub-packages separate so CI can include mocked tests and exclude live tests without test selection gymnastics.

Gate live tests with a custom pytest marker:

```python
# tests/providers/<name>/live/conftest.py
import pytest

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live", default=False):
        skip = pytest.mark.skip(reason="pass --live to run real-cloud tests")
        for item in items:
            if "live" in str(item.fspath):
                item.add_marker(skip)
```

The AWS provider tests are the reference layout:

- Unit tests: `tests/providers/aws/unit/`
- Mocked integration tests: `tests/providers/aws/mocked/`
- Real-AWS tests: `tests/providers/aws/live/`
- Contract tests: `tests/providers/aws/contract/`

---

## Cross-references

- [Clean Architecture](../architecture/clean_architecture.md) — layer boundaries enforced by architecture tests
- [Strategy Pattern](../patterns/strategy_pattern.md) — how `BaseProviderStrategy` and `ProviderRegistry` work together
- [Ports and Adapters](../patterns/ports_and_adapters.md) — the port/registry decoupling pattern used throughout
- AWS reference implementation: `src/orb/providers/aws/registration.py`
