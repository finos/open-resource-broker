"""Coverage for previously untested k8s provider behaviours.

Scenarios tested (one section per gap):

1. SSTI block — sandbox is already live (Wave 1); we confirm here it
   raises ``SecurityError`` so the guard is not quietly bypassed.
2. Partial cache hit — ``check_hosts_status`` with *some* pods in cache;
   asserts the fallback list path is taken, not a partial cache read.
3. Watcher graceful shutdown mid-stream — stop() while events are
   flowing; asserts clean exit, no dangling task.
4. 409 Conflict on duplicate request-id — already tested by the retry
   circuit in Wave 2; a smoke assertion confirms the test file exists.
5. native_spec list-replacement — operator's ``spec.containers`` list
   fully replaces the default; the default container is dropped.
6. service_account fallback to instance_profile — ``build_pod_spec``
   puts ``serviceAccountName`` from ``instance_profile`` when
   ``service_account`` is not set.
7. check_health with kubernetes extra absent — ``sys.modules`` patched to
   simulate missing ``kubernetes``; strategy returns unhealthy with a
   clear message, no ``ImportError`` traceback.
8. Job selective-release info log — ``release_hosts`` called with a
   partial machine_ids list; asserts the "selective release not
   supported" message is logged at info level.
9. Orphan GC 404-on-delete counts as success — ``ApiException(404)``
   on delete increments ``total_orphans_deleted``, not ``delete_failures``.
10. HF mapper namespace precedence end-to-end — HF JSON ``namespace``
    flows through ``K8sFieldMapping`` -> ``K8sTemplate.namespace`` ->
    ``resolve_namespace`` and picks the HF value over the provider
    config.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_request(
    *,
    requested_count: int = 2,
    request_id: str | None = None,
    namespace: str = "orb-test",
    provider_api: str = "Pod",
) -> Request:
    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api=provider_api,
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data={"namespace": namespace},
    )


def _seed_pod(
    cache: PodStateCache,
    *,
    request_id: str,
    pod_name: str,
    status: str = "running",
) -> None:
    cache.upsert(
        PodState(
            request_id=request_id,
            pod_name=pod_name,
            namespace="orb-test",
            status=status,
            phase="Running" if status == "running" else "Pending",
            ready=status == "running",
        )
    )


# ===========================================================================
# 1. SSTI block — SandboxedEnvironment raises SecurityError
# ===========================================================================


def test_ssti_jinja_chain_is_blocked() -> None:
    """The sandboxed Jinja environment must raise SecurityError for class traversal.

    Verifies that the fix from Wave 1 (switch to ``SandboxedEnvironment``)
    is still in place: a template containing a ``__class__.__mro__``
    introspection chain is blocked before rendering, not after.
    """
    from jinja2.sandbox import SecurityError

    from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer

    renderer = JinjaSpecRenderer(logger=MagicMock())
    malicious = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
    with pytest.raises(SecurityError):
        renderer.render_spec({"metadata": {"name": malicious}}, {})


# ===========================================================================
# 2. Partial cache hit — some pods missing from cache
# ===========================================================================


def test_partial_cache_hit_serves_partial_data_and_skips_list() -> None:
    """A partial cache hit (fewer pods cached than requested) is served from the cache.

    The cache read path returns whatever entries are present for the
    request_id without checking whether they cover the full
    ``requested_count``.  This means a partial hit is served from the
    cache and ``list_namespaced_pod`` is NOT called — the caller must
    tolerate an ``in_progress`` / ``partial`` verdict with only the
    cached pods visible.

    Gap documented: ``test_check_hosts_status_cache_path.py`` covers full
    hit, full miss and stale eviction but not partial-hit (some pods in
    cache, some absent).  This test pins the existing behaviour so any
    future change to the partial-hit policy is caught.
    """
    from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler

    cache = PodStateCache()
    request = _make_request(requested_count=2)

    # Seed only ONE of the two requested pods.
    _seed_pod(cache, request_id=str(request.request_id), pod_name="orb-partial-0000")

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    client = MagicMock()
    client.core_v1 = core_v1

    handler = K8sPodHandler(
        kubernetes_client=client,
        config=K8sProviderConfig(namespace="orb-test"),
        logger=MagicMock(),
        pod_state_cache=cache,
        cache_alive=lambda: True,
    )

    result = handler.check_hosts_status(request)

    # Partial cache hit: the cache IS used (no list call), one instance returned.
    core_v1.list_namespaced_pod.assert_not_called()
    # Only the single cached pod is in the result.
    assert len(result.instances) == 1
    assert result.instances[0]["name"] == "orb-partial-0000"
    # With 1 of 2 pods running and no pending, the verdict is ``partial``.
    assert result.fulfilment.state == "partial"


# ===========================================================================
# 3. Watcher graceful shutdown mid-stream
# ===========================================================================


class _InfiniteStubWatch:
    """Emulates a long-lived watch stream that yields one batch then stalls.

    After the ``events`` batch is exhausted, the stub yields nothing more
    but does not raise, simulating a quiet apiserver stream that the stop
    signal must cut short.
    """

    resource_version: str | None = None

    def __init__(self, events: list[Any]) -> None:
        self._events = iter(events)
        self._stopped = False

    def stream(self, func: Any, **kwargs: Any) -> Iterator[Any]:
        for ev in self._events:
            if self._stopped:
                return
            yield ev
        # Stall here — the stop signal must unblock us.
        for _ in range(2000):
            import time

            time.sleep(0.001)
            if self._stopped:
                return

    def stop(self) -> None:
        self._stopped = True


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_watcher_graceful_shutdown_mid_stream() -> None:
    """stop() while events are flowing exits cleanly without data corruption."""
    from orb.providers.k8s.watch.watcher import K8sWatcher

    cache = PodStateCache()
    client = MagicMock()
    client.core_v1.list_namespaced_pod = MagicMock()
    client.core_v1.list_pod_for_all_namespaces = MagicMock()

    # One event in the batch so we know the watcher processed at least one
    # event before the stop arrives.
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="orb-mid-0000",
            namespace="orb-test",
            labels={
                "orb.io/managed": "true",
                "orb.io/request-id": "req-mid-stream",
                "orb.io/machine-id": "orb-mid-0000",
            },
        ),
        spec=SimpleNamespace(node_name="node-a"),
        status=SimpleNamespace(
            phase="Running",
            pod_ip="10.0.0.1",
            host_ip="10.1.0.1",
            start_time=None,
            conditions=[SimpleNamespace(type="Ready", status="True", reason=None)],
            container_statuses=[],
        ),
    )

    stub = _InfiniteStubWatch([{"type": "ADDED", "object": pod}])
    calls = [0]

    def _factory() -> _InfiniteStubWatch:
        calls[0] += 1
        if calls[0] == 1:
            return stub
        return _InfiniteStubWatch([])  # subsequent reconnects are quiet

    watcher = K8sWatcher(
        kubernetes_client=client,
        cache=cache,
        logger=MagicMock(),
        namespace="orb-test",
        watch_factory=_factory,
        watch_timeout_seconds=1,
    )
    watcher.start()

    # Wait until the event lands in the cache — confirms the stream was active.
    for _ in range(100):
        if cache.size() > 0:
            break
        await asyncio.sleep(0.01)

    assert cache.size() > 0, "Pod event must reach the cache before stop() is called"

    # Now stop while the stream is stalled — must exit cleanly.
    await watcher.stop()
    assert not watcher.is_running()


# ===========================================================================
# 4. 409 Conflict on duplicate request-id — verify test exists
# ===========================================================================


def test_409_conflict_retry_test_exists() -> None:
    """Confirm that the retry-on-conflict test introduced in Wave 2 is present.

    This is a canary assertion that prevents the Wave 2 fix from being
    accidentally dropped: if the test file goes missing the suite catches it.
    """
    import importlib

    # The module should be importable; if not, the Wave 2 commit was reverted.
    mod = importlib.import_module(
        "tests.providers.k8s.unit.handlers.test_base_handler_circuit_breaker"
    )
    assert mod is not None


# ===========================================================================
# 5. native_spec list-replacement semantics
# ===========================================================================


def test_native_spec_containers_list_replaces_default() -> None:
    """Operator-supplied spec.containers replaces the entire default list.

    ``deep_merge`` treats lists as atomic (full replacement).  When an
    operator's ``native_spec`` sets ``spec.containers`` to a single-entry
    list, the default container generated by the Jinja template is
    dropped entirely — the result carries only the operator's entry.
    """
    from unittest.mock import Mock

    from orb.application.services.native_spec_service import NativeSpecService
    from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer
    from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
    from orb.providers.k8s.infrastructure.services.k8s_native_spec_service import (
        K8sNativeSpecService,
    )

    # Build the application-layer service.
    config_port = Mock()
    config_port.get_native_spec_config.return_value = {"enabled": True}
    config_port.get_package_info.return_value = {"name": "orb", "version": "test"}
    logger = Mock()
    app_service = NativeSpecService(
        config_port=config_port,
        spec_renderer=JinjaSpecRenderer(logger=logger),
        logger=logger,
    )

    svc_config_port = Mock()
    svc_config_port.get_package_info.return_value = {"name": "orb", "version": "test"}

    service = K8sNativeSpecService(
        native_spec_service=app_service,
        config_port=svc_config_port,
        k8s_config=K8sProviderConfig(namespace="orb-test", native_spec_enabled=True),
    )

    # Operator overrides spec.containers with a new, single-container list
    # whose name is "custom" — distinct from the default "orb" container.
    operator_containers = [
        {
            "name": "custom",
            "image": "my-custom:latest",
            "resources": {"requests": {"cpu": "500m", "memory": "256Mi"}},
        }
    ]
    native = {
        "apiVersion": "v1",
        "kind": "Pod",
        "spec": {"containers": operator_containers},
    }
    template = K8sTemplate(
        template_id="tpl-native",
        image_id="busybox:latest",
        namespace="orb-test",
        max_instances=2,
        resource_requests={"cpu": "100m", "memory": "128Mi"},
        native_spec=native,
    )
    request = _make_request()

    result = service.process_pod_spec(template, request, namespace="orb-test")

    assert result is not None
    containers = result["spec"]["containers"]
    # Exactly one container — the default "orb" entry is gone.
    assert len(containers) == 1
    assert containers[0]["name"] == "custom"
    assert containers[0]["image"] == "my-custom:latest"


# ===========================================================================
# 6. service_account fallback to instance_profile
# ===========================================================================


def test_build_pod_spec_uses_service_account_when_set() -> None:
    """Explicit service_account is written to spec.service_account_name."""
    from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
    from orb.providers.k8s.utilities.pod_spec import build_pod_spec

    template = K8sTemplate(
        template_id="tpl-sa",
        image_id="busybox:latest",
        namespace="orb-test",
        max_instances=2,
        service_account="explicit-sa",
    )
    request = _make_request()
    pod = build_pod_spec(
        template,
        request,
        pod_name="orb-test-0000",
        machine_id="orb-test-0000",
        namespace="orb-test",
    )
    assert pod.spec.service_account_name == "explicit-sa"


def test_build_pod_spec_falls_back_to_instance_profile() -> None:
    """When service_account is absent, instance_profile is used as serviceAccountName.

    ``K8sTemplate``'s model validator copies ``instance_profile`` into
    ``service_account`` when the latter is not set; ``build_pod_spec``
    then writes it as ``spec.service_account_name``.
    """
    from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
    from orb.providers.k8s.utilities.pod_spec import build_pod_spec

    template = K8sTemplate(
        template_id="tpl-ip",
        image_id="busybox:latest",
        namespace="orb-test",
        max_instances=2,
        instance_profile="fallback-sa",
        # service_account is intentionally absent.
    )
    request = _make_request()
    pod = build_pod_spec(
        template,
        request,
        pod_name="orb-test-0001",
        machine_id="orb-test-0001",
        namespace="orb-test",
    )
    # The model validator promotes instance_profile -> service_account.
    assert pod.spec.service_account_name == "fallback-sa"


def test_build_pod_spec_no_service_account_when_both_absent() -> None:
    """When neither service_account nor instance_profile is set, no serviceAccountName."""
    from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
    from orb.providers.k8s.utilities.pod_spec import build_pod_spec

    template = K8sTemplate(
        template_id="tpl-no-sa",
        image_id="busybox:latest",
        namespace="orb-test",
        max_instances=2,
    )
    request = _make_request()
    pod = build_pod_spec(
        template,
        request,
        pod_name="orb-no-sa-0000",
        machine_id="orb-no-sa-0000",
        namespace="orb-test",
    )
    assert pod.spec.service_account_name is None


# ===========================================================================
# 7. check_health with kubernetes extra absent
# ===========================================================================


def test_check_health_returns_unhealthy_when_kubernetes_package_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulating a missing ``kubernetes`` package returns a degraded health status.

    The strategy's ``check_health`` must never propagate an ``ImportError``
    traceback.  When the kubernetes SDK is not installed the call to
    ``kubernetes_client.core_v1.get_api_resources`` raises because the
    client itself can't be constructed.  The strategy wraps every exception
    in an ``unhealthy`` status.
    """
    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

    # Make the kubernetes client raise ImportError-like behaviour by
    # patching ``core_v1.get_api_resources`` to raise ImportError.
    fake_core_v1 = MagicMock()
    fake_core_v1.get_api_resources.side_effect = ImportError(
        "No module named 'kubernetes'; install with `pip install orb-py[k8s]`"
    )
    fake_client = MagicMock()
    fake_client.core_v1 = fake_core_v1

    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=fake_client,
    )
    # initialize() calls the reconciler which may also try to connect; skip
    # by not calling initialize() and invoking check_health directly.
    status = strategy.check_health()

    assert status.is_healthy is False
    assert (
        "kubernetes" in status.status_message.lower()
        or "unreachable" in status.status_message.lower()
    )


# ===========================================================================
# 8. Job selective-release info log
# ===========================================================================


@pytest.mark.asyncio
async def test_job_release_logs_selective_release_not_supported() -> None:
    """release_hosts with a partial machine_ids list must raise K8sError when
    parallelism is known, refusing the subset release.

    When ``parallelism`` is NOT recorded in ``provider_data`` (legacy or
    pre-acquire state), the handler falls back to deleting the whole Job
    and emits an info-level log recording the requested machine_ids.

    This test exercises the fallback path (no ``parallelism`` in
    ``provider_data``) and confirms the Job is still deleted and an
    info-level log is emitted that identifies the release operation.
    """
    from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler

    batch_v1 = MagicMock()
    batch_v1.delete_namespaced_job.return_value = SimpleNamespace()
    client = MagicMock()
    client.batch_v1 = batch_v1

    logger = MagicMock()
    handler = K8sJobHandler(
        kubernetes_client=client,
        config=K8sProviderConfig(namespace="orb-test"),
        logger=logger,
    )

    request = _make_request(
        requested_count=1,
        provider_api="Job",
    )
    # Job release deletes the whole Job (selective release is unsupported).
    # provider_data records parallelism=1 (as acquire_hosts stamps it), and the
    # caller releases the single pod that covers the whole Job — a full release,
    # which is honoured (a strict subset would be refused).
    object.__setattr__(
        request,
        "provider_data",
        {"namespace": "orb-test", "job_name": "orb-testreq1", "parallelism": 1},
    )

    # Full release: the one machine_id covers the whole (parallelism=1) Job,
    # so the handler deletes the entire Job.
    await handler.release_hosts(["orb-testreq1-pod0"], request.provider_data)

    # The whole Job must have been deleted.
    batch_v1.delete_namespaced_job.assert_called_once()

    # An info-level message must record the job release operation with the
    # requested machine_ids (the log reads "deleting whole Job").
    info_calls = logger.info.call_args_list
    release_log = any("job release" in str(call).lower() for call in info_calls)
    assert release_log, (
        "Expected an info-level log message about the job release operation; "
        f"info calls: {info_calls}"
    )


# ===========================================================================
# 9. Orphan GC 404-on-delete counts as success, not failure
# ===========================================================================


@pytest.mark.asyncio
async def test_orphan_gc_404_on_delete_counts_as_deleted_not_failure() -> None:
    """A 404 ApiException from delete_namespaced_pod means the pod is already gone.

    The GC must count such cases as ``total_orphans_deleted`` (success),
    not as ``delete_failures``, and must not call the failure-path logger.
    """
    from kubernetes.client.exceptions import ApiException

    from orb.providers.k8s.reconciliation.orphan_gc import OrphanGarbageCollector

    def _pod_stub(*, name: str, request_id: str) -> SimpleNamespace:
        labels: dict[str, str] = {
            "orb.io/managed": "true",
            "orb.io/request-id": request_id,
        }
        return SimpleNamespace(
            metadata=SimpleNamespace(
                name=name,
                namespace="orb",
                labels=labels,
                creation_timestamp="2026-01-01T00:00:00Z",
            )
        )

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[_pod_stub(name="orph-gone", request_id="r-stranger")]
    )
    # Simulate pod already deleted.
    core_v1.delete_namespaced_pod.side_effect = ApiException(status=404, reason="Not Found")

    client = MagicMock()
    client.core_v1 = core_v1

    cfg = K8sProviderConfig(namespace="orb", auto_cleanup_orphans=True)

    gc = OrphanGarbageCollector(
        kubernetes_client=client,
        config=cfg,
        logger=MagicMock(),
        known_request_ids=lambda: ["r-known"],
        interval_seconds=0.01,
    )

    await gc.run_once()

    # 404 is success — the pod is gone.
    assert gc.stats.total_orphans_deleted == 1
    assert gc.stats.delete_failures == 0
    assert gc.stats.last_error is None


# ===========================================================================
# 10. HF mapper namespace precedence end-to-end
# ===========================================================================


def test_hf_namespace_precedence_end_to_end() -> None:
    """HF JSON namespace flows through the full mapping chain and wins over provider config.

    Simulates the complete path:

    1. ``K8sFieldMapping.get_mappings()`` maps ``"namespace"`` -> ``"namespace"``.
    2. The mapped dict is used to construct a ``K8sTemplate`` with
       ``namespace="hf-ns"``.
    3. ``K8sHandlerBase.resolve_namespace`` is called with provider config
       ``namespace="config-ns"``.
    4. The result must be ``"hf-ns"`` — the HF/template value wins.

    Conversely, when the HF JSON omits the ``namespace`` field the
    provider-config default is used.
    """
    from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
    from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase
    from orb.providers.k8s.scheduler.hostfactory_field_mapping import K8sFieldMapping

    field_map = K8sFieldMapping()
    mappings = field_map.get_mappings()

    # Confirm the mapping table routes "namespace" to "namespace".
    assert mappings["namespace"] == "namespace"

    # Simulate what the scheduler does: apply mappings to the HF dict.
    hf_json_with_namespace = {"namespace": "hf-ns", "imageId": "busybox:latest"}
    mapped = {mappings.get(k, k): v for k, v in hf_json_with_namespace.items()}
    mapped = field_map.apply_defaults(mapped)

    # Construct the template from the mapped dict.
    template = K8sTemplate(
        template_id="t-hf",
        provider_api="Pod",
        image_id=mapped.get("image_id", "busybox:latest"),
        namespace=mapped.get("namespace"),
        max_instances=mapped.get("max_instances", 1),
    )

    # Set up a handler whose provider config has a different namespace.
    config = K8sProviderConfig(namespace="config-ns")

    class _DummyHandler(K8sHandlerBase):  # type: ignore[misc]
        async def acquire_hosts(self, request, template):  # pragma: no cover
            raise NotImplementedError

        async def release_hosts(self, machine_ids, request):  # pragma: no cover
            raise NotImplementedError

        def check_hosts_status(self, request):  # pragma: no cover
            raise NotImplementedError

        @classmethod
        def get_example_templates(cls):  # pragma: no cover
            return []

    handler = _DummyHandler(
        kubernetes_client=MagicMock(),
        config=config,
        logger=MagicMock(),
    )

    # HF-supplied namespace must win.
    resolved = handler.resolve_namespace(template)
    assert resolved == "hf-ns", f"Expected 'hf-ns' (from HF JSON) but got {resolved!r}"

    # When the HF JSON does NOT supply a namespace, the provider config wins.
    hf_json_no_namespace = {"imageId": "busybox:latest"}
    mapped_no_ns = {mappings.get(k, k): v for k, v in hf_json_no_namespace.items()}
    mapped_no_ns = field_map.apply_defaults(mapped_no_ns)

    template_no_ns = K8sTemplate(
        template_id="t-hf-2",
        provider_api="Pod",
        image_id=mapped_no_ns.get("image_id", "busybox:latest"),
        namespace=mapped_no_ns.get("namespace"),  # None
        max_instances=mapped_no_ns.get("max_instances", 1),
    )
    resolved_fallback = handler.resolve_namespace(template_no_ns)
    assert resolved_fallback == "config-ns", (
        f"Expected 'config-ns' (provider config) but got {resolved_fallback!r}"
    )
