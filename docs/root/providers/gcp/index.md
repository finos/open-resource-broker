# Google Cloud (GCP) provider

The GCP provider lets ORB acquire, track, and release compute capacity
backed by Google Compute Engine, either as a Managed Instance Group (MIG)
or as standalone VM instances. It reuses the same template, request, and
machine model as the AWS, Kubernetes, and Azure providers, so callers of
the CLI, REST API, SDK, and HostFactory plugin do not need to
special-case GCP.

The provider supports two provisioning shapes (`provider_api` values):

| `provider_api` | Compute Engine resource(s)                              | Start/stop | Max instances |
|------------------|-------------------------------------------------------------|--------------|------------------|
| `MIG`            | Instance template + Managed Instance Group (regional or zonal) | no          | 1000             |
| `SingleVM`        | A single standalone VM instance                              | yes          | 1                |

See [Handlers](handlers.md) for how to pick between them.

## Install

The GCP provider lives behind an optional install extra so operators who
only target AWS, Kubernetes, or Azure do not pay for the Google Cloud
SDKs.

```bash
pip install "orb-py[gcp]"
```

This pulls in `google-cloud-compute` and `google-auth`.

## Quick start

### 1. Authenticate

The GCP provider authenticates exclusively with
[Application Default Credentials (ADC)](https://cloud.google.com/docs/authentication/application-default-credentials) —
there is no separate ORB-level credential configuration. See
[Authentication](auth.md) for the supported ADC sources and
troubleshooting.

### 2. Configure the provider

```json
{
  "providers": [
    {
      "name": "gcp_main",
      "type": "gcp",
      "config": {
        "project_id": "my-gcp-project",
        "region": "us-central1"
      }
    }
  ]
}
```

The full set of fields lives in [Configuration reference](configuration.md).

### 3. Create a template

```bash
orb templates generate --provider-type gcp --provider-api MIG
```

This emits a template targeting the MIG handler. Set `machine_type`,
`project_id`, `region`/`zones`, and a source image, then save it. See
[Configuration reference](configuration.md#template-fields) for the full
field list and [Handlers](handlers.md) for per-handler requirements.

### 4. Request capacity

```bash
orb machines request my-gcp-template 3
```

### 5. Track and release

```bash
orb requests status <request-id>
orb machines return <machine-id> <machine-id> ...
```

Releases delete the named standalone VM(s) for `SingleVM`. For `MIG`,
releasing every member of a group deletes the Managed Instance Group and
its backing instance template; releasing a subset deletes only those
managed instances.

## AWS concepts mapped to GCP

| AWS concept                       | GCP equivalent                                                           |
|-----------------------------------|-----------------------------------------------------------------------------|
| EC2 instance                      | Compute Engine instance                                                     |
| Auto Scaling Group / EC2 Fleet    | Managed Instance Group (MIG), regional or zonal                             |
| Amazon Machine Image (AMI)        | `source_image`, or `source_image_family` + `source_image_project`           |
| Instance type                     | `machine_type`, e.g. `e2-standard-4`                                        |
| Spot / On-Demand                  | `price_type` (`"ondemand"` or `"spot"`)                                     |
| Availability Zone                 | GCP zone, e.g. `us-central1-a` (`zones` field)                             |
| EC2 key pair                      | Not supported — GCP does not use named SSH key pairs (`key_name` is rejected). |
| VPC subnet                        | `network` / `subnetwork`                                                    |
| IAM instance profile              | Attached service account (`service_account_email`, `service_account_scopes`) |
| EC2 user data                     | Not modelled by the current template; use image-baked startup scripts.       |

Key differences to keep in mind:

* GCP has exactly one credential identity per ORB process: Application
  Default Credentials. There is no equivalent to AWS multi-profile or
  Azure managed-identity-selection — if you need to target more than one
  GCP project or account, run separate ORB deployments with distinct ADC
  sources.
* Start/stop is only supported for `SingleVM`. `MIG`-managed instances
  follow group policy; ORB's `start`/`stop` operations against a MIG
  return a warning and perform no action. See
  [Provider capabilities](configuration.md#provider-capabilities).
* GCP templates reject `key_name` (named SSH key pairs) outright — GCP
  does not have that concept at the Compute Engine API level.

## What is in this section

* [Configuration reference](configuration.md) - every `GCPProviderConfig`
  and `GCPTemplate` field, with worked examples.
* [Handlers](handlers.md) - MIG and SingleVM; when to pick each.
* [Authentication](auth.md) - Application Default Credentials and
  troubleshooting.
