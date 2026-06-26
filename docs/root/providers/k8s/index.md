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
orb templates generate --provider kubernetes --provider-api Pod
```

This emits a template that targets the Kubernetes provider's Pod handler.
Tweak `container_image`, `resource_requests`, `resource_limits`, and any
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

## What is in this section

* [Configuration reference](configuration.md) - every `K8sProviderConfig` field.
* [Handlers](handlers.md) - Pod, Deployment, StatefulSet, Job; when to pick each.
* [Native spec escape hatch](native-spec.md) - submit a full kubernetes
  API body and bypass the typed builders for fields ORB does not model.
* [Authentication](auth.md) - in-cluster vs kubeconfig, troubleshooting.
* [RBAC example](rbac.yaml) - minimum ServiceAccount + Role + RoleBinding.
* [Migrating from `orb.k8s_legacy`](migrating-from-k8s-legacy.md) - template field
  mapping, label deltas, coexistence guidance.
* [Security hardening](security-hardening.md) - pod-spec audit, high-risk
  field warnings, and how to enable reject mode.
* [Authoring a provider plugin](plugin-authoring.md) - extending the
  provider via the `orb.providers` entry-point group, with a worked
  MPIJob example.
