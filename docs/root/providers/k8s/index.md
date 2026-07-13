# Kubernetes provider

The Kubernetes provider lets ORB acquire, track, and release compute capacity
backed by Kubernetes workloads.  It treats every managed pod as a "machine"
in the ORB sense and reuses the same template, request, and machine model
that the AWS provider relies on, so callers of the CLI, REST API, SDK, and
HostFactory plugin do not need to special-case Kubernetes.

The provider supports four workload shapes (`provider_api` values):

| `provider_api`         | Workload          | Typical use                                              |
|------------------------|-------------------|----------------------------------------------------------|
| `Pod`        | bare `v1/Pod`     | Stateless short-lived workers; smallest blast radius.    |
| `Deployment` | `apps/v1/Deployment` | Long-running stateless services; replica-driven scaling. |
| `StatefulSet`| `apps/v1/StatefulSet` | Workloads needing stable network identity / storage. |
| `Job`        | `batch/v1/Job`    | Run-to-completion batches.                               |

See [Handlers](handlers.md) for how to pick between them.

## Install

The Kubernetes provider lives behind an optional install extra so that
operators who only target AWS do not pay for the `kubernetes` SDK.

```bash
pip install "orb-py[k8s]"
```

For local development against a kind cluster, also install the CLI extra:

```bash
pip install "orb-py[kubernetes,cli]"
```

The legacy Symphony-on-Kubernetes HostFactory plugin is a separate extra
(`[k8s-legacy]`).  See [Migrating from `orb.k8s_legacy`](migrating-from-k8s-legacy.md)
for the relationship between the two.

## Quick start

### 1. Authenticate to a cluster

ORB picks one of two paths at runtime:

* **In-cluster** - when the `/var/run/secrets/kubernetes.io` sentinel
  exists (i.e. ORB is itself running as a pod), the provider loads the
  pod's service-account token.
* **kubeconfig** - otherwise the provider loads a kubeconfig file, in this
  precedence: explicit `kubeconfig_path` config field, `KUBECONFIG` env
  var, default `~/.kube/config`.

See [Authentication](auth.md) for the full decision matrix and
[`rbac.yaml`](rbac.yaml) for the minimum RBAC the in-cluster path needs.

### 2. Configure the provider

```json
{
  "providers": {
    "k8s": {
      "provider_type": "k8s",
      "namespace": "orb",
      "label_prefix": "orb.io",
      "watch_enabled": true
    }
  }
}
```

The full set of fields lives in [Configuration reference](configuration.md);
the example above is the minimum needed for single-namespace mode against
the current kubeconfig context.

### 3. Create a template

```bash
orb templates generate --provider-name kubernetes --provider-api Pod
```

This emits a template that targets the Kubernetes provider's Pod handler.
Tweak `image_id`, `resource_requests`, `resource_limits`, and any
`node_selector` / `tolerations` to match your cluster, then save it.

### 4. Request capacity

```bash
orb machines request my-k8s-template 3
```

ORB creates three pods labelled with `orb.io/managed=true`,
`orb.io/request-id=<id>`, and `orb.io/machine-id=<id>` so the in-cluster
view can be reconciled against ORB storage at any time.

### 5. Track and release

```bash
orb requests status <request-id>
orb machines return <machine-id> <machine-id> ...
```

Releases trigger a `delete_namespaced_pod` call (or the appropriate
controller-driven replica reduction for Deployment / StatefulSet / Job
workloads).

## AWS concepts mapped to Kubernetes

If you are familiar with the AWS provider, this table shows the
equivalent concept in the Kubernetes world.

| AWS concept                       | Kubernetes equivalent                                                     |
|-----------------------------------|---------------------------------------------------------------------------|
| EC2 instance                      | Pod                                                                       |
| Auto Scaling Group / EC2 Fleet    | Deployment or StatefulSet (controller manages the replica set)            |
| Amazon Machine Image (AMI)        | Container image (`image_id` field, e.g. `registry/name:tag`)             |
| Instance type label               | Node label (`node.kubernetes.io/instance-type` or custom node selector)   |
| Spot / On-Demand capacity type    | Karpenter `karpenter.sh/capacity-type: spot` / `on-demand` node label     |
| `terminate`                       | Pod `delete`, or scale Deployment / StatefulSet to 0                      |
| `start` / `stop`                  | Scale Deployment / StatefulSet `spec.replicas` between 0 and N (see [START/STOP operations](#startstop-operations)) |
| IAM instance profile              | Kubernetes ServiceAccount (`service_account` template field)              |
| AWS region                        | Kubernetes namespace or cluster context                                   |
| EC2 security group                | NetworkPolicy                                                             |
| EBS volume                        | PersistentVolumeClaim (declare via `volumeClaimTemplates` on StatefulSet) |
| `provider_api_spec` native body   | `native_spec` field on `K8sTemplate` (see [Native spec escape hatch](native-spec.md)) |

Key differences to keep in mind:

* ORB identifies managed resources by the `orb.io/request-id` label,
  not by name.  Names are cosmetic and generated according to the
  [naming policy](#configurable-resource-naming); do not rely on them in
  external scripts.
* Kubernetes does not have a machine-level "stopped" state for bare
  Pods or Jobs.  START and STOP operations are only meaningful for
  Deployment and StatefulSet workloads (scale to 0 / restore).

## START/STOP operations

For Deployment and StatefulSet workloads, ORB maps the
`START_INSTANCES` and `STOP_INSTANCES` operations to `spec.replicas`
scaling:

* **STOP** — patches `spec.replicas` to `0` and archives the original
  count in `provider_data["replicas_before_stop"]`.  All pods are
  terminated by the controller.
* **START** — patches `spec.replicas` back to the archived pre-stop
  count (or falls back to the acquire-time count).

Pod and Job workloads return an `UNSUPPORTED_OPERATION_FOR_KIND` error
because pods and jobs cannot be meaningfully paused and resumed.

Required RBAC (`deployments/scale` and `statefulsets/scale` get/patch)
is included in the baseline [`rbac.yaml`](rbac.yaml).

## Configurable resource naming

Every managed resource receives a name generated as
`<prefix>-<uuid_segment>` for controller kinds (Deployment, StatefulSet,
Job) and `<prefix>-<uuid_segment>-<seq:04d>` for individual Pods, where
`uuid_segment` is the first `uuid_chars` hex characters of the
hyphen-stripped request UUID.

Names are cosmetic — ORB recovers state via the `orb.io/request-id`
label, not by parsing the name.  The defaults reproduce the historical
naming pattern so upgrades do not affect existing resources.

The naming policy is controlled by the `naming` field on
`K8sProviderConfig`.  See [Configuration reference](configuration.md#resource-naming)
for all available fields.

## What is in this section

* [Infrastructure discovery](discovery.md) - interactive `orb init` flow, the
  six operator prompts, minimum RBAC, 403 fallback paths, and deployment
  examples for in-cluster and out-of-cluster modes.
* [Configuration reference](configuration.md) - every `K8sProviderConfig` field.
* [Handlers](handlers.md) - Pod, Deployment, StatefulSet, Job; when to pick each.
* [Native spec escape hatch](native-spec.md) - submit a full kubernetes
  API body and bypass the typed builders for fields ORB does not model.
* [Authentication](auth.md) - in-cluster vs kubeconfig, inbound TokenReview auth, troubleshooting.
* [RBAC example](rbac.yaml) - minimum ServiceAccount + Role + RoleBinding, with all opt-in grants documented.
* [Migrating from `orb.k8s_legacy`](migrating-from-k8s-legacy.md) - template field
  mapping, label deltas, coexistence guidance.
* [Security hardening](security-hardening.md) - pod-spec audit, high-risk
  field reject mode (on by default), and how to disable it for legitimate workloads.
* [Authoring a provider plugin](plugin-authoring.md) - extending the
  provider via the `orb.providers` entry-point group, with a worked
  MPIJob example.
