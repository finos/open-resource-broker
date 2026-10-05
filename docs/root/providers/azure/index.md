# Azure provider

The Azure provider lets ORB acquire, track, and release compute capacity
backed by Azure Virtual Machines, Virtual Machine Scale Sets (VMSS), and
Azure CycleCloud HPC clusters. It reuses the same template, request, and
machine model as the AWS and Kubernetes providers, so callers of the CLI,
REST API, SDK, and HostFactory plugin do not need to special-case Azure.

The provider supports four provisioning shapes (`provider_api` values):

| `provider_api` | Azure resource                              | Typical use                                              |
|-----------------|----------------------------------------------|-----------------------------------------------------------|
| `VMSS`          | VMSS, Flexible orchestration                 | Default. Spot/on-demand fleets, instance mix, zone spread. |
| `VMSSUniform`   | VMSS, Uniform orchestration                  | Legacy VMSS orchestration mode.                            |
| `SingleVM`      | Individual `Microsoft.Compute/virtualMachines` | Long-lived singleton VMs.                                |
| `CycleCloud`    | Nodes added to an existing CycleCloud cluster | HPC clusters managed by Azure CycleCloud.                 |

See [Handlers](handlers.md) for how to pick between them.

## Install

The Azure provider lives behind an optional install extra so operators who
only target AWS or Kubernetes do not pay for the Azure SDKs.

```bash
pip install "orb-py[azure]"
```

This pulls in `azure-core`, `azure-identity`, `azure-mgmt-compute`,
`azure-mgmt-network`, `azure-mgmt-resource`, and `httpx` (used for the
CycleCloud REST client).

## Quick start

### 1. Authenticate

ORB authenticates to Azure Resource Manager using
`azure.identity.DefaultAzureCredential`, which tries managed identity,
environment credentials, and the Azure CLI login in sequence. See
[Authentication](auth.md) for the full credential chain and CycleCloud's
separate authentication path.

### 2. Configure the provider

```json
{
  "providers": [
    {
      "name": "azure_main",
      "type": "azure",
      "config": {
        "subscription_id": "00000000-0000-0000-0000-000000000000",
        "resource_group": "orb-compute",
        "region": "eastus2"
      }
    }
  ]
}
```

The full set of fields lives in [Configuration reference](configuration.md).

### 3. Create a template

```bash
orb templates generate --provider-type azure --provider-api VMSS
```

This emits a template targeting the VMSS handler. Set `vm_size`,
`resource_group`, `location`, an image reference, and SSH access, then save
it. See [Configuration reference](configuration.md#template-fields) for the
full field list and [Handlers](handlers.md) for per-handler requirements.

### 4. Request capacity

```bash
orb machines request my-azure-template 3
```

### 5. Track and release

```bash
orb requests status <request-id>
orb machines return <machine-id> <machine-id> ...
```

Releases delete the named VM(s) directly for `SingleVM`, delete individual
VMSS members (or the whole VMSS when every member is released) for
`VMSS`/`VMSSUniform`, and submit a terminate request to CycleCloud for
`CycleCloud` nodes.

## AWS concepts mapped to Azure

| AWS concept                       | Azure equivalent                                                         |
|-----------------------------------|-----------------------------------------------------------------------------|
| EC2 instance                      | Virtual Machine                                                             |
| Auto Scaling Group / EC2 Fleet    | Virtual Machine Scale Set (VMSS)                                            |
| Amazon Machine Image (AMI)        | Image reference (Marketplace `publisher`/`offer`/`sku`, or a gallery/custom `image_id`) |
| Instance type                     | VM size (`vm_size`, e.g. `Standard_D4s_v5`)                                 |
| Spot / On-Demand                  | `priority` (`Spot`, `Regular`, or legacy `Low`)                            |
| Availability Zone                 | Azure availability zone (`zones`, e.g. `["1","2","3"]`)                     |
| EC2 key pair                      | Azure SSH Public Key resource (`ssh_key_name`) or inline `ssh_public_keys` |
| EBS volume                        | Managed disk (`os_disk`, `data_disks`)                                     |
| VPC subnet / security group       | `network_config.subnet_id` / `network_config.network_security_group_id`  |
| IAM instance profile              | Managed identity (`system_assigned_identity`, `user_assigned_identity_ids`) |
| EC2 user data                     | `custom_data` (base64-encoded cloud-init)                                  |

Key differences to keep in mind:

* Azure Linux VMs never fall back to password authentication. Every
  template that is not `CycleCloud` must set `ssh_key_name` or
  `ssh_public_keys`, and must set `image` or `image_id`.
* Azure does not support ORB's `start`/`stop` operations on any
  `provider_api`. There is no equivalent to AWS `StartInstances`/
  `StopInstances` in the current Azure provider; only create and terminate
  are supported. See [Provider capabilities](configuration.md#provider-capabilities).
* `resource_group` and `location` are required per template; Azure's
  platform term is `location`, but `region` is also accepted as an alias
  for cross-provider configuration reuse.

## What is in this section

* [Configuration reference](configuration.md) - every `AzureProviderConfig`
  and `AzureTemplate` field, with worked examples.
* [Handlers](handlers.md) - VMSS, SingleVM, CycleCloud; when to pick each.
* [Authentication](auth.md) - `DefaultAzureCredential`, managed identity,
  CycleCloud credential files, and troubleshooting.
