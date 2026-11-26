# KubeSwift sandboxes

`provider_api: KubeSwiftSandbox` runs each machine as a
[KubeSwift](https://github.com/kubeswift-io/kubeswift) `SwiftSandbox`: an
OCI image booted as a microVM, with a VM boundary around the workload. It
uses the same `k8s` provider, configuration and CLI as the built-in
handlers; it needs KubeSwift installed in the cluster and nothing extra in
ORB.

A machine is either checked out of a pre-booted `SwiftSandboxPool` (well
under a second when a slot is warm) or booted cold.

## Template

KubeSwift settings go in `metadata.kubeswift`.

```json
{
  "template_id": "ks-workers",
  "provider_type": "k8s",
  "provider_api": "KubeSwiftSandbox",
  "machine_image": "docker.io/library/alpine:3.20",
  "max_machines": 10,
  "metadata": {
    "kubeswift": {
      "poolRef": "workers",
      "command": ["/bin/sh", "-c", "exec my-worker"]
    }
  }
}
```

| Key | Meaning |
|---|---|
| `poolRef` | `SwiftSandboxPool` to check out of. Image, CPU, memory, network and node selector come from the pool; `machine_image` must equal the pool's image. |
| `cpu`, `memory` | Cold boot size (default `512Mi`, one vCPU). |
| `network` | `SwiftSandbox` network block; default `{"mode": "restricted"}`. |
| `command`, `args`, `env`, `workingDir` | The workload. `env` is an object of name: value. Template `command`/`args` are used when the block has none. |
| `timeout`, `ttl` | `SwiftSandbox` durations, for example `"1h"`. |
| `nodeSelector`, `securityContext`, `scratchDisk`, `model` | Copied to the `SwiftSandbox` spec as written. |
| `gpuProfileRef` or `gpuResourceClaim` | A GPU for a cold boot. A pooled machine takes its GPU from the pool and must not set either. |

A `SwiftSandbox` is not a pod. `orb templates validate` and acquire refuse a
template that sets `machine_bootstrap` (there is no cloud-init) or a pod
field such as `resource_requests`, `volumes`, `tolerations` or
`security_context`.

## Status

| `SwiftSandbox` | ORB machine |
|---|---|
| `Pending`, `Materializing` | `pending` |
| `Running`, guest not up yet | `launching` |
| `Running` with `GuestRunning=True` | `running` |
| `Exporting`, or being deleted | `shutting-down` |
| `Completed` | `terminated` |
| `Failed` | `failed` |

A sandbox whose command exits is `Completed`, which ORB reports as
`terminated`, so a worker that should live until it is returned must keep
running. `private_ip` is set only when the guest has a network-scope address;
the default NAT address is private to the launcher pod and is never reported.

Return deletes the named sandboxes.

## RBAC

On top of [rbac.yaml](rbac.yaml), in each namespace ORB uses:

```yaml
- apiGroups: ["sandbox.kubeswift.io"]
  resources: ["swiftsandboxes"]
  verbs: ["get", "list", "watch", "create", "delete"]
- apiGroups: ["sandbox.kubeswift.io"]
  resources: ["swiftsandboxpools"]
  verbs: ["get", "list"]
```

## Orphan cleanup

Sandboxes carry the same labels as ORB's pods, including
`<label_prefix>/orb-instance`, and the orphan garbage collector sweeps them
with the same rules (see
[plugin authoring](plugin-authoring.md#orphan-cleanup)). ORB never labels
the launcher pods that KubeSwift creates, so pod cleanup cannot remove the
launcher of a live sandbox.
