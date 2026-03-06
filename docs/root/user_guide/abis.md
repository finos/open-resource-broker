## Attribute-Based Instance Selection (ABIS) Support

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

> Note: When an ABIS block is present, handlers ignore any explicit `vmType`/`vmTypes` (`instance_type`/`instance_types`) and let AWS select matching types from the `InstanceRequirements` payload.

### Example Workflow (HostFactory scheduler)

1. Add an ABIS block to a HostFactory template (camelCase) in `config/awsprov_templates.json`:

```json
{
  "template_id": "ABIS_DEMO",
  "provider_api": "EC2Fleet",
  "abisInstanceRequirements": {
    "VCpuCount": { "Min": 2, "Max": 4 },
    "MemoryMiB": { "Min": 4096, "Max": 8192 },
    "CpuManufacturers": ["intel", "amd"],
    "LocalStorage": "required"
  }
}
```

2. The scheduler strategy normalizes this to `abis_instance_requirements` internally.
3. The EC2 Fleet handler injects the normalized `InstanceRequirements` into the fleet launch template overrides (one per subnet if multiple subnets are provided).
4. AWS selects any matching instance type at provisioning time; no explicit `instance_types` list is required. If the block is absent, the handler falls back to the legacy explicit instance list.

### When ABIS Is Absent

If templates omit the ABIS block the handlers retain legacy behavior:
- EC2/Spot Fleets use weighted instance-type overrides plus subnet permutations.
- ASG uses the base launch template without a mixed instances policy.

This means existing templates continue to work unchanged, while new templates can opt into ABIS for capacity-aware provisioning.
