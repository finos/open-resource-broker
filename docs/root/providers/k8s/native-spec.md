# Kubernetes native spec escape hatch

The kubernetes provider supports a **native spec** escape hatch that
lets operators submit a complete kubernetes API body (a full `V1Pod`,
`V1Deployment`, `V1StatefulSet`, or `V1Job` dict) on the template.  When
enabled, ORB renders the dict as a Jinja template, deep-merges it onto a
per-API default Jinja template, and passes the result straight to the
kubernetes SDK — bypassing the typed spec builders under
`orb.providers.k8s.utilities`.

This mirrors the AWS provider's `provider_api_spec` field; see
`orb.providers.aws.infrastructure.services.aws_native_spec_service` for
the analogous AWS pattern.

## When to use it

Use the native spec hatch when one of the following is true:

* The workload needs a kubernetes API field that ORB does not model as a
  first-class `K8sTemplate` attribute (e.g. `initContainers`,
  `securityContext`, `affinity`, `topologySpreadConstraints`,
  `hostAliases`).
* The operator wants to layer policy onto the workload (e.g. a
  pre-baked Kyverno annotation set, a sidecar injection template) that
  is easier to express as a single full-body override than as multiple
  small `K8sTemplate` fields.
* The team already has a canonical pod / deployment manifest they want
  to keep verbatim under operator control rather than translate into
  ORB's typed fields.

**Do not use it for** small tweaks that can be expressed with the
existing `K8sTemplate` fields or with `pod_spec_override` (a
*partial-merge* override applied after the typed builder runs).

## How it works

1. The provider's `K8sNativeSpecService` consults two layers:
   * The provider-level kill switch `K8sProviderConfig.native_spec_enabled`
     (default `False` — operators opt in deliberately).
   * The application-level flag (`native_spec.enabled`) — the generic
     flag honoured by every provider.
   Both must be true for the hatch to fire.
2. The service renders the per-API default Jinja template at
   `providers/k8s/specs/<api>/default.json` against the standard
   template context (image, namespace, replicas, labels, ...).
3. If `K8sTemplate.native_spec` is set, the operator dict is rendered
   the same way and **deep-merged** onto the default.  Defaults stay
   present for fields the operator omits; operator values win on leaf
   collisions.
4. The handler stamps per-pod / per-workload identity onto the rendered
   dict (request-id label, ORB-managed sentinel, replica count) so the
   workload remains discoverable by ORB's label-selector reads.
5. The handler passes the dict straight to the relevant kubernetes API
   (e.g. `create_namespaced_pod(body=...)`).

When the hatch is disabled, the handlers fall back to the typed builders
(`build_pod_spec`, `build_deployment_spec`, ...) — the path that runs in
production today.

## Enabling the hatch

```json
{
  "providers": {
    "k8s": {
      "provider_type": "k8s",
      "native_spec_enabled": true
    }
  },
  "native_spec": {
    "enabled": true
  }
}
```

Or via environment variables:

```bash
export ORB_K8S_NATIVE_SPEC_ENABLED=true
```

## Examples

### Full Pod override

```json
{
  "template_id": "ml-worker",
  "provider_type": "k8s",
  "provider_api": "Pod",
  "image_id": "registry.example.com/ml-worker:1",
  "namespace": "orb-ml",
  "max_instances": 16,
  "native_spec": {
    "spec": {
      "initContainers": [
        {
          "name": "warm-cache",
          "image": "registry.example.com/cache-warmer:1",
          "command": ["sh", "-c", "/usr/local/bin/warm"]
        }
      ],
      "securityContext": {
        "runAsUser": 1000,
        "fsGroup": 2000
      },
      "topologySpreadConstraints": [
        {
          "maxSkew": 1,
          "topologyKey": "topology.kubernetes.io/zone",
          "whenUnsatisfiable": "DoNotSchedule"
        }
      ]
    }
  }
}
```

The operator only specifies the fields they want to set; the default
Jinja template fills in `metadata.name`, `metadata.labels`,
`spec.containers[0]` (image, resources), `spec.restartPolicy`, and so
on.

### Partial Deployment override

```json
{
  "template_id": "long-running-service",
  "provider_type": "k8s",
  "provider_api": "Deployment",
  "image_id": "registry.example.com/svc:42",
  "namespace": "orb",
  "max_instances": 8,
  "native_spec": {
    "spec": {
      "strategy": {
        "type": "RollingUpdate",
        "rollingUpdate": {
          "maxSurge": "25%",
          "maxUnavailable": "0"
        }
      }
    }
  }
}
```

Only `spec.strategy` is overridden; `replicas`, `selector`, `template`
(pod-template) are produced by the default template.

## Safety implications

* The hatch surrenders the typed-builder invariants to the operator's
  spec.  ORB stamps the request-id / managed labels onto the rendered
  body, but it does **not** validate the rest of the operator-supplied
  fields.  Mistakes in the native spec land in the cluster as a failed
  create call.
* Validation feedback is downgraded to whatever the kubernetes API
  server returns at submit time.  Use a dry-run (e.g. `kubectl create
  --dry-run=server`) to vet the operator spec before deploying it.
* The default-Jinja template is the contract for what ORB will populate
  when the operator omits fields.  Inspect
  `providers/k8s/specs/<api>/default.json` to see exactly what gets
  rendered when only a partial override is supplied.
* The hatch is opt-in for a reason: the typed builders enforce the
  guard-rails that the provider's controller-side code relies on
  (e.g. `spec.restartPolicy = Never` for the Pod handler;
  `backoffLimit = 0` for the Job handler).  Override at your own risk.
