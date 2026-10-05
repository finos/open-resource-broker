# Google Cloud provider - configuration reference

This page documents every field on
[`GCPProviderConfig`](https://github.com/finos/open-resource-broker/blob/main/src/orb/providers/gcp/configuration/config.py)
(provider-level settings) and
[`GCPTemplate`](https://github.com/finos/open-resource-broker/blob/main/src/orb/providers/gcp/domain/template/gcp_template_aggregate.py)
(per-template settings). Provider config is a pydantic-settings model
with the `ORB_GCP_` env-var prefix, so every provider-level field can also
be set via environment variable.

## Provider configuration

| Field             | Type           | Default         | Env var              | Description                                                                 |
|-------------------|-----------------|-----------------|------------------------|--------------------------------------------------------------------------------|
| `provider_type`   | `str`           | `"gcp"`         | `ORB_GCP_PROVIDER_TYPE`| Provider type identifier.                                                     |
| `project_id`      | `str` (required)| -               | `ORB_GCP_PROJECT_ID`   | Google Cloud project ID used for all Compute Engine operations. Must match the canonical Google Cloud project ID format (lowercase letters, digits, hyphens; 6-30 characters). |
| `region`          | `str`           | `"us-central1"` | `ORB_GCP_REGION`       | Default Google Cloud region, e.g. `"us-central1"` or `"europe-west4"`.                |
| `zones`           | `list[str]`     | `[]`            | `ORB_GCP_ZONES`        | Optional preferred zones, e.g. `["us-central1-a", "us-central1-b"]`.         |
| `network`         | `str \| None`   | `None`          | `ORB_GCP_NETWORK`      | Default VPC network self-link or name.                                       |
| `subnetwork`      | `str \| None`   | `None`          | `ORB_GCP_SUBNETWORK`   | Default subnetwork self-link or name.                                        |
| `max_retries`     | `int`           | `3`             | `ORB_GCP_MAX_RETRIES`  | Maximum retry attempts for Google Cloud API calls.                                    |
| `connect_timeout` | `int`           | `30`            | `ORB_GCP_CONNECT_TIMEOUT` | Connection timeout, in seconds.                                           |
| `read_timeout`    | `int`           | `60`            | `ORB_GCP_READ_TIMEOUT` | Read timeout, in seconds.                                                     |

There is no credential-related field on `GCPProviderConfig` — the Google Cloud
provider authenticates exclusively via Application Default Credentials.
See [Authentication](auth.md).

### Worked provider config example

```json
{
  "providers": [
    {
      "name": "gcp_main",
      "type": "gcp",
      "config": {
        "project_id": "my-gcp-project",
        "region": "us-central1",
        "zones": ["us-central1-a", "us-central1-b"],
        "network": "projects/my-gcp-project/global/networks/orb-vpc",
        "subnetwork": "projects/my-gcp-project/regions/us-central1/subnetworks/orb-subnet"
      }
    }
  ]
}
```

### CLI equivalents

```bash
orb provider add --provider-type gcp \
  --gcp-project-id my-gcp-project \
  --gcp-region us-central1 \
  --gcp-zones us-central1-a,us-central1-b \
  --gcp-network projects/my-gcp-project/global/networks/orb-vpc \
  --gcp-subnetwork projects/my-gcp-project/regions/us-central1/subnetworks/orb-subnet
```

Service account flags (`--gcp-service-account-email`,
`--gcp-service-account-scopes`) are also available on `orb provider add`
but map to template defaults rather than the provider config above — see
[Template fields](#template-fields).

## Provider capabilities

| `provider_api` | Spot | On-demand | Async operations | Start/stop | Max instances |
|------------------|------|-------------|---------------------|--------------|------------------|
| `MIG`             | yes  | yes          | yes                  | no            | 1000              |
| `SingleVM`         | yes  | yes          | no                   | yes           | 1                 |

The Google Cloud provider strategy supports `create_instances`,
`terminate_instances`, `cleanup_machine_resources`,
`get_instance_status`, `describe_resource_instances`,
`validate_template`, `get_available_templates`, `health_check`,
`resolve_image`, `start_instances`, and `stop_instances`. Start/stop only
takes effect for `SingleVM`; calling it against a `MIG`-backed request
returns a warning and performs no action, because MIG-managed instances
follow the group's own lifecycle policy.

## Template fields

Every field below lives on `GCPTemplate`.

### Identity and placement

| Field          | Type               | Required | Description                                                                 |
|-----------------|--------------------|----------|--------------------------------------------------------------------------------|
| `provider_api`  | enum               | no (default `MIG`) | `MIG` or `SingleVM`.                                                 |
| `project_id`    | string             | yes      | Google Cloud project ID (canonical format).                                           |
| `region`        | string             | yes      | Google Cloud region, e.g. `"us-central1"`.                                             |
| `zones`         | list[string]       | conditionally | See placement rules below.                                              |
| `mig_scope`     | enum               | no (default `regional`) | `regional` or `zonal`. Only used for `provider_api="MIG"`.    |

Placement rules enforced at validation time:

* `provider_api="MIG"`, `mig_scope="zonal"` requires exactly one zone.
* `provider_api="MIG"`, `mig_scope="regional"` should use at least two
  zones when zones are specified at all (an empty list lets Google Cloud choose).
* `provider_api="SingleVM"` requires `max_machines == 1` and exactly one
  explicit zone.

### Compute

| Field          | Type   | Default | Description                                                                 |
|-----------------|--------|---------|--------------------------------------------------------------------------------|
| `machine_type`  | string (required) | - | Google Cloud machine type, e.g. `"e2-standard-4"`. Alias: `instance_type`.      |

### Networking

| Field          | Type           | Description                                                                 |
|-----------------|----------------|--------------------------------------------------------------------------------|
| `network`       | string \| None | VPC network self-link or name. Falls back to the provider-level `network`.    |
| `subnetwork`    | string \| None | Subnetwork self-link or name. Falls back to the provider-level `subnetwork`.  |
| `network_tags`  | list[string]    | Network tags applied to created instances (used for firewall targeting).      |

### Identity and access

| Field                      | Type               | Default                                             | Description                                                                 |
|------------------------------|--------------------|------------------------------------------------------|--------------------------------------------------------------------------------|
| `service_account_email`      | string \| None      | `None`                                                 | Service account attached to created instances.                               |
| `service_account_scopes`     | list[string]        | `["https://www.googleapis.com/auth/compute"]`         | OAuth scopes for the attached service account. Every scope must start with `https://www.googleapis.com/auth/` and the list cannot be empty. |

### Image

| Field                   | Type           | Description                                                                 |
|---------------------------|----------------|--------------------------------------------------------------------------------|
| `source_image`            | string \| None | Full image self-link. Takes priority over `source_image_family`/`source_image_project` when both are present. |
| `source_image_family`     | string \| None | Image family, e.g. `"debian-12"`. Must be paired with `source_image_project`. |
| `source_image_project`    | string \| None | Project owning the image family, e.g. `"debian-cloud"`.                      |

A template must supply either `source_image`, or both
`source_image_family` and `source_image_project`.

### Disks

| Field                | Type              | Description                                                                 |
|------------------------|-------------------|---------------------------------------------------------------------------------|
| `boot_disk_type`       | string (disk type resource name) | e.g. `"pd-balanced"`, `"pd-ssd"`, `"pd-standard"`.             |
| `boot_disk_size_gb`    | int (`>= 10`)      | Boot disk size in GiB.                                                        |

### Labels and naming

| Field                           | Type           | Description                                                                 |
|-----------------------------------|----------------|--------------------------------------------------------------------------------|
| `labels`                          | dict[str, str] | Labels applied to created resources.                                          |
| `mig_name`                        | string \| None | Explicit MIG name. Auto-generated (`orb-mig-<template_id>-<hex>`) when omitted. |
| `instance_template_name_prefix`   | string \| None | Prefix for the generated instance template name. Default `"orb"`.             |

### Unsupported fields

| Field       | Behaviour                                                                        |
|--------------|-------------------------------------------------------------------------------------|
| `key_name`   | Rejected with a validation error. Google Cloud does not support named SSH key pairs.         |

## Worked template examples

### Regional MIG, Debian 12

```json
{
  "template_id": "gcp-mig-example",
  "provider_name": "gcp_main",
  "provider_api": "MIG",
  "project_id": "my-gcp-project",
  "region": "us-central1",
  "zones": ["us-central1-a", "us-central1-b"],
  "machine_type": "e2-standard-4",
  "source_image_family": "debian-12",
  "source_image_project": "debian-cloud",
  "max_machines": 2
}
```

### Standalone VM (SingleVM)

```json
{
  "template_id": "gcp-single-vm-example",
  "provider_name": "gcp_main",
  "provider_api": "SingleVM",
  "project_id": "my-gcp-project",
  "region": "us-central1",
  "zones": ["us-central1-a"],
  "machine_type": "e2-standard-4",
  "source_image_family": "debian-12",
  "source_image_project": "debian-cloud",
  "max_machines": 1
}
```

## Environment-variable cheat sheet

```bash
export ORB_GCP_PROJECT_ID="my-gcp-project"
export ORB_GCP_REGION="us-central1"
export ORB_GCP_ZONES='["us-central1-a","us-central1-b"]'
export ORB_GCP_NETWORK="projects/my-gcp-project/global/networks/orb-vpc"
export ORB_GCP_SUBNETWORK="projects/my-gcp-project/regions/us-central1/subnetworks/orb-subnet"
export ORB_GCP_MAX_RETRIES="5"
```
