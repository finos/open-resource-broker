# Provider System

ORB's provider system lets AWS, Azure, Google Cloud, and Kubernetes plug into
the same core (CLI, REST API, scheduler integration, DI container) without
that core importing any provider-specific code. Each provider is a
self-contained package under `src/orb/providers/<name>/` that registers its
behaviour with shared registries at startup.

For how registration, discovery, and provider selection work internally, see
[Multi-Provider Design](../architecture/multi-provider-design.md). This page
is a developer-facing overview of how a provider package is structured and
what each piece is responsible for. For a step-by-step checklist to add a new
provider, see [Adding a Provider](adding_a_provider.md).

## Common package layout

Every provider mirrors the same skeleton:

```
src/orb/providers/<name>/
    provider_plugin.py        # ProviderPlugin subclass — the single registration entry point
    registration.py           # Factory functions used by the plugin (strategy, config, resolver, validator)
    defaults_loader.py        # ProviderDefaultsLoaderPort — bundled config defaults
    exceptions*                # Provider-specific exception types
    strategy/
        <name>_provider_strategy.py   # ProviderStrategy implementation
    configuration/
        config.py             # Provider's BaseSettings config class
    domain/
        template/
            <name>_template_aggregate.py   # Provider-specific Template subclass
    cli/
        <name>_cli_spec.py     # ProviderCLISpecPort implementation for `orb providers add/update`
    scheduler/
        hostfactory_field_mapping.py   # FieldMappingPort — HostFactory field translation
    infrastructure/
        <name>_client.py              # Thin SDK client wrapper
        <name>_handler_factory.py     # Per-API-type handler creation
        handlers/                     # Concrete handlers (one per provider API/operation family)
    services/                 # Provider-scoped domain services (health checks, provisioning, capability checks, ...)
```

AWS and Kubernetes additionally expose a `<name>_template_dto_config.py` under
`domain/template/`; Azure and Google Cloud expose the equivalent extension model as
`TemplateExtensionConfig` in `configuration/template_extension.py`. Either
shape is returned from the plugin's `template_dto_config()` accessor and
registered with `TemplateExtensionRegistry` so `TemplateDTO` can parse
provider-specific template fields.

## The pieces, and what registers them

- **`provider_plugin.py`** — a `ProviderPlugin` subclass (see
  `src/orb/providers/base/provider_plugin.py`). Declares `provider_name` and
  implements the satellite accessors (`strategy_factory`, `config_factory`,
  `template_dto_config`, `cli_spec`, `field_mapping`, `defaults_loader`,
  `template_example_generator`, plus optional hooks). This is the only file
  wired into `pyproject.toml`'s `[project.entry-points."orb.providers"]`
  group — nothing else needs the entry point declared.
- **`registration.py`** — holds the factory functions the plugin exposes
  (`create_<name>_strategy`, `create_<name>_config`, and optionally
  `create_<name>_resolver` / `create_<name>_validator`), plus any
  provider-specific registration helpers (for example AWS registers IAM/Cognito
  auth strategies and DynamoDB/Aurora storage backends from here).
- **`strategy/<name>_provider_strategy.py`** — the `ProviderStrategy`
  implementation (`AWSProviderStrategy`, `AzureProviderStrategy`,
  `GCPProviderStrategy`, `K8sProviderStrategy`). All provisioning,
  termination, status, validation, and health-check operations are dispatched
  through its `execute_operation(ProviderOperation) -> ProviderResult` method.
  It is registered with the global `ProviderRegistry` (type-level) or as a
  named instance, and is what `ProviderSelectionService` resolves a template
  or CLI call to.
- **`configuration/config.py`** — a `pydantic_settings.BaseSettings` subclass
  holding the provider's typed configuration (credentials, region/project/
  cluster, handler toggles, and so on), with automatic environment-variable
  mapping. Registered with `ProviderSettingsRegistry` via the plugin's
  `provider_settings_class()`.
- **`domain/template/<name>_template_aggregate.py`** — a provider-specific
  subclass of the domain `Template` aggregate. Registered with
  `TemplateFactory` via the plugin's `template_class()` when a
  `template_factory` is supplied during initialization.
- **`cli/<name>_cli_spec.py`** — implements `ProviderCLISpecPort`
  (`add_arguments`, `extract_config`, `extract_partial_config`, `validate_add`,
  `generate_name`, `format_display`). Registered with `CLISpecRegistry`, and
  used by the `orb providers add/update` commands to collect and validate
  provider-specific configuration interactively or via flags.
- **`scheduler/hostfactory_field_mapping.py`** — implements `FieldMappingPort`,
  translating HostFactory's camelCase template fields (for example AWS's
  `subnetId`, `fleetRole`) to ORB's internal snake_case fields on top of the
  mappings shared by every provider. Registered with `FieldMappingRegistry`.
- **`defaults_loader.py`** — implements `ProviderDefaultsLoaderPort`. Its
  `load_defaults()` reads a bundled JSON file (for example AWS's
  `config/aws_defaults.json`) and returns a dict in the same shape as
  `default_config.json`, merged in by `ConfigurationLoader` at startup.
  Registered with `DefaultsLoaderRegistry`.
- **`infrastructure/<name>_handler_factory.py` and `infrastructure/handlers/`**
  — the provider's internal dispatch from a template/API type to a concrete
  handler implementation (for example AWS's `EC2Fleet`, `ASG`, `SpotFleet`,
  `RunInstances`, and MicroVM handlers). This layer is internal to the
  provider; the strategy is the only thing the rest of ORB calls.
- **`services/`** — provider-scoped domain services the strategy and handlers
  delegate to, such as health checks, capability/template validation,
  provisioning orchestration, and infrastructure discovery. Contents vary by
  provider since each cloud's operational model differs.

## Provider notes

### AWS (`src/orb/providers/aws/`)

The most complete provider package, with dedicated handlers for EC2 Fleet,
Auto Scaling Groups, Spot Fleet, RunInstances, and Lambda-based MicroVMs under
`infrastructure/handlers/`, plus its own `auth/` (IAM, Cognito), `resilience/`,
`storage/` (DynamoDB and Aurora backends registered in the plugin's
`_do_initialize`), and `persistence/` modules. `boto3`/`botocore` currently
ship in ORB's core dependencies for backward compatibility, with
`pip install orb-py[aws]` as the forward-compatible explicit install path.

### Azure (`src/orb/providers/azure/`)

Wraps Azure Compute, Network, and Resource management SDKs behind
`infrastructure/azure_client.py` and `infrastructure/azure_handler_factory.py`,
with a dedicated `managers/azure_resource_manager.py` for higher-level
resource operations (such as VMSS capacity) and `infrastructure/
cyclecloud_session*.py` for CycleCloud-based session handling. Install with
`pip install orb-py[azure]`.

### Google Cloud (`src/orb/providers/gcp/`)

Uses `infrastructure/compute_client.py` and `infrastructure/
gcp_handler_factory.py` to drive Compute Engine, with `factories.py`
centralizing the shared factory protocols used by both `registration.py` and
`provider_plugin.py`. Install with `pip install orb-py[gcp]`.

### Kubernetes (`src/orb/providers/k8s/`)

Structurally the most distinct provider: in addition to the standard
`strategy/`, `infrastructure/`, and `services/` layout, it has dedicated
`watch/` (node and pod watchers, event watching, multi-namespace support) and
`reconciliation/` (startup reconciliation, orphan and timeout garbage
collection) packages supporting its reconciler-based operating model, plus a
`native_spec_resolver.py` for resolving native Kubernetes manifests from
templates. Install with `pip install orb-py[k8s]`.

## Where to go next

- [Multi-Provider Design](../architecture/multi-provider-design.md) — the
  full plugin architecture: entry points, the `ProviderPlugin` and
  `ProviderStrategy` contracts, provider selection and load balancing, and
  the template-extension/defaults/field-mapping registries.
- [Adding a Provider](adding_a_provider.md) — a concrete checklist for
  implementing a new provider package.
- [Resilience Patterns](resilience.md) — error handling and retry logic
  shared across providers.
- [Data Models](data_models.md) — the domain validation architecture
  providers extend via template DTOs.
