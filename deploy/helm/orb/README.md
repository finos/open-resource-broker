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
| `replicaCount` | `1` | Pod replicas (see /metrics caveat) |
| `provider.type` | `k8s` | Active provider (`k8s` or `aws`) |
| `server.port` | `8000` | REST API port |
| `server.workers` | `1` | uvicorn worker count |
| `auth.enabled` | `false` | Enable authentication |
| `auth.strategy` | `none` | Auth strategy |
| `auth.existingSecret` | `""` | Secret name for bearer token |
| `rbac.create` | `true` | Create namespaced Role/RoleBinding |
| `rbac.clusterScoped` | `false` | Also create ClusterRole/ClusterRoleBinding |

## Authentication

Auth defaults to **off** (matching the ORB `AuthConfig` schema default). To enable bearer token auth:

```yaml
auth:
  enabled: true
  strategy: bearer_token
  existingSecret: orb-auth   # kubectl create secret generic orb-auth --from-literal=bearer-token=<token>
```

For IAM auth on AWS set `auth.strategy: iam` and annotate the ServiceAccount with an IRSA role ARN via `serviceAccount.annotations`.

## RBAC

By default a namespaced `Role` + `RoleBinding` is created granting the k8s provider access to pods, deployments, statefulsets, jobs, and events within the release namespace.

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

## /metrics Caveat (Multi-Worker)

Each uvicorn worker process maintains its own in-process Prometheus registry. With `server.workers > 1`, scraping `/metrics` returns only one worker's counters depending on which process the load balancer routes to. For accurate metrics in multi-worker mode, use a shared metrics backend (Redis, Prometheus push gateway) or keep `server.workers: 1` and scale via `replicaCount` instead (each pod is fully scraped).

## Server Entrypoint

The container runs `docker-entrypoint.sh serve`, which translates `HF_*` environment variables into:

```
orb server start --foreground --host HOST --port PORT [--workers N]
```

`docker-entrypoint.sh` has a fixed config-file scan order: `/app/config/docker.json` → `/app/config/production.json` → `/app/config/default_config.json`. It sets `HF_CONFIG_FILE` to whichever file it finds first. The chart mounts the generated ConfigMap as `/app/config/docker.json` (using `subPath`) so it is always picked up ahead of the baked-in `default_config.json`.

## Probe Path with UI Embedded Mode

When `ui.enabled=true` with embedded mode, FastAPI mounts the application at `/orb`, which removes the root `/health` route. Override the probe path so liveness/readiness checks still work:

```yaml
probes:
  path: /orb/health
```
