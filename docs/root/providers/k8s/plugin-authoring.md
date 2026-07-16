# Authoring a Kubernetes provider plugin

ORB supports third-party provider plugins via Python's standard
[entry-points][ep] mechanism.  A plugin is a regular Python distribution
that:

1. Declares an entry point under the `orb.providers` group.
2. Provides a registration function that ORB calls during boot.
3. Either ships its own `ProviderStrategy` from scratch, or extends one
   of the built-in providers (most usefully
   `K8sProviderStrategy`).

This page walks through "Pattern A" - extending the Kubernetes provider
to support a new workload type the upstream code does not handle.  The
worked example is an [MPIJob][mpi] from Kubeflow's `kubeflow.org/v2beta1`
API.

[ep]: https://packaging.python.org/en/latest/specifications/entry-points/
[mpi]: https://www.kubeflow.org/docs/components/training/user-guides/mpi/

## When to write a plugin

| Goal                                                         | Right approach                                              |
|--------------------------------------------------------------|-------------------------------------------------------------|
| Add a workload shape that maps to a CRD (MPIJob, RayCluster, etc.) | Pattern A - subclass `K8sProviderStrategy`, add a new handler. |
| Wrap a SaaS API or a non-Kubernetes cloud                    | Pattern B - write a fresh `ProviderStrategy` subclass.       |
| Tweak the behaviour of an existing handler                   | Submit a PR to ORB - provider config and template extension are usually the right surface, not a plugin. |
| Add a new provider config field                              | PR to ORB.                                                   |

Pattern B (a fresh provider) is out of scope for this page; see
[Adding a Provider](../../developer_guide/adding_a_provider.md) for the
`ProviderPlugin`-based walkthrough, using the AWS provider
(`src/orb/providers/aws/provider_plugin.py`) as the canonical reference.
This page covers Pattern A.

## The entry-point contract

Plugins declare themselves in their distribution metadata.  With
`pyproject.toml`:

```toml
[project]
name = "orb-mpi-job"
version = "0.1.0"
dependencies = [
  "orb-py[k8s]>=1.7.0",
]

[project.entry-points."orb.providers"]
mpi_job = "orb_mpi_job.registration:register"
```

The entry-point **name** (`mpi_job` above) is a free-form identifier the
plugin author picks; ORB does not interpret it.  The entry-point
**value** is a Python import path to a zero-argument callable that ORB
will invoke during provider discovery.

For a **Pattern A plugin** (extending an existing provider with a new
handler), a plain `register()` function is sufficient because you only need
to touch one registry — the handler table of the existing strategy.  For a
**Pattern B plugin** (a wholly new provider), point the entry-point at
`YourPlugin.register_plugin` instead; see
[Adding a Provider](../../developer_guide/adding_a_provider.md) for the full
`ProviderPlugin` walkthrough.

### What ORB calls

At boot, ORB walks `importlib.metadata.entry_points(group="orb.providers")`
after the built-in providers have registered.  For each entry-point it
imports the target module, fetches the callable, and calls it.  The
contract of the callable is:

```python
def register() -> None:
    """Register the plugin's provider strategy / handler with ORB.

    Must not raise.  Plugins should log and swallow internal errors so a
    broken plugin does not prevent ORB from starting.
    """
```

The callable is responsible for hitting whichever ORB registries the
plugin needs:

* `orb.providers.registry.get_provider_registry()` - to register a
  fully fresh `ProviderStrategy`.
* `K8sProviderStrategy._HANDLERS` (via the
  `register_k8s_handler` helper introduced below) - to attach a
  new handler to the existing Kubernetes provider.
* Anything else needed (CLI specs, defaults loaders, etc.) - same
  registries the built-in providers use.

## Worked example - MPIJob

The MPIJob CRD (`kubeflow.org/v2beta1`) represents a multi-pod MPI
training run.  We will extend the Kubernetes provider with a new
`KubernetesMPIJob` `provider_api` handler.

### Project layout

```
orb-mpi-job/
├── pyproject.toml
└── src/
    └── orb_mpi_job/
        ├── __init__.py
        ├── handler.py
        └── registration.py
```

### `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=80", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "orb-mpi-job"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "orb-py[k8s]>=1.7.0",
]

[project.entry-points."orb.providers"]
mpi_job = "orb_mpi_job.registration:register"

[tool.setuptools.packages.find]
where = ["src"]
```

### `handler.py`

```python
"""KubernetesMPIJobHandler - provisions Kubeflow MPIJob workloads.

Subclasses the standard K8sHandlerBase so it inherits client
wiring, namespace resolution, label injection, and retry helpers.
"""

from __future__ import annotations

from typing import Any

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.handlers.base_handler import K8sHandlerBase


class KubernetesMPIJobHandler(K8sHandlerBase):
    """Provider-API: ``KubernetesMPIJob`` - Kubeflow MPIJob v2beta1."""

    PROVIDER_API = "KubernetesMPIJob"

    GROUP = "kubeflow.org"
    VERSION = "v2beta1"
    PLURAL = "mpijobs"

    # The strategy's handler factory passes ``pod_state_cache`` and
    # ``cache_alive`` kwargs to every handler it builds (so the Pod-based
    # handlers can read from the shared watch cache).  Plugin handlers
    # that do not consume the cache should still accept these kwargs and
    # ignore them.
    def __init__(self, *args, pod_state_cache=None, cache_alive=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pod_state_cache = pod_state_cache
        self._cache_alive = cache_alive

    async def acquire_hosts(
        self, request: Request, template: Template
    ) -> dict[str, Any]:
        namespace = self.resolve_namespace(template)
        body = self._build_mpijob_body(request, template)
        api = self.client.custom_objects_api  # provided by K8sClient
        self.with_retry(
            api.create_namespaced_custom_object,
            group=self.GROUP,
            version=self.VERSION,
            namespace=namespace,
            plural=self.PLURAL,
            body=body,
            operation_name="create_mpijob",
        )
        # Machine IDs follow the launcher / worker pod naming convention.
        machine_ids = [f"{body['metadata']['name']}-launcher"] + [
            f"{body['metadata']['name']}-worker-{i}"
            for i in range(request.requested_count - 1)
        ]
        return {
            "resource_ids": [body["metadata"]["name"]],
            "machine_ids": machine_ids,
            "provider_data": {
                "k8s": {
                    "mpijob_name": body["metadata"]["name"],
                    "namespace": namespace,
                }
            },
        }

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        # The standard Pod-based check works because MPIJob owns pods
        # that carry the request-id label we stamped at acquire time.
        ...  # delegate to a shared helper or inline the list_namespaced_pod call

    async def release_hosts(
        self, machine_ids: list[str], request: Request
    ) -> None:
        # MPIJob, like Job, is run-to-completion.  Selective release is
        # not meaningful - delete the MPIJob and let the CRD controller
        # cascade-delete pods.
        namespace = (
            (request.provider_data or {}).get("k8s", {}).get("namespace")
            or self._config.namespace
        )
        mpijob_name = (
            (request.provider_data or {}).get("k8s", {}).get("mpijob_name")
        )
        if not mpijob_name:
            return
        api = self.client.custom_objects_api
        self.with_retry(
            api.delete_namespaced_custom_object,
            group=self.GROUP,
            version=self.VERSION,
            namespace=namespace,
            plural=self.PLURAL,
            name=mpijob_name,
            operation_name="delete_mpijob",
        )

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        # Return a single example so `orb templates generate` can emit a stub.
        return []

    def _build_mpijob_body(
        self, request: Request, template: Template
    ) -> dict[str, Any]:
        # Stamp the canonical ORB labels so the orphan reconciler picks the
        # MPIJob up.  Use the base class's label_prefix.
        labels = {
            f"{self._config.label_prefix}/managed": "true",
            f"{self._config.label_prefix}/request-id": str(request.request_id),
            f"{self._config.label_prefix}/provider-api": self.PROVIDER_API,
        }
        return {
            "apiVersion": f"{self.GROUP}/{self.VERSION}",
            "kind": "MPIJob",
            "metadata": {
                "name": f"orb-{request.request_id}",
                "namespace": self.resolve_namespace(template),
                "labels": labels,
            },
            "spec": {
                "slotsPerWorker": 1,
                "runPolicy": {"cleanPodPolicy": "Running"},
                "mpiReplicaSpecs": {
                    "Launcher": {
                        "replicas": 1,
                        "template": {
                            "metadata": {"labels": labels},
                            "spec": {
                                "containers": [
                                    {
                                        "name": "mpi-launcher",
                                        "image": template.image_id,
                                    }
                                ],
                            },
                        },
                    },
                    "Worker": {
                        "replicas": max(request.requested_count - 1, 0),
                        "template": {
                            "metadata": {"labels": labels},
                            "spec": {
                                "containers": [
                                    {
                                        "name": "mpi-worker",
                                        "image": template.image_id,
                                    }
                                ],
                            },
                        },
                    },
                },
            },
        }
```

### `registration.py`

```python
"""Entry-point glue.

The ``register()`` callable is what ORB invokes during plugin discovery.
"""

from __future__ import annotations

import logging

from orb.providers.k8s.strategy.k8s_provider_strategy import (
    K8sProviderStrategy,
)
from orb.providers.k8s.value_objects import KubernetesProviderApi

from orb_mpi_job.handler import KubernetesMPIJobHandler

log = logging.getLogger(__name__)


def register() -> None:
    """Register the MPIJob handler with the Kubernetes provider strategy.

    Must not raise.  Plugin authors should log and swallow internal
    errors so a broken plugin cannot prevent ORB from booting.
    """
    try:
        # The Kubernetes provider strategy exposes a class-level registry
        # of handlers keyed by `provider_api`.  Adding to it is the entire
        # integration surface.
        K8sProviderStrategy.register_handler(
            provider_api="KubernetesMPIJob",
            handler_class=KubernetesMPIJobHandler,
        )
    except Exception:  # pragma: no cover - defensive
        log.exception("Failed to register orb-mpi-job plugin")
```

> Plugin authors who need a different API key than the
> `KubernetesProviderApi` enum publishes can register any string.  ORB
> never asks `KubernetesProviderApi` to round-trip the key - the
> handler registry is the source of truth at runtime.

### RBAC additions

The MPIJob handler creates `kubeflow.org/v2beta1/mpijobs` objects.
The operator running ORB needs additional RBAC verbs on top of the
baseline [`rbac.yaml`](rbac.yaml):

```yaml
- apiGroups: ["kubeflow.org"]
  resources: ["mpijobs"]
  verbs: ["get", "list", "watch", "create", "patch", "delete"]
- apiGroups: ["kubeflow.org"]
  resources: ["mpijobs/status"]
  verbs: ["get"]
```

Apply by either adding the rules to your existing Role or by shipping a
companion `Role` in the plugin's own manifests.

### Testing

Plugin tests can reuse ORB's kubernetes-client mocking utilities:

```python
# tests/test_mpijob_handler.py
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb_mpi_job.handler import KubernetesMPIJobHandler


@pytest.fixture
def handler() -> KubernetesMPIJobHandler:
    client = MagicMock(spec=K8sClient)
    config = K8sProviderConfig(namespace="orb-test")
    return KubernetesMPIJobHandler(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
    )


def test_provider_api(handler: KubernetesMPIJobHandler) -> None:
    assert handler.PROVIDER_API == "KubernetesMPIJob"
```

A quick smoke test for entry-point wiring:

```python
import importlib.metadata as md

def test_entry_point_is_discoverable() -> None:
    eps = md.entry_points(group="orb.providers")
    assert any(ep.name == "mpi_job" for ep in eps)
```

### Installing the plugin

```bash
pip install orb-py[k8s]
pip install orb-mpi-job
```

ORB's startup discovery walks every distribution that ships an
`orb.providers` entry-point; no further configuration is needed.
Verify with:

```bash
orb providers list
```

The list will include the standard kubernetes provider; the MPIJob
handler shows up as an extra `provider_api` value on the kubernetes
provider when running `orb templates generate --help`.

## Failure semantics

* If a plugin's `register()` callable raises, ORB logs the exception at
  `ERROR` and continues - the plugin is excluded but the rest of ORB
  boots normally.  This is intentional: a misconfigured plugin must
  never take ORB offline.
* If the entry-point's target module cannot be imported (missing
  dependency, syntax error), ORB logs at `ERROR` and skips it.
* If the registered handler raises at request time, the failure is
  routed through ORB's normal request-failure path (`OperationOutcome`
  → `Failed`).

## Versioning

Plugins should pin a compatible ORB range in their dependencies:

```toml
dependencies = [
  "orb-py[k8s]>=1.7,<2.0",
]
```

The plugin contract - `register()`, `K8sHandlerBase`,
`K8sProviderStrategy.register_handler`, the
`orb.providers` entry-point group - is part of ORB's public surface
and follows semantic versioning.  Breaking changes will be called out
in `CHANGELOG.md` and held to major-version bumps.

## Further reading

* [Configuration reference](configuration.md) - every field your plugin
  can read off the provider config.
* [Handlers](handlers.md) - the four built-in handlers; copy them when
  in doubt about the contract.
* [Authentication](auth.md) - your plugin inherits the parent
  provider's auth path; no plugin-level auth wiring is required.
* [Adding a Provider](../../developer_guide/adding_a_provider.md) - full
  `ProviderPlugin`-based walkthrough for a fresh provider (Pattern B),
  including the `pyproject.toml` entry-point declaration and startup
  completeness check.
* `src/orb/providers/base/provider_plugin.py` - the `ProviderPlugin`
  abstract base class and the `register_plugin` entry-point hook.
* `src/orb/providers/aws/provider_plugin.py` - the AWS provider as a
  fully-populated `ProviderPlugin` reference (Pattern B).
