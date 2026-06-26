# Kubernetes handlers

The Kubernetes provider ships four handlers.  Each maps to one
`provider_api` value on the ORB template and to one native Kubernetes
workload primitive.

| `provider_api`           | Native object         | Selective release | Best for                                       |
|--------------------------|-----------------------|-------------------|------------------------------------------------|
| `KubernetesPod`          | `v1/Pod`              | yes (per pod)     | Short-lived workers; lowest blast radius.      |
| `KubernetesDeployment`   | `apps/v1/Deployment`  | yes (cost-based)  | Long-running stateless services.               |
| `KubernetesStatefulSet`  | `apps/v1/StatefulSet` | yes (ordinal)     | Workloads needing stable identity / storage.   |
| `KubernetesJob`          | `batch/v1/Job`        | no                | Run-to-completion batches.                     |

## Decision tree

1. Does the workload need to run to completion (exit 0 and stop)?
   * Yes - use **`KubernetesJob`**.
2. Does the workload need stable network identity or a per-replica
   PersistentVolume?
   * Yes - use **`KubernetesStatefulSet`**.
3. Is the workload long-lived and stateless?
   * Yes - use **`KubernetesDeployment`**.
4. Otherwise - use **`KubernetesPod`**.

## `KubernetesPod`

The Pod handler creates N bare pods directly via
`CoreV1Api.create_namespaced_pod`.  There is no controller in the
loop, so:

* `acquire_hosts` creates pods concurrently, capped at 50 in-flight
  creates to avoid apiserver throttling.
* `release_hosts` deletes the named pods.  A 404 is treated as
  best-effort "already gone".

This is the simplest handler and is the right default for ephemeral
worker pods (HPC scratch, CI agents, short-lived RPC workers).  Choose
it whenever you do not need controller-driven replica management.

### Template fields the Pod handler honours

| Template field          | Meaning                                                                  |
|-------------------------|--------------------------------------------------------------------------|
| `container_image`       | Required.  Mapped to `spec.containers[0].image`.                         |
| `namespace`             | Override the provider-level `namespace`.                                  |
| `resource_requests`     | Container resource requests (e.g. `{"cpu":"1","memory":"2Gi"}`).          |
| `resource_limits`       | Container resource limits.                                                |
| `node_selector`         | `spec.nodeSelector`.                                                      |
| `tolerations`           | `spec.tolerations`.                                                       |
| `service_account`       | `spec.serviceAccountName`.                                                |
| `runtime_class`         | `spec.runtimeClassName`.                                                  |
| `environment_variables` | Injected into the container env.                                          |
| `image_pull_secrets`    | Appended to `spec.imagePullSecrets`.                                      |
| `labels`                | Merged into pod metadata labels (ORB labels always win).                  |
| `annotations`           | Merged into pod metadata annotations.                                     |

## `KubernetesDeployment`

The Deployment handler creates one `apps/v1/Deployment` per ORB request
with `spec.replicas = request.requested_count`.  Pods are owned by the
controller, so deleting a pod directly would cause the ReplicaSet to
re-create it - which would defeat ORB's "return this specific machine"
contract.

ORB uses the **`controller.kubernetes.io/pod-deletion-cost`** annotation
to drive selective termination.  When `release_hosts(machine_ids=[m1, m2])`
is called the handler:

1. Patches each victim pod with `pod-deletion-cost: "-9999"`.
2. Decrements `spec.replicas` by the number of victims.

The ReplicaSet controller then sorts the pod set by deletion cost
ascending and removes the annotated pods first.  The annotation is
stable since Kubernetes 1.22.

If `machine_ids` covers every pod in the request - or the caller passes
the deployment-name shortcut - the handler skips the cost annotation
and deletes the Deployment entirely so no idle controller is left behind.

The handler never deletes pods directly.  The controller stays in charge
of the actual termination so that `PodDisruptionBudget` and
`maxUnavailable` invariants remain honoured.

## `KubernetesStatefulSet`

The StatefulSet handler creates one `apps/v1/StatefulSet` per ORB
request.  Pod ordinals are stable (`<sts-name>-0`, `<sts-name>-1`, ...)
which means selective release reduces to "scale down to the smallest
ordinal that keeps the surviving set contiguous":

* For `release_hosts([m_k])` the handler maps `m_k` to its ordinal `k`
  and patches `spec.replicas = max(ordinal_of_surviving_pods)+1`.  All
  pods with ordinal `>= replicas` are removed by the controller in
  reverse-ordinal order.
* Selective release of a non-tail pod (`m_1` in a 3-replica set) is
  rejected with a clear error - the StatefulSet ordering contract
  cannot honour it.  Callers wanting that behaviour should pick the
  Deployment handler instead.

Per-replica volumes declared via `volumeClaimTemplates` are honoured
unchanged.

## `KubernetesJob`

The Job handler maps one ORB request to one `batch/v1/Job` with
`parallelism = completions = request.requested_count`.  Every requested
unit must exit `0` for the Job (and the ORB request) to be considered
complete.

Crucial invariants:

* **`backoffLimit = 0`** - ORB owns retry semantics at the request
  level.  The Job controller must not silently restart failed pods.
* **`restartPolicy = Never`** on the pod template - required when
  `backoffLimit=0`.
* **Selective release is not supported.**  `release_hosts` deletes the
  Job (cascade-deletes pods) regardless of how many `machine_ids` the
  caller passes.  Requested IDs are logged at info level for audit.

Choose the Job handler whenever the workload has a well-defined exit
condition.  If you find yourself needing to keep some Job pods running
after others succeed, the workload is conceptually a Deployment, not a
Job - switch handlers.

## Cross-handler defaults

All handlers honour the provider-level defaults from
[`KubernetesProviderConfig`](configuration.md):

* `default_node_selector` is merged into every pod's `nodeSelector`.
* `default_tolerations` are appended to every pod's `tolerations`.
* `default_image_pull_secret` is added when none is set on the template.
* `label_prefix` controls the namespace of ORB-emitted labels.

Per-template values always win over provider-level defaults.  ORB's
own labels (`orb.io/managed`, `orb.io/request-id`, `orb.io/machine-id`,
`orb.io/provider-api`) cannot be overridden - the reconciler and the
HostFactory adapter depend on them.

## Choosing a `provider_api` at template-generate time

```bash
# Pod (default)
orb templates generate --provider kubernetes --provider-api KubernetesPod

# Deployment
orb templates generate --provider kubernetes --provider-api KubernetesDeployment

# StatefulSet
orb templates generate --provider kubernetes --provider-api KubernetesStatefulSet

# Job
orb templates generate --provider kubernetes --provider-api KubernetesJob
```

Existing templates can be retargeted by editing the `provider_api` field
in-place.  The handler dispatch happens at request time, not at
template-create time.
