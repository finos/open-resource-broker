# Multi-Provider Design

## Overview

Open Resource Broker (ORB) provisions compute capacity across multiple cloud and
scheduler backends — AWS, Azure, Google Cloud, and Kubernetes — through a single
plugin architecture. Each provider is a self-contained Python package under
`src/orb/providers/<name>/` that registers itself with shared infrastructure at
startup. The core (CLI, REST API, scheduler integration, DI container) never
imports a provider package directly; it only talks to registries that providers
populate during bootstrap.

This document describes that plugin architecture: how providers are discovered
and registered, the strategy contract every provider implements, how a request
is routed to a specific provider instance (including load balancing across
multiple instances of the same provider type), and the supporting registries
for template extensions, configuration defaults, HostFactory field mapping, and
CLI argument specs.

For a step-by-step guide to adding a new provider, see
[Adding a Provider](../developer_guide/adding_a_provider.md). For an overview of
provider package layout from a developer's perspective, see
[Provider System](../developer_guide/providers.md).

## Entry points and discovery

Providers declare themselves under the `orb.providers` entry-point group in
`pyproject.toml`:

```toml
[project.entry-points."orb.providers"]
aws = "orb.providers.aws.provider_plugin:AWSPlugin.register_plugin"
azure = "orb.providers.azure.provider_plugin:AzurePlugin.register_plugin"
gcp = "orb.providers.gcp.provider_plugin:GCPPlugin.register_plugin"
k8s = "orb.providers.k8s.provider_plugin:K8sPlugin.register_plugin"
```

Each entry point points at a zero-argument, idempotent classmethod. At startup,
`discover_provider_plugins()` (in `src/orb/providers/registration.py`) walks
`importlib.metadata.entry_points(group="orb.providers")` and invokes each
entry point's callable. A plugin that fails to load or raises during
registration is logged at `ERROR` and skipped — ORB still boots with whatever
providers loaded successfully. Loaded provider names are appended to the
module-level `_REGISTERED_PROVIDERS` list, which the rest of bootstrap iterates
over.

Third-party plugins use the same mechanism: declare an entry point in the
plugin package's own `pyproject.toml` with no changes required to any file in
ORB itself. See `docs/root/providers/k8s/plugin-authoring.md` for the plugin
callable contract (zero-argument, returns `None`, must not raise).

Three bootstrap entry points consume the discovered provider list, each calling
`discover_provider_plugins()` first so entry-point providers are present before
iterating:

- `register_all_providers(container)` — registers each provider's strategy and
  config factories with the `ProviderRegistry`, then (when a DI container is
  supplied) resolves `TemplateFactory` and `LoggingPort` and runs the
  provider's DI-level initialization.
- `register_all_provider_cli_specs()` — registers each provider's
  `ProviderCLISpecPort` implementation with `CLISpecRegistry`, used by
  `orb providers add` and related CLI commands.
- `register_all_defaults_loaders()` — registers each provider's
  `ProviderDefaultsLoaderPort` implementation with `DefaultsLoaderRegistry`.

## The `ProviderPlugin` contract

`src/orb/providers/base/provider_plugin.py` defines `ProviderPlugin`, the
abstract base every provider subclasses. It separates the pieces every
provider must supply ("satellite accessors") from the orchestrated lifecycle
that calls them in the correct order, so a new provider implements only the
accessors.

Mandatory satellite accessors:

- `strategy_factory()` — callable that builds a `ProviderStrategy` instance
  from a provider config.
- `config_factory()` — callable that builds the provider's typed config object
  (a `pydantic_settings.BaseSettings` subclass) from a dict.
- `template_dto_config()` — the provider's template DTO extension class (or
  `None`), registered with `TemplateExtensionRegistry`.
- `cli_spec()` — an instance of the provider's `ProviderCLISpecPort`
  implementation (or `None`), registered with `CLISpecRegistry`.
- `field_mapping()` — an instance of the provider's HostFactory field-mapping
  adapter (or `None`), registered with `FieldMappingRegistry`.
- `defaults_loader()` — an instance of the provider's
  `ProviderDefaultsLoaderPort` implementation (or `None`), registered with
  `DefaultsLoaderRegistry`.
- `template_example_generator(container)` — an instance of the provider's
  template-example-generator port (or `None`), registered with
  `TemplateExampleGeneratorRegistry` once the DI container is available.

Optional hooks with no-op or empty defaults: `resolver_factory()`,
`validator_factory()`, `strategy_class()`, `default_api()`,
`provider_settings_class()`, `template_class()`, `register_auth_strategies()`,
`register_additional_services()`, and `_do_initialize()` for any
provider-specific steps that must run after the standard satellites (for
example, AWS registers its DynamoDB and Aurora storage backends in
`_do_initialize`).

The orchestrated lifecycle methods are:

- `register_provider(registry=None, logger=None, instance_name=None)` —
  registers the strategy/config factories (and optionally resolver/validator)
  with the live `ProviderRegistry`. Registers a named instance instead of the
  provider type when `instance_name` is given.
- `initialize_provider(template_factory=None, logger=None)` — registers
  provider settings, the template DTO extension, auth strategies, the
  template class, the CLI spec, the field mapping, and the defaults loader, in
  that order, then runs `_do_initialize`. Guarded by a module-level
  `_initialized_providers` set so a second call is a safe no-op; a failed
  attempt is not recorded, so a retry after fixing the underlying problem
  re-runs the full sequence.
- `register_services_with_di(container)` — runs `register_additional_services`
  and registers the template example generator.

Every satellite accessor import inside a concrete plugin (for example
`AWSPlugin`) is a local, deferred import wrapped in `try`/`except ImportError`
where the accessor is optional, so a provider module can be imported — and the
entry point loaded — even when that provider's SDK extra is not installed.
Only calling the strategy at runtime requires the actual dependency.

## `ProviderStrategy`: the operation contract

`src/orb/providers/base/strategy/provider_strategy.py` defines
`ProviderStrategy`, the abstract class each provider's strategy implements
(for example `AWSProviderStrategy`, `AzureProviderStrategy`,
`GCPProviderStrategy`, `K8sProviderStrategy`). It is constructed with the
provider's typed config and exposes:

- `provider_type` (property) — the provider type identifier (`"aws"`,
  `"azure"`, `"gcp"`, `"k8s"`).
- `initialize() -> bool` — cheap, synchronous setup; validate config, no I/O
  or background tasks.
- `execute_operation(operation: ProviderOperation) -> ProviderResult` — the
  core strategy-pattern entry point. All provider work (provisioning,
  terminating, status, validation, health) flows through this single method.
- `execute_operation_async(...)` — defaults to running the sync method in a
  thread pool; providers may override for a native async implementation.
- `start_daemon_services()` — no-op by default; providers that run background
  tasks (watch streams, reconcilers, garbage collectors) override it. Only
  called in long-lived daemon contexts (the REST API server), never from the
  CLI.
- `get_capabilities() -> ProviderCapabilities` and
  `check_health() -> ProviderHealthStatus`.
- `generate_provider_name(config)`, `parse_provider_name(name)`, and
  `get_provider_name_pattern()` — provider-specific naming convention for
  instance names.
- `cleanup()` — resource teardown; the class also implements the context
  manager protocol (`__enter__`/`__exit__`) calling `initialize`/`cleanup`.

`ProviderOperation` carries an `operation_type` (one of the
`ProviderOperationType` enum values — `CREATE_INSTANCES`,
`TERMINATE_INSTANCES`, `GET_INSTANCE_STATUS`,
`DESCRIBE_RESOURCE_INSTANCES`, `VALIDATE_TEMPLATE`,
`GET_AVAILABLE_TEMPLATES`, `HEALTH_CHECK`, `RESOLVE_IMAGE`, `START_INSTANCES`,
`STOP_INSTANCES`, `CLEANUP_MACHINE_RESOURCES`, `GET_MACHINE_HEALTH`,
`TAG_INSTANCES`), a `parameters` dict, and an optional `context` dict.
`ProviderResult` is a Pydantic model with `success`, `data`, `error_message`,
`error_code`, `metadata`, and `routing_info`; `error_message` is required
whenever `success` is `False`.

Additional classmethods let a strategy opt into CLI-driven onboarding and
operator tooling without the core needing provider-specific code:
`get_available_credential_sources()`, `test_credentials()`,
`get_credential_requirements()`, `get_operational_requirements()`,
`get_ui_column_schema()`, `get_cli_extra_config_keys()`,
`get_cli_provider_config()`, `get_resource_id_pattern()`, and
`get_cli_infrastructure_defaults()`. `resolve_api_alias()` lets a strategy map
legacy or alternate API names to its canonical registry key.

## Provider registry and named instances

`ProviderRegistry` (`src/orb/providers/registry/provider_registry.py`) is a
thread-safe singleton, obtained via `get_provider_registry()`, that holds
registered strategy/config factories in `MULTI_CHOICE` mode — multiple
provider strategies can be registered and used simultaneously.

- `register_provider(provider_type, strategy_factory, config_factory, ...)`
  registers a provider *type* (one factory pair per type).
- `register_provider_instance(provider_type, instance_name, strategy_factory,
  config_factory, ...)` registers a named *instance* of a type (for example
  two differently configured AWS instances), each with its own factories.
- `get_or_create_strategy(provider_identifier, config)` looks up a cached
  strategy by type or instance name, creating and caching it on first use.
- `register_fallback_strategy(strategy)` / `get_fallback_strategy()` register
  a `FallbackProviderStrategy` used when no provider configuration matches —
  constructed via `register_fallback_provider()` in
  `src/orb/providers/registration.py`.

## Provider selection and load balancing

Request-to-provider routing is implemented by `ProviderSelectionService`
(`src/orb/infrastructure/services/provider_selection_service.py`), injected
into `ProviderRegistry`. It depends only on `ProviderRegistryPort` and
`ConfigurationPort`, so it has no dependency on any specific provider — the
same selection logic applies whether the configured instances are AWS, Azure,
Google Cloud, or Kubernetes.

`select_provider_for_template(template, provider_name=None, logger=None)`
resolves a provider for a template request using this precedence:

1. CLI override (`--provider-name`), if supplied.
2. The template's explicit `provider_name` (a named instance).
3. The template's `provider_type`, load-balanced across enabled instances of
   that type.
4. The template's `provider_api`, matched against instances whose effective
   handlers or declared `capabilities` support that API.
5. The configuration default (`default_provider_instance` /
   `default_provider_type`, or the first enabled instance), falling back to
   a registered fallback strategy if no provider configuration exists at all.

`select_active_provider(logger=None, provider_name=None, provider_type=None)`
performs the equivalent resolution for operator-facing (CLI/REST) calls that
are not tied to a specific template.

### Configuration schema

Provider instances and selection policy are defined by `ProviderConfig` and
`ProviderInstanceConfig` in
`src/orb/config/schemas/provider_strategy_schema.py`:

```yaml
providers:
  selection_policy: WEIGHTED_ROUND_ROBIN
  default_provider_type: aws
  default_provider_instance: aws-us-east-1
  providers:
    - name: aws-us-east-1
      type: aws
      enabled: true
      priority: 1
      weight: 10
      capabilities: [EC2Fleet, SpotFleet, RunInstances]
    - name: aws-us-west-2
      type: aws
      enabled: true
      priority: 2
      weight: 5
      capabilities: [EC2Fleet, RunInstances]
```

`selection_policy` is validated against a fixed set of named policies
(`FIRST_AVAILABLE`, `ROUND_ROBIN`, `WEIGHTED_ROUND_ROBIN`,
`LEAST_CONNECTIONS`, `FASTEST_RESPONSE`, `HIGHEST_SUCCESS_RATE`,
`CAPABILITY_BASED`, `HEALTH_BASED`, `RANDOM`, `PERFORMANCE_BASED`). The
load-balancing step actually implemented today
(`_apply_load_balancing_strategy`) selects by priority first (lower
`priority` wins) and only consults `weight` as a tie-breaker among instances
sharing the highest priority when the policy is `WEIGHTED_ROUND_ROBIN`;
`HEALTH_BASED` currently also resolves to the lowest-priority instance; any
other configured policy falls back to the same priority-ordered selection.
`get_active_providers()` treats `WEIGHTED_ROUND_ROBIN`, `ROUND_ROBIN`,
`LEAST_CONNECTIONS`, `PERFORMANCE_BASED`, `FASTEST_RESPONSE`,
`HIGHEST_SUCCESS_RATE`, `CAPABILITY_BASED`, and `HEALTH_BASED` as
multi-provider policies that return every enabled instance; otherwise it
returns the single `active_provider` instance, or the sole configured
instance.

Each `ProviderInstanceConfig` carries `name`, `type`, `enabled`, `priority`,
`weight` (must be positive), `capabilities`, `handlers` /
`handler_overrides` (merged with the provider type's defaults via
`get_effective_handlers`), `template_defaults`, `extensions`, and
`health_check`. None of these fields are AWS-specific — the same schema backs
Azure, Google Cloud, and Kubernetes instances; the example above uses two AWS instances
because multi-instance load balancing is most commonly exercised with
multiple regions of one provider type, but an installation can equally define
multiple named instances of `azure`, `gcp`, or `k8s`, or mix provider types
under one `selection_policy`.

## Template extensions, defaults, and field mapping

Three registries let each provider contribute provider-specific behaviour to
shared template and scheduler code without that code knowing about any
specific provider:

- **`TemplateExtensionRegistry`**
  (`src/orb/infrastructure/registry/template_extension_registry.py`) maps a
  provider type to a Pydantic model class (the plugin's
  `template_dto_config()`) so `TemplateDTO` can deserialize provider-specific
  template fields. AWS and Kubernetes expose this as a
  `<name>_template_dto_config.py` module under `domain/template/`; Azure and
  Google Cloud expose an equivalent `TemplateExtensionConfig` class under
  `configuration/template_extension.py`.
- **`DefaultsLoaderRegistry`**
  (`src/orb/providers/registry/defaults_loader_registry.py`) maps a provider
  type to a `ProviderDefaultsLoaderPort` implementation (the plugin's
  `defaults_loader()`, typically `<Name>DefaultsLoader` in
  `defaults_loader.py`). Each loader's `load_defaults()` returns a raw config
  dict in the same shape as `default_config.json`, which
  `ConfigurationLoader` merges in during startup.
- **`FieldMappingRegistry`**
  (`src/orb/infrastructure/scheduler/hostfactory/field_mapping_registry.py`)
  maps a provider type to a `FieldMappingPort` implementation (the plugin's
  `field_mapping()`, in `scheduler/hostfactory_field_mapping.py`). Each
  provider contributes its own camelCase-HostFactory-field to
  internal-snake_case-field mappings — for example AWS maps `subnetId` to
  `subnet_ids` and `fleetRole` to `fleet_role` — on top of the generic
  mappings shared by all providers.

## CLI integration

`ProviderCLISpecPort` (`src/orb/providers/base/provider_cli_spec_port.py`) is
the protocol each provider's `cli_spec()` instance implements:
`add_arguments(parser)`, `extract_config(args)`,
`extract_partial_config(args)`, `validate_add(args)`, `generate_name(args)`,
and `format_display(config)`. Instances are registered under the provider
name in `CLISpecRegistry` and used by the `orb providers` command group:

```bash
orb providers list
orb providers show <name>
orb providers add
orb providers update <name>
orb providers remove <name>
orb providers set-default <name>
orb providers get-default
orb providers health
orb providers metrics
orb providers select
```

## Optional extras and import guards

AWS's SDK dependencies (`boto3`, `botocore`) currently ship in ORB's core
dependency set for backward compatibility, with an explicit `orb-py[aws]`
extra as a forward-compatible alias. Azure, Google Cloud, and Kubernetes are
genuinely optional and declared as separate extras in `pyproject.toml`:

```toml
aws = ["boto3>=1.42.21", "botocore>=1.42.21"]
k8s = ["kubernetes"]
azure = [
    "azure-core>=1.38.2",
    "azure-identity>=1.25.2",
    "azure-mgmt-compute>=37.2.0",
    "azure-mgmt-network>=30.2.0",
    "azure-mgmt-resource>=25.0.0",
    "azure-mgmt-resource-subscriptions==1.0.0b1",
    "httpx>=0.27.0",
]
gcp = ["google-cloud-compute>=1.14.0", "google-auth>=2.23.0"]
all-providers = ["orb-py[aws]", "orb-py[azure]", "orb-py[k8s]", "orb-py[gcp]"]
```

Each `provider_plugin.py` module imports only `ProviderPlugin` at module
level; every satellite accessor defers its real import (the provider's
strategy class, config class, CLI spec, and so on) to inside the method body,
and several wrap that import in `try`/`except ImportError` to return `None`
when the optional dependency is absent. This means the entry point for a
provider whose extra is not installed can still be discovered and partially
registered — the registry knows the provider type exists — while any attempt
to actually construct and use its strategy fails only once the missing
dependency is exercised, with a clear `ImportError`.

## Related documentation

- [Developer Guide: Provider System](../developer_guide/providers.md)
- [Adding a Provider](../developer_guide/adding_a_provider.md)
- [Strategy Pattern](../patterns/strategy_pattern.md)
- [Ports and Adapters](../patterns/ports_and_adapters.md)
- [Field Mapping Architecture](field_mapping_architecture.md)
