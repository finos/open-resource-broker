"""Shared fixtures for the Kubernetes provider end-to-end integration tests.

These tests exercise the strategy + handler + watcher stack against a
fully mocked Kubernetes client.  No real cluster, no real ``kubernetes``
SDK calls (other than the small handful of helpers the strategy uses to
classify exceptions).  Every test wires up:

* a :class:`K8sProviderStrategy` configured with the per-test
  namespace ``orb-it`` and a ``MagicMock`` ``K8sClient`` whose
  ``core_v1`` / ``apps_v1`` / ``batch_v1`` facets are programmable per
  test;
* a fresh :class:`PodStateCache` shared with the strategy via the
  ``watch_manager`` argument so cache-fed reads can be exercised
  alongside list-fed reads;
* a real :class:`Request` / :class:`Template` pair so that label
  propagation, request-id linkage, and status transitions are checked
  against the actual handler code paths rather than fake intermediaries.

The helpers below are deliberately framework-agnostic: they construct
``SimpleNamespace`` mocks that mimic the shape of the kubernetes SDK
return values so we do not need to import ``kubernetes`` at the test
seam.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional
from unittest.mock import MagicMock

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.k8s_provider_strategy import (
    K8sProviderStrategy,
)
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.pod_state_cache import PodStateCache

# ---------------------------------------------------------------------------
# Common builders — shared by every integration test in this directory.
# ---------------------------------------------------------------------------


def make_namespaced_config(
    *,
    namespace: str = "orb-it",
    orphan_gc_enabled: bool = False,
    auto_cleanup_orphans: bool = False,
    watch_enabled: bool = False,
) -> K8sProviderConfig:
    """Build a deterministic provider config for integration tests.

    ``watch_enabled=False`` keeps the watch task out of the way unless a
    specific test enables it; the watcher is exercised by
    ``test_watch_lifecycle.py`` directly via :class:`K8sWatcher`.
    """
    return K8sProviderConfig(
        namespace=namespace,
        orphan_gc_enabled=orphan_gc_enabled,
        auto_cleanup_orphans=auto_cleanup_orphans,
        watch_enabled=watch_enabled,
    )


def make_template(
    *,
    template_id: str = "k8s-it-template",
    provider_api: str = "Pod",
    image: str = "busybox:latest",
    namespace: str = "orb-it",
    max_instances: int = 4,
    extra_kubernetes: Optional[dict[str, Any]] = None,
) -> Template:
    """Build a Template with the kubernetes provider_data block populated."""
    kubernetes_block: dict[str, Any] = {
        "namespace": namespace,
        "container_image": image,
        "resource_requests": {"cpu": "100m", "memory": "64Mi"},
        "resource_limits": {"cpu": "500m", "memory": "128Mi"},
    }
    if extra_kubernetes:
        kubernetes_block.update(extra_kubernetes)
    return Template(
        template_id=template_id,
        provider_type="k8s",
        provider_api=provider_api,
        image_id=image,
        max_instances=max_instances,
        provider_data={"k8s": kubernetes_block},
    )


def make_request(
    *,
    request_id: Optional[str] = None,
    provider_api: str = "Pod",
    requested_count: int = 2,
    namespace: str = "orb-it",
    extra_provider_data: Optional[dict[str, Any]] = None,
    template: Optional[Template] = None,
) -> Request:
    """Build a Request scoped to the kubernetes provider.

    When ``template`` is supplied it is attached to
    ``request.metadata['template']`` so the strategy's
    ``_build_template_for_request`` returns it verbatim — this is how
    the REST / CLI submission paths pass the resolved template down.
    """
    provider_data: dict[str, Any] = {"namespace": namespace}
    if extra_provider_data:
        provider_data.update(extra_provider_data)
    rid = request_id or f"req-{uuid.uuid4()}"
    metadata: dict[str, Any] = {}
    if template is not None:
        metadata["template"] = template
    return Request(
        request_id=RequestId(value=rid),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api=provider_api,
        template_id="k8s-it-template",
        requested_count=requested_count,
        provider_data=provider_data,
        metadata=metadata,
    )


def make_pod_object(
    *,
    name: str,
    namespace: str,
    request_id: str,
    machine_id: Optional[str] = None,
    phase: str = "Running",
    ready: bool = True,
    label_prefix: str = "orb.io",
    extra_labels: Optional[dict[str, str]] = None,
    container_reason: Optional[str] = None,
    condition_reason: Optional[str] = None,
    creation_timestamp: Optional[str] = None,
) -> SimpleNamespace:
    """Construct a fake ``V1Pod`` that matches what the kubernetes SDK returns."""
    labels: dict[str, str] = {
        f"{label_prefix}/managed": "true",
        f"{label_prefix}/request-id": request_id,
        f"{label_prefix}/machine-id": machine_id or name,
        f"{label_prefix}/provider-type": "k8s",
    }
    if extra_labels:
        labels.update(extra_labels)

    conditions: list[SimpleNamespace] = []
    if ready:
        conditions.append(SimpleNamespace(type="Ready", status="True", reason=None))
    else:
        conditions.append(SimpleNamespace(type="Ready", status="False", reason=None))
    if condition_reason is not None:
        conditions.append(
            SimpleNamespace(type="PodScheduled", status="False", reason=condition_reason)
        )

    container_statuses: list[SimpleNamespace] = []
    if container_reason is not None:
        if phase == "Failed":
            state = SimpleNamespace(
                terminated=SimpleNamespace(reason=container_reason),
                waiting=None,
            )
        else:
            state = SimpleNamespace(
                terminated=None,
                waiting=SimpleNamespace(reason=container_reason),
            )
        container_statuses.append(SimpleNamespace(state=state))

    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels=labels,
            creation_timestamp=creation_timestamp,
        ),
        spec=SimpleNamespace(node_name=f"node-{name[-1]}" if name else "node-a"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1" if phase == "Running" else None,
            host_ip="10.1.0.1" if phase == "Running" else None,
            start_time=None,
            conditions=conditions,
            container_statuses=container_statuses,
        ),
    )


def make_deployment_object(
    *,
    name: str,
    namespace: str,
    spec_replicas: int,
    ready_replicas: Optional[int] = None,
    available_replicas: Optional[int] = None,
    updated_replicas: Optional[int] = None,
) -> SimpleNamespace:
    """Construct a fake ``V1Deployment`` mirroring the kubernetes SDK shape."""
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace),
        spec=SimpleNamespace(replicas=spec_replicas),
        status=SimpleNamespace(
            replicas=spec_replicas,
            ready_replicas=ready_replicas,
            available_replicas=available_replicas,
            updated_replicas=updated_replicas,
            conditions=[],
        ),
    )


def make_statefulset_object(
    *,
    name: str,
    namespace: str,
    spec_replicas: int,
    ready_replicas: Optional[int] = None,
    current_replicas: Optional[int] = None,
    updated_replicas: Optional[int] = None,
) -> SimpleNamespace:
    """Construct a fake ``V1StatefulSet`` mirroring the kubernetes SDK shape."""
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace),
        spec=SimpleNamespace(replicas=spec_replicas),
        status=SimpleNamespace(
            replicas=spec_replicas,
            ready_replicas=ready_replicas,
            current_replicas=current_replicas,
            updated_replicas=updated_replicas,
            conditions=[],
        ),
    )


def make_job_object(
    *,
    name: str,
    namespace: str,
    parallelism: int,
    active: Optional[int] = None,
    succeeded: Optional[int] = None,
    failed: Optional[int] = None,
    completion_conditions: Optional[list[tuple[str, str]]] = None,
) -> SimpleNamespace:
    """Construct a fake ``V1Job`` mirroring the kubernetes SDK shape."""
    conditions = [
        SimpleNamespace(type=ctype, status=cstatus, reason=None, message=None)
        for ctype, cstatus in (completion_conditions or [])
    ]
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace),
        spec=SimpleNamespace(parallelism=parallelism, completions=parallelism),
        status=SimpleNamespace(
            active=active,
            succeeded=succeeded,
            failed=failed,
            conditions=conditions,
        ),
    )


def make_kubernetes_client_mock(
    *,
    core_v1: Optional[Any] = None,
    apps_v1: Optional[Any] = None,
    batch_v1: Optional[Any] = None,
) -> MagicMock:
    """Return a mock :class:`K8sClient` with the three API facets populated."""
    client = MagicMock(name="K8sClient")
    client.core_v1 = core_v1 if core_v1 is not None else MagicMock(name="CoreV1Api")
    client.apps_v1 = apps_v1 if apps_v1 is not None else MagicMock(name="AppsV1Api")
    client.batch_v1 = batch_v1 if batch_v1 is not None else MagicMock(name="BatchV1Api")
    return client


def make_strategy(
    *,
    client: MagicMock,
    config: Optional[K8sProviderConfig] = None,
    cache: Optional[PodStateCache] = None,
    known_request_ids: Optional[Callable[[], Iterable[str]]] = None,
    initialise: bool = True,
) -> K8sProviderStrategy:
    """Build a fully wired :class:`K8sProviderStrategy` for tests.

    The strategy is wired with a real :class:`MultiNamespaceWatcher`
    seeded with an injected :class:`PodStateCache` (when supplied) so
    cache-fed read paths can be exercised end-to-end.  The watcher's
    ``start`` is never called (``watch_enabled=False`` in the default
    config), so no asyncio task is spawned by the watcher itself.
    """
    cfg = config or make_namespaced_config()
    watch_cache = cache or PodStateCache()
    watch_manager = MultiNamespaceWatcher(
        kubernetes_client=client,
        config=cfg,
        logger=MagicMock(),
        cache=watch_cache,
    )
    strategy = K8sProviderStrategy(
        config=cfg,
        logger=MagicMock(),
        kubernetes_client=client,
        watch_manager=watch_manager,
        known_request_ids=known_request_ids,
    )
    if initialise:
        # Make ``check_health`` succeed during ``initialize`` by default.
        client.core_v1.get_api_resources.return_value = SimpleNamespace(
            group_version="v1", resources=[object(), object()]
        )
        assert strategy.initialize() is True
    return strategy


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def k8s_namespace() -> str:
    """The namespace shared by every test in this directory."""
    return "orb-it"


@pytest.fixture
def k8s_config(k8s_namespace: str) -> K8sProviderConfig:
    return make_namespaced_config(namespace=k8s_namespace)


@pytest.fixture
def k8s_cache() -> PodStateCache:
    return PodStateCache()
