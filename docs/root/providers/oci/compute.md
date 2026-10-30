# OCI Compute Instances (OCICompute) Provider

The `OCICompute` handler provisions individual OCI Compute instances through the
OCI CLI. It supports VM Flex and fixed bare metal shapes, on-demand and
preemptible capacity, and worker placement in an existing compute cluster.
Every requested machine counts as one instance; OCPUs and memory size each worker.

Use [Compute Templates and Flex Sizing](templates.md) to choose a shape and
[Compute Clusters and RDMA](compute-clusters.md) for `BM.Optimized3.36` workers.
Installation, credentials, IAM policy, and deployment defaults are in the
[OCI deployment and authentication guide](../../deployment/oci-integration.md).

## Quick start

Install an OCI-enabled ORB build using Python 3.12 and install OCI CLI separately
on `PATH`. Before requesting workers, configure authentication and IAM using the
deployment guide. On an OCI broker configured for instance principals:

```bash
orb init --non-interactive --provider-type oci --oci-region us-phoenix-1 --oci-credential-source instance_principal
```

For a workstation session, use `--oci-credential-source security_token` and
`--oci-profile <session-profile>` instead. Pass `--config /path/to/config.json`
on subsequent commands when using a custom config location.

### 1. Create a template

Save this single object as `worker.json`, replacing the deployment placeholders:

```json
{
  "template_id": "oci-e5-worker",
  "name": "OCI E5 Worker",
  "provider_type": "oci",
  "provider_api": "OCICompute",
  "machine_image": "<compatible-x86-image-ocid>",
  "machine_types": {"VM.Standard.E5.Flex": 1},
  "shape_config": {"ocpus": 1, "memoryInGBs": 8},
  "subnet_ids": ["<subnet-ocid>"],
  "security_group_ids": ["<nsg-ocid>"],
  "compartment_id": "<compartment-ocid>",
  "availability_domain": "<availability-domain>",
  "capacity_type": "ondemand",
  "boot_volume_gbs": 50,
  "max_machines": 1,
  "tags": {"Environment": "dev"}
}
```

Choose a boot disk at least as large as your image requires. Add SSH public keys
and bootstrap settings for your workload, or supply them through provider
template defaults. With one enabled OCI provider, it is selected automatically;
with several providers, set `provider_name` to the intended configured instance.

```bash
orb templates create --file worker.json
orb templates validate oci-e5-worker
```

### 2. Request instances

```bash
orb machines request oci-e5-worker 1
```

### 3. Check status

```bash
orb requests status <request-id>
orb machines list --request-id <request-id>
```

### 4. Return instances

```bash
orb machines return <instance-ocid>
orb requests status <return-request-id>
```

## Template configuration

| Field | Required | Type | Description |
| --- | --- | --- | --- |
| `provider_api` | Yes | string | `OCICompute`. |
| `provider_type` | Yes for explicit routing | string | `oci`. |
| `provider_name` | With multiple provider instances | string | Name of the configured provider instance. |
| `machine_image` | Yes | string | Compatible image OCID; also accepts `image_id`. |
| `machine_types` | Yes, or another shape field | object | One shape per template. Only the first key is selected; weights do not size or diversify workers. |
| `subnet_ids` | Yes, or `subnet_id` | list[string] | Only the first subnet is selected. |
| `compartment_id` | Yes | string | Target compartment OCID. |
| `availability_domain` | For compute clusters; recommended otherwise | string | Target AD. Otherwise the handler attempts resolution from subnet/compartment information. |
| `security_group_ids` | No | list[string] | OCI NSG OCIDs attached to the worker VNIC. |
| `shape_config` | Explicit in shipped VM examples | object | `ocpus` and `memoryInGBs`; DenseIO requires a supported pair. Omit for bare metal. |
| `ocpus`, `memory_gbs` | No | number | Alternate top-level sizing inputs; override the corresponding `shape_config` values. |
| `capacity_type` | No | string | `ondemand` (default) or `preemptible`. |
| `fallback_to_ondemand` | No | boolean | Retry a failed preemptible launch as on-demand using the same shape and image. Default `false`. |
| `preemptible_preserve_boot_volume` | No | boolean | Preserve the boot disk on preemption. Default `false`; does not configure ordinary worker return. |
| `boot_volume_gbs` | No | number | Boot disk size in GB, independent of DenseIO local NVMe. |
| `max_machines` | No | integer | Maximum machines for the template; accepts `max_instances`. |
| `ssh_authorized_keys` | No | string | SSH public key text. |
| `machine_bootstrap` | No | string | Base64 cloud-init/user data; also accepts `user_data`. |
| `tags` | No | object | OCI freeform instance tags. |
| `compute_cluster_id` | No | string | Existing cluster OCID; requires bare metal, explicit AD, and on-demand capacity. |
| `agent_config` | No | object | OCI agent configuration, including HPC plugin settings. |
| `pricing` | No | object | Static estimate overrides; does not constrain OCI billing. |

Required deployment fields may come from provider template defaults. OCI-specific
fields persist through `provider_config`; explicit saved fields take precedence
over defaults. Shape aliases include `shape`, `machine_type`, and `instance_type`.
The complete shipped shape matrix and architecture requirements are in the
[template guide](templates.md).

### Preemptible capacity

Use `capacity_type: "preemptible"`; the AWS `price_type: "spot"` field does not
select OCI preemptible capacity. With `fallback_to_ondemand: true`, a failed
preemptible launch triggers an on-demand attempt. This is a retry after a launch
error, not a replacement service for workers that are preempted later.
Fallback never changes the shape, image, or architecture.

## Machine data

The handler reports flat instance records. A running worker includes:

```json
{
  "instance_id": "<instance-ocid>",
  "resource_id": "<instance-ocid>",
  "status": "running",
  "instance_type": "VM.Standard.E5.Flex",
  "private_ip": "10.0.0.10",
  "public_ip": null,
  "subnet_id": "<subnet-ocid>",
  "security_group_ids": ["<nsg-ocid>"],
  "provider_type": "oci",
  "provider_api": "OCICompute",
  "provider_data": {
    "lifecycle_state": "RUNNING",
    "compute_cluster_id": null
  }
}
```

The public CLI presents the instance OCID as `machine_id`. OCI uses the same
instance OCID for `resource_id`; there is no fleet or instance-pool resource ID.
Network fields come from the primary VNIC. `private_dns_name` is included when the
VNIC hostname and subnet DNS domain are available. Cluster membership is verified
before exposing `provider_data.compute_cluster_id` for running cluster workers.

## Fulfilment semantics

Live launch calls return accepted instance IDs. ORB continues polling until the
workers are running; acceptance alone does not complete the acquire request.

| Condition | Fulfilment state |
| --- | --- |
| Running count reaches the requested count | `fulfilled` |
| Capacity is still pending | `in_progress` |
| Some workers run, the remaining workers have stopped/terminated, and none remain pending | `partial` |
| No workers run and every requested worker has stopped/terminated | `failed` |
| All requested return targets are terminated | Return is `fulfilled` |

Provider health checks authenticate with OCI using the configured identity.
Template validation and a successful health probe do not prove available capacity
or every required compute permission. A successful acquire does not prove guest bootstrap or
Symphony registration. Use the [live canary procedure](../../deployment/oci-integration.md#live-one-machine-canary)
and verify the workload separately.

## Release behaviour

`orb machines return` terminates the named instances directly and polls their
termination. It leaves a shared compute cluster intact. Normal returns use OCI
CLI termination defaults; verify boot-volume cleanup independently.

Each logical launch carries a stable retry token and request/launch tags. After
a timeout, ORB searches for the matching worker without issuing an on-demand
fallback. Unresolved launches retain recovery identifiers in the failure metadata.
On-demand fallback is limited to explicit capacity rejection codes.

After a partial batch launch failure, ORB requests rollback of accepted
workers whose IDs it received or reconciled. A lost response may require tag-based recovery;
see [RDMA cleanup](compute-clusters.md#configure-the-template). This applies to
ordinary instance batches and compute-cluster batches. Additional block
volumes, networks, and application data are not managed by this handler.

All operations use the provider region; cross-region resource references are
rejected. A template that references resources from another region fails with
`OCI_REGION_MISMATCH`; the error details name the provider region. Configure another
provider instance for a different region. If the region lookup itself fails (for example the
`iam region list` call), the launch fails with `OCI_REGION_VALIDATION_FAILED`.

During status polling, an instance that OCI answers with a 404 is left out of the
result and the other instances are still reported. OCI uses the same 404 for an
instance that no longer exists and for one the broker may not read, so a
persistent 404 for an instance that should exist usually means the IAM policy
does not grant `read instances` on the compartment that holds it, or the instance
is in a different compartment than the policy covers. Fix the policy or compartment
scope and the instance is reported again on the next poll. For a return request,
a 404 (with any error code) fails the whole status call instead, because leaving
the instance out would make the return look complete. The stored machines are
kept and the return request stays in progress until the policy or compartment
scope is fixed.

To resolve a return that stays in progress after a 404:

1. Check that the broker's policy grants `read instances` on the compartment that
   holds the instance, and that the instance is in a compartment the policy covers.
2. Look the instance up in the OCI console. If it is listed, fix the policy or
   compartment scope; the return completes on the next poll once the instance is
   readable and reports a terminal state.
3. If the instance is not listed at all, it was deleted and has aged out of OCI.
   The broker cannot tell that apart from an unreadable instance, so it does not
   complete the return on its own and the request stays in progress until the
   machine state is reconciled by hand.

Any other error for an instance, including a failed VNIC lookup, fails the whole
status call; stored machine state is kept and the call is retried on the next poll.

Termination attempts every requested instance and reports per-instance outcomes.
A `NotAuthorizedOrNotFound` response is recorded as `not_found_or_not_authorized`
and is excluded from confirmed termination results. If there are no other errors,
the operation fails with `OCI_TERMINATION_UNCONFIRMED`. Return-status polling
fails for such an instance and leaves the return in progress. Verify OCI
state and permissions before retrying; an ambiguous 404 does not prove deletion.

Launch metadata is read from a temporary JSON file, protected with 0600 permissions
on POSIX or an owner-only ACL on Windows, and removed after execution.
Launch payloads, including user data and SSH keys, are not included in create results.

## IAM permissions

See [broker instance-principal setup](../../deployment/oci-integration.md#broker-instance-principal-setup)
for an example dynamic-group policy and its compartment scope. The broker needs
instance launch/status/termination, image access, and the relevant volume/network
permissions. Cluster placement additionally needs `use compute-clusters`.

## Limitations

- One shape and one subnet per template; no weighted capacity or cross-shape fallback.
- Flex CPU/memory overrides apply to VM shapes; bare metal examples are fixed-size.
- No instance-pool, cluster-network creation, or automatic capacity reconciliation.
- No OCI native API-spec merge path; use the documented template fields.
- Pricing estimates are static and may be unknown for a shape.
- HostFactory field mapping and CLI workflows are tested offline; actual Symphony
  registration remains unvalidated.
- Availability and image compatibility must be validated for each selected shape.
- Launch and status calls are sequential; large batches incur per-instance CLI overhead.

## Related

- [Deployment and Authentication](../../deployment/oci-integration.md)
- [Compute Templates and Flex Sizing](templates.md)
- [Compute Clusters and RDMA](compute-clusters.md)
- [Template Management](../../user_guide/templates.md)
