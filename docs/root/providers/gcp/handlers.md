# Google Cloud handlers

The Google Cloud provider ships two handlers, selected by `provider_api`.

| `provider_api` | Handler class                   | Native resource(s)                                      | Selective release | Start/stop |
|------------------|------------------------------------|--------------------------------------------------------------|---------------------|--------------|
| `MIG`             | `GCPManagedInstanceGroupHandler`  | Instance template + Managed Instance Group (regional or zonal) | yes (per instance)   | no            |
| `SingleVM`        | `GCPSingleVMHandler`              | One or more standalone Compute Engine instances                | yes (per instance)   | yes           |

## Decision tree

1. Does the workload need to run as a single, individually addressable VM
   rather than a scalable, policy-managed pool?
   * Yes - use **`SingleVM`**.
2. Otherwise - use **`MIG`**, Google Cloud's native scalable instance pool.

## `MIG`

The MIG handler creates one Compute Engine **instance template** per ORB
request, then one **Managed Instance Group** (regional or zonal,
depending on `mig_scope`) referencing that template, with
`target_size = request.requested_count`.

* **Acquire** - submits the instance template insert, waits for it to
  complete, then submits the MIG insert (regional via
  `create_regional_mig`, zonal via `create_zonal_mig`) with
  `base_instance_name = template.template_id`. For regional MIGs with
  `zones` set, a `DistributionPolicy` pins the group to those zones. If
  either step fails, both the instance template and any partially
  created MIG are rolled back.
* **Release** - when the requested instance IDs cover every member of a
  MIG, the handler deletes the MIG and its backing instance template.
  When only some members are requested, it deletes just those managed
  instances and leaves the MIG (and its target size) otherwise intact.
* **Start/stop** - not supported. Calling start or stop against a
  `MIG`-backed request returns a `GCPMutationOutcome` with a warning
  ("MIG-managed instances follow group policy; start/stop is not
  supported directly") and makes no API call. MIG membership and health
  are governed by the group's own autohealing/update policy, not by
  ORB's start/stop operations.

### Template fields the MIG handler honours

In addition to the shared fields in
[Configuration reference](configuration.md#template-fields):
`mig_scope` (`regional` default, or `zonal`), `zones` (required for
`zonal`; optional distribution hint for `regional`), `mig_name`
(auto-generated as `orb-mig-<template_id>-<hex>` when omitted), and
`instance_template_name_prefix` (default `"orb"`, used to name the
generated instance template as `<prefix>-<template_id>-<hex>`).

## `SingleVM`

The SingleVM handler creates `request.requested_count` standalone
Compute Engine instances directly — no instance template or group is
involved.

* **Acquire** - submits one `create_instance` call per requested VM,
  named `gcp-<template_id>-<hex>`. Per-instance failures are collected as
  partial failures rather than aborting the whole batch; the result
  reports `submitted_count` and `partial_failure` alongside the
  successfully created resource IDs.
* **Release** - deletes the named instance(s) directly.
* **Start/stop** - supported. `start_instances`/`stop_instances` map
  directly to the Compute Engine `start`/`stop` API calls, run
  per-instance.

### Template fields the SingleVM handler honours

`SingleVM` templates require `max_machines == 1` and exactly one explicit
`zones` entry — Google Cloud's `SingleVM` API operates on one VM in one zone per
template, by design.

## Capabilities summary

| `provider_api` | Spot | On-demand | Async operations | Start/stop | Max instances |
|------------------|------|-------------|---------------------|--------------|------------------|
| `MIG`             | yes  | yes          | yes                  | no            | 1000              |
| `SingleVM`         | yes  | yes          | no                   | yes           | 1                 |

## Choosing a `provider_api` at template-generate time

```bash
# MIG (default)
orb templates generate --provider-type gcp --provider-api MIG

# SingleVM
orb templates generate --provider-type gcp --provider-api SingleVM
```

Existing templates can be retargeted by editing the `provider_api` field
in-place. Handler dispatch happens at request time, not at
template-create time.
