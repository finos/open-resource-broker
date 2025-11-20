## Attribute-Based Instance Selection (ABIS) Support

This document summarizes how the plugin supports AWS attribute-based instance selection (ABIS) across schedulers and handlers.

### Overview

ABIS lets you describe compute requirements (CPU, memory, hardware attributes) instead of enumerating instance types. Templates can now include an `abis_instance_requirements` block (snake_case) or `abisInstanceRequirements` (camelCase) mirroring the `InstanceRequirements` structure from `EC2 Fleet`, `Spot Fleet`, and `ASG`

Minimum required fields (default templates use snake_case; the plugin converts to AWS casing automatically):
- `vcpu_count -> { "min": int, "max": int }`
- `memory_mib -> { "min": int, "max": int }`

All other keys from the AWS API are optional (e.g., `CpuManufacturers`, `LocalStorageTypes`, `AcceleratorTypes`).

### Template Configuration

**Default scheduler (snake_case)**
- File: `config/templates.json`
- Key: `abis_instance_requirements`

```json
{
  "abis_instance_requirements": {
    "vcpu_count": { "min": 2, "max": 4 },
    "memory_mib": { "min": 4096, "max": 8192 },
    "cpu_manufacturers": ["intel", "amd"],
    "local_storage": "required",
    "allowed_instance_types": ["m6i.*", "c7g.*"]
  }
}
```

**HostFactory scheduler (camelCase)**
- File: `config/awsprov_templates.json` (and generated run templates)
- Key: `abisInstanceRequirements`

```json
{
  "abisInstanceRequirements": {
    "VCpuCount": { "Min": 1, "Max": 2 },
    "MemoryMiB": { "Min": 2048, "Max": 4096 },
    "LocalStorage": "required"
  }
}
```

The scheduler strategies normalize these keys so `AWSTemplate.abis_instance_requirements` always contains the structured Pydantic model.

### Handler Behavior

| Handler | Configuration impact |
|---------|----------------------|
| **EC2 Fleet** | `_create_fleet_config_legacy` swaps instance-type overrides with `InstanceRequirements` overrides (one per subnet if provided). This feeds the `InstanceRequirements` block directly into EC2 Fleet API calls. |
| **Spot Fleet** | `_create_spot_fleet_config_legacy` mirrors the EC2 Fleet behavior: when ABIS data exists, LaunchTemplate overrides contain `InstanceRequirements` instead of enumerated types. |
| **ASG** | `_create_asg_config_legacy` emits a `MixedInstancesPolicy` that references the launch template and supplies the `InstanceRequirements` override so ASG can resolve matching instance types at scale. When no ABIS block exists, the handler falls back to the previous single LaunchTemplate configuration. |

### When ABIS Is Absent

If templates omit the ABIS block the handlers retain legacy behavior:
- EC2/Spot Fleets use weighted instance-type overrides plus subnet permutations.
- ASG uses the base launch template without a mixed instances policy.

This means existing templates continue to work unchanged, while new templates can opt into ABIS for capacity-aware provisioning.
