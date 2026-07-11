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

| Field             | Type            | Default | Env var                    | Description                                                                                                  |
|-------------------|-----------------|---------|----------------------------|--------------------------------------------------------------------------------------------------------------|
| `kubeconfig_path` | `str \| None`   | `None`  | `ORB_K8S_KUBECONFIG_PATH`  | Explicit path to a kubeconfig file.  When unset the kubernetes client falls back to `KUBECONFIG` then `~/.kube/config`. |
| `context`         | `str \| None`   | `None`  | `ORB_K8S_CONTEXT`          | kubeconfig context to activate.  When unset the current context is used.                                     |
| `in_cluster`      | `bool \| None`  | `None`  | `ORB_K8S_IN_CLUSTER`       | Force in-cluster (`True`) or kubeconfig (`False`) auth.  `None` auto-detects via the `/var/run/secrets/kubernetes.io` sentinel. |

See [Authentication](auth.md) for the decision matrix and worked
examples.

## Namespacing

| Field        | Type                  | Default | Env var            | Description                                                                                                                                        |
|--------------|-----------------------|---------|--------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| `namespace`  | `str \| None`         | `None`  | `ORB_K8S_NAMESPACE`| Single-namespace mode target.  When `None` (default) the provider auto-detects the namespace from the in-cluster ServiceAccount token file and falls back to `"default"` for out-of-cluster deployments. |
| `namespaces` | `list[str] \| None`   | `None`  | `ORB_K8S_NAMESPACES`| Explicit list of namespaces to manage.  `None` falls back to `namespace`; `["*"]` runs a cluster-scoped watch and requires cluster-level RBAC.    |

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

| Field                | Type     | Default     | Env var                        | Description                                                                                              |
|----------------------|----------|-------------|--------------------------------|----------------------------------------------------------------------------------------------------------|
| `label_prefix`       | `str`    | `"orb.io"`  | `ORB_K8S_LABEL_PREFIX`         | DNS-subdomain prefix for ORB labels.  Must be a valid RFC 1123 subdomain (no slashes, no spaces).        |
| `emit_legacy_labels` | `bool`   | `True`      | `ORB_K8S_EMIT_LEGACY_LABELS`   | When `True`, also emit the legacy `symphony/open-resource-broker-reqid` label alongside the modern one. |

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

| Field                         | Type                    | Default | Env var                              | Description                                                          |
|-------------------------------|-------------------------|---------|--------------------------------------|----------------------------------------------------------------------|
| `default_node_selector`       | `dict[str,str] \| None` | `None`  | `ORB_K8S_DEFAULT_NODE_SELECTOR__*`   | `nodeSelector` applied to every managed pod.                         |
| `default_tolerations`         | `list[dict] \| None`    | `None`  | `ORB_K8S_DEFAULT_TOLERATIONS`        | `tolerations` applied to every managed pod.                          |
| `default_image_pull_secret`   | `str \| None`           | `None`  | `ORB_K8S_DEFAULT_IMAGE_PULL_SECRET`  | Image pull secret name applied to every managed pod.                 |

## Timing

| Field                         | Type   | Default | Env var                              | Description                                                                                          |
|-------------------------------|--------|---------|--------------------------------------|------------------------------------------------------------------------------------------------------|
| `pod_timeout_seconds`         | `int`  | `300`   | `ORB_K8S_POD_TIMEOUT_SECONDS`        | Maximum seconds a pod may stay `Pending` before being treated as terminal (fulfilment fails).        |
| `delete_timed_out_pods`       | `bool` | `True`  | `ORB_K8S_DELETE_TIMED_OUT_PODS`      | When `True` (default), pods that have been `Pending` past `pod_timeout_seconds` are deleted immediately after their status is rewritten to `terminated`.  Set to `False` to preserve timed-out pods for operator inspection. |
| `stale_cache_timeout_seconds` | `int`  | `600`   | `ORB_K8S_STALE_CACHE_TIMEOUT_SECONDS`| After the in-memory watch cache loses its watch task, stale reads may serve for this many seconds before the provider falls back to on-demand list calls. |

## Watch and reconciliation

| Field                         | Type   | Default | Env var                               | Description                                                                                          |
|-------------------------------|--------|---------|---------------------------------------|------------------------------------------------------------------------------------------------------|
| `watch_enabled`               | `bool` | `True`  | `ORB_K8S_WATCH_ENABLED`               | Operator-level kill switch for the asyncio watch task.  When `False` ORB falls back to polling.      |
| `min_kubernetes_version`      | `str`  | `"1.28"`| `ORB_K8S_MIN_KUBERNETES_VERSION`      | Minimum kube-API server version the provider supports.  Validated on health check.                  |
| `auto_cleanup_orphans`        | `bool` | `False` | `ORB_K8S_AUTO_CLEANUP_ORPHANS`        | When `True` the orphan GC deletes managed pods that have no record in ORB storage.    |
| `orphan_gc_enabled`           | `bool` | `False` | `ORB_K8S_ORPHAN_GC_ENABLED`           | Enables the periodic orphan-GC task.  Default off so operators can dry-run reconciliation first.    |
| `orphan_gc_interval_seconds`  | `int`  | `300`   | `ORB_K8S_ORPHAN_GC_INTERVAL_SECONDS`  | Poll interval for the orphan-GC task.                                                                |
| `orphan_min_age_seconds`      | `int`  | `300`   | `ORB_K8S_ORPHAN_MIN_AGE_SECONDS`      | Orphan pods younger than this many seconds are skipped by the GC to avoid races against in-flight request commits. |
| `periodic_resync_interval_seconds` | `int` | `0` | `ORB_K8S_PERIODIC_RESYNC_INTERVAL_SECONDS` | When > 0, the pod watcher performs a full LIST reconcile against the in-process cache at this interval (seconds), independent of 410-Gone responses.  `0` disables the backstop.  Recommended value when enabled: `180`. |

### Operational note - orphan GC

The orphan GC only ever touches resources stamped with
`orb.io/managed=true` (or the customised `label_prefix`).  Cluster
resources without that label are invisible to it, by design.  Even with
`auto_cleanup_orphans=False`, orphans are logged at `WARNING` so
operators can spot drift before enabling delete.

### Operational note - periodic resync backstop

The `periodic_resync_interval_seconds` field enables a background task
that periodically issues a full LIST of managed pods and reconciles the
result against the in-process watch cache.  This guards against rare
cache drift that can occur on apiservers with aggressive connection
timeouts.  Enable it with a value such as `180` in environments where
the watch stream has historically been unreliable.  Leave it at `0`
(the default) when the watch is stable to avoid unnecessary apiserver
load.

## Security audit

| Field                        | Type   | Default | Env var                                 | Description                                                                                          |
|------------------------------|--------|---------|-----------------------------------------|------------------------------------------------------------------------------------------------------|
| `audit_high_risk_pod_fields` | `bool` | `True`  | `ORB_K8S_AUDIT_HIGH_RISK_POD_FIELDS`   | Enable or disable the pod-spec security audit.  Set to `False` to silence all audit warnings.        |
| `reject_high_risk_pod_fields`| `bool` | `True`  | `ORB_K8S_REJECT_HIGH_RISK_POD_FIELDS`  | When `True` (default), ORB raises a `K8sError` instead of logging a warning when any high-risk field is found.  Set to `False` to revert to warning-only behaviour — operators must opt out explicitly. |

See [Security hardening](security-hardening.md) for the full list of
audited fields and reject-mode behaviour.

## Opt-in features

These features are off by default and require additional RBAC grants
when enabled.  See [`rbac.yaml`](rbac.yaml) for the required rules
(clearly commented per feature).

| Field                      | Type   | Default | Env var                          | Description                                                                                         |
|----------------------------|--------|---------|----------------------------------|-----------------------------------------------------------------------------------------------------|
| `node_watch_enabled`       | `bool` | `False` | `ORB_K8S_NODE_WATCH_ENABLED`     | Starts a background node-state watcher that caches per-node metadata (instance type, zone, capacity type, CPU/memory capacity).  Requires a cluster-scoped `nodes: get/list/watch` RBAC grant. |
| `events_watch_enabled`     | `bool` | `False` | `ORB_K8S_EVENTS_WATCH_ENABLED`   | Starts a background Events API watcher that streams Node events and surfaces Karpenter disruption reasons.  Requires `events: get/list/watch` RBAC. |
| `inbound_auth_enabled`     | `bool` | `False` | `ORB_K8S_INBOUND_AUTH_ENABLED`   | Enables Kubernetes ServiceAccount Bearer-token validation of inbound ORB REST API calls via the `authentication.k8s.io/v1 TokenReview` API.  Requires `system:auth-delegator` ClusterRoleBinding (or targeted `tokenreviews: create`).  See [Authentication](auth.md#inbound-tokenreview-auth). |

## Controller status cache

| Field                           | Type    | Default | Env var                                    | Description                                                                              |
|---------------------------------|---------|---------|--------------------------------------------|------------------------------------------------------------------------------------------|
| `controller_status_cache_ttl_seconds` | `float` | `5.0` | `ORB_K8S_CONTROLLER_STATUS_CACHE_TTL_SECONDS` | Seconds to serve a cached controller status read before re-issuing the GET.  Applied to Deployment, StatefulSet, and Job status polls.  Set to `0` to disable caching entirely. |

## Resilience — circuit breaker and retries

| Field                             | Type    | Default | Env var                                  | Description                                                                                          |
|-----------------------------------|---------|---------|------------------------------------------|------------------------------------------------------------------------------------------------------|
| `circuit_breaker_failure_threshold` | `int` | `5`     | `ORB_K8S_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | Consecutive apiserver failures that trip the per-handler circuit breaker.                        |
| `circuit_breaker_reset_timeout`   | `int`   | `60`    | `ORB_K8S_CIRCUIT_BREAKER_RESET_TIMEOUT`  | Seconds after the circuit opens before the breaker transitions to half-open.                         |
| `max_retries`                     | `int`   | `3`     | `ORB_K8S_MAX_RETRIES`                    | Maximum retry attempts for transient errors (429/5xx).  Non-recoverable codes (400/403/404/409/410/422) are never retried. |
| `retry_base_delay`                | `float` | `1.0`   | `ORB_K8S_RETRY_BASE_DELAY`               | Base delay in seconds for exponential-backoff retries.                                               |
| `retry_max_delay`                 | `float` | `30.0`  | `ORB_K8S_RETRY_MAX_DELAY`                | Cap on the exponential-backoff delay.                                                                |

## Observability

| Field             | Type   | Default | Env var                   | Description                                                                                          |
|-------------------|--------|---------|---------------------------|------------------------------------------------------------------------------------------------------|
| `metrics_enabled` | `bool` | `True`  | `ORB_K8S_METRICS_ENABLED` | When `True` (default), the provider registers Prometheus metrics for acquire/release counts, pod-creation outcomes, watch events, and watch reconnects.  Set to `False` to disable metric emission entirely. |

## Native spec escape hatch

| Field                  | Type   | Default | Env var                         | Description                                                                                          |
|------------------------|--------|---------|---------------------------------|------------------------------------------------------------------------------------------------------|
| `native_spec_enabled`  | `bool` | `False` | `ORB_K8S_NATIVE_SPEC_ENABLED`   | Opt-in flag for the native-spec escape hatch.  When `True`, handlers pass a rendered kubernetes API body straight to the SDK, bypassing the typed spec builders.  Both this flag and the application-level `native_spec.enabled` must be `True` for the hatch to fire. |

See [Native spec escape hatch](native-spec.md) for full details.

## Resource naming

The `naming` field hosts a nested `K8sNamingConfig` model that controls
how ORB generates resource names.  All nested fields use `__` as the
env-var delimiter (e.g. `ORB_K8S_NAMING__PREFIX=my-orb`).

| Field                        | Type  | Default | Env var                              | Description                                                                                          |
|------------------------------|-------|---------|--------------------------------------|------------------------------------------------------------------------------------------------------|
| `naming.prefix`              | `str` | `"orb"` | `ORB_K8S_NAMING__PREFIX`             | Name prefix applied to every managed resource.  Must be a valid DNS-1123 label segment (lowercase alphanumeric and hyphens, max 20 chars). |
| `naming.uuid_chars`          | `int` | `20`    | `ORB_K8S_NAMING__UUID_CHARS`         | Number of hex characters (8–32) taken from the request UUID to form the name's uuid segment. |
| `naming.max_pod_name_len`    | `int` | `63`    | `ORB_K8S_NAMING__MAX_POD_NAME_LEN`   | Maximum DNS-1123 label length for Pod names (default 63). |
| `naming.max_deployment_name_len` | `int` | `47` | `ORB_K8S_NAMING__MAX_DEPLOYMENT_NAME_LEN` | Maximum length for Deployment names.  Default 47 = 63 minus a 16-char ReplicaSet controller suffix budget. |
| `naming.max_statefulset_name_len` | `int` | `57` | `ORB_K8S_NAMING__MAX_STATEFULSET_NAME_LEN` | Maximum length for StatefulSet names.  Default 57 = 63 minus a 6-char ordinal suffix budget. |
| `naming.max_job_name_len`    | `int` | `50`    | `ORB_K8S_NAMING__MAX_JOB_NAME_LEN`   | Maximum length for Job names.  Default 50 = 63 minus a 13-char controller suffix margin. |

### Naming pattern

```
Deployment / StatefulSet / Job:  <prefix>-<uuid_segment>
Pod:                             <prefix>-<uuid_segment>-<seq:04d>
```

where `uuid_segment` is the first `uuid_chars` characters of the
hyphen-stripped request UUID.

**Recovery is via label, not name.**  ORB identifies managed resources
using the `orb.io/request-id` label, so names are cosmetic.  The
defaults reproduce the historical naming pattern, ensuring that
existing resources are unaffected on upgrade.

**DNS-1123 constraint.**  Kubernetes resource names must be valid
DNS-1123 labels (lowercase alphanumeric and hyphens, max 253 chars).
The per-kind `max_*_name_len` budgets account for controller-appended
suffixes (ReplicaSet hash, StatefulSet ordinal, Job suffix) so the
final pod names remain within the DNS-1123 limit.  A model validator
raises an error at startup if the configured `prefix` and `uuid_chars`
overflow the per-kind budget.

## Environment-variable cheat sheet

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

# Timing
export ORB_K8S_POD_TIMEOUT_SECONDS="240"
export ORB_K8S_DELETE_TIMED_OUT_PODS="true"

# Security audit (reject mode is on by default)
export ORB_K8S_REJECT_HIGH_RISK_POD_FIELDS="false"   # opt out for privileged workloads

# Reconciliation
export ORB_K8S_ORPHAN_GC_ENABLED="true"
export ORB_K8S_AUTO_CLEANUP_ORPHANS="false"
export ORB_K8S_PERIODIC_RESYNC_INTERVAL_SECONDS="180"

# Opt-in features
export ORB_K8S_EVENTS_WATCH_ENABLED="true"
export ORB_K8S_INBOUND_AUTH_ENABLED="true"

# Resource naming
export ORB_K8S_NAMING__PREFIX="my-orb"
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
      "delete_timed_out_pods": true,
      "watch_enabled": true,
      "min_kubernetes_version": "1.28",
      "orphan_gc_enabled": true,
      "orphan_gc_interval_seconds": 600,
      "auto_cleanup_orphans": false,
      "periodic_resync_interval_seconds": 180,
      "events_watch_enabled": true,
      "reject_high_risk_pod_fields": true,
      "naming": {
        "prefix": "orb",
        "uuid_chars": 20
      }
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
* Polls for orphans every ten minutes but only logs them — operators
  must inspect and approve before flipping `auto_cleanup_orphans=True`.
* Runs the periodic resync backstop every 180 seconds to catch any
  cache drift.
* Enables the events watcher for Karpenter disruption visibility.
* Keeps reject mode enabled (the default) so high-risk pod specs are
  blocked at submit time.
