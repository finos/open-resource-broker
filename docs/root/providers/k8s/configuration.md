# Kubernetes provider - configuration reference

This page documents every field on
[`K8sProviderConfig`](https://github.com/finos/open-resource-broker/blob/main/src/orb/providers/k8s/configuration/config.py).
The class is a pydantic-settings model with the `ORB_K8S_` env-var
prefix, so every field can also be set via env var.

## Where the config comes from

ORB loads provider config from three places in the following order
(later sources win):

1. The `providers.<name>` block in `config.json` (or whichever file is
   pointed at by `ORB_CONFIG_DIR`).
2. Environment variables of the form `ORB_K8S_<FIELD_NAME>`.
3. Per-template overrides on the template aggregate (see
   [Handlers](handlers.md)).

Nested fields use the `__` env-var delimiter.  Example:
`ORB_K8S_DEFAULT_NODE_SELECTOR__NODE_TYPE=compute`.

## Authentication and cluster targeting

| Field             | Type            | Default | Description                                                                                                  |
|-------------------|-----------------|---------|--------------------------------------------------------------------------------------------------------------|
| `kubeconfig_path` | `str \| None`   | `None`  | Explicit path to a kubeconfig file.  When unset the kubernetes client falls back to `KUBECONFIG` then `~/.kube/config`. |
| `context`         | `str \| None`   | `None`  | kubeconfig context to activate.  When unset the current context is used.                                     |
| `in_cluster`      | `bool \| None`  | `None`  | Force in-cluster (`True`) or kubeconfig (`False`) auth.  `None` auto-detects via the `/var/run/secrets/kubernetes.io` sentinel. |

See [Authentication](auth.md) for the decision matrix and worked
examples.

## Namespacing

| Field        | Type             | Default      | Description                                                                                                                                        |
|--------------|------------------|--------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| `namespace`  | `str`            | `"default"`  | Single-namespace mode target.  Used when `namespaces` is `None`.                                                                                   |
| `namespaces` | `list[str] \| None` | `None`    | Explicit list of namespaces to manage.  `None` falls back to `namespace`; `["*"]` runs a cluster-scoped watch and requires cluster-level RBAC.    |

Pick one of three modes:

* **Single namespace** - set `namespace` only.  This is the most common
  setup and the safest from an RBAC perspective.
* **Multi-namespace** - set `namespaces=["a", "b", "c"]`.  ORB runs one
  watch task per namespace and only needs namespaced RBAC in each.
* **Cluster-wide** - set `namespaces=["*"]`.  ORB runs a single
  cluster-scoped watch; you must grant `ClusterRole` instead of `Role`.

## Labels

ORB stamps every managed resource with a small set of identifying labels
so that operators (and ORB itself) can correlate cluster state with the
ORB database.

| Field                | Type     | Default     | Description                                                                                              |
|----------------------|----------|-------------|----------------------------------------------------------------------------------------------------------|
| `label_prefix`       | `str`    | `"orb.io"`  | DNS-subdomain prefix for ORB labels.  Must be a valid RFC 1123 subdomain (no slashes, no spaces).        |
| `emit_legacy_labels` | `bool`   | `True`      | When `True`, also emit the legacy `symphony/open-resource-broker-reqid` label alongside the modern one. |

With the defaults the provider stamps:

```yaml
metadata:
  labels:
    orb.io/managed: "true"
    orb.io/request-id: "<request-id>"
    orb.io/machine-id: "<machine-id>"
    orb.io/provider-api: "Pod"
    # When emit_legacy_labels=True:
    symphony/open-resource-broker-reqid: "<request-id>"
```

The legacy label is intended for coexistence with the
`orb.k8s_legacy` plugin; once the legacy watcher is decommissioned,
operators are expected to flip `emit_legacy_labels=False`.

## Pod defaults

These are baseline values applied to every managed pod, regardless of
which handler created it.  Per-template values, when present, win.

| Field                         | Type                    | Default | Description                                                          |
|-------------------------------|-------------------------|---------|----------------------------------------------------------------------|
| `default_node_selector`       | `dict[str,str] \| None` | `None`  | `nodeSelector` applied to every managed pod.                         |
| `default_tolerations`         | `list[dict] \| None`    | `None`  | `tolerations` applied to every managed pod.                          |
| `default_image_pull_secret`   | `str \| None`           | `None`  | Image pull secret name applied to every managed pod.                 |

## Timing

| Field                         | Type   | Default | Description                                                                                          |
|-------------------------------|--------|---------|------------------------------------------------------------------------------------------------------|
| `pod_timeout_seconds`         | `int`  | `300`   | Maximum seconds a pod may stay `Pending` before being treated as terminal (fulfilment fails).        |
| `stale_cache_timeout_seconds` | `int`  | `600`   | After the in-memory watch cache loses its watch task, this is how long stale reads may serve before the provider falls back to on-demand list calls. |

## Watch and reconciliation

| Field                         | Type   | Default | Description                                                                                          |
|-------------------------------|--------|---------|------------------------------------------------------------------------------------------------------|
| `watch_enabled`               | `bool` | `True`  | Operator-level kill switch for the asyncio watch task.  When `False` ORB falls back to polling.      |
| `min_kubernetes_version`      | `str`  | `"1.28"`| Minimum kube-API server version the provider supports.  Validated on health check.                  |
| `auto_cleanup_orphans`        | `bool` | `False` | When `True` the orphan garbage collector deletes managed pods that have no record in ORB storage.    |
| `orphan_gc_enabled`           | `bool` | `False` | Enables the periodic orphan-GC task.  Default off so operators can dry-run reconciliation first.    |
| `orphan_gc_interval_seconds`  | `int`  | `300`   | Poll interval for the orphan-GC task.                                                                |

### Operational note - orphan GC

The orphan GC only ever touches resources stamped with
`orb.io/managed=true` (or the customised `label_prefix`).  Cluster
resources without that label are invisible to it, by design.  Even with
`auto_cleanup_orphans=False`, orphans are logged at `WARNING` so
operators can spot drift before enabling delete.

## Environment-variable cheat sheet

A handful of frequently set env vars:

```bash
# Auth
export ORB_K8S_KUBECONFIG_PATH="$HOME/.kube/dev"
export ORB_K8S_CONTEXT="dev-cluster"
export ORB_K8S_IN_CLUSTER="false"

# Namespacing
export ORB_K8S_NAMESPACE="orb"
# Multi-namespace mode (JSON list parsed by pydantic-settings):
export ORB_K8S_NAMESPACES='["team-a","team-b"]'

# Labels
export ORB_K8S_LABEL_PREFIX="orb.example.com"
export ORB_K8S_EMIT_LEGACY_LABELS="false"

# Reconciliation
export ORB_K8S_ORPHAN_GC_ENABLED="true"
export ORB_K8S_AUTO_CLEANUP_ORPHANS="false"
```

## Worked example

```json
{
  "providers": {
    "k8s": {
      "provider_type": "k8s",
      "kubeconfig_path": "/etc/orb/kubeconfig",
      "context": "prod",
      "namespaces": ["orb", "orb-batch"],
      "label_prefix": "orb.example.com",
      "emit_legacy_labels": false,
      "default_node_selector": {"workload": "orb"},
      "default_tolerations": [
        {"key": "orb", "operator": "Exists", "effect": "NoSchedule"}
      ],
      "default_image_pull_secret": "orb-registry",
      "pod_timeout_seconds": 240,
      "watch_enabled": true,
      "min_kubernetes_version": "1.28",
      "orphan_gc_enabled": true,
      "orphan_gc_interval_seconds": 600,
      "auto_cleanup_orphans": false
    }
  }
}
```

This is the configuration of a production deployment that:

* Authenticates out-of-cluster via a dedicated kubeconfig.
* Manages two namespaces, but stays out of every other namespace.
* Brands its labels under `orb.example.com` and has cut over from the
  legacy label set.
* Pins a node selector and a toleration so ORB-managed pods land on a
  dedicated node pool.
* Polls for orphans every ten minutes but only logs them - operators
  must inspect and approve before flipping `auto_cleanup_orphans=True`.
