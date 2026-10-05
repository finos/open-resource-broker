# Azure provider - configuration reference

This page documents every field on
[`AzureProviderConfig`](https://github.com/finos/open-resource-broker/blob/main/src/orb/providers/azure/configuration/config.py)
(provider-level settings) and
[`AzureTemplate`](https://github.com/finos/open-resource-broker/blob/main/src/orb/providers/azure/domain/template/azure_template_aggregate.py)
(per-template settings). Both are pydantic models; provider config is also a
pydantic-settings model with the `ORB_AZURE_` env-var prefix, so every
provider-level field can also be set via environment variable. Nested
fields use `__` as the env-var delimiter, e.g.
`ORB_AZURE_CYCLECLOUD__URL=https://cyclecloud.example.com`.

## Provider configuration

| Field             | Type            | Default     | Env var                 | Description                                                                 |
|-------------------|-----------------|-------------|--------------------------|------------------------------------------------------------------------------|
| `provider_type`   | `str`           | `"azure"`   | `ORB_AZURE_PROVIDER_TYPE`| Provider type identifier.                                                    |
| `region`          | `str`           | `"eastus2"` | `ORB_AZURE_REGION`       | Azure location slug. Azure-native `location` input is accepted and normalised onto this field; the two cannot conflict. |
| `subscription_id` | `str \| None`   | `None`      | `ORB_AZURE_SUBSCRIPTION_ID` | Azure subscription ID (UUID). Validated as `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`. |
| `resource_group`  | `str \| None`   | `None`      | `ORB_AZURE_RESOURCE_GROUP`  | Default resource group for created resources. 1-90 characters; allowed characters are alphanumeric, `_`, `-`, `.`, `(`, `)`, `[`, `]`. |
| `client_id`       | `str \| None`   | `None`      | `ORB_AZURE_CLIENT_ID`    | Managed-identity client ID, used to select a specific user-assigned identity when more than one is attached. |
| `max_retries`     | `int`           | `3`         | `ORB_AZURE_MAX_RETRIES`  | Maximum SDK retry attempts for transient ARM errors.                        |
| `connect_timeout` | `int`           | `30`        | `ORB_AZURE_CONNECT_TIMEOUT` | Connection timeout for ARM API calls, in seconds.                        |
| `read_timeout`    | `int`           | `60`        | `ORB_AZURE_READ_TIMEOUT`   | Read timeout for ARM API calls, in seconds.                               |

### CycleCloud connection (`cyclecloud`)

Nested model, only needed when using the `CycleCloud` `provider_api`.

| Field             | Type            | Default | Env var                              | Description                                                                 |
|-------------------|-----------------|---------|----------------------------------------|------------------------------------------------------------------------------|
| `url`             | `str \| None`   | `None`  | `ORB_AZURE_CYCLECLOUD__URL`           | CycleCloud REST API base URL.                                                |
| `credential_path` | `str \| None`   | `None`  | `ORB_AZURE_CYCLECLOUD__CREDENTIAL_PATH` | Path to a JSON file containing CycleCloud credentials and optional auth overrides. See [Authentication](auth.md#cyclecloud-authentication). |
| `verify_ssl`      | `bool \| None`  | `None`  | `ORB_AZURE_CYCLECLOUD__VERIFY_SSL`    | Whether to verify TLS certificates for CycleCloud API calls. `None` defers to the credential-file value or the secure default. |
| `auth_mode`       | `str \| None`   | `None`  | `ORB_AZURE_CYCLECLOUD__AUTH_MODE`     | CycleCloud auth mode override, e.g. `"basic"` or `"bearer"`.                 |
| `aad_scope`       | `str \| None`   | `None`  | `ORB_AZURE_CYCLECLOUD__AAD_SCOPE`     | Microsoft Entra ID (AAD) scope used to resolve a bearer token for CycleCloud. |

Inline `username`/`password`/`bearer_token` fields are rejected at
construction time; CycleCloud credentials must come from the file at
`credential_path`.

### Worked provider config example

```json
{
  "providers": [
    {
      "name": "azure_main",
      "type": "azure",
      "config": {
        "subscription_id": "11111111-2222-3333-4444-555555555555",
        "resource_group": "orb-compute",
        "region": "eastus2",
        "client_id": "66666666-7777-8888-9999-000000000000",
        "max_retries": 5
      }
    }
  ]
}
```

### CLI equivalents

`orb provider add` exposes the same fields as flags:

```bash
orb provider add --provider-type azure \
  --azure-subscription-id 11111111-2222-3333-4444-555555555555 \
  --azure-resource-group orb-compute \
  --azure-location eastus2 \
  --azure-client-id 66666666-7777-8888-9999-000000000000
```

CycleCloud-specific flags: `--azure-cyclecloud-url`,
`--azure-cyclecloud-credential-path`, `--azure-cyclecloud-auth-mode`,
`--azure-cyclecloud-aad-scope`, and `--azure-cyclecloud-verify-ssl` /
`--azure-cyclecloud-no-verify-ssl` (mutually exclusive).

## Provider capabilities

| `provider_api` | Spot support | On-demand support | Max instances |
|-----------------|--------------|---------------------|-----------------|
| `VMSS`          | yes          | yes                  | 1000            |
| `VMSSUniform`   | yes          | yes                  | 1000            |
| `SingleVM`      | yes          | yes                  | 1000            |
| `CycleCloud`    | no           | yes                  | -               |

The Azure provider strategy supports `create_instances`,
`terminate_instances`, `get_instance_status`, `describe_resource_instances`,
`validate_template`, `get_available_templates`, and `health_check`. It does
**not** support `start_instances` or `stop_instances` on any
`provider_api` — there is no equivalent to AWS/Google Cloud start/stop in the current
Azure provider; terminating and re-acquiring is the only way to resize a
pool today.

## Template fields

Every field below lives on `AzureTemplate`. Fields accept both
`snake_case` and `camelCase` keys (e.g. `resource_group` or
`resourceGroup`); the tables use the canonical `snake_case` name.

### Identity and location

| Field             | Type     | Required | Description                                                                 |
|-------------------|----------|----------|-------------------------------------------------------------------------------|
| `provider_api`    | enum     | no (default `VMSS`) | `VMSS`, `VMSSUniform`, `SingleVM`, or `CycleCloud`.               |
| `resource_group`  | string   | yes      | Azure resource group for the created resources.                              |
| `location`        | string   | yes      | Azure location, e.g. `"eastus2"`. `region` is accepted as an alias.          |
| `subscription_id` | string   | no       | Overrides the provider-level subscription for this template.                 |

### VMSS configuration

| Field                           | Type    | Default      | Description                                                                 |
|----------------------------------|---------|--------------|-------------------------------------------------------------------------------|
| `vmss_name`                      | string  | auto-generated | Explicit VMSS name.                                                         |
| `orchestration_mode`             | enum    | `Flexible`   | `Flexible` or `Uniform`. `VMSSUniform` requires `orchestration_mode="Uniform"`. |
| `platform_fault_domain_count`    | int     | `None`       | Fault domain count for Flexible orchestration (1-5).                        |
| `single_placement_group`         | bool    | `False`      | Restrict the VMSS to a single placement group (max ~100 VMs).               |
| `overprovision`                  | bool    | `False`      | Enable overprovisioning. Valid only for Uniform orchestration.              |
| `upgrade_policy_mode`            | enum    | `Manual`     | `Manual`, `Rolling`, or `Automatic`.                                        |

### VM size selection

| Field                  | Type              | Description                                                                 |
|-------------------------|-------------------|-------------------------------------------------------------------------------|
| `vm_size`               | string (required) | Primary Azure VM size, e.g. `"Standard_D4s_v5"`.                             |
| `vm_sizes`              | list[string]       | Additional unranked VM size candidates for instance mix.                     |
| `vm_size_preferences`   | list of `{name, rank}` | Ranked VM size candidates. Only valid with `vmss_allocation_strategy="Prioritized"`. Mutually exclusive with `vm_sizes`. |

### Image

| Field   | Type                                      | Description                                                                 |
|---------|--------------------------------------------|-------------------------------------------------------------------------------|
| `image` | `{publisher, offer, sku, version}` or `{image_id}` | Marketplace image (publisher/offer/sku, `version` defaults to `"latest"`) or a custom/gallery image by `image_id`. Exactly one form must be provided. |

### Pricing and Spot

| Field                          | Type    | Default    | Description                                                                 |
|----------------------------------|---------|------------|-------------------------------------------------------------------------------|
| `priority`                       | enum    | `Regular`  | `Regular`, `Spot`, or legacy `Low`.                                           |
| `eviction_policy`                | enum    | `None`     | `Deallocate` or `Delete`. Required when `priority="Spot"` (defaults to `Deallocate` if a `spot_percentage` implies Spot). Only valid for `Spot`/`Low`. |
| `billing_profile_max_price`      | float   | `None`     | Max price per hour for Spot VMs. `-1` means pay up to on-demand price. Only valid for `Spot`. |
| `spot_percentage`                | int     | `None`     | Desired percentage of Spot VMs above `base_regular_priority_count`, mapped to VMSS `priorityMixPolicy`. Requires `priority="Spot"`, Flexible orchestration, and `single_placement_group=False`. |
| `base_regular_priority_count`    | int     | `0`        | Minimum regular-priority VM count to keep when using Spot Priority Mix.      |
| `vmss_allocation_strategy`       | enum    | `None`     | `LowestPrice`, `CapacityOptimized`, or `Prioritized` (instance-mix allocation). |
| `spot_restore_enabled`           | bool    | `False`    | Enable Spot Try-Restore to automatically re-create evicted instances.        |
| `spot_restore_timeout`           | string  | `None`     | ISO 8601 duration for the spot restore timeout, e.g. `"PT1H"`.               |
| `spot_placement_score_enabled`   | bool    | `False`    | Enable Azure Spot Placement Score planning before launch. Requires at least two candidate VM sizes. |
| `placement_split_strategy`       | enum    | `hybrid`   | `greedy` or `hybrid`. How spot placement score launches split capacity across candidates. |
| `placement_primary_share_percent`| int     | `80`       | Capacity percentage assigned to the top placement candidate.                 |
| `placement_regions`              | list[string] | `[]`  | Azure regions considered for spot placement score planning.                  |
| `placement_zones`                | list[string] | `[]`  | Azure zones considered for spot placement score planning.                    |

### Availability

| Field                            | Type           | Description                                                                 |
|------------------------------------|----------------|-------------------------------------------------------------------------------|
| `zones`                            | list[string]   | Availability zones, e.g. `["1", "2", "3"]`.                                  |
| `zone_balance`                     | bool           | Strictly balance instances across zones. Requires at least one zone.         |
| `proximity_placement_group_id`     | ARM resource ID | ID of a proximity placement group.                                          |
| `capacity_reservation_group_id`    | ARM resource ID | ID of a capacity reservation group. Mutually exclusive with `priority="Spot"`. |

### OS and storage

| Field          | Type                 | Description                                                                 |
|-----------------|----------------------|-------------------------------------------------------------------------------|
| `os_disk`       | `AzureOSDiskConfig`  | `disk_size_gb`, `storage_account_type` (default `Premium_LRS`), `caching` (default `ReadWrite`), `ephemeral_os_disk`, `ephemeral_placement`. |
| `data_disks`    | list of `{lun, disk_size_gb, storage_account_type, caching}` | Additional data disks (`lun` 0-63).                   |

### Networking

| Field                                      | Type           | Description                                                                 |
|----------------------------------------------|----------------|-------------------------------------------------------------------------------|
| `network_config.subnet_id`                   | ARM resource ID (required) | Full ARM resource ID of the subnet.                             |
| `network_config.network_security_group_id`   | ARM resource ID | NSG to attach.                                                               |
| `network_config.accelerated_networking`      | bool           | Enable accelerated networking (SR-IOV).                                      |
| `network_config.public_ip_enabled`           | bool           | Default `False`. Attach a public IP configuration.                           |
| `network_config.load_balancer_backend_pool_ids` | list[ARM ID] | Existing Load Balancer backend pools to attach.                              |
| `network_config.load_balancer_inbound_nat_pool_ids` | list[ARM ID] | Existing Load Balancer inbound NAT pools to attach.                     |
| `network_config.application_gateway_backend_pool_ids` | list[ARM ID] | Existing Application Gateway backend pools to attach.                |

If `network_config` is omitted, the handler falls back to the shared
`subnet_ids` template field; supplying more than one subnet ID there is an
error (Azure needs exactly one subnet per template).

### Security

| Field                     | Type   | Description                                                                 |
|----------------------------|--------|-------------------------------------------------------------------------------|
| `security_type`            | enum   | `Standard`, `TrustedLaunch`, or `ConfidentialVM`.                             |
| `secure_boot_enabled`      | bool   | Required when `security_type="TrustedLaunch"`. Defaults to `True` for Trusted Launch unless set. |
| `vtpm_enabled`             | bool   | Required when `security_type="TrustedLaunch"`. Defaults to `True` for Trusted Launch unless set. |
| `encryption_at_host`       | bool   | Enable host-based encryption for all disks.                                  |
| `disk_encryption_set_id`   | ARM resource ID | Customer-managed key (CMK) disk encryption set.                        |

### Identity and access

| Field                        | Type         | Default        | Description                                                                 |
|--------------------------------|--------------|----------------|-------------------------------------------------------------------------------|
| `admin_username`               | string       | `"azureuser"`  | VM admin username.                                                           |
| `ssh_key_name`                  | string       | `None`         | Name of an Azure SSH Public Key resource (`Microsoft.Compute/sshPublicKeys`) in the same resource group. The handler resolves the key data at provisioning time. |
| `ssh_public_keys`               | list[string] | `[]`           | Inline SSH public key strings. Prefer `ssh_key_name` where possible.         |
| `user_assigned_identity_ids`    | list[ARM ID] | `[]`           | User-assigned managed identities to attach.                                  |
| `system_assigned_identity`      | bool         | `False`        | Enable a system-assigned managed identity.                                   |

One of `ssh_key_name` or `ssh_public_keys` is required for every
`provider_api` except `CycleCloud` — Azure Linux VMs never fall back to
password authentication.

### Bootstrapping

| Field                | Type              | Description                                                                 |
|-----------------------|-------------------|-------------------------------------------------------------------------------|
| `custom_data`         | string             | Base64-encoded custom data / cloud-init payload.                             |
| `extension_profile`   | list[dict]         | VMSS VM extension definitions (custom script, monitoring agents, etc.).      |

### Freeform pass-through

| Field                     | Type   | Description                                                                 |
|-----------------------------|--------|-------------------------------------------------------------------------------|
| `provider_api_spec`         | dict   | Raw Azure request payload override/overlay. Mutually exclusive with `provider_api_spec_file`. |
| `provider_api_spec_file`    | string | Path to a JSON native spec file.                                             |
| `node_attributes`           | dict   | Additional provider properties. Cannot replace fields the ARM payload mapper already manages. |

### CycleCloud fields

Only used when `provider_api="CycleCloud"`.

| Field          | Type   | Default     | Description                                                                 |
|-----------------|--------|-------------|-------------------------------------------------------------------------------|
| `cluster_name`  | string | required    | Name of an existing CycleCloud cluster to add nodes to.                      |
| `node_array`    | string | `"execute"` | CycleCloud node array (partition) to target, e.g. `"execute"`, `"hpc"`, `"htc"`. |

CycleCloud templates do not require `image`, `ssh_key_name`, or
`ssh_public_keys` — CycleCloud manages SSH access and the node image
internally.

## Worked template examples

### VMSS, on-demand Linux fleet

```json
{
  "template_id": "azure-vmss-linux-basic",
  "name": "Azure VMSS Linux Basic",
  "provider_type": "azure",
  "provider_api": "VMSS",
  "vm_size": "Standard_D4s_v5",
  "resource_group": "my-resource-group",
  "location": "eastus2",
  "image": {
    "publisher": "Canonical",
    "offer": "0001-com-ubuntu-server-jammy",
    "sku": "22_04-lts-gen2",
    "version": "latest"
  },
  "ssh_key_name": "orb-build-key",
  "network_config": {
    "subnet_id": "/subscriptions/<sub>/resourceGroups/my-resource-group/providers/Microsoft.Network/virtualNetworks/orb-vnet/subnets/orb-subnet"
  },
  "max_instances": 2
}
```

### VMSS, Spot instances

```json
{
  "template_id": "azure-vmss-spot",
  "name": "Azure VMSS Spot Instances",
  "provider_type": "azure",
  "provider_api": "VMSS",
  "vm_size": "Standard_D4s_v5",
  "resource_group": "my-resource-group",
  "location": "eastus2",
  "priority": "Spot",
  "eviction_policy": "Deallocate",
  "billing_profile_max_price": -1.0,
  "image": {
    "publisher": "Canonical",
    "offer": "0001-com-ubuntu-server-jammy",
    "sku": "22_04-lts-gen2",
    "version": "latest"
  },
  "ssh_key_name": "orb-build-key",
  "network_config": {
    "subnet_id": "/subscriptions/<sub>/resourceGroups/my-resource-group/providers/Microsoft.Network/virtualNetworks/orb-vnet/subnets/orb-subnet"
  },
  "max_instances": 5
}
```

### CycleCloud HPC node array

```json
{
  "template_id": "azure-cyclecloud-hpc",
  "name": "Azure CycleCloud HPC Cluster",
  "provider_type": "azure",
  "provider_api": "CycleCloud",
  "vm_size": "Standard_HB120rs_v3",
  "resource_group": "my-resource-group",
  "location": "eastus2",
  "cluster_name": "my-hpc-cluster",
  "node_array": "hpc",
  "max_instances": 100
}
```

## Environment-variable cheat sheet

```bash
# Provider identity and targeting
export ORB_AZURE_SUBSCRIPTION_ID="11111111-2222-3333-4444-555555555555"
export ORB_AZURE_RESOURCE_GROUP="orb-compute"
export ORB_AZURE_REGION="eastus2"
export ORB_AZURE_CLIENT_ID="66666666-7777-8888-9999-000000000000"

# Retry / timeout
export ORB_AZURE_MAX_RETRIES="5"
export ORB_AZURE_CONNECT_TIMEOUT="30"
export ORB_AZURE_READ_TIMEOUT="60"

# CycleCloud (nested fields use the "__" delimiter)
export ORB_AZURE_CYCLECLOUD__URL="https://cyclecloud.example.com"
export ORB_AZURE_CYCLECLOUD__CREDENTIAL_PATH="/etc/orb/cyclecloud-creds.json"
```
