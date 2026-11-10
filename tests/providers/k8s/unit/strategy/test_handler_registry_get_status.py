"""Unit tests for K8sHandlerRegistry.get_status terminated-synthesis logic.

Covers the reconciliation of caller-supplied resource_ids against the live
cluster list: absent IDs for confirmed-submitted pods are surfaced as
synthetic 'terminated' entries; request-type determines whether the overall
outcome is Completed or Accepted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.operation_outcome import Accepted, Completed, Failed
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.request_types import RequestType
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(handler: Any | None = None) -> K8sHandlerRegistry:
    """Build a K8sHandlerRegistry with all providers mocked out."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig

    fake_client = MagicMock()
    fake_watcher = None

    registry = K8sHandlerRegistry(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        client_provider=lambda: fake_client,
        watch_manager_provider=lambda: fake_watcher,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
        handler_overrides={"Pod": handler} if handler is not None else {},
    )
    return registry


def _make_check_result(
    instances: list[dict],
    state: str = "fulfilled",
) -> CheckHostsStatusResult:
    return CheckHostsStatusResult(
        instances=instances,
        fulfilment=ProviderFulfilment(state=state, message="test"),  # type: ignore[arg-type]
    )


def _make_request(
    *,
    request_type: RequestType = RequestType.ACQUIRE,
    pod_names: list[str] | None = None,
    request_id: str = "req-test",
) -> MagicMock:
    req = MagicMock()
    req.request_id = request_id
    req.provider_api = "Pod"
    req.request_type = request_type
    req.provider_data = {"pod_names": pod_names or []}
    return req


def _instance(instance_id: str, status: str = "running") -> dict:
    return {
        "instance_id": instance_id,
        "resource_id": instance_id,
        "instance_type": "k8s-pod",
        "image_id": "unknown",
        "launch_time": None,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_pods_present_returns_completed() -> None:
    """When every caller-supplied resource_id is found in the live list and
    fulfilment is 'fulfilled', the outcome is Completed with an empty missing list."""
    handler = MagicMock()
    handler.check_hosts_status.return_value = _make_check_result(
        instances=[_instance("pod-1"), _instance("pod-2")],
        state="fulfilled",
    )

    registry = _make_registry(handler=handler)
    request = _make_request(
        request_type=RequestType.ACQUIRE,
        pod_names=["pod-1", "pod-2"],
    )

    outcome = await registry.get_status(["pod-1", "pod-2"], request)

    assert isinstance(outcome, Completed)
    assert "pod-1" in outcome.resource_ids
    assert "pod-2" in outcome.resource_ids


@pytest.mark.asyncio
async def test_handler_exception_returns_failed() -> None:
    """When check_hosts_status raises, get_status returns a Failed outcome
    with recoverable=True (transient errors should be retried)."""
    handler = MagicMock()
    handler.check_hosts_status.side_effect = RuntimeError("k8s API unavailable")

    registry = _make_registry(handler=handler)
    request = _make_request(request_type=RequestType.ACQUIRE)

    outcome = await registry.get_status(["pod-1"], request)

    assert isinstance(outcome, Failed)
    assert outcome.recoverable is True


@pytest.mark.asyncio
async def test_acquire_request_in_progress_when_pods_pending() -> None:
    """For an ACQUIRE request with all pods in 'pending' state and
    fulfilment='in_progress', the outcome is Accepted with all pod IDs
    listed in pending_resource_ids."""
    handler = MagicMock()
    handler.check_hosts_status.return_value = _make_check_result(
        instances=[
            _instance("pod-1", "pending"),
            _instance("pod-2", "pending"),
        ],
        state="in_progress",
    )

    registry = _make_registry(handler=handler)
    request = _make_request(
        request_type=RequestType.ACQUIRE,
        pod_names=["pod-1", "pod-2"],
    )

    outcome = await registry.get_status(["pod-1", "pod-2"], request)

    assert isinstance(outcome, Accepted)
    assert "pod-1" in outcome.pending_resource_ids
    assert "pod-2" in outcome.pending_resource_ids


# ---------------------------------------------------------------------------
# F5 — providerConfig.namespace is respected in build_template_for_request
# ---------------------------------------------------------------------------


def test_build_template_namespace_from_provider_config_is_promoted() -> None:
    """providerConfig.namespace in a TemplateDTO must be promoted to K8sTemplate.namespace.

    Regression for the bug where build_template_for_request used
    dict.setdefault() to merge provider_config keys into the flat model_dump()
    dict. setdefault() only inserts when the key is absent, but model_dump()
    always includes all fields (including those set to None). When
    flat['namespace'] = None, setdefault('namespace', 'custom-ns') was silently
    ignored, leaving the namespace as None and falling through to the
    provider-level default.
    """

    import uuid

    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType
    from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate

    # Build a fake TemplateDTO-like object that carries namespace only in
    # provider_config (as the REST submission path would produce).
    class _FakeTemplateDTO:
        def model_dump(self) -> dict:
            return {
                "template_id": "tpl-test",
                "image_id": "busybox:latest",
                "max_instances": 1,
                "provider_type": "k8s",
                "provider_api": "Pod",
                # namespace is NOT set at the top level — it is None.
                "namespace": None,
                # The operator set it in provider_config.
                "provider_config": {"namespace": "custom-namespace-xyz"},
            }

    request = Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id="tpl-test",
        requested_count=1,
        metadata={"template": _FakeTemplateDTO()},
        provider_data={},
    )

    registry = _make_registry()
    template = registry.build_template_for_request(request)

    assert isinstance(template, K8sTemplate), f"Expected K8sTemplate, got {type(template)}"
    assert template.namespace == "custom-namespace-xyz", (
        f"Expected namespace='custom-namespace-xyz', got {template.namespace!r}. "
        "providerConfig.namespace was silently ignored."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_api", ["Deployment", "StatefulSet", "Job"])
async def test_controller_acquire_status_lists_only_its_pods(provider_api: str) -> None:
    """The controller named in resource_ids is not a machine, so a status read
    reports the pods it runs and nothing for the controller itself."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig

    handler = MagicMock()
    handler.check_hosts_status.return_value = _make_check_result(
        instances=[_instance("ctl-0"), _instance("ctl-1")],
        state="fulfilled",
    )
    registry = K8sHandlerRegistry(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        client_provider=MagicMock,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
        handler_overrides={provider_api: handler},
    )
    request = _make_request(request_type=RequestType.ACQUIRE)
    request.provider_api = provider_api
    request.provider_data = {}

    outcome = await registry.get_status(["ctl"], request)

    assert isinstance(outcome, Completed)
    assert [i["instance_id"] for i in outcome.metadata["instances"]] == ["ctl-0", "ctl-1"]
    assert sorted(outcome.resource_ids) == ["ctl-0", "ctl-1"]
