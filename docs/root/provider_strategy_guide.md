# Provider Strategy Guide

A provider strategy is the object that carries out operations against one infrastructure platform. ORB ships strategies for AWS, Azure, Google Cloud, Kubernetes and OCI, registered through the `orb.providers` entry-point group in `pyproject.toml`. Callers, whether the CLI, REST API, SDK or a scheduler integration, address every strategy through the same operation contract.

This guide describes the operations a strategy exposes, how ORB chooses a provider for a request, and where provider-specific behaviour is documented. For the full design, see [Multi-Provider Design](architecture/multi-provider-design.md). To write a new provider, see [Adding a Provider](developer_guide/adding_a_provider.md).

## Operations

Each strategy implements `ProviderStrategy` (`src/orb/providers/base/strategy/provider_strategy.py`). Work is submitted as a `ProviderOperation` whose `operation_type` is a value of `OperationType` (`src/orb/domain/base/operations.py`):

| Operation | Purpose |
|---|---|
| `CREATE_INSTANCES` | Provision machines for a template. |
| `TERMINATE_INSTANCES` | Release machines. |
| `GET_INSTANCE_STATUS` | Report the status of machines. |
| `DESCRIBE_RESOURCE_INSTANCES` | List the machines behind a provider resource. |
| `VALIDATE_TEMPLATE` | Validate a template against the provider. |
| `GET_AVAILABLE_TEMPLATES` | List templates the provider can offer. |
| `HEALTH_CHECK` | Probe provider connectivity. |
| `RESOLVE_IMAGE` | Resolve an image reference. |
| `START_INSTANCES`, `STOP_INSTANCES` | Power machines on and off without releasing them. |
| `CLEANUP_MACHINE_RESOURCES` | Remove resources attached to a released machine. |
| `GET_MACHINE_HEALTH` | Report the health of one machine. |
| `TAG_INSTANCES` | Apply tags or labels to machines. |
| `CANCEL_PROVISIONING` | Cancel an in-flight provisioning request. |
| `CLEANUP_ORPHANED_RESOURCES` | Remove provider resources that ORB created and nothing uses. |

A strategy does not support every operation. `get_capabilities()` returns the operations a strategy supports, and a strategy returns a failed result for an operation it does not support. The provider documentation lists what each provider supports.

## Selecting a provider

Selection decides which configured provider instance serves a request. The precedence, from highest to lowest, is:

1. The `--provider-name` command-line flag (see [Provider Override](cli/provider-override.md)).
2. The template's `provider_name`.
3. The template's `provider_type`, balanced across the enabled instances of that type.
4. The template's `provider_api`, matched against instances whose handlers or declared `capabilities` support it.
5. The configured default: `default_provider_instance`, then `default_provider_type`, then the first enabled instance.

Provider instances, their `priority` and `weight`, and the `selection_policy` are configured under `providers` in the configuration file. The schema is `ProviderConfig` in `src/orb/config/schemas/provider_strategy_schema.py`; [Multi-Provider Design](architecture/multi-provider-design.md#configuration-schema) documents each field and which policies have behaviour today. [Multi-Provider Configuration](configuration/multi-provider.md) has configuration examples.

## Provider commands

The `orb providers` command group inspects and manages provider instances. The [CLI reference](cli/cli-reference.md#providers) documents every option.

```bash
orb providers list --detailed
orb providers show --provider-name aws_prod_us-east-1
orb providers health
orb providers metrics --provider-name aws_prod_us-east-1
orb providers set-default aws-prod
orb providers exec --list
```

`orb providers exec` runs provider-side maintenance operations only. Operations that create, start, stop or terminate machines have their own commands.

## Provider-specific behaviour

| Provider | Documentation |
|---|---|
| AWS | [AWS](#aws) below |
| Azure | [Overview](providers/azure/index.md), [Handlers](providers/azure/handlers.md), [Configuration](providers/azure/configuration.md) |
| Google Cloud | [Overview](providers/gcp/index.md), [Handlers](providers/gcp/handlers.md), [Configuration](providers/gcp/configuration.md) |
| Kubernetes | [Overview](providers/k8s/index.md), [Handlers](providers/k8s/handlers.md), [Configuration](providers/k8s/configuration.md) |
| OCI | [Compute](providers/oci/compute.md), [Templates](providers/oci/templates.md) |

### Azure operations

The Azure strategy reports `CREATE_INSTANCES`, `TERMINATE_INSTANCES`, `GET_INSTANCE_STATUS`, `DESCRIBE_RESOURCE_INSTANCES`, `VALIDATE_TEMPLATE`, `HEALTH_CHECK`, `START_INSTANCES` and `STOP_INSTANCES`. `STOP_INSTANCES` deallocates machines by default; the operation parameter `deallocate` set to `false` requests a plain power-off. See [Azure handlers](providers/azure/handlers.md#capabilities-summary).

### AWS

The AWS provider selects a handler with the template's `provider_api`:

| `provider_api` | Documentation |
|---|---|
| `RunInstances` | [EC2 Instances](providers/aws/run-instances.md) |
| `EC2Fleet` | [EC2 Fleet](providers/aws/ec2-fleet.md) |
| `SpotFleet` | [Spot Fleet](providers/aws/spot-fleet.md) |
| `ASG` | [Auto Scaling Groups](providers/aws/asg.md) |
| `MicroVM` | [Lambda MicroVMs](providers/aws/microvm.md) |

An instance in the configuration names the profile and region it uses, for example `aws_prod_us-east-1`. The required permissions are in the [IAM permissions guide](deployment/iam-permissions.md).

The AWS strategy supports the `cleanup-orphaned-resources` maintenance operation, which removes launch templates ORB created that nothing uses any more:

```bash
orb providers exec cleanup-orphaned-resources --provider-name aws_prod_us-east-1 --dry-run
```

The parameters, safety checks and result format are in the [CLI reference](cli/cli-reference.md#cleanup-orphaned-resources-aws).
