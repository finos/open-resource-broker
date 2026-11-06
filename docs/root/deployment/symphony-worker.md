# Symphony Worker Image

ORB provisions IBM Spectrum Symphony compute workers on Kubernetes. The worker
pods run an image that contains the Symphony compute binaries plus a few ORB
helper scripts. The sources for that image live in `deploy/symphony-worker/`:

```
deploy/symphony-worker/
├── Dockerfile
└── bin/
    ├── compute-liveness
    ├── configure-pod-params
    └── symphony-exporter-bootstrap
```

ORB does not publish a worker image. The Symphony binaries are licensed, so
you build the image `FROM` your own Symphony compute image.

## Building the image

```bash
docker build \
  --build-arg BASE_IMAGE=registry.example.com/symphony-compute:7.3.2 \
  --tag registry.example.com/symphony-worker:7.3.2 \
  deploy/symphony-worker
```

`BASE_IMAGE` has no default; the build fails if it is not set. The base must
provide `sh`, `bash`, `pgrep` (procps) and `uuidgen` (uuid-runtime or
util-linux). The build checks for them and stops with `missing <tool>` if one
is absent.

The scripts are copied to `/opt/orb-worker/bin/`. The Dockerfile does not
change the base image's user, entrypoint or command.

## Scripts

### compute-liveness

A liveness probe for the Symphony `lim` process. It fails when `lim` has not
been running for 600 seconds, or when `lim` has had no child processes for
600 seconds. It needs `sh` and `pgrep`.

| Variable | Required | Purpose |
|---|---|---|
| `ORB_WORKER_STATE_DIR` | no, default `/tmp` | Directory for the probe's state files (`lim_missing_since`, `lim_no_child_since`). Must be writable. |

### configure-pod-params

Writes pod and Symphony settings from the container environment to files
under `/run/k8s-env/`, one file per variable, so that processes which do not inherit the container environment can read
them. `/run/k8s-env` must
be writable. Run it from the container entrypoint before Symphony starts.
Pods whose name contains `ssm` (and not `master`) are skipped.

| Variable | Required | Source |
|---|---|---|
| `MY_POD_NAME` | yes | downward API, `metadata.name` |
| `MY_POD_IP` | yes | downward API, `status.podIP` |
| `MY_POD_NAMESPACE` | yes | downward API, `metadata.namespace` |
| `KUBERNETES_SERVICE_HOST` | yes | set by the kubelet |
| `SYMPHONY_SVC_VCPU` | yes | template |
| `SYMPHONY_SVC_MEMORY` | yes | template |
| `EGO_IGNORE_CPU_CGROUP_SETTING_IN_CONTAINER` | yes | template |
| `EGO_LIM_IS_IN_CONTAINER` | yes | template |
| `SYMPHONY_SVC_WORKER_LABEL` | no | template |
| `HF_K8S_PROVIDER_CONFDIR`, `HF_K8S_LOG_FILE`, `HF_INIT_TAKS_DURATION`, `HF_RETURN_IDLE_DURATION_SECONDS` | no | template |
| `PODMAN_CERT_DIR`, `AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_ROLE_ARN`, `AWS_REGION`, `AWS_STS_REGIONAL_ENDPOINTS` | no, compute pods only | template or EKS pod identity |

### symphony-exporter-bootstrap

Creates a read-only Symphony user for `symphony-exporter`,
logs it on, starts the exporter in the background and renews the logon every
hour. It needs `bash`, `uuidgen`, and the Symphony `egosh` and
`symphony-exporter` binaries on `PATH`; it exits with
`Symphony exporter not found` if the exporter is missing.

| Variable / file | Required | Purpose |
|---|---|---|
| `SYMPHONY_CLUSTER_NAME` | yes | Names the exporter's credential cache file |
| `/var/tmp/symphony/profile.platform` | yes | Symphony environment, sourced before `egosh` calls |
| `/etc/symphony-exporter/symphony-exporter.yaml` | yes | Exporter configuration |

The script waits until Symphony has written `/tmp/secegocc_<uid>` before it
creates the exporter user. The exporter log is `/tmp/symphony-exporter.log`.

## Template example

A k8s provider template that sets the environment the scripts read and wires
`compute-liveness` as the liveness probe:

```json
{
  "template_id": "symphony-compute",
  "provider_type": "k8s",
  "provider_api": "Pod",
  "image_id": "registry.example.com/symphony-worker:7.3.2",
  "namespace": "symphony",
  "max_instances": 50,
  "env": [
    {"name": "MY_POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
    {"name": "MY_POD_IP", "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}}},
    {"name": "MY_POD_NAMESPACE", "valueFrom": {"fieldRef": {"fieldPath": "metadata.namespace"}}},
    {"name": "SYMPHONY_SVC_VCPU", "value": "4"},
    {"name": "SYMPHONY_SVC_MEMORY", "value": "16384"},
    {"name": "EGO_IGNORE_CPU_CGROUP_SETTING_IN_CONTAINER", "value": "Y"},
    {"name": "EGO_LIM_IS_IN_CONTAINER", "value": "Y"},
    {"name": "SYMPHONY_CLUSTER_NAME", "value": "cluster1"}
  ],
  "liveness_probe": {
    "exec": {"command": ["/opt/orb-worker/bin/compute-liveness"]},
    "initialDelaySeconds": 60,
    "periodSeconds": 30,
    "failureThreshold": 3
  }
}
```

The probe itself keeps the 600-second grace period, so `initialDelaySeconds`
only needs to cover container start-up.

## What stays in k8s-legacy

The other scripts in `k8s-legacy/tools/` are not part of the worker image:

- `sync-registry-creds` needs podman, the AWS CLI, gcloud and kubectl, and
  writes Kubernetes Secrets. Shipping it would require Secret-write RBAC on
  every worker.
- `refresh-pod-hostnames` lists pods with kubectl and writes the Symphony
  hosts file. It runs on the Symphony master, not on compute workers.
- `run-kubectl` and `run-podman` wrap kubectl and podman, which a compute
  worker does not need.
- `sync-hmac-creds` and `push-cmp-logs` are specific to Google Cloud Storage.

The worker image therefore needs no kubectl and no Kubernetes RBAC.
