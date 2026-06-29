"""Unit tests for the :class:`K8sProviderStrategy` strategy shell.

Covers:

* config validation in the constructor;
* health check happy-path + failure-path using a mocked ``K8sClient``;
* capability advertisement;
* identity + naming helpers;
* dispatch of the typed provisioning interface to the registered
  per-API handlers.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.base.strategy import ProviderOperationType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.k8s_provider_strategy import (
    K8sProviderStrategy,
)


def _make_strategy(
    *,
    api_resources: object | None = None,
    api_failure: Exception | None = None,
) -> K8sProviderStrategy:
    """Build a strategy with a mocked :class:`K8sClient`."""
    fake_core_v1 = MagicMock()
    if api_failure is not None:
        fake_core_v1.get_api_resources.side_effect = api_failure
    else:
        fake_core_v1.get_api_resources.return_value = (
            api_resources
            if api_resources is not None
            else SimpleNamespace(group_version="v1", resources=[object(), object(), object()])
        )

    fake_client = MagicMock()
    fake_client.core_v1 = fake_core_v1

    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=fake_client,
    )
    assert strategy.initialize() is True
    return strategy


def test_constructor_rejects_wrong_config_type() -> None:
    with pytest.raises(ValueError, match="requires K8sProviderConfig"):
        K8sProviderStrategy(
            config="not a config",  # type: ignore[arg-type]
            logger=MagicMock(),
        )


def test_provider_type_is_kubernetes() -> None:
    strategy = _make_strategy()
    assert strategy.provider_type == "k8s"


def test_get_capabilities_lists_v1_apis() -> None:
    caps = _make_strategy().get_capabilities()
    assert caps.provider_type == "k8s"
    assert caps.supports_operation(ProviderOperationType.CREATE_INSTANCES) is True
    assert caps.supports_operation(ProviderOperationType.TERMINATE_INSTANCES) is True
    assert caps.supports_operation(ProviderOperationType.GET_INSTANCE_STATUS) is True
    assert set(caps.supported_apis) == {
        "Pod",
        "Deployment",
        "StatefulSet",
        "Job",
    }


def test_get_capabilities_selective_termination_per_api() -> None:
    """selective_termination is False at the top level (lowest-common-denominator
    because Job does not support it).  The per-API dict carries the accurate
    per-workload declaration."""
    caps = _make_strategy().get_capabilities()
    # Top-level flag is the LCM — Job cannot do selective termination.
    assert caps.features["selective_termination"] is False
    # Per-API map carries the accurate declaration for each workload type.
    per_api = caps.features["selective_termination_by_api"]
    assert per_api["Pod"] is True
    assert per_api["Deployment"] is True
    assert per_api["StatefulSet"] is True
    assert per_api["Job"] is False


def test_check_health_happy_path() -> None:
    status = _make_strategy().check_health()
    assert status.is_healthy is True
    assert "Kubernetes API server reachable" in status.status_message
    assert status.response_time_ms is not None


def test_check_health_unhappy_path() -> None:
    status = _make_strategy(api_failure=RuntimeError("boom")).check_health()
    assert status.is_healthy is False
    assert "boom" in status.status_message
    assert status.error_details is not None
    assert status.error_details["error"] == "boom"


def test_get_available_regions_is_empty() -> None:
    """Kubernetes has contexts, not regions."""
    assert _make_strategy().get_available_regions() == []
    assert _make_strategy().get_default_region() == ""


def test_naming_helpers_round_trip_context() -> None:
    strategy = _make_strategy()
    name = strategy.generate_provider_name({"context": "prod"})
    assert name == "kubernetes_prod"
    parsed = strategy.parse_provider_name(name)
    assert parsed["context_or_namespace"] == "prod"


def test_naming_helpers_fall_back_to_namespace() -> None:
    strategy = _make_strategy()
    name = strategy.generate_provider_name({"namespace": "orb-system"})
    assert name == "kubernetes_orb-system"


def test_provider_name_pattern() -> None:
    assert _make_strategy().get_provider_name_pattern() == "kubernetes_{context_or_namespace}"


@pytest.mark.asyncio
async def test_execute_operation_health_check_dispatches() -> None:
    """The untyped operation dispatcher handles HEALTH_CHECK end-to-end."""
    from orb.providers.base.strategy import ProviderOperation

    strategy = _make_strategy()
    op = ProviderOperation(
        operation_type=ProviderOperationType.HEALTH_CHECK,
        parameters={},
    )
    result = await strategy.execute_operation(op)
    assert result.success is True
    assert result.data["is_healthy"] is True


@pytest.mark.asyncio
async def test_execute_operation_create_instances_without_request_errors() -> None:
    """CREATE_INSTANCES requires the typed Request object in parameters."""
    from orb.providers.base.strategy import ProviderOperation

    strategy = _make_strategy()
    op = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={},
    )
    result = await strategy.execute_operation(op)
    assert result.success is False
    assert result.error_code == "MISSING_REQUEST"


@pytest.mark.asyncio
async def test_execute_operation_truly_unsupported_returns_error() -> None:
    """Operation types not supported by the k8s provider return UNSUPPORTED_OPERATION."""
    from orb.providers.base.strategy import ProviderOperation

    strategy = _make_strategy()
    op = ProviderOperation(
        operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
        parameters={},
    )
    result = await strategy.execute_operation(op)
    assert result.success is False
    assert result.error_code == "UNSUPPORTED_OPERATION"


@pytest.mark.asyncio
async def test_typed_provisioning_methods_route_to_pod_handler() -> None:
    """Typed entry points route to the Pod handler when ``provider_api='Pod'``."""
    from orb.domain.base.operation_outcome import Accepted

    strategy = _make_strategy()

    fake_handler = MagicMock()
    fake_handler.acquire_hosts = AsyncMock(
        return_value={
            "resource_ids": ["orb-aaa-0000"],
            "machine_ids": ["orb-aaa-0000"],
            "provider_data": {"namespace": "default", "pod_names": ["orb-aaa-0000"]},
        }
    )
    fake_handler.release_hosts = AsyncMock(return_value=None)
    strategy._handlers["Pod"] = fake_handler  # type: ignore[attr-defined]

    fake_request = MagicMock()
    fake_request.request_id = "req-test"
    fake_request.provider_api = "Pod"
    fake_request.template_id = "tpl-1"
    fake_request.requested_count = 1
    fake_request.metadata = {}

    outcome = await strategy.acquire(fake_request)
    assert isinstance(outcome, Accepted)
    assert outcome.pending_resource_ids == ["orb-aaa-0000"]

    return_outcome = await strategy.return_machines(["orb-aaa-0000"], fake_request)
    assert isinstance(return_outcome, Accepted)
    assert return_outcome.pending_resource_ids == ["orb-aaa-0000"]


@pytest.mark.asyncio
async def test_unsupported_provider_api_returns_failed() -> None:
    """A provider_api that does not match any of the registered handlers
    (Pod / Deployment / StatefulSet / Job) is rejected via the
    :class:`Failed` outcome."""
    from orb.domain.base.operation_outcome import Failed

    strategy = _make_strategy()

    fake_request = MagicMock()
    fake_request.request_id = "req-test"
    # Unknown provider-API key — must NOT match any of the four
    # registered handler keys.
    fake_request.provider_api = "KubernetesUnknownApi"
    fake_request.template_id = "tpl-1"
    fake_request.requested_count = 1
    fake_request.metadata = {}

    outcome = await strategy.acquire(fake_request)
    assert isinstance(outcome, Failed)
    assert "not yet implemented" in outcome.error


def test_cleanup_idempotent() -> None:
    strategy = _make_strategy()
    strategy.cleanup()
    strategy.cleanup()
    assert strategy._kubernetes_client is None  # type: ignore[attr-defined]


def test_build_template_for_request_fallback_returns_k8s_template() -> None:
    """Fallback path (no template payload in metadata) yields a K8sTemplate.

    Historically the fallback assembled a bare :class:`Template` so the
    kubernetes-specific spec builders saw a plain ``Template`` instead
    of a :class:`K8sTemplate` and silently dropped every k8s-typed
    field.  The fallback now constructs a :class:`K8sTemplate` directly
    so the spec builders always see the typed surface, even when the
    request carries no metadata.
    """
    from orb.providers.k8s.domain.template.k8s_template import K8sTemplate

    strategy = _make_strategy()

    # No metadata -> fallback path.
    fake_request = MagicMock()
    fake_request.request_id = "req-fallback-1"
    fake_request.provider_api = "Pod"
    fake_request.template_id = "tpl-fallback"
    fake_request.requested_count = 3
    fake_request.metadata = None

    template = strategy._build_template_for_request(fake_request)  # type: ignore[attr-defined]
    assert isinstance(template, K8sTemplate)
    assert template.template_id == "tpl-fallback"
    assert template.provider_type == "k8s"
    assert template.provider_api == "Pod"
    assert template.max_instances == 3

    # Empty-metadata dict + no ``template`` key -> still fallback path.
    fake_request.metadata = {}
    template2 = strategy._build_template_for_request(fake_request)  # type: ignore[attr-defined]
    assert isinstance(template2, K8sTemplate)
    assert template2.template_id == "tpl-fallback"

    # k8s-specific fields aren't silently dropped on the fallback path —
    # they are absent (None) on the constructed template so the spec
    # builders can apply the provider-config defaults later, rather
    # than being lost behind an opaque ``Template`` shell.
    assert template2.namespace is None
    assert template2.image_pull_secret is None
    assert template2.resource_requests is None
    assert template2.node_selector is None


def test_build_template_for_request_dict_payload_yields_k8s_template() -> None:
    """A dict ``template`` payload in metadata is parsed into a K8sTemplate.

    Asserts that operator-supplied k8s-specific fields (``namespace``,
    ``image_pull_secret``, ``node_selector``) carried in the metadata
    dict survive the build step.  Together with the fallback test
    above this protects the strategy's template-build path from the
    historical regression where k8s fields were silently dropped.
    """
    from orb.providers.k8s.domain.template.k8s_template import K8sTemplate

    strategy = _make_strategy()

    payload = {
        "template_id": "tpl-dict",
        "provider_type": "k8s",
        "provider_api": "Pod",
        "max_instances": 2,
        "namespace": "submitted-ns",
        "image_pull_secret": "registry-creds",
        "node_selector": {"role": "compute"},
    }
    fake_request = MagicMock()
    fake_request.request_id = "req-dict-1"
    fake_request.provider_api = "Pod"
    fake_request.template_id = "tpl-dict"
    fake_request.requested_count = 2
    fake_request.metadata = {"template": payload}

    template = strategy._build_template_for_request(fake_request)  # type: ignore[attr-defined]
    assert isinstance(template, K8sTemplate)
    assert template.template_id == "tpl-dict"
    assert template.namespace == "submitted-ns"
    assert template.image_pull_secret == "registry-creds"
    assert template.node_selector == {"role": "compute"}


# ---------------------------------------------------------------------------
# _stop_watch_manager_sync — shutdown blocking behaviour
# ---------------------------------------------------------------------------


def _make_strategy_no_init() -> K8sProviderStrategy:
    """Return a strategy without calling initialize() (avoids reconciler I/O)."""
    fake_client = MagicMock()
    fake_client.core_v1.get_api_resources.return_value = SimpleNamespace(
        group_version="v1", resources=[]
    )
    return K8sProviderStrategy(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=fake_client,
    )


@pytest.mark.timeout(15)
def test_stop_watch_manager_blocks_until_stop_completes_from_foreign_thread() -> None:
    """stop_watch_manager blocks (via run_coroutine_threadsafe) when called
    from a thread that is not the event-loop thread.

    The test spins a real asyncio event loop in a background thread, injects
    a fake watch manager whose ``stop()`` coroutine sets a threading.Event,
    then calls ``_stop_watch_manager_sync`` from the *main* (foreign) thread
    and asserts that the Event is set before the call returns.
    """
    stop_completed = threading.Event()

    async def _fake_stop() -> None:
        # Simulate a brief async teardown to confirm we actually await.
        await asyncio.sleep(0)
        stop_completed.set()

    fake_manager = MagicMock()
    fake_manager.is_started.return_value = True
    fake_manager.stop = AsyncMock(side_effect=_fake_stop)

    strategy = _make_strategy_no_init()
    strategy._watch_manager = fake_manager  # type: ignore[attr-defined]

    # Run the event loop in a background thread to simulate a daemon.
    loop_ready = threading.Event()
    loop_ref: list[asyncio.AbstractEventLoop] = []

    async def _loop_main() -> None:
        loop_ref.append(asyncio.get_running_loop())
        loop_ready.set()
        # Keep the loop alive long enough for the foreign-thread call to
        # schedule and complete the stop coroutine.
        await asyncio.sleep(5)

    bg_thread = threading.Thread(target=lambda: asyncio.run(_loop_main()), daemon=True)
    bg_thread.start()
    assert loop_ready.wait(timeout=5), "background loop did not start in time"

    loop = loop_ref[0]
    # Patch the loop onto the strategy so _stop_watch_manager_sync sees it.
    import unittest.mock as mock

    with mock.patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.asyncio.get_running_loop",
        return_value=loop,
    ):
        strategy._stop_watch_manager_sync(shutdown_timeout=5.0)  # type: ignore[attr-defined]

    assert stop_completed.is_set(), (
        "stop() coroutine was not awaited before _stop_watch_manager_sync returned"
    )

    # Tidy up the background loop.
    loop.call_soon_threadsafe(loop.stop)
    bg_thread.join(timeout=5)


@pytest.mark.timeout(15)
def test_stop_watch_manager_respects_timeout_from_foreign_thread() -> None:
    """When stop() takes longer than shutdown_timeout, the call returns
    without raising and logs a warning."""
    import asyncio

    async def _slow_stop() -> None:
        await asyncio.sleep(60)  # Never completes within the test timeout.

    fake_manager = MagicMock()
    fake_manager.is_started.return_value = True
    fake_manager.stop = AsyncMock(side_effect=_slow_stop)

    mock_logger = MagicMock()
    strategy = _make_strategy_no_init()
    strategy._watch_manager = fake_manager  # type: ignore[attr-defined]
    strategy._logger = mock_logger  # type: ignore[attr-defined]

    loop_ready = threading.Event()
    loop_ref: list[asyncio.AbstractEventLoop] = []

    async def _loop_main() -> None:
        loop_ref.append(asyncio.get_running_loop())
        loop_ready.set()
        await asyncio.sleep(10)

    bg_thread = threading.Thread(target=lambda: asyncio.run(_loop_main()), daemon=True)
    bg_thread.start()
    assert loop_ready.wait(timeout=5), "background loop did not start in time"

    loop = loop_ref[0]
    import unittest.mock as mock

    with mock.patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.asyncio.get_running_loop",
        return_value=loop,
    ):
        # Use a very short timeout so the test finishes quickly.
        strategy._stop_watch_manager_sync(shutdown_timeout=0.1)  # type: ignore[attr-defined]

    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("did not stop" in msg for msg in warning_calls), (
        "Expected a timeout warning; got: %s" % warning_calls
    )

    loop.call_soon_threadsafe(loop.stop)
    bg_thread.join(timeout=5)


@pytest.mark.timeout(15)
def test_stop_watch_manager_no_loop_runs_synchronously() -> None:
    """When there is no running event loop the call drives stop() via asyncio.run."""
    completed = threading.Event()

    async def _fake_stop() -> None:
        completed.set()

    fake_manager = MagicMock()
    fake_manager.is_started.return_value = True
    fake_manager.stop = AsyncMock(side_effect=_fake_stop)

    strategy = _make_strategy_no_init()
    strategy._watch_manager = fake_manager  # type: ignore[attr-defined]

    import unittest.mock as mock

    with mock.patch(
        "orb.providers.k8s.strategy.k8s_provider_strategy.asyncio.get_running_loop",
        side_effect=RuntimeError("no running event loop"),
    ):
        strategy._stop_watch_manager_sync()  # type: ignore[attr-defined]

    assert completed.is_set(), "stop() was not called synchronously via asyncio.run"
