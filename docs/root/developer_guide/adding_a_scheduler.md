# Adding a Scheduler

A scheduler strategy translates between ORB's internal model and the wire format of an external workload scheduler: template files, request and response payloads, directories, exit codes and log settings. ORB ships three strategies: `hostfactory` (IBM Spectrum Symphony Host Factory), `default` (ORB's native format) and `slurm`.

This guide describes the scheduler contract, how a strategy is registered and selected, and how the three built-in strategies fill the contract. Unlike providers, schedulers have no entry-point group: a new scheduler is added inside the ORB source tree (see [Registering a strategy](#registering-a-strategy)).

For provider extension, see [Adding a Provider](adding_a_provider.md).

## The scheduler contract

The contract lives in `src/orb/application/ports/scheduler_port.py`. It is split into four sub-ports so that a collaborator can depend on only the part it uses. `SchedulerPort` inherits all four and is the type a strategy implements.

| Port | Responsibility |
|---|---|
| `SchedulerFormattingPort` | Format request, machine, template, health and status responses for the scheduler's wire protocol; map request status to exit codes. |
| `SchedulerEnvironmentPort` | Report the scheduler's config, working and log directories, log level, console logging, scripts directory and type name. |
| `SchedulerTemplateIOPort` | Parse scheduler template files into template DTOs and resolve the template file names and paths. |
| `SchedulerRequestParsingPort` | Parse incoming request data into the domain format; expand node-range expressions. |

Every method decorated with `@abstractmethod` in these ports must be implemented. Other methods have defaults that a strategy may override, for example `format_reclaimed_machines_response`, `get_status_poll_exit_code`, `is_provider_reclaim_poll` and `expand_node_range`. `SchedulerPort.generate_scheduler_templates` returns `None` by default, which makes ORB fall back to the provider's example templates.

The shared base class `BaseSchedulerStrategy` (`src/orb/infrastructure/scheduler/base/strategy.py`) inherits `SchedulerPort` and supplies common behaviour: it holds the configuration port, logger, provider registry service and path resolver set through `_init_base`, and resolves the active provider name and type. All three built-in strategies extend it.

## Registering a strategy

A strategy is made available to ORB by registering it with `SchedulerRegistry` (`src/orb/infrastructure/scheduler/registry.py`):

```python
registry.register(
    type_name="slurm",
    strategy_factory=create_slurm_strategy,
    config_factory=create_slurm_config,
    strategy_class=SlurmSchedulerStrategy,
)
```

- `type_name` is the name used in configuration, on the command line and in API calls. It must equal the string the strategy returns from `get_scheduler_type()`.
- `strategy_factory` receives the DI container (or a plain dict on non-container paths) and returns a strategy instance.
- `config_factory` builds the scheduler-specific configuration object from a dict.
- `strategy_class` lets ORB call class methods, such as `get_defaults_config`, before the application is initialised.

The registrations for the built-in strategies are in `src/orb/infrastructure/scheduler/registration.py`: `register_symphony_hostfactory_scheduler`, `register_default_scheduler` and `register_slurm_scheduler`. `register_all_scheduler_types` calls all three during bootstrap. Registration is idempotent.

### Adding a new type

The scheduler type is a closed set today. Adding one means changing the source tree:

1. Add a strategy class under `src/orb/infrastructure/scheduler/<name>/` that extends `BaseSchedulerStrategy`.
2. Add a `create_<name>_strategy` factory, a `create_<name>_config` factory and a `register_<name>_scheduler` function to `registration.py`. Call the new function from `register_all_scheduler_types` and add it to the `registration_functions` map in `register_active_scheduler_only`.
3. Add the name to the `Literal` of `SchedulerConfig.type` in `src/orb/config/schemas/scheduler_schema.py`. Without this, configuration validation rejects the name.
4. Add the name to the `--scheduler` choices in `src/orb/cli/parsers/parent_parsers.py` and `src/orb/cli/parsers/init.py`, to the `SCHEDULER_TYPE_*` constants in `src/orb/config/constants.py` and to `BUILT_IN_FORMATS` in `src/orb/api/documentation/openapi_config.py`.
5. Add tests under `tests/unit/infrastructure/scheduler/` and update the user documentation.

## Selecting a scheduler

| Mechanism | Scope | Source |
|---|---|---|
| `scheduler.type` configuration key | Default for the installation. One of `default`, `hostfactory`, `slurm`; defaults to `default`. | `src/orb/config/managers/provider_manager.py`, `src/orb/config/schemas/scheduler_schema.py` |
| `--scheduler` command-line flag | One CLI invocation. Overrides `scheduler.type`. | `src/orb/cli/main.py`, `ConfigurationManager.override_scheduler_strategy` |
| `X-ORB-Scheduler` header or `scheduler` query parameter | One REST call. Chooses the response format. | `src/orb/api/dependencies.py`, `src/orb/interface/format_selection.py` |

For REST calls, a header and a query parameter that name different formats, or a name that is not registered, return HTTP 400.

## Field mapping

Strategies that exchange templates in a foreign field vocabulary use a field mapper. `SchedulerFieldMapper` (`src/orb/infrastructure/scheduler/base/field_mapper.py`) defines the contract: a `field_mappings` property (external name to internal name), `map_input_fields`, `map_output_fields` and `format_for_generation`. Subclasses may override `_apply_output_transformations` to adjust the output after renaming.

The HostFactory strategy adds a second layer for provider-specific fields. `FieldMappingPort` (`src/orb/infrastructure/scheduler/hostfactory/field_mapping_port.py`) is a protocol with `get_mappings`, `apply_defaults` and `derive_attributes`. A provider implements it under `providers/<name>/scheduler/` and registers the instance with `FieldMappingRegistry` (`field_mapping_registry.py` in the same directory), keyed by provider type. The AWS implementation is `AWSFieldMapping` in `src/orb/providers/aws/scheduler/hostfactory_field_mapping.py`.

## Built-in strategies

### HostFactory

`HostFactorySchedulerStrategy` (`src/orb/infrastructure/scheduler/hostfactory/hostfactory_strategy.py`) implements the IBM Spectrum Symphony Host Factory provider protocol. It uses `HostFactoryFieldMapper` (Host Factory field names to ORB field names) and applies per-provider mappings through `FieldMappingRegistry`. Related user documentation is under [HostFactory Integration](../hostfactory/integration_guide.md).

### Default

`DefaultSchedulerStrategy` (`src/orb/infrastructure/scheduler/default/default_strategy.py`) uses ORB's native format. Its `DefaultFieldMapper` returns an empty mapping and passes templates through unchanged, which makes it the smallest complete example of the contract.

### Slurm

`SlurmSchedulerStrategy` (`src/orb/infrastructure/scheduler/slurm/slurm_strategy.py`) maps Slurm partition and node data to ORB templates through `SlurmFieldMapper`, expands Slurm hostlist expressions by overriding `expand_node_range`, and can generate templates from `slurm.conf` through `generate_scheduler_templates`. Related user documentation is under [Slurm Integration](../slurm/integration_guide.md).

## Verifying a new strategy

- Confirm the strategy can be instantiated: a class that leaves an abstract method unimplemented raises `TypeError` on construction.
- Confirm `get_scheduler_type()` returns the registered `type_name`.
- Run `orb scheduler list` to confirm the type is registered, and select it with `--scheduler <name>` or `scheduler.type`.
