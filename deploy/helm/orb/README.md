# ORB Helm Chart

Deploys Open Resource Broker (ORB) as a REST server in a Kubernetes cluster.

## Install

```bash
# Default install (k8s provider, auth off)
helm install orb ./deploy/helm/orb --namespace orb --create-namespace

# With custom values
helm install orb ./deploy/helm/orb \
  --namespace orb --create-namespace \
  -f my-values.yaml

# Upgrade
helm upgrade orb ./deploy/helm/orb --namespace orb -f my-values.yaml
```

## Key Values

| Value | Default | Description |
|---|---|---|
| `image.repository` | `ghcr.io/finos/open-resource-broker` | Container image |
| `image.tag` | chart `appVersion` | Image tag |
| `replicaCount` | `1` | Pod replicas; must be 1 with `json` or `sqlite` storage |
| `storage.type` | `json` | `json` or `sqlite` |
| `storage.persistence.enabled` | `true` | Keep `/app/data` on a PersistentVolumeClaim |
| `storage.persistence.size` | `1Gi` | Claim size |
| `storage.persistence.existingClaim` | `""` | Use a claim you manage |
| `ui.mode` | `embedded` | `embedded`, `split` or `disabled`; selects probe paths |
| `ingress.enabled` | `false` | Create an Ingress |
| `provider.type` | `k8s` | Active provider (`k8s` or `aws`) |
| `provider.k8s.namespaces` | `[]` | Namespaces the k8s provider manages; empty means the release namespace |
| `server.port` | `8000` | REST API port |
| `server.workers` | `1` | uvicorn worker count |
| `auth.enabled` | `false` | Enable authentication |
| `auth.strategy` | `none` | `none` or `bearer_token` |
| `auth.existingSecret` | `""` | Secret holding the JWT signing key (`bearer_token`) |
| `rbac.create` | `true` | Create a Role/RoleBinding in each managed namespace |
| `rbac.clusterScoped` | `false` | Also create ClusterRole/ClusterRoleBinding |

## Authentication

Auth is off by default. To require a bearer token, create a Secret holding the JWT signing key
(at least 32 bytes) and reference it:

```bash
kubectl create secret generic orb-auth --namespace orb \
  --from-literal=secret-key="$(openssl rand -base64 48)"
```

```yaml
auth:
  enabled: true
  strategy: bearer_token
  existingSecret: orb-auth
  # existingSecretKey: secret-key
```

The key reaches the server through the `ORB_AUTH_BEARER_SECRET_KEY` environment variable, which the
generated configuration references as `${ORB_AUTH_BEARER_SECRET_KEY}`; it is never written to the
ConfigMap. The chart refuses to render `auth.enabled: true` with `strategy: none` or with
`bearer_token` and no `existingSecret`. The `/livez`, `/readyz` and `/health` paths stay public.

Only `none` and `bearer_token` are supported. The `iam` and `cognito` strategies need provider
specific settings that the chart does not render yet.

## RBAC

With the k8s provider, the chart creates a `Role` and `RoleBinding` in each namespace the provider
manages, granting access to pods, deployments, statefulsets, jobs and events. By default
`provider.k8s.namespaces` is empty and the provider manages the release namespace only. To run
workloads in other namespaces, list them; the first entry becomes the default for templates that
name no namespace, unless the release namespace is in the list:

```yaml
provider:
  k8s:
    namespaces: [hpc-jobs, batch]
```

No Kubernetes RBAC is created when `provider.type` is `aws`.

For **cluster-scoped watch** (`provider.k8s.namespaces: ["*"]`) or **node watch** (`provider.k8s.nodeWatchEnabled: true`), enable the ClusterRole:

```yaml
rbac:
  clusterScoped: true
provider:
  k8s:
    namespaces: ["*"]
    nodeWatchEnabled: true
```

This grants nodes `get/list/watch` cluster-wide. Review with your security team before applying in shared clusters.
The chart refuses to render `namespaces: ["*"]` or `nodeWatchEnabled: true` without
`rbac.clusterScoped: true`, unless `rbac.create` is false and you manage RBAC yourself.

## Storage

`json` and `sqlite` store state in files under `/app/data` and support a single writer. The chart
therefore:

- renders `replicaCount` greater than 1 as an error,
- uses the `Recreate` update strategy so the old pod releases the volume before the new one starts,
- mounts a PersistentVolumeClaim at `/app/data` and annotates it `helm.sh/resource-policy: keep`
  so `helm uninstall` does not delete request and machine data.

The file backends rely on a single-host file lock, which does not protect two pods that write
the same volume. A `ReadWriteOnce` volume is also bound to one zone: if the node is lost the pod can
only restart where the volume can attach. Use a SQL or DynamoDB backend when that matters.

`helm uninstall` leaves the claim in place, so a later `helm install` with the same release name
fails because the claim already exists. Either delete the claim first
(`kubectl delete pvc <release>-data`, which discards the data) or reinstall with
`storage.persistence.existingClaim: <release>-data` to reuse it.

Set `storage.persistence.enabled: false` for throwaway installs. The data directory is then an
`emptyDir` and everything is lost when the pod restarts.

```yaml
storage:
  type: sqlite
  persistence:
    size: 5Gi
    storageClass: fast-ssd
    # existingClaim: my-claim
```

## Health Probes

| Probe | Path (embedded UI) | Path (`split` or `disabled`) |
|---|---|---|
| startup | `/orb/livez` | `/livez` |
| liveness | `/orb/livez` | `/livez` |
| readiness | `/orb/readyz` | `/readyz` |

`/livez` answers whenever the process is up and never checks a provider, so a slow or unreachable
provider cannot restart the pod. `/readyz` returns 503 while a core dependency, including a sole
provider, is unhealthy, which holds traffic back during a rollout. The startup probe allows up to
five minutes for the first boot.

Each probe can be tuned or given its own `path`; `probes.pathOverride` replaces the path of all three.

## UI Mode

`ui.mode` sets the `ui` block of the generated configuration and the probe paths.

| Mode | Behaviour |
|---|---|
| `embedded` | One process serves the API and UI; the API is at `/orb/api/v1`, the UI at `/orb` |
| `split` | API only at `/api/v1`; the UI is served elsewhere |
| `disabled` | API only at `/api/v1`, no UI |

## Ingress and TLS

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt
  hosts:
    - host: orb.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: orb-tls
      hosts:
        - orb.example.com
```

TLS ends at the ingress and the pod serves plain HTTP inside the cluster. A path may set
`serviceName` and `servicePort` to send it to another Service, for example a UI hosted outside
the chart when `ui.mode` is `split`.

## Container User

The pod runs as the numeric ids in `podSecurityContext` (`999:999`), with a read-only root
filesystem. These ids must match the non-root user in the image, because Kubernetes cannot verify
`runAsNonRoot` for a user given by name. The image creates its user as a system account, so the
ids follow the base image; if you build your own image with different ids, set
`podSecurityContext.runAsUser`, `runAsGroup` and `fsGroup` to match. `fsGroup` lets that user
write to the volume.

Writable paths are `/app/data` (volume), `/app/logs`, `/app/tmp`, `/app/work` and `/tmp` (`emptyDir`).

## Trusted Hosts

The server rejects requests whose `Host` header is not trusted with a 400. Loopback addresses,
the Service DNS names and the `ingress.hosts` entries are always trusted. Requests that address
the pod or a node by IP, such as Prometheus scraping pods by IP or `NodePort` and `LoadBalancer`
access, need those addresses or names added to `server.trustedHosts`.

## /metrics Caveat (Multi-Worker)

Each uvicorn worker process maintains its own in-process Prometheus registry. With `server.workers > 1`, scraping `/metrics` returns only one worker's counters depending on which process the load balancer routes to. For accurate metrics in multi-worker mode, use a shared metrics backend (Redis, Prometheus push gateway) or keep `server.workers: 1` and scale via `replicaCount` instead (each pod is fully scraped). Scaling out requires a storage backend that supports multiple writers.

## Server Entrypoint

The container runs `docker-entrypoint.sh serve`, which translates `HF_*` environment variables into:

```
orb server start --foreground --host HOST --port PORT [--workers N]
```

`docker-entrypoint.sh` has a fixed config-file scan order: `/app/config/docker.json` → `/app/config/production.json` → `/app/config/default_config.json`. It sets `HF_CONFIG_FILE` to whichever file it finds first. The chart mounts the generated ConfigMap as `/app/config/docker.json` (using `subPath`) so it is always picked up ahead of the baked-in `default_config.json`.
