# Infrastructure discovery (`orb init --provider k8s`)

`orb init --provider k8s` walks an operator through a six-step interactive
prompt that discovers cluster topology and populates `K8sProviderConfig`
fields automatically.  This page describes what each step does, what RBAC
the discovery path requires, and what to expect when permissions are missing.

---

## What `orb init --provider k8s` does

1. **In-cluster detection** — reads the kubelet sentinel at
   `/var/run/secrets/kubernetes.io` to decide automatically whether ORB is
   running inside a pod.  The operator confirms or overrides the result.

2. **Context selection** (out-of-cluster only) — parses `~/.kube/config` (or
   `KUBECONFIG`) and lists available contexts.  The currently active context
   is pre-selected.

3. **Cluster endpoint display** — resolves the API-server URL from the
   chosen context and displays it.  This value is informational only; it is
   never written into the config file.

4. **Namespace selection** — calls `CoreV1Api.list_namespace()` and presents
   a numbered list.  When the calling identity lacks `namespaces/list` RBAC
   (very common for in-cluster service accounts), ORB falls back to the
   SA-bound namespace at
   `/var/run/secrets/kubernetes.io/serviceaccount/namespace` and skips the
   prompt with a notice (see [403 fallback paths](#403-fallback-paths) below).

5. **ServiceAccount selection** — calls
   `CoreV1Api.list_namespaced_service_account()` in the chosen namespace and
   presents a numbered list.  The chosen name becomes the
   `service_account` template default.  Skippable; a 403 response also skips
   this step automatically.

6. **Image pull secret selection** — calls
   `CoreV1Api.list_namespaced_secret()` filtered to
   `type=kubernetes.io/dockerconfigjson`.  Only secret *names* are surfaced;
   secret values are never read.  The chosen name becomes
   `K8sProviderConfig.default_image_pull_secret`.  Skippable; a 403 or empty
   result also skips this step.

7. **RBAC probe** — calls `AuthorizationV1Api.create_self_subject_access_review()`
   once per verb (`create`, `watch`, `delete`) against `resource=pods` in the
   chosen namespace.  When any verb is denied, a pre-formatted
   `kubectl create rolebinding` remediation command is shown and the operator
   is asked whether to continue.

---

## The six operator prompts

```
Detecting cluster access mode...
  Auto-detected: running OUTSIDE the cluster (no in-cluster service account)
  Confirm? [Y/n]:

Available kubeconfig contexts:
    (1) prod-us-east-1   [current]
    (2) staging-us-west-2
    (3) local-minikube
  Pick a kubeconfig context [1]:

  Cluster endpoint: https://1.2.3.4:6443

Available namespaces:
    (1) default
    (2) orb-system   [selected]
    (3) ml-workloads
  Pick a namespace [2]:

Available ServiceAccounts:
    (1) default
    (2) orb-runner   [current]
  Pick a ServiceAccount [2]:

Available image pull secrets:
    (1) ecr-pull-secret
    (2) ghcr-token
    (3) none
  Pick an image pull secret [none]:

  Probing required permissions...

    create pods   granted
    watch pods    granted
    delete pods   granted

  All required permissions are present.
```

When RBAC permissions are missing, step 7 shows the remediation command:

```
  create pods   DENIED
  watch pods    granted
  delete pods   granted

  Missing required permissions in namespace 'orb-system'.
  To grant them, run:
    kubectl create rolebinding orb-runner-pods \
      --clusterrole=orb-pod-manager \
      --serviceaccount=orb-system:orb-runner \
      --namespace=orb-system

  Continue with degraded permissions? [y/N]:
```

---

## Config field routing

`orb init` splits the discovered fields between two sections:

| Discovered field             | Written to                                     |
|------------------------------|------------------------------------------------|
| `context`                    | `provider.providers[].config.context`          |
| `in_cluster`                 | `provider.providers[].config.in_cluster`       |
| `namespace`                  | `provider.providers[].config.namespace`        |
| `default_image_pull_secret`  | `provider.providers[].config.default_image_pull_secret` |
| `service_account`            | `provider.providers[].template_defaults.service_account` |

This routing is determined by `K8sProviderStrategy.get_cli_extra_config_keys()`,
which returns the four keys that belong in `config` rather than
`template_defaults`.

---

## Minimum RBAC required at init time

The discovery path probes permissions interactively, so the minimum RBAC
needed to complete `orb init` depends on what the operator wants to discover:

| Discovery step              | Required RBAC                                             | Without it                        |
|-----------------------------|-----------------------------------------------------------|-----------------------------------|
| Namespace list (step 4)     | `namespaces` `list` (ClusterRole required)                | Falls back to SA-bound namespace  |
| ServiceAccount list (step 5)| `serviceaccounts` `list` in target namespace              | Step skipped with notice          |
| Image pull secrets (step 6) | `secrets` `list` in target namespace                      | Step skipped; no default set      |
| RBAC probe (step 7)         | `selfsubjectaccessreviews` `create` (granted by default)  | `K8sDiscoveryError` raised        |

The RBAC probe itself (`SelfSubjectAccessReview`) is granted to every
authenticated subject by the Kubernetes API server by default.  A 403 on
this call indicates an unusually restrictive cluster policy and surfaces as an
error.

The minimum RBAC to *run ORB at runtime* (after `orb init` completes) is
documented in [`rbac.yaml`](rbac.yaml) and requires `create`, `watch`, and
`delete` on `pods` in the target namespace.

---

## 403 fallback paths

Two 403 fallback behaviours are built into the discovery service:

### Namespace list (step 4)

Most in-cluster ServiceAccounts lack the cluster-scoped `namespaces/list`
RBAC grant.  When `CoreV1Api.list_namespace()` returns 403, the service
reads the SA-bound namespace from the kubelet-written file at
`/var/run/secrets/kubernetes.io/serviceaccount/namespace` and returns a
single-element list.  The namespace prompt is then skipped with a notice:

```
  Note: namespace list permission not available; using SA-bound namespace 'orb-system'
```

If neither the API call nor the SA-bound file are available (out-of-cluster
403 with no SA file), the namespace list is empty and the operator must
type the namespace manually — or press Enter to accept `"default"`.

### ServiceAccount list (step 5)

A 403 on `CoreV1Api.list_namespaced_service_account()` causes the step to be
skipped entirely:

```
  Note: could not list ServiceAccounts — you can set `service_account` in your template later.
```

---

## Examples

### Out-of-cluster deployment (kubeconfig)

```bash
orb init --provider k8s
```

ORB detects out-of-cluster mode, reads `~/.kube/config`, lists contexts and
namespaces, and writes the chosen values to `provider.providers[0].config`.

Typical output config fragment:

```json
{
  "provider": {
    "providers": [
      {
        "name": "kubernetes_prod-us-east-1",
        "provider_type": "k8s",
        "config": {
          "context": "prod-us-east-1",
          "namespace": "orb-system",
          "default_image_pull_secret": "ecr-pull-secret"
        },
        "template_defaults": {
          "service_account": "orb-runner"
        }
      }
    ]
  }
}
```

### In-cluster deployment (pod with ServiceAccount)

When ORB runs as a pod, it auto-detects in-cluster mode and the context
selection step (step 2) is skipped:

```bash
orb init --provider k8s
```

```
  Auto-detected: running INSIDE the cluster (in-cluster service account present)
  Confirm? [Y/n]:
  Note: namespace list permission not available; using SA-bound namespace 'orb-system'
  ...
```

Typical output config fragment:

```json
{
  "provider": {
    "providers": [
      {
        "name": "kubernetes_orb-system",
        "provider_type": "k8s",
        "config": {
          "in_cluster": true,
          "namespace": "orb-system"
        }
      }
    ]
  }
}
```

### KinD (local development)

```bash
kind create cluster --name orb-dev
orb init --provider k8s
```

`kind create cluster` writes a context named `kind-orb-dev` to
`~/.kube/config`.  Select it at the context prompt.  KinD clusters grant
cluster-admin to the default user, so all discovery steps succeed without
any RBAC setup.

After `orb init`, install the runtime RBAC if you plan to test in-cluster
mode:

```bash
kubectl apply -f docs/root/providers/k8s/rbac.yaml
```

---

## See also

- [Authentication](auth.md) — in-cluster vs kubeconfig path, KUBECONFIG resolution.
- [Configuration reference](configuration.md) — all `K8sProviderConfig` fields.
- [RBAC example](rbac.yaml) — minimum ServiceAccount + Role + RoleBinding for runtime.
- [Security hardening](security-hardening.md) — pod-spec restrictions and reject mode.
