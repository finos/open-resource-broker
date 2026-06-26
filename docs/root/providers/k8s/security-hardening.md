# Security hardening

ORB includes a **pod-spec security audit** that inspects every rendered pod
spec at acquire time and logs a `WARNING` for each high-risk field it finds.
The audit is on by default and costs nothing at steady state — most specs are
clean and the function returns immediately after the field checks.

## What is audited

The following fields are inspected for every spec submitted through any of the
four workload handlers (Pod, Deployment, StatefulSet, Job).

| Field path | Condition | Risk |
|---|---|---|
| `spec.hostNetwork` | `== true` | Shares the host's network namespace; pods can sniff host traffic and bind privileged ports. |
| `spec.hostPID` | `== true` | Shares the host PID namespace; processes inside the pod can inspect or signal host processes. |
| `spec.hostIPC` | `== true` | Shares the host IPC namespace; enables shared-memory attacks against co-located workloads. |
| `spec.volumes[*].hostPath` | any non-empty path | Mounts a path from the host filesystem; a misconfigured or compromised pod can read or write host files. |
| `spec.containers[*].securityContext.privileged` | `== true` | Grants effectively-root access to the host kernel. |
| `spec.containers[*].securityContext.allowPrivilegeEscalation` | `== true` | Allows a process inside the container to gain more privileges than its parent. |
| `spec.containers[*].securityContext.runAsUser` | `== 0` | Runs the container's primary process as root (UID 0). |
| `spec.containers[*].securityContext.capabilities.add` | contains `SYS_ADMIN`, `NET_ADMIN`, or `NET_RAW` | Grants kernel capabilities that can be used for privilege escalation or network manipulation. |

The same checks are applied to `spec.initContainers`.

> **Note:** The audit is informational.  RBAC on the Kubernetes apiserver
> remains the authoritative enforcement point.  The audit gives operators
> visibility into what they are submitting without blocking workloads at
> the policy level (unless reject mode is enabled — see below).

## When warnings fire

A warning is emitted once per acquire call, before the pod is submitted to
the apiserver.  Each finding produces one log line at `WARNING` level:

```
WARN: high-risk pod-spec field detected: spec.hostNetwork = True
WARN: high-risk pod-spec field detected: spec.volumes[0].hostPath (scratch) = '/var/data'
WARN: high-risk pod-spec field detected: spec.containers[0] (worker).securityContext.privileged = True
```

The log lines appear in the ORB application log (whichever sink the operator
has configured).  Each line identifies the exact field path, the container or
volume name, and the value that triggered the warning so the operator can
locate the relevant template section quickly.

## Configuration

Two fields in `K8sProviderConfig` control the audit:

| Field | Type | Default | Description |
|---|---|---|---|
| `audit_high_risk_pod_fields` | `bool` | `True` | Enable or disable the entire audit.  Set to `False` to silence all warnings. |
| `reject_high_risk_pod_fields` | `bool` | `False` | When `True`, ORB raises a `K8sError` instead of logging a warning if any findings are present.  Acquire fails before the spec reaches the apiserver. |

### Enable reject mode

Add to your provider configuration:

```json
{
  "providers": {
    "k8s": {
      "provider_type": "k8s",
      "namespace": "orb",
      "audit_high_risk_pod_fields": true,
      "reject_high_risk_pod_fields": true
    }
  }
}
```

Or via environment variable:

```bash
export ORB_K8S_REJECT_HIGH_RISK_POD_FIELDS=true
```

When reject mode is active and a spec contains one or more flagged fields,
the acquire call raises `K8sError` with a message listing every finding:

```
K8sError: Acquire rejected: pod spec contains high-risk fields —
  high-risk pod-spec field detected: spec.hostNetwork = True;
  high-risk pod-spec field detected: spec.volumes[0].hostPath (data) = '/var/data'
```

The request is left in the `pending` state and no pod is created.

### Disable warnings entirely

If your workloads legitimately require one or more of the flagged fields
and you do not want the noise:

```json
{
  "providers": {
    "k8s": {
      "audit_high_risk_pod_fields": false
    }
  }
}
```

## Interaction with the native-spec escape hatch

The audit applies equally to specs built by the typed spec builders
(the default path) and to specs rendered via the
[native-spec escape hatch](native-spec.md).  Both paths call
`_audit_spec_body` with the final body before it is submitted to the
apiserver.

Native-spec dicts use camelCase keys (matching the Kubernetes API JSON
schema); typed-builder objects use snake_case (matching the Python SDK's
`to_dict()` output).  The audit function accepts both formats
transparently.

## See also

* [RBAC example](rbac.yaml) — minimum ServiceAccount permissions; RBAC is
  the authoritative enforcement layer.
* [Native spec escape hatch](native-spec.md) — how to supply a full
  Kubernetes API body when the typed builders do not cover a required field.
* [Configuration reference](configuration.md) — full list of
  `K8sProviderConfig` fields.
