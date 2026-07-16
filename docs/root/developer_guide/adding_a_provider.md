# Adding a Provider

This guide walks through adding a new cloud provider (such as Azure, GCP, or OCI) to the Open Resource Broker. It is aimed at developers who are already familiar with the overall architecture and want a concrete checklist of required steps and extension points.

## Prerequisites

Before reading this guide, review:

- [Clean Architecture](../architecture/clean_architecture.md) — layer boundaries and dependency rules
- [Strategy Pattern](../patterns/strategy_pattern.md) — how provider strategies are structured
- [Ports and Adapters](../patterns/ports_and_adapters.md) — how registries decouple providers from shared infrastructure

## Overview

ORB's provider system is built around an extension-point model: all provider-specific behaviour is registered through a set of dedicated registries at startup. Shared infrastructure (the CLI, the scheduler, the REST API, the DI container) never imports provider packages directly; instead it queries registries that were populated during bootstrap.

Providers are discovered at startup via Python's standard [entry-points](https://packaging.python.org/en/latest/specifications/entry-points/) mechanism. Each provider declares itself under the `orb.providers` entry-point group, pointing to a `ProviderPlugin.register_plugin` classmethod. No shared ORB file needs to be edited to add a new provider.

The goal of this model is that adding a new provider should touch exactly:

- `src/orb/providers/<name>/` — your provider package (all provider logic lives here)
- One line in `[project.entry-points."orb.providers"]` in `pyproject.toml`

The AWS provider (`src/orb/providers/aws/provider_plugin.py`) and the Kubernetes provider (`src/orb/providers/k8s/provider_plugin.py`) are the canonical reference implementations.

### Provider package layout

Mirror the AWS structure:

```
src/orb/providers/<name>/
    __init__.py
    provider_plugin.py            # ProviderPlugin subclass — the single registration entry point
    registration.py               # Factory functions used by the plugin (strategy, config, etc.)
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

Create `src/orb/providers/<name>/` following the layout above. The strategy class must extend `ProviderStrategy` from `orb.providers.base.strategy` (see `src/orb/providers/base/strategy/provider_strategy.py`). Both the AWS and k8s providers use this base — for example, `AWSProviderStrategy(ProviderStrategy)` in `src/orb/providers/aws/strategy/aws_provider_strategy.py`.

### 2. Implement a `ProviderPlugin` subclass

Create `src/orb/providers/<name>/provider_plugin.py` and subclass `ProviderPlugin` from `src/orb/providers/base/provider_plugin.py`. You must set `provider_name` and implement all seven mandatory satellite accessors.

Here is the minimal skeleton for an `azure` provider:

```python
# src/orb/providers/azure/provider_plugin.py
from __future__ import annotations

from typing import Any, Optional

from orb.providers.base.provider_plugin import ProviderPlugin


class AzurePlugin(ProviderPlugin):
    provider_name = "azure"

    # ------------------------------------------------------------------
    # Mandatory satellite accessors
    # ------------------------------------------------------------------

    def strategy_factory(self) -> Any:
        from orb.providers.azure.registration import create_azure_strategy
        return create_azure_strategy

    def config_factory(self) -> Any:
        from orb.providers.azure.registration import create_azure_config
        return create_azure_config

    def template_dto_config(self) -> Any:
        try:
            from orb.providers.azure.domain.template.azure_template_dto_config import (
                AzureTemplateDTOConfig,
            )
            return AzureTemplateDTOConfig
        except ImportError:
            return None

    def cli_spec(self) -> Any:
        try:
            from orb.providers.azure.cli.azure_cli_spec import AzureCLISpec
            return AzureCLISpec()
        except ImportError:
            return None

    def field_mapping(self) -> Any:
        try:
            from orb.providers.azure.scheduler.hostfactory_field_mapping import (
                AzureFieldMapping,
            )
            return AzureFieldMapping()
        except ImportError:
            return None

    def defaults_loader(self) -> Any:
        try:
            from orb.providers.azure.defaults_loader import AzureDefaultsLoader
            return AzureDefaultsLoader()
        except ImportError:
            return None

    def template_example_generator(self, container: Any) -> Any:
        try:
            from orb.providers.azure.adapters.template_example_generator_adapter import (
                AzureTemplateExampleGeneratorAdapter,
            )
            return AzureTemplateExampleGeneratorAdapter()
        except ImportError:
            return None
```

All satellite accessor methods use `try/except ImportError` so the plugin module imports cleanly even when the provider's optional SDK dependencies are not installed.

For optional satellite hooks (`resolver_factory`, `validator_factory`, `strategy_class`, `default_api`, `provider_settings_class`, `template_class`, `register_auth_strategies`, `register_additional_services`, `_do_initialize`) the base class provides no-op defaults; override only the ones your provider needs. See `src/orb/providers/aws/provider_plugin.py` for a fully-overridden example and `src/orb/providers/k8s/provider_plugin.py` for another reference.

### 3. Declare the entry point in `pyproject.toml`

Add a single line under `[project.entry-points."orb.providers"]`:

```toml
[project.entry-points."orb.providers"]
aws = "orb.providers.aws.provider_plugin:AWSPlugin.register_plugin"
k8s = "orb.providers.k8s.provider_plugin:K8sPlugin.register_plugin"
azure = "orb.providers.azure.provider_plugin:AzurePlugin.register_plugin"
```

That is the complete integration point for bootstrap. No shared ORB registration or bootstrap file needs editing.

When ORB starts it calls `discover_provider_plugins()` (in `src/orb/providers/registration.py`), which walks `importlib.metadata.entry_points(group="orb.providers")` and invokes each target callable. `ProviderPlugin.register_plugin` constructs an instance of your subclass, calls `register_provider()` against the live `ProviderRegistry`, and appends `provider_name` to the internal discovery list so the bootstrap loops pick up the provider.

### 4. Add SDK dependencies

Add your cloud provider SDK to `pyproject.toml` and regenerate the lockfile:

```toml
[project.optional-dependencies]
azure = ["azure-mgmt-compute>=30.0", "azure-identity>=1.15"]
```

Then run `uv lock` to update `uv.lock`.

---

## Startup completeness check

After all providers are registered, the bootstrap calls `assert_provider_registrations_complete()` (in `src/orb/bootstrap/provider_completeness.py`). This assertion verifies that every provider type registered with `ProviderRegistry` also has an entry in every required satellite registry:

- `CLISpecRegistry`
- `FieldMappingRegistry`
- `DefaultsLoaderRegistry`
- `TemplateExtensionRegistry`
- `TemplateExampleGeneratorRegistry`

If any satellite is missing, startup **fails immediately** with a `ProviderCompletenessError` that names the provider and every registry it is absent from, for example:

```
ProviderCompletenessError: Provider registration is incomplete — satellite registries not populated:
  provider='azure': missing in [FieldMappingRegistry, DefaultsLoaderRegistry]
Fix: ensure initialize_<provider>_provider() is called during bootstrap
     for each registered provider type.
```

This fast-fail catches incomplete plugin implementations before any request is served. If you see this error, check that all seven mandatory satellite accessors in your `ProviderPlugin` subclass return non-`None` values (or that `ImportError` is not silently suppressing a real missing-module error).

---

## Extension points reference

Each registry is a class-level singleton. Register during startup; never register lazily inside a request handler.

### ProviderRegistry

**Location:** `src/orb/providers/registry/provider_registry.py`

**What it does:** The central strategy factory. When a request arrives for a named provider, `ProviderRegistry` calls your `strategy_factory` to create the strategy instance and `config_factory` to parse configuration data.

**When to register:** `ProviderPlugin.register_provider()` handles this automatically when `register_plugin()` is invoked at startup. Your `strategy_factory()` and `config_factory()` accessors supply the factory callables.

**How to implement the factories:**

```python
# src/orb/providers/azure/registration.py

def create_azure_strategy(provider_config):
    from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy
    return AzureProviderStrategy(config=provider_config)


def create_azure_config(raw: dict):
    from orb.providers.azure.configuration.config import AzureProviderConfig
    return AzureProviderConfig(**raw)
```

**AWS reference:** `create_aws_strategy` and `create_aws_config` in `src/orb/providers/aws/registration.py`.

---

### CLISpecRegistry + ProviderCLISpecPort

**Location:** `src/orb/infrastructure/registry/cli_spec_registry.py`

**What it does:** Supplies provider-specific CLI argument definitions, input validation logic, field extraction from parsed arguments, and display formatting. The shared `cli/args.py` and `interface/init_command_handler.py` iterate over `CLISpecRegistry.all()` rather than hard-coding provider flags.

**When to register:** `ProviderPlugin.initialize_provider()` registers the instance returned by your `cli_spec()` accessor. This happens during the DI bootstrap phase, before any request is handled.

**How to implement:**

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

**Location:** `src/orb/infrastructure/registry/template_extension_registry.py`

**What it does:** Holds a typed Pydantic model class for each provider's template configuration. When `TemplateDTO.from_domain` serialises a template, it calls `TemplateExtensionRegistry.get_extension_class(provider_type)` to obtain and populate the `provider_config` field. This replaces ad-hoc `metadata` dict keys and dynamic dispatch.

**When to register:** `ProviderPlugin.initialize_provider()` registers the class returned by your `template_dto_config()` accessor.

**How to implement:**

```python
from pydantic import BaseModel, Field

class AzureTemplateDTOConfig(BaseModel):
    vm_size: str = Field("Standard_D2s_v3", description="Azure VM size")
    location: str = Field("eastus", description="Azure region")
    resource_group: str = Field("", description="Azure resource group")

    def get_provider_type(self) -> str:
        return "azure"

    def to_template_defaults(self) -> dict:
        return self.model_dump()
```

**AWS reference:** `src/orb/providers/aws/domain/template/aws_template_dto_config.py` and `src/orb/providers/aws/provider_plugin.py` (the `template_dto_config` accessor).

---

### AuthRegistry

**Location:** `src/orb/infrastructure/auth/registry.py`

**What it does:** Maps auth strategy names (strings like `"iam"`, `"cognito"`) to auth strategy classes. The REST server calls `AuthRegistry.get_strategy(name, **config)` rather than dispatching through `if/elif` chains. Register all auth strategies your provider supports.

**When to register:** Override `register_auth_strategies(self, logger)` in your `ProviderPlugin` subclass. `initialize_provider()` calls this hook after standard satellite registrations.

**How to implement:**

```python
def register_auth_strategies(self, logger=None) -> None:
    from orb.infrastructure.auth.registry import get_auth_registry
    registry = get_auth_registry()

    if not registry.is_registered("azure_ad"):
        from orb.providers.azure.auth.azure_ad_strategy import AzureADAuthStrategy
        registry.register_strategy("azure_ad", AzureADAuthStrategy)

    if not registry.is_registered("managed_identity"):
        from orb.providers.azure.auth.managed_identity_strategy import ManagedIdentityAuthStrategy
        registry.register_strategy("managed_identity", ManagedIdentityAuthStrategy)
```

**AWS reference:** `register_auth_strategies` in `src/orb/providers/aws/provider_plugin.py`.

---

### FieldMappingRegistry

**Location:** `src/orb/infrastructure/scheduler/hostfactory/field_mapping_registry.py`

**What it does:** Holds a per-provider `FieldMappingPort` adapter. The HostFactory scheduler calls `FieldMappingRegistry.get(provider_type)` to translate IBM Spectrum Symphony camelCase field names to the provider's snake_case equivalents, apply provider-specific defaults, and resolve CPU/RAM values from a provider-specific instance type catalogue.

**When to register:** `ProviderPlugin.initialize_provider()` registers the instance returned by your `field_mapping()` accessor.

**How to implement:**

Implement `FieldMappingPort` on your mapping class. The two critical methods are `map_fields(raw: dict) -> dict` (camelCase-to-snake_case translation + provider defaults) and `resolve_cpu_ram(vm_size: str) -> tuple[int, int]` (returns `(cpu_count, ram_mb)` from your provider's instance catalogue).

```python
# src/orb/providers/azure/scheduler/hostfactory_field_mapping.py
class AzureFieldMapping:
    def map_fields(self, raw: dict) -> dict: ...
    def resolve_cpu_ram(self, vm_size: str) -> tuple[int, int]: ...
```

**AWS reference:** `src/orb/providers/aws/scheduler/hostfactory_field_mapping.py`.

---

### DefaultsLoaderRegistry

**Location:** `src/orb/providers/registry/defaults_loader_registry.py`

**What it does:** Holds a per-provider `ProviderDefaultsLoaderPort` that loads a provider's defaults JSON file. The template defaults service calls this registry to populate provider-specific default values rather than hard-coding file paths for each provider.

**When to register:** `ProviderPlugin.initialize_provider()` registers the instance returned by your `defaults_loader()` accessor.

**How to implement:**

```python
# src/orb/providers/azure/defaults_loader.py
from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort

class AzureDefaultsLoader(ProviderDefaultsLoaderPort):
    def load_defaults(self) -> dict:
        import json
        import importlib.resources as pkg
        with pkg.open_text("orb.providers.azure.config", "azure_defaults.json") as f:
            return json.load(f)
```

**AWS reference:** `src/orb/providers/aws/defaults_loader.py`.

---

### TemplateAdapterPort

**Location:** `src/orb/domain/base/ports/template_adapter_port.py`

**What it does:** Resolves provider-specific template fields that require a live API call (for example, looking up an AMI by name on AWS, or resolving an image reference on Azure). Registered in the DI container as a singleton, not in a class-level registry.

**When to register:** Override `register_additional_services(self, container, logger)` in your `ProviderPlugin` subclass. `register_services_with_di()` calls this hook when the DI container is available.

**How to implement:**

```python
def register_additional_services(self, container, logger=None) -> None:
    from orb.domain.base.ports.template_adapter_port import TemplateAdapterPort
    from orb.providers.azure.infrastructure.adapters.template_adapter import (
        AzureTemplateAdapter,
    )

    def create_azure_template_adapter(c):
        from orb.domain.base.ports import LoggingPort, ConfigurationPort
        return AzureTemplateAdapter(
            logger=c.get(LoggingPort),
            config=c.get(ConfigurationPort),
        )

    container.register_singleton(TemplateAdapterPort, create_azure_template_adapter)
```

**AWS reference:** `register_additional_services` in `src/orb/providers/aws/provider_plugin.py`.

---

### TemplateExampleGeneratorRegistry

**Location:** `src/orb/infrastructure/registry/template_example_generator_registry.py`

**What it does:** Generates example template JSON for the `orb template generate` command. ORB resolves this from the registry and calls it to produce provider-appropriate example output. No live API connection is required; the generator uses handler class metadata only.

**When to register:** `ProviderPlugin.register_services_with_di()` registers the instance returned by your `template_example_generator(container)` accessor.

**How to implement:**

```python
def template_example_generator(self, container: Any) -> Any:
    try:
        from orb.providers.azure.adapters.template_example_generator_adapter import (
            AzureTemplateExampleGeneratorAdapter,
        )
        return AzureTemplateExampleGeneratorAdapter()
    except ImportError:
        return None
```

**AWS reference:** `template_example_generator` in `src/orb/providers/aws/provider_plugin.py`.

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

# Right — in AzureTemplateDTOConfig (a Pydantic model inside the Azure package)
class AzureTemplateDTOConfig(BaseModel):
    resource_group: str = ""
```

### Do not add provider-specific fields to the shared `TemplateDTO`

`TemplateDTO` is the serialisation boundary between the application and API layers. Provider-specific fields belong in the `provider_config: BaseModel | None` extension field populated by `TemplateExtensionRegistry`, not as top-level DTO attributes.

```python
# Wrong — in src/orb/application/dto/template_dto.py
azure_vm_size: str | None = None

# Right — TemplateDTO.provider_config carries AzureTemplateDTOConfig
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

Add a smoke test to verify entry-point wiring:

```python
import importlib.metadata as md

def test_entry_point_is_discoverable() -> None:
    eps = md.entry_points(group="orb.providers")
    assert any(ep.name == "azure" for ep in eps)
```

The AWS provider tests are the reference layout:

- Unit tests: `tests/providers/aws/unit/`
- Mocked integration tests: `tests/providers/aws/mocked/`
- Real-AWS tests: `tests/providers/aws/live/`
- Contract tests: `tests/providers/aws/contract/`

---

## Cross-references

- [Clean Architecture](../architecture/clean_architecture.md) — layer boundaries enforced by architecture tests
- [Strategy Pattern](../patterns/strategy_pattern.md) — how `ProviderStrategy` and `ProviderRegistry` work together
- [Ports and Adapters](../patterns/ports_and_adapters.md) — the port/registry decoupling pattern used throughout
- AWS reference: `src/orb/providers/aws/provider_plugin.py` and `src/orb/providers/aws/registration.py`
- K8s reference: `src/orb/providers/k8s/provider_plugin.py` and `src/orb/providers/k8s/registration.py`
- Base class: `src/orb/providers/base/provider_plugin.py`
- Startup completeness check: `src/orb/bootstrap/provider_completeness.py`
