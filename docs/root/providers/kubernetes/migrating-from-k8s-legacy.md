# Migrating from `orb.k8s_legacy` to the modern Kubernetes provider

This page is for operators currently running the legacy
Symphony-on-Kubernetes HostFactory plugin (`orb k8s-legacy ...`, or the
older `open-resource-broker` PyPI distribution) who want to move to the
new Kubernetes provider under `orb.providers.kubernetes`.

The two implementations target the same problem space but they are
**not the same code path**.  This guide spells out the differences so
operators can plan their migration with full visibility.

## tl;dr - what changes

| Concern                          | `orb.k8s_legacy` (legacy)                                          | `orb.providers.kubernetes` (modern)                                 |
|----------------------------------|--------------------------------------------------------------------|---------------------------------------------------------------------|
| Install extra                    | `pip install "orb-py[k8s-legacy]"`                                 | `pip install "orb-py[kubernetes]"`                                  |
| Code location                    | `src/orb/k8s_legacy/`                                              | `src/orb/providers/kubernetes/`                                     |
| Architecture                     | Standalone HostFactory plugin with its own watchers + storage      | First-class ORB provider behind the standard `ProviderStrategy` contract |
| State store                      | Filesystem workdir (`/var/tmp/hostfactory`) + event log            | ORB primary storage (SQLite / DynamoDB / SQL - operator's choice)    |
| Templates                        | Legacy template format with HF-camelCase fields                    | ORB template aggregate (`provider_api`, `container_image`, etc.)    |
| Identifying labels               | `symphony/open-resource-broker-reqid`                              | `orb.io/managed`, `orb.io/request-id`, `orb.io/machine-id`           |
| Workloads supported              | Bare pods                                                          | Pod, Deployment, StatefulSet, Job (see [Handlers](handlers.md))     |
| HostFactory API                  | Native HF JSON (`requestMachines.sh` etc.)                         | Same HF JSON, via ORB's HostFactory adapter                          |
| CLI                              | `orb k8s-legacy <verb>`                                            | `orb machines request / requests status / machines return`           |

## Coexistence

The two providers can run side by side in the same cluster.  The
modern provider stamps `orb.io/managed=true` on its own resources; the
legacy plugin stamps `symphony/open-resource-broker-reqid` on its own.
Each watcher only cares about resources stamped with its own labels,
so cross-talk is not possible by accident.

If you want belt-and-braces during the cutover, leave
`emit_legacy_labels: true` (the default) in
[`KubernetesProviderConfig`](configuration.md).  The modern provider
will then stamp the legacy label as well, which is harmless and lets
operators query the cluster with the legacy label selector while
verifying the new provider behaves as expected.  Once cutover is
complete, flip it to `false`.

## Phased migration plan

1. **Install the modern extra alongside the legacy one**

   ```bash
   pip install "orb-py[kubernetes,k8s-legacy]"
   ```

   Both extras can be installed at the same time; the only shared
   dependency is `kubernetes`.

2. **Configure the modern provider in a non-production namespace**

   Create a fresh namespace and apply the RBAC bundle:

   ```bash
   kubectl create namespace orb-modern
   sed 's/namespace: orb/namespace: orb-modern/g' \
     docs/root/providers/kubernetes/rbac.yaml | kubectl apply -f -
   ```

   Add a `providers.kubernetes` block to `config.json` (see
   [Configuration reference](configuration.md)).

3. **Translate one template at a time**

   Use the field mapping table below to rewrite a single legacy
   template against the modern schema.  Generate the stub with:

   ```bash
   orb templates generate --provider kubernetes --provider-api KubernetesPod
   ```

   then fill in the legacy field equivalents.

4. **Cutover one HostFactory provider plugin at a time**

   The HostFactory shell scripts (`requestMachines.sh` etc.) can be
   pointed at either provider.  Cut over one plugin definition at a
   time so you can roll back per workload.

5. **Decommission the legacy plugin**

   Once every workload is on the modern provider and the modern
   provider has been stable for an operator-defined burn-in window:

   * set `emit_legacy_labels: false` in the modern provider config,
   * uninstall the legacy extra:

     ```bash
     pip uninstall orb-py  # then reinstall without [k8s-legacy]
     pip install "orb-py[kubernetes]"
     ```

## Template field mapping

| Legacy template field             | Modern equivalent                              | Notes                                                                       |
|-----------------------------------|------------------------------------------------|-----------------------------------------------------------------------------|
| `templateId` / `template_id`      | `template_id`                                  | Same field, snake_case at rest.                                              |
| `imageId`                         | `container_image`                              | Modern field always carries the full OCI ref (`registry/name:tag`).          |
| `attributes.namespace`            | `namespace` (on template) or `namespace` (on provider config) | Per-template wins.                                                          |
| `attributes.serviceAccountName`   | `service_account`                              | Maps to `spec.serviceAccountName`.                                           |
| `attributes.runtimeClassName`     | `runtime_class`                                | Maps to `spec.runtimeClassName`.                                             |
| `attributes.nodeSelector`         | `node_selector`                                | Same `dict[str,str]` shape.                                                  |
| `attributes.tolerations`          | `tolerations`                                  | Same `list[dict]` shape.                                                     |
| `attributes.resources.requests`   | `resource_requests`                            | Same `dict[str,str]` shape (e.g. `{"cpu":"1","memory":"2Gi"}`).              |
| `attributes.resources.limits`     | `resource_limits`                              | Same shape as requests.                                                      |
| `attributes.environment`          | `environment_variables`                        | `dict[str,str]`.                                                             |
| `attributes.imagePullSecrets`     | `image_pull_secrets`                           | `list[str]`.                                                                 |
| `attributes.labels`               | `labels`                                       | ORB-emitted labels always win when key conflicts arise.                      |
| `attributes.annotations`          | `annotations`                                  | Free-form passthrough.                                                       |
| n/a                               | `provider_api`                                 | New required field - `KubernetesPod`, `KubernetesDeployment`, `KubernetesStatefulSet`, or `KubernetesJob`. |

## Label deltas

The modern provider emits a stable, namespaced label set so multiple
ORB instances can coexist in the same cluster:

| Modern label                       | Meaning                                                  |
|------------------------------------|----------------------------------------------------------|
| `orb.io/managed: "true"`           | Resource is owned by ORB.  The orphan-GC reconciler only considers resources carrying this label. |
| `orb.io/request-id: "<request>"`   | The ORB request this resource belongs to.                |
| `orb.io/machine-id: "<machine>"`   | The ORB machine ID for this resource.                    |
| `orb.io/provider-api: "<api>"`     | Which handler created the resource.                      |

The legacy label `symphony/open-resource-broker-reqid` is emitted in
parallel by default (`emit_legacy_labels: true`).  Operators can change
the prefix via `label_prefix` if `orb.io` collides with another in-house
namespace.

## Behaviour deltas

| Area                       | Legacy behaviour                                              | Modern behaviour                                                       |
|----------------------------|----------------------------------------------------------------|------------------------------------------------------------------------|
| Storage of request state   | Filesystem workdir; events written to a binary event log       | ORB primary storage (whatever strategy the operator configured)         |
| Watch model                | Per-watcher daemons (`orb k8s-legacy watch pods` etc.)         | Single asyncio watch task per namespace, sharing an in-process cache    |
| Selective release          | Pod-by-pod delete (only valid for bare pods)                   | Pod, Deployment (cost-based), StatefulSet (ordinal-tail), Job (not supported) |
| Pod timeout policy         | Implicit, governed by HF retry semantics                       | Explicit `pod_timeout_seconds` on `KubernetesProviderConfig`            |
| Cluster compat             | Best-effort, no version gate                                   | `min_kubernetes_version` validated on health check                      |
| Orphan reconciliation      | None (legacy plugin assumed exclusive ownership of the namespace) | Periodic asyncio task; opt-in delete (`auto_cleanup_orphans`)         |
| HostFactory output         | Native legacy JSON                                              | Same on the wire, mapped via the ORB HostFactory adapter                |

## Things the modern provider does that the legacy plugin does not

* Multi-namespace and cluster-scoped watch modes.
* Controller-backed handlers (Deployment, StatefulSet, Job) with
  selective-release semantics that honour `PodDisruptionBudget`.
* Startup reconciliation - at boot, ORB lists managed resources in the
  cluster and reconciles them against its primary store before serving
  requests.
* First-class observability via the standard ORB metrics, logging port,
  and tracing surfaces.

## Things the legacy plugin does that the modern provider does not (yet)

* The legacy plugin ships its own admin / utils HTTP server; the modern
  provider relies on the standard ORB REST API instead.
* The legacy plugin's binary event log has no modern equivalent -
  consumers should pick up the standard ORB events (`bd list`-style
  audit is in primary storage, not in a separate log).

If any of the legacy-only features is load-bearing for your deployment,
please raise an issue before cutting over - the modern provider's roadmap
prioritises closing such gaps based on operator demand.
