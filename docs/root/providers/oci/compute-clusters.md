# OCI Compute Clusters and RDMA

The `OCICompute` handler can launch individual `BM.Optimized3.36` workers into an
existing OCI compute cluster. The operator owns the cluster; ORB owns the workers
it requests and returns. Returning workers leaves the cluster available for reuse.
ORB does not create instance pools or cluster networks with this handler.

See the [OCI integration guide](../../deployment/oci-integration.md) for installation,
authentication, and deployment defaults. The provider-owned example catalog is
`src/orb/providers/oci/config/oci_templates.json`. Keep deployment OCIDs and keys in local configuration.

## Prerequisites

- A region and availability domain supporting compute clusters and the shape.
- Quota and physical capacity for two nodes: 72 OCPUs and 1,024 GB RAM in total.
  A capacity report is a point-in-time check, not a reservation within a cluster.
- A compatible HPC image with RDMA drivers, userspace libraries, and working
  Oracle Cloud Agent HPC network configuration/authentication. An ordinary Linux
  image is not sufficient evidence that RDMA works. Check the image's Marketplace
  subscription requirements before launching.
- A management subnet in the cluster's availability domain, or a regional subnet;
  an SSH path through a VPN, bastion, or existing management host; and the required
  worker-to-worker control traffic. Management IP connectivity alone does not prove
  the RDMA interconnect works.
- Permission to launch, inspect, and terminate instances and `use compute-clusters`.
  Operators creating or deleting clusters additionally need `manage compute-clusters`
  in the target compartment. Keep the normal image, subnet, VNIC, and volume permissions.

Oracle documents the [shape specifications](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm),
[compute-cluster placement and IAM requirements](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/compute-clusters.htm),
and [HPC agent configuration](https://docs.oracle.com/en-us/iaas/Content/Compute/References/high-performance-compute.htm).

## Create the shared cluster

Create an empty RDMA group once, using your configured OCI authentication:

```bash
oci compute compute-cluster create --compartment-id <compartment-ocid> --availability-domain <availability-domain> --display-name orb-rdma-optimized3 --region us-phoenix-1
oci compute compute-cluster get --compute-cluster-id <cluster-ocid> --region us-phoenix-1
```

Wait for `ACTIVE`. Instances must use the same compartment and availability domain.
Creating this group does not launch any compute nodes.

## Configure the template

Generate the examples with `orb templates generate`, then replace the cluster, AD,
and HPC image placeholders in `oci-bm-optimized3-rdma`. For a separate deployment,
create a single template object with `templates create --file rdma-template.json`:

```json
{
  "template_id": "oci-bm-optimized3-rdma",
  "provider_name": "oci-default",
  "provider_type": "oci",
  "provider_api": "OCICompute",
  "machine_types": {"BM.Optimized3.36": 1},
  "max_instances": 2,
  "image_id": "<compatible-hpc-image-ocid>",
  "subnet_ids": ["<management-subnet-ocid>"],
  "compartment_id": "<compartment-ocid>",
  "availability_domain": "<cluster-availability-domain>",
  "compute_cluster_id": "<cluster-ocid>",
  "agent_config": {
    "areAllPluginsDisabled": false,
    "isManagementDisabled": false,
    "pluginsConfig": [
      {"name": "Compute HPC RDMA Authentication", "desiredState": "ENABLED"},
      {"name": "Compute HPC RDMA Auto-Configuration", "desiredState": "ENABLED"}
    ]
  },
  "ssh_authorized_keys": "<ssh-public-key>",
  "boot_volume_gbs": 200,
  "capacity_type": "ondemand",
  "fallback_to_ondemand": false
}
```

The fixed-size shape provides 36 OCPUs and 512 GB RAM per node. Do not set `ocpus`,
`memory_gbs`, or `shape_config`: ORB rejects Flex overrides for bare metal.
Compute-cluster templates require bare metal, on-demand capacity, and an explicit AD.
Not every bare-metal shape supports RDMA; this guide targets `BM.Optimized3.36`.
ORB leaves the price unknown unless an applicable reference rate is configured.

`compute_cluster_id` persists through the provider extension, template create/update,
and reload. HostFactory maps it to `computeClusterId`; only OCI opts into the
provider output preparation hook, leaving other providers' mappings unchanged. Immediately before a live
request, ORB reads the cluster and checks `ACTIVE`, compartment, and AD. A failed
check stops the request before launching any nodes. The launch passes
`--compute-cluster-id`. Cluster launches disable CLI retries and use bounded HTTP
timeouts so placement errors surface within ORB's command deadline; other OCI
launch modes retain their existing options.

Cluster workers also carry the reserved `orb_request_id` tag. If a cluster batch
fails after accepting some workers, ORB requests termination of those known workers
and records rollback errors in the provider result/log. Independently verify cleanup,
including after a lost launch response: list the compartment's instances and match
the failed acquire request's `orb_request_id` tag. A rollback request is not proof
that termination and boot-volume removal have finished.

The current `GetInstance` response does not expose the cluster ID. ORB records the
requested ID in the reserved `orb_compute_cluster_id` tag, then verifies running
workers using `ListInstances` filtered by `compute_cluster_id`. Status exposes the
verified membership in `provider_data.compute_cluster_id`; the tag alone is not
accepted as proof of membership.

The example also enables the two HPC agent plugins explicitly through
`agent_config` (`agentConfig` in HostFactory output). Drivers and an active HCA
alone do not establish readiness: wait for a configured RDMA IP/GID and an
authorized RDMA supplicant before starting the workload. Oracle documents the
[agent plugin setup and legacy-package migration](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/transitioning-to-hpc-plugin.htm).
Use an image prepared for that plugin configuration; do not run legacy
`oci-cn-auth` services alongside the agent's RDMA authentication plugin.

Check both cloud and guest firewalls. The guest must permit benchmark/MPI control
connections between the workers; allowing only SSH is insufficient. Restrict those
rules to the worker group or exact peer IPs. If using OCI Bastion, verify its subnet
also permits SSH egress to the new workers. Keep existing rules intact and remove
temporary test rules afterward.

## Validate two nodes end to end

Use an isolated config and database. Pass `--config /path/to/config.json` to every
ORB command if using a custom configuration directory.

1. Build and install a wheel from this source. Run the OCI tests and check that
   template create/show/reload retains the cluster ID for the chosen scheduler.
2. Run `orb templates validate oci-bm-optimized3-rdma`, then
   `orb machines request oci-bm-optimized3-rdma 2 --dry-run`. These are offline
   checks; they do not establish cloud capacity or RDMA connectivity.
3. Request exactly two live nodes:
   `orb machines request oci-bm-optimized3-rdma 2 --provider-name oci-default`.
   Record the request and both instance IDs immediately. Poll `orb requests status
   <request-id>` until complete. If either launch fails, recover all instances from
   that request before retrying; a partially launched node still incurs charges.
4. Independently inspect both instances with OCI CLI. Require `RUNNING`, the correct
   shape, matching `compute-cluster-id`, AD, compartment, and usable private IPs.
5. SSH to both workers. Check bootstrap completion, HPC agent health, `rdma link`,
   `ibv_devinfo`, and the image's RDMA device/GID configuration. Check memlock limits
   and node-to-node access for the benchmark's control connections.
6. Run an RDMA verbs bandwidth and latency test, then an MPI ping-pong test with
   RDMA transport explicitly selected. Save commands, device/GID, exit codes, and
   output. A successful TCP fallback or SSH connection is not a passing RDMA test.
7. Return both nodes through ORB, poll the return request, and independently confirm
   `TERMINATED` in OCI. Inspect boot volumes for leftovers. Perform this cleanup on
   failure or interruption too. The empty shared compute cluster remains active.

For example, after confirming the device and GID index on your image, run the
server on node 1 and client on node 2:

```bash
# Node 1 (server)
ib_write_bw -d <rdma-device> -i 1 -x <gid-index> -F --report_gbits
# Node 2 (client); use node 1's reachable management IP for control setup
ib_write_bw -d <rdma-device> -i 1 -x <gid-index> -F --report_gbits <node-1-private-ip>
```

Repeat with `ib_write_lat` for latency. Select the RDMA HCA rather than a management
NIC. Oracle's [cluster networking application guide](https://blogs.oracle.com/cloud-infrastructure/running-applications-on-oracle-cloud-using-cluster-networking)
includes MPI configuration examples; adapt them to the installed MPI/UCX version
and explicitly prevent TCP fallback. Record measured performance against agreed
thresholds rather than assuming the advertised link rate is application throughput.

## Acceptance and cleanup

Record infrastructure readiness, driver/agent readiness, verbs results, MPI results,
and cleanup as separate outcomes. A cluster reaching `ACTIVE` or two nodes reaching
`RUNNING` does not establish that the RDMA data path works. Scheduler registration,
advertised CPU/memory attributes, and a real Symphony job require separate validation.

When retiring the deployment, first terminate all its nodes and check for remaining
volumes. Delete the empty cluster explicitly only when it is no longer needed:

```bash
oci compute compute-cluster delete --compute-cluster-id <cluster-ocid> --region us-phoenix-1
```
