# Azure handlers

The Azure provider ships three handler implementations behind four
`provider_api` values. `VMSS` and `VMSSUniform` share one handler class
(`VMSSHandler`), distinguished only by `orchestration_mode`.

| `provider_api` | Handler class       | Native resource(s)                             | Selective release |
|------------------|----------------------|---------------------------------------------------|---------------------|
| `VMSS`           | `VMSSHandler`         | VMSS, Flexible orchestration                       | yes (per VM)         |
| `VMSSUniform`     | `VMSSHandler`         | VMSS, Uniform orchestration                        | yes (per instance ID) |
| `SingleVM`        | `SingleVMHandler`     | Individual `Microsoft.Compute/virtualMachines`    | yes (per VM name)    |
| `CycleCloud`      | `CycleCloudHandler`   | Nodes in an existing CycleCloud cluster            | yes (per node)       |

## Decision tree

1. Is the workload managed by an existing Azure CycleCloud HPC cluster?
   * Yes - use **`CycleCloud`**.
2. Does the workload need to be a long-lived, individually addressable VM
   rather than a scalable pool?
   * Yes - use **`SingleVM`**.
3. Otherwise - use **`VMSS`** (Flexible orchestration is the default and
   supports Spot, instance mix, and zone spreading). Use `VMSSUniform`
   only if you specifically need Uniform orchestration semantics.

## `VMSS` (Flexible orchestration)

The VMSS handler creates one `Microsoft.Compute/virtualMachineScaleSets`
resource per ORB request, with
`sku.capacity = request.requested_count`. Networking defaults to
`template.network_config`; if that is omitted, the handler falls back to
the shared `subnet_ids` field and auto-builds a minimal `network_config`
from it.

* **Acquire** - submits one VMSS create/update deployment via the async
  Compute SDK.
* **Release** - for Flexible orchestration, deletes the named VM members
  individually (`virtual_machines.begin_delete`); if every member of the
  VMSS is released, the handler deletes the VMSS itself afterward instead
  of leaving an empty scale set behind.

Instance mix (`vm_sizes` or ranked `vm_size_preferences` with
`vmss_allocation_strategy="Prioritized"`) is submitted as VMSS
`skuProfile.vmSizes`. Spot Priority Mix (`spot_percentage`,
`base_regular_priority_count`) is submitted as VMSS `priorityMixPolicy`
and requires Flexible orchestration with `priority="Spot"`.

## `VMSSUniform`

Same handler class as `VMSS`, with `orchestration_mode="Uniform"`
required. Release behaviour differs because Uniform VMSS members are
identified by numeric instance ID rather than VM name:

* **Release** - either deletes the whole VMSS (when every member is
  released) or calls `virtual_machine_scale_sets.begin_delete_instances`
  with the resolved instance IDs.
* `overprovision` is only valid in Uniform orchestration mode.

## `SingleVM`

The SingleVM handler provisions individual VMs via ARM deployments
(`AzureDeploymentService`), one deployment per requested VM, up to
`request.requested_count`.

* **Acquire** - resolves the subnet (`network_config.subnet_id` or
  `subnet_ids`), resolves SSH keys (`ssh_key_name` via the Azure SSH
  Public Key resource API, or inline `ssh_public_keys`), then submits one
  ARM deployment per VM. If a transient allocation failure occurs
  (`AllocationFailed`, `ZonalAllocationFailed`, `SkuNotAvailable`,
  `OverconstrainedAllocationRequest`) and more than one `vm_size`/
  `vm_size_preferences` candidate is configured, the handler retries with
  the next candidate size.
* **Release** - deletes the named VM(s) directly;
  deletion of the dependent NIC and public IP relies on Azure's
  `deleteOption: Delete` cascade from the VM.

## `CycleCloud`

The CycleCloud handler does not call Azure Compute APIs directly — it
talks to a CycleCloud cluster's own REST API (see
[Authentication](auth.md#cyclecloud-authentication)).

* **Acquire** - submits an "add nodes" request against
  `cluster_name`/`node_array`.
* **Release** - submits a terminate request for the given node IDs.
* **Status** - CycleCloud node states map to ORB machine status as
  follows:

| CycleCloud state | ORB status     |
|--------------------|------------------|
| `Off`               | `stopped`         |
| `Acquiring`         | `pending`          |
| `Preparing`         | `pending`          |
| `Starting`          | `pending`          |
| `Started`           | `running`          |
| `Software Configuration` | `pending`     |
| `Ready`             | `running`          |
| `Deallocating`      | `shutting-down`    |
| `Deallocated`       | `stopped`          |
| `Terminated`        | `terminated`       |
| `Failed`            | `failed`           |
| (any other state)   | `unknown`          |

CycleCloud templates do not need `image`, `ssh_key_name`, or
`ssh_public_keys` — SSH access and node imaging are managed by
CycleCloud itself.

## Capabilities summary

| `provider_api` | Spot | On-demand | Max instances |
|------------------|------|-------------|------------------|
| `VMSS`            | yes  | yes          | 1000              |
| `VMSSUniform`      | yes  | yes          | 1000              |
| `SingleVM`         | yes  | yes          | 1000              |
| `CycleCloud`       | no   | yes          | -                 |

None of the four handlers support ORB's `start`/`stop` operations — the
Azure provider strategy only advertises `create_instances`,
`terminate_instances`, `get_instance_status`,
`describe_resource_instances`, `validate_template`,
`get_available_templates`, and `health_check` in its capabilities. To
resize a pool, release machines and acquire new ones.

## Choosing a `provider_api` at template-generate time

```bash
# VMSS (default)
orb templates generate --provider-type azure --provider-api VMSS

# VMSS, Uniform orchestration
orb templates generate --provider-type azure --provider-api VMSSUniform

# SingleVM
orb templates generate --provider-type azure --provider-api SingleVM

# CycleCloud
orb templates generate --provider-type azure --provider-api CycleCloud
```

Existing templates can be retargeted by editing the `provider_api` field
in-place. Handler dispatch happens at request time, not at template-create
time.
