# OCI compute templates

The packaged catalog at `src/orb/providers/oci/config/oci_templates.json` contains
25 examples covering 23 shapes. `orb templates generate` reads this single catalog
in both source checkouts and installed wheels.

For the full handler contract and acquire/status/return workflow, see
[OCI Compute Instances](compute.md).

## Catalog examples and configured templates

The shipped catalog is a portable set of shape and sizing examples. It does not
contain a customer's image, compartment, subnet, NSG, SSH public key, or bootstrap
script. It requires deployment configuration before use; generating examples
does not make every shape ready to launch.

| Location | Contents |
| --- | --- |
| Packaged `oci_templates.json` | Shape, Flex sizing, capacity type, boot size, and explicit ARM/HPC configuration placeholders |
| Deployment `config.json` | Your provider credentials/auth mode and deployment defaults: image, compartment, subnet, NSGs, SSH public key, and optional bootstrap/tags |
| `orb templates list` output | Runtime templates after local defaults have been applied, including the actual resource IDs used for a launch |

Seeing a subnet or image OCID in `templates list` does not mean it is hard coded
in the shipped catalog. Inspect your generated template file to see the saved
catalog, and `provider.provider_defaults.oci.template_defaults` in your local
`config.json` to see shared deployment settings. A saved per-template value
overrides the shared default. Edit OCI-specific settings in the generated catalog. Generic template create/update
commands do not provide an OCI-specific authoring extension. Keep deployment files local.

`<replace-...>` values deliberately mark incomplete configuration. ARM templates
must not inherit a shared x86 image, and RDMA requires its own compatible HPC
image, cluster, and availability domain. Configure each template you intend to
use and check image/shape compatibility. A shared image used successfully for
E5 is not proof that it supports every Intel, DenseIO, or bare metal shape.

`is_active: true` means the template is enabled, not that OCI compatibility or
capacity has been verified. `created_at` and `updated_at` describe the template
object, not the image's release date or a successful deployment. The catalog
limit `max_instances: 1` is an example limit, not the shape's hardware capacity.

Bootstrap (`machine_bootstrap`, also accepted as `user_data`), public keys, and
environment tags are deployment choices. The example configs do not install a
canary bootstrap or assume a development/production environment tag. A manual
test deployment can still have its own `manual-test` tag and test bootstrap;
these appear in its resolved list output and are not production defaults.

## Shape coverage

| Processor / family | VM shapes | Bare metal shapes |
| --- | --- | --- |
| AMD E5 Standard | `VM.Standard.E5.Flex` | `BM.Standard.E5.192` |
| AMD E6 Standard | `VM.Standard.E6.Flex`, `VM.Standard.E6.Ax.Flex` | `BM.Standard.E6.256`, `BM.Standard.E6.Ax.192` |
| Intel Standard | `VM.Standard3.Flex`, `VM.Standard4.Ax.Flex` | `BM.Standard3.64`, `BM.Standard4.Ax.120` |
| Intel Optimized | `VM.Optimized3.Flex` | `BM.Optimized3.36` (existing RDMA example) |
| Ampere Standard | `VM.Standard.A1.Flex`, `VM.Standard.A2.Flex`, `VM.Standard.A4.Flex`, `VM.Standard.A4.Ax.Flex` | `BM.Standard.A1.160`, `BM.Standard.A4.48`, `BM.Standard.A4.Ax.48` |
| AMD E5 DenseIO | `VM.DenseIO.E5.Flex` | `BM.DenseIO.E5.128` |
| AMD E6 DenseIO | `VM.DenseIO.E6.Ax.Flex` | `BM.DenseIO.E6.Ax.192` |

These are examples of supported template definitions, not a promise of regional
capacity. Check your region, availability domain, image compatibility, limits,
and access using OCI's `ListShapes` API before requesting workers. The exact E6
DenseIO shape includes `.Ax`; there is no `VM.DenseIO.E6.Flex` example. Current
Intel and Ampere families have Standard examples; current DenseIO examples are
AMD. See [Oracle's shape reference](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm).

Existing IDs remain valid: `oci-vm-flex-ondemand-small`,
`oci-vm-flex-preemptible-small`, and
`oci-vm-flex-preemptible-fallback-medium` use E6 Standard;
`oci-bm-standard-ondemand` uses E5 Standard. New IDs follow
`oci-<shape-in-lowercase-with-hyphens-without-flex>-ondemand`, for example
`oci-vm-standard-e5-ondemand` and `oci-vm-denseio-e6-ax-ondemand`.

## Flex sizing

Each VM example explicitly declares its CPU and memory. Standard and Optimized
examples start with 1 OCPU / 8 GB; DenseIO examples start with 8 OCPUs / 96 GB.
Clone a template, give it a unique `template_id`, and edit `shape_config`:

```json
{
  "shape_config": {
    "ocpus": 4,
    "memoryInGBs": 32
  }
}
```

This is a sizing fragment for Standard/Optimized Flex, not a complete template.
ORB passes these values to OCI's launch `shapeConfig`. OCI enforces the shape's
CPU/memory limits and permitted ratios; values must be finite and positive.
`machine_types: {"VM.Standard.E5.Flex": 1}` selects the shape; its weight `1`
does not set the CPU count. Use one shape per OCI template, so the image,
architecture, sizing, and scheduling identity are unambiguous. The OCI handler
selects the first entry; the map is not a multi-shape fallback list.

DenseIO Flex uses fixed CPU/memory steps. For both shipped E5 and E6 Ax examples,
the allowed pairs are 8/96, 16/192, 24/288, 32/384, 40/480, and 48/576
(OCPUs/GB). ORB rejects other pairs before provisioning. Local NVMe capacity
follows the selected shape configuration; `boot_volume_gbs` sets the separate
boot disk and does not resize or mount NVMe. Treat local NVMe as temporary worker
storage and configure mounts in your own bootstrap. See
[Oracle's NVMe guidance](https://docs.oracle.com/en-us/iaas/Content/Compute/References/nvmedeviceinformation.htm).

Bare metal examples are fixed shapes. Do not add `shape_config`, `ocpus`, or
`memory_gbs`; ORB rejects Flex overrides on bare metal.

## Secondary block volumes

For each direct worker that needs additional storage, add `secondary_volumes` to its OCI template. Each list entry creates one blank disk in the worker's compartment and availability domain. To request one scratch disk and one disk to keep, add the following fragment to the generated template.

```json
{
  "secondary_volumes": [
    {"display_name": "scratch", "size_in_gbs": 50, "vpus_per_gb": 10, "delete_on_return": true},
    {"display_name": "results", "size_in_gbs": 50, "delete_on_return": false}
  ]
}
```

Supply a whole number from 50 to 32,768 for `size_in_gbs` and an explicit JSON boolean for `delete_on_return`. The optional `vpus_per_gb` accepts 0, 10 or 20. Omit it to use the service default. The number of attachable disks depends on the shape. For example, VM.Standard.E5.Flex supports up to 16 paravirtualized data volumes. The template cost estimate excludes these data disks. Retained volumes continue to incur storage charges until their owner removes them. [Oracle Block Volume](https://docs.oracle.com/en-us/iaas/Content/Block/Concepts/overview.htm)

The broker manages attachment at the OCI control plane. Configure the guest separately to connect iSCSI sessions where required, partition the disk, and mount a filesystem. Use the returned volume OCID to locate a retained disk. Display names can repeat across workers.

The broker records the disk ID, attachment ID and final retention outcome on the original acquire request. After returning a worker, use that acquire request ID to list disks whose retention has been confirmed.

```bash
orb requests show "$ACQUIRE_REQUEST_ID" | jq -r '
  .requests[0].provider_data as $data
  | $data | to_entries[]
  | select((.key | startswith("oci_secondary_volumes/")) and (.key | endswith("/outcome")) and .value == "retained")
  | .key | sub("/outcome$"; "/volume") | $data[.]'
```

This feature requires persistent request storage with version-checked writes. Use a file-backed SQL store on Windows. Shared brokers need a request database accessible to every broker process. Give the broker principal access to create, read, attach and delete data volumes in the target compartment. If OCI cannot confirm the exact disk or attachment for a request, inspect the stored request and OCI inventory before taking action.

## Images and deployment settings

Keep compartment, subnet, NSGs, SSH public keys, and bootstrap settings in
`provider_defaults.oci.template_defaults`, as described in the
[integration guide](../../deployment/oci-integration.md).

Use a compatible x86_64 image for AMD/Intel. Every Ampere example explicitly has
`image_id: "ocid1.image.oc1..<replace-arm64-image-ocid>"`; replace this on each
chosen template with a compatible ARM64 image before launching. The placeholder
intentionally overrides a shared x86 image default. Your worker software and
bootstrap must also support ARM64. OCPU-to-vCPU conversion differs between A1
and later Ampere families; sizing is always expressed in OCI OCPUs, not an
assumed universal thread count. See
[Arm-based Compute](https://docs.oracle.com/en-us/iaas/Content/Compute/References/arm.htm).

The RDMA example keeps its explicit HPC image, availability domain, cluster ID,
and agent settings. Follow [Compute Clusters and RDMA](compute-clusters.md).
Other new examples launch independent on-demand workers using the same existing
OCI handler. To use preemptible capacity on a compatible VM shape, clone an
existing preemptible example and replace its shape, sizing, and image; fallback
changes capacity type for that same shape, not processor architecture.

Pricing remains an estimate. Shapes without configured reference rates report
unknown compute cost; supply `pricing` overrides for your applicable rates.
Catalog coverage and offline tests do not establish a live launch on every shape.

## Validate a selected template

After filling deployment defaults and the selected template's image/settings:

```bash
export ORB_CONFIG_DIR="$PWD/config"
orb --config "$ORB_CONFIG_DIR/config.json" templates generate
orb --config "$ORB_CONFIG_DIR/config.json" templates list
orb --config "$ORB_CONFIG_DIR/config.json" templates validate oci-vm-standard-e5-ondemand
orb --config "$ORB_CONFIG_DIR/config.json" machines request oci-vm-standard-e5-ondemand 1 --dry-run
```

Generation writes examples; edit your generated catalog afterward. Do not
regenerate over deployment edits. For a real canary, use the acquire/status/return
procedure in the integration guide and confirm cleanup independently in OCI.
