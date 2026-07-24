# Kubernetes live integration tests

These tests run against a **real Kubernetes cluster**. They exercise the ORB
Kubernetes provider end to end — acquiring and releasing real pods,
provisioning real nodes via Karpenter, and driving the REST API and Go SDK
against a real ORB server process.

They are skipped by default. Enable them with the `--run-k8s` gate.

## Running

```bash
# Via make (installs the k8s extra, applies the --run-k8s gate, runs -n 4):
make test-providers-k8s-live

# Raw pytest (equivalent):
ORB_SKIP_UI_BUILD=1 uv run --extra k8s pytest --no-cov -q -ra -n 4 \
  --run-k8s tests/providers/k8s/live

# A single file, serially:
ORB_SKIP_UI_BUILD=1 uv run --extra k8s pytest --no-cov -q -ra -p no:xdist \
  --run-k8s tests/providers/k8s/live/test_rest_api_cycle_live.py
```

The suite runs 4-way parallel (`-n 4`, configured in
`tests/providers/k8s/testconf.mk`). Every test uses a unique per-test
request-id and cleans up only its own resources; the tests that touch
namespace-global objects run in their own throwaway namespace. New fixtures
must preserve this: unique names, own cleanup, no shared mutable state.

## Prerequisites

- **kubeconfig / cluster access.** The tests read the k8s provider block from
  the ORB config (`orb init` writes it; discovered via
  `orb.config.platform_dirs.get_config_location()`). The provider `config`
  block supplies `context`, `namespace`, and optional `kubeconfig_path`. A
  session pre-flight (`pytest_sessionstart`) verifies the cluster is reachable
  and exits early if not.
- **Provider selection.** By default the first `type: k8s` provider in the
  config is used. Override with `ORB_K8S_LIVE_PROVIDER_NAME=<provider-name>`.
- **Namespace.** Taken from the provider config `namespace` (falls back to
  `default`).
- **TTY / exec-plugin auth.** For EKS/GKE/AKS clusters whose kubeconfig uses an
  exec credential plugin (e.g. `aws eks get-token`), the plugin is minted
  non-interactively, so the suite works from a normal terminal. No special TTY
  handling is required.

## Capability matrix

Most capabilities are provisioned automatically now. Two remain manually gated
because they need infrastructure the test cannot create itself.

### Auto-provisioned (run unskipped out of the box)

| Capability | Tests | How it is provided |
|---|---|---|
| ORB REST server | `test_rest_api_cycle_live.py` | The `orb_rest_server` fixture launches `orb server start --foreground --api-only --scheduler default` as a subprocess on a free loopback port under an isolated `ORB_WORK_DIR`, waits for `/health`, and reads the loopback-admin bearer token so POST routes authenticate as admin. Torn down at session end. |
| ORB REST server + Go SDK | `test_go_sdk_cycle_live.py` | Same server fixture, plus the `go_sdk_cli` fixture, which builds a small CLI over the in-repo Go SDK (`sdk/go`) using the local Go toolchain and a temporary module with a `replace` directive (the repo tree is not modified). Skips only when no Go toolchain is present. |
| Cold-node Karpenter NodePool | `test_karpenter_cold_node_timeout_live.py` | The `cold_karpenter_nodepool` fixture creates a NodePool named `orb-test-karpenter-cold` that targets a non-existent instance family, so pods pinned to it stay `Pending` forever. **No node is ever launched — this is free.** Deleted on teardown. |
| ARM64 Karpenter NodePool | `test_arm64_nodeaffinity_live.py` (positive cases) | The `arm64_karpenter_nodepool` fixture creates a NodePool named `orb-test-karpenter-arm64` advertising `kubernetes.io/arch=arm64` capacity. An arm64-pinned pod triggers Karpenter to launch a **real, briefly-billed arm64 (Graviton) node**. Deleting the pool on teardown makes Karpenter reclaim the node, so none is left running. |

Notes:

- The two Karpenter NodePools are cluster-scoped singletons. Under `-n 4` a
  file-lock + refcount in the shared xdist temp directory coordinates workers:
  the first worker creates the pool, the last one out deletes it.
- `test_arm64_template_rejected_when_no_arm64_nodes` (the negative case) is the
  one arm64 test that still **skips** during a normal run: it only applies to
  genuinely amd64-only clusters, and the arm64 fixture guarantees arm64
  capacity is present. This is correct, intended behaviour.
- Override the referenced EC2NodeClass with `ORB_K8S_TEST_NODECLASS` if your
  cluster does not use `ms-default`.
- Point the tests at an already-running ORB server with
  `ORB_REST_BASE_URL=http://host:port` (and `ORB_REST_ADMIN_TOKEN` if that
  server has auth enabled); the fixture then skips launching its own.
- Use a pre-built Go SDK binary instead of building one with
  `ORB_GO_SDK_BINARY=/path/to/binary`.

### Manually gated (still skip until you provide the infrastructure)

| Capability | Tests | Why it stays gated / how to enable |
|---|---|---|
| Prometheus scraping ORB metrics | `test_metric_emission_live.py` (3 tests) | Needs a running Prometheus that scrapes ORB's metrics endpoint — provisioning and wiring a monitoring stack is out of scope for an auto-fixture. Enable by pointing the tests at a reachable scrape URL: set `ORB_PROMETHEUS_SCRAPE_URL` (e.g. `http://localhost:9090/metrics`) or the ORB config `metrics.prometheus_url`. Typical setup: deploy Prometheus (e.g. the `kube-prometheus-stack` Helm chart) configured to scrape the ORB server's metrics endpoint, then `kubectl port-forward` its service to localhost and point the env var at it. |
| Watch resource-version compaction | `test_watch_lifecycle.py` (1 test) | A client cannot force a managed EKS apiserver to evict a resource version from its watch cache (a `410 Gone`), so the reconnect path cannot be triggered from the test side. This is genuinely un-triggerable against managed EKS. The reconnect behaviour is covered by mocked tests in `tests/providers/k8s/mocked/test_watch_ingest_mocked.py` (`test_watch_reconnects_on_410_gone`) and `tests/providers/k8s/unit/watch/test_watcher.py`. |

## Cleanup guarantees

- Each test releases its own pods in a `finally` block.
- A session-scoped `nuclear_cleanup` fixture sweeps every resource carrying
  `orb.io/managed=true` in the target namespace after the run.
- The Karpenter NodePool fixtures delete their pools on teardown; deleting an
  arm64 NodePool makes Karpenter reclaim any node it launched.
- The REST server fixture terminates its subprocess and removes its isolated
  work directory on teardown.
