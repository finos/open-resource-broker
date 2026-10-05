# GCP provider - authentication

The GCP provider authenticates exclusively with
[Application Default Credentials (ADC)](https://cloud.google.com/docs/authentication/application-default-credentials).
There is no provider-config field for credentials, no service-account-key
file setting, and no credential-selection flag — `GCPProviderConfig` only
carries `project_id`, `region`, `zones`, `network`, and `subnetwork`.

## How ORB resolves credentials

Every Compute Engine client (`InstancesClient`, `InstanceTemplatesClient`,
`InstanceGroupManagersClient`, `RegionInstanceGroupManagersClient`,
`ImagesClient`) is constructed with no explicit credentials argument. The
underlying `google-cloud-compute` client library resolves ADC itself, in
its standard order:

1. `GOOGLE_APPLICATION_CREDENTIALS` environment variable, pointing at a
   service-account key file.
2. The credentials set by `gcloud auth application-default login` (local
   development).
3. The attached service account on Compute Engine, GKE (via Workload
   Identity), Cloud Run, or Cloud Functions metadata server.

ORB's own health check (`check_health`) calls `google.auth.default()`
directly and refreshes the resulting credential against
`google.auth.transport.requests.Request()` to confirm ADC is reachable
before reporting the provider healthy.

### Single identity, by design

GCP's ADC model resolves to exactly one credential per process. Unlike
the AWS provider (multiple named profiles) or the Azure provider
(`client_id` to select a specific managed identity), the GCP provider has
no mechanism to use a different identity per provider instance or per
template. If you need to operate against more than one GCP project or
service account, run separate ORB deployments (or processes) each with
their own ADC source.

## Required IAM roles

The identity ORB runs as needs, at minimum, the Compute Engine permissions
to create and delete instances, instance templates, and managed instance
groups in `project_id`:

* `roles/compute.instanceAdmin.v1` is the simplest broad grant covering
  instance, instance-template, and MIG lifecycle operations.
* If instances are attached to a service account
  (`service_account_email`), the identity running ORB also needs
  `roles/iam.serviceAccountUser` on that service account (GCP requires
  this to allow attaching it to a new instance).

## Setting up ADC

### Local development

```bash
gcloud auth application-default login
```

### Service account key file

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/etc/orb/gcp-service-account.json"
```

### Running on GCP compute (recommended for production)

Attach a service account to the GCE instance, GKE node pool (with
Workload Identity), or Cloud Run service running ORB. No environment
variable is required; the metadata server supplies credentials
automatically.

## Troubleshooting

| Symptom                                                                 | Likely cause                                                        | Fix                                                                         |
|----------------------------------------------------------------------------|--------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| Health check reports `GCP credential check failed: ...` with the ADC hint   | No ADC source available in the current environment                       | Set `GOOGLE_APPLICATION_CREDENTIALS`, run `gcloud auth application-default login`, or attach a service account via workload identity. |
| `GCPAuthorizationError` on create/delete operations                        | The ADC identity lacks IAM permissions on `project_id`                    | Grant `roles/compute.instanceAdmin.v1` (or a narrower custom role with equivalent permissions). |
| Instance creation fails with a service-account attachment error            | ADC identity lacks `roles/iam.serviceAccountUser` on `service_account_email` | Grant `roles/iam.serviceAccountUser` on the target service account.         |
| `GCPQuotaExceededError`                                                    | Compute Engine quota exhausted for the project/region                     | Request a quota increase in the Google Cloud console, or reduce `max_machines`. |
| `GCPRateLimitError`                                                        | Compute Engine API throttling (`TooManyRequests`)                         | Retried automatically up to `max_retries`; reduce request concurrency if it persists. |
