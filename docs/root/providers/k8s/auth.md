# Kubernetes provider - authentication

The Kubernetes provider supports two authentication paths to the
kube-API server:

* **In-cluster** - ORB itself runs as a pod and uses its mounted
  service-account token.
* **kubeconfig** - ORB runs outside the cluster and loads a kubeconfig
  file (the same one `kubectl` uses).

The decision happens at provider initialisation and is influenced by the
`in_cluster`, `kubeconfig_path`, and `context` fields on
[`K8sProviderConfig`](configuration.md).

## Decision matrix

| `in_cluster` config | `/var/run/secrets/kubernetes.io` exists | Outcome                                       |
|---------------------|----------------------------------------|-----------------------------------------------|
| `None` (default)    | yes                                    | In-cluster auth                                |
| `None` (default)    | no                                     | kubeconfig (path / KUBECONFIG / `~/.kube/config`) |
| `True`              | yes                                    | In-cluster auth                                |
| `True`              | no                                     | `KubernetesAuthError` - sentinel missing       |
| `False`             | yes                                    | kubeconfig (the in-cluster mount is ignored)   |
| `False`             | no                                     | kubeconfig                                     |

The sentinel path is the canonical one used by the upstream
`kubernetes` Python client, so detection matches the wider ecosystem.

## In-cluster auth

This is the recommended deployment shape for ORB-as-a-controller: ORB
runs inside the same cluster it manages, scoped to a ServiceAccount
with a tightly-scoped Role.

### When it triggers

* `in_cluster: True` is set explicitly, **or**
* the sentinel `/var/run/secrets/kubernetes.io` exists at process start
  (which is automatically the case for every pod that has a mounted
  service-account token).

### What ORB needs

* A ServiceAccount in the namespace ORB will manage.
* A Role (or ClusterRole, for cluster-scoped watches) granting the verbs
  listed in [`rbac.yaml`](rbac.yaml).
* A RoleBinding (or ClusterRoleBinding) tying the two together.

The minimum-viable manifest lives at [`rbac.yaml`](rbac.yaml) and can be
applied directly.

### Token rotation

Modern Kubernetes uses **projected ServiceAccount tokens** with
automatic rotation.  The `kubernetes` Python client picks up the
rotated token on the next API call, so ORB does not need to restart on
rotation.  No action required.

### Troubleshooting

| Symptom                                                                | Likely cause                                            | Fix                                                                    |
|------------------------------------------------------------------------|----------------------------------------------------------|------------------------------------------------------------------------|
| `KubernetesAuthError: Failed to load in-cluster config`                | Pod's ServiceAccount token is missing or unreadable      | Confirm the pod spec mounts a SA token; check `automountServiceAccountToken`. |
| `403 Forbidden` on `list_namespaced_pod`                               | Role missing `pods/list`                                 | Apply [`rbac.yaml`](rbac.yaml).                                        |
| `404 Not Found` on `delete_namespaced_pod`                             | Operating in the wrong namespace                         | Set `ORB_K8S_NAMESPACE` to the namespace the SA is bound to.   |
| Watch task dies repeatedly                                             | API-server timing out long-running connections           | The provider auto-restarts watches; persistent failures usually point to an upstream proxy with aggressive idle timeouts. |

## kubeconfig auth

This path is used for development against a remote cluster
(`kind`/`minikube`/EKS via `aws eks update-kubeconfig`) and for
out-of-cluster control planes.

### Precedence

ORB resolves the kubeconfig file path in this order:

1. The `kubeconfig_path` field in provider config.
2. The `KUBECONFIG` environment variable (the standard kubernetes
   precedence).
3. The default `~/.kube/config`.

The context is resolved in this order:

1. The `context` field in provider config.
2. The `current-context` field in the kubeconfig itself.

### What ORB needs

* A user / token / certificate entry in the kubeconfig that maps to a
  ClusterRole or Role with the verbs in [`rbac.yaml`](rbac.yaml).
* For EKS, the standard `aws eks update-kubeconfig` flow plus an IAM
  user/role mapped via `aws-auth`.

### Worked examples

#### Local kind cluster

```bash
kind create cluster --name orb-dev
kubectl --context kind-orb-dev apply -f docs/root/providers/k8s/rbac.yaml
export ORB_K8S_CONTEXT="kind-orb-dev"
export ORB_K8S_NAMESPACE="orb"
orb machines request my-template 3
```

#### EKS via federated identity

```bash
aws eks update-kubeconfig --region eu-west-1 --name prod
kubectl --context arn:aws:eks:eu-west-1:123456789012:cluster/prod \
  apply -f docs/root/providers/k8s/rbac.yaml
export ORB_K8S_CONTEXT="arn:aws:eks:eu-west-1:123456789012:cluster/prod"
export ORB_K8S_NAMESPACE="orb"
orb machines request my-template 3
```

#### Multiple kubeconfigs

```bash
export ORB_K8S_KUBECONFIG_PATH="/etc/orb/kubeconfig-prod"
export ORB_K8S_CONTEXT="prod"
```

### Troubleshooting

| Symptom                                                            | Likely cause                                | Fix                                                                |
|--------------------------------------------------------------------|---------------------------------------------|--------------------------------------------------------------------|
| `KubernetesAuthError: Failed to load kubeconfig (config_file=None, ...)` | No kubeconfig discoverable                  | Set `kubeconfig_path` or `KUBECONFIG`, or place a file at `~/.kube/config`. |
| `KubernetesAuthError: ... unknown context`                         | Typo in `context` field                     | Run `kubectl config get-contexts` to list valid names.             |
| Auth works locally but fails when ORB runs as a system service     | The service env does not inherit `$HOME`    | Set `ORB_K8S_KUBECONFIG_PATH` to an absolute path.          |
| Auth works initially then 401s after some hours (EKS)              | Federated token expired                     | Use `aws eks update-kubeconfig` with a long-lived identity, or run ORB in-cluster with IRSA. |

## Inbound TokenReview auth

ORB can optionally validate Bearer tokens on its own REST API by
submitting them to the Kubernetes
`authentication.k8s.io/v1 TokenReview` endpoint.  This lets callers
of ORB's REST API authenticate with a Kubernetes ServiceAccount token
instead of a separate credential system.

### Enabling

Set `inbound_auth_enabled: true` in the provider config (or
`ORB_K8S_INBOUND_AUTH_ENABLED=true`) to register the
`KubeAuthStrategy` with ORB's auth registry.

```json
{
  "providers": {
    "k8s": {
      "provider_type": "k8s",
      "namespace": "orb",
      "inbound_auth_enabled": true
    }
  }
}
```

### How it works

1. An inbound REST request arrives with an `Authorization: Bearer <token>` header.
2. ORB extracts the token and submits it to `authentication.k8s.io/v1 TokenReview`
   on the cluster.
3. If the API server reports the token is authenticated, the
   `system:serviceaccount:<namespace>:<name>` principal is extracted.
4. The principal is mapped to an ORB role using the `sa_role_mapping`
   in the auth config.  Principals not in the map receive the default
   `["user"]` role.  Exact matches (`namespace:sa-name`) take priority;
   wildcard namespace matches (`*:sa-name`) are tried next.

TokenReview is preferred over local JWKS validation because the API
server is the authoritative signer — there is no possibility of ORB
accepting a token the cluster itself would reject.

### Required RBAC

The ORB pod's ServiceAccount must be able to create `TokenReview`
objects.  The recommended approach is to bind the
`system:auth-delegator` ClusterRole (a standard Kubernetes role that
grants precisely this permission):

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: orb-auth-delegator
subjects:
  - kind: ServiceAccount
    name: orb
    namespace: orb
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: system:auth-delegator
```

Alternatively, grant only `tokenreviews: create` on the
`authentication.k8s.io` API group via a targeted ClusterRole:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: orb-tokenreview
rules:
  - apiGroups: ["authentication.k8s.io"]
    resources: ["tokenreviews"]
    verbs: ["create"]
```

Both options are included (commented out) in [`rbac.yaml`](rbac.yaml).

### Role mapping

Configure which ServiceAccounts map to which ORB roles in the auth
config section (not the provider config):

```json
{
  "auth": {
    "provider_auth": {
      "kubernetes": {
        "sa_role_mapping": {
          "orb-system:orb-admin": ["admin"],
          "orb-system:orb-operator": ["operator"],
          "*:ci-runner": ["user"]
        }
      }
    }
  }
}
```

Principals not matched by any entry default to `["user"]`.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `403 Forbidden` when ORB submits a `TokenReview` | ORB's ServiceAccount lacks `tokenreviews: create` | Apply the auth-delegator ClusterRoleBinding from [`rbac.yaml`](rbac.yaml). |
| Callers get `Token rejected by Kubernetes API server` | Token has expired or is not a valid projected SA token | The caller must obtain a fresh projected token via `kubectl create token`. |
| Auth succeeds but the caller gets the wrong role | SA principal not in `sa_role_mapping` | Add the `namespace:sa-name` entry to the mapping; or it matched a wildcard entry — check the order. |

## Why two wrappers?

ORB confines every `kubernetes` SDK import to
`src/orb/providers/k8s/`.  The two auth helpers
(`auth/in_cluster.py` and `auth/kubeconfig.py`) are the only modules
that call into `kubernetes.config`; the rest of the provider uses the
configured global client.  This makes the SDK trivially mockable from
unit tests and keeps the architecture-test allowlist small.
