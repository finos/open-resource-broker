"""Unit tests for :class:`K8sStatefulSetHandler`.

Mocks ``CoreV1Api`` and ``AppsV1Api`` so no cluster is required.
Covers:

* ``acquire_hosts`` creates a single StatefulSet with ``spec.replicas=N``
  and persists the StatefulSet name in ``provider_data``.
* ``release_hosts`` selective path: when the victims are the top-of-stack
  ordinals, no warning is emitted and ``spec.replicas`` is patched down
  by the victim count.  Pods are never deleted directly.
* ``release_hosts`` ordinal caveat: when victims are NOT the top-of-stack
  ordinals, a ``K8sError`` is raised and no scale patch is issued.
  Silently evicting different pods would cause data loss.
* ``release_hosts`` full-release path: ``spec.replicas`` is patched to
  zero and the StatefulSet is deleted.
* ``check_hosts_status`` reads both the pod list and the StatefulSet
  status, rolling up via the controller's ``ready_replicas`` view.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    requested_count: int = 3,
    request_id: str | None = None,
    statefulset_name: str | None = None,
    namespace: str = "orb-test",
) -> Request:
    provider_data: dict[str, Any] = {"namespace": namespace}
    if statefulset_name:
        provider_data["statefulset_name"] = statefulset_name
    return Request(
        request_id=RequestId(value=request_id or f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="StatefulSet",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data=provider_data,
    )


def _make_template() -> Template:
    return Template(
        template_id="tpl-1",
        provider_type="k8s",
        provider_api="StatefulSet",
        image_id="busybox:latest",
        max_instances=5,
        provider_data={
            "k8s": {
                "namespace": "orb-test",
                "container_image": "busybox:latest",
                "resource_requests": {"cpu": "100m", "memory": "64Mi"},
            }
        },
    )


def _make_client(
    core_v1: Any | None = None,
    apps_v1: Any | None = None,
) -> MagicMock:
    client = MagicMock()
    client.core_v1 = core_v1 if core_v1 is not None else MagicMock()
    client.apps_v1 = apps_v1 if apps_v1 is not None else MagicMock()
    return client


def _make_handler(client: Any | None = None) -> K8sStatefulSetHandler:
    if client is None:
        client = _make_client()
    config = K8sProviderConfig(namespace="orb-test")
    return K8sStatefulSetHandler(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
    )


def _make_pod(*, name: str, phase: str, ready: bool = False) -> SimpleNamespace:
    conditions: list[SimpleNamespace] = []
    if ready:
        conditions.append(SimpleNamespace(type="Ready", status="True", reason=None))
    else:
        conditions.append(SimpleNamespace(type="Ready", status="False", reason=None))
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace="orb-test",
            labels={"orb.io/request-id": "req-abc"},
        ),
        spec=SimpleNamespace(node_name="node-1"),
        status=SimpleNamespace(
            phase=phase,
            pod_ip="10.0.0.1" if phase == "Running" else None,
            host_ip="10.1.0.1" if phase == "Running" else None,
            start_time=None,
            conditions=conditions,
            container_statuses=[],
        ),
    )


def _make_statefulset_status(
    *,
    spec_replicas: int,
    ready_replicas: int | None = None,
    current_replicas: int | None = None,
    updated_replicas: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(name="orb-deadbeef", namespace="orb-test"),
        spec=SimpleNamespace(replicas=spec_replicas, service_name="orb-deadbeef"),
        status=SimpleNamespace(
            ready_replicas=ready_replicas,
            current_replicas=current_replicas,
            updated_replicas=updated_replicas,
            conditions=[],
        ),
    )


# ---------------------------------------------------------------------------
# acquire_hosts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_hosts_creates_single_statefulset_with_replicas() -> None:
    apps_v1 = MagicMock()
    apps_v1.create_namespaced_stateful_set.return_value = SimpleNamespace()
    client = _make_client(apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(requested_count=4)
    template = _make_template()

    result = await handler.acquire_hosts(request, template)

    # Exactly one StatefulSet created.
    assert apps_v1.create_namespaced_stateful_set.call_count == 1
    call_kwargs = apps_v1.create_namespaced_stateful_set.call_args.kwargs
    assert call_kwargs["namespace"] == "orb-test"
    body = call_kwargs["body"]
    assert body.spec.replicas == 4
    # The Kubernetes API server requires a non-empty serviceName.  We
    # fall back to the StatefulSet name when no explicit override is
    # supplied so the API accepts the create call.
    assert body.spec.service_name == body.metadata.name
    # resource_ids carries the StatefulSet name; machine_ids stays
    # empty because the controller stamps ordinals asynchronously.
    assert len(result["resource_ids"]) == 1
    assert result["resource_ids"][0].startswith("orb-")
    assert result["machine_ids"] == []
    assert result["provider_data"]["replicas"] == 4
    assert result["provider_data"]["namespace"] == "orb-test"
    assert result["provider_data"]["statefulset_name"] == result["resource_ids"][0]


@pytest.mark.asyncio
async def test_acquire_hosts_replicas_floors_at_one() -> None:
    apps_v1 = MagicMock()
    apps_v1.create_namespaced_stateful_set.return_value = SimpleNamespace()
    client = _make_client(apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(requested_count=0)
    template = _make_template()

    await handler.acquire_hosts(request, template)
    body = apps_v1.create_namespaced_stateful_set.call_args.kwargs["body"]
    # Even when requested_count is 0 or negative, we never submit a
    # zero-replica StatefulSet on acquire — that path is reserved for
    # the full-release sequence.
    assert body.spec.replicas == 1


# ---------------------------------------------------------------------------
# release_hosts — selective via ordinal-aware scale-down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_hosts_selective_highest_ordinal_no_warning() -> None:
    """When the victims are exactly the top-of-stack ordinals no warning
    is emitted and the replicas patch goes through cleanly."""
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_status(spec_replicas=5)
    apps_v1.patch_namespaced_stateful_set_scale.return_value = SimpleNamespace()

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(
        requested_count=5,
        statefulset_name="orb-deadbeef",
        namespace="orb-test",
    )

    # current=5, victims=[orb-deadbeef-4, orb-deadbeef-3] => the
    # top-of-stack ordinals; the controller would have picked these
    # anyway.
    await handler.release_hosts(["orb-deadbeef-4", "orb-deadbeef-3"], request.provider_data)

    # No WARNING logged (the requested victims are the top of stack).
    warning_calls = [
        call
        for call in handler._logger.warning.call_args_list  # type: ignore[attr-defined]
        if "non-highest-ordinal" in (call.args[0] if call.args else "")
    ]
    assert warning_calls == []

    # replicas patched from 5 -> 3 (5 minus 2 victims).
    apps_v1.patch_namespaced_stateful_set_scale.assert_called_once()
    patch_kwargs = apps_v1.patch_namespaced_stateful_set_scale.call_args.kwargs
    assert patch_kwargs["name"] == "orb-deadbeef"
    assert patch_kwargs["namespace"] == "orb-test"
    assert patch_kwargs["body"]["spec"]["replicas"] == 3

    # Pods are NEVER deleted directly — the controller picks the
    # eviction order via the ordinal contract.
    core_v1.delete_namespaced_pod.assert_not_called()
    # And we never delete the StatefulSet itself on a selective release.
    apps_v1.delete_namespaced_stateful_set.assert_not_called()


@pytest.mark.asyncio
async def test_release_hosts_selective_non_highest_ordinal_raises_and_does_not_scale() -> None:
    """When the victims include non-highest ordinals, the handler refuses
    the request with ``K8sError`` and does NOT patch the StatefulSet.

    The controller only supports scale-down from the top of the ordinal
    stack; the previous warn-and-scale behaviour silently mutated a
    different pod than the caller asked for, so the current
    implementation raises instead — see the sibling test module
    ``test_statefulset_release_reject.py`` for the guard function's own
    unit coverage.
    """
    from orb.providers.k8s.exceptions.k8s_exceptions import K8sError

    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_status(spec_replicas=5)
    apps_v1.patch_namespaced_stateful_set_scale.return_value = SimpleNamespace()

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(
        requested_count=5,
        statefulset_name="orb-deadbeef",
        namespace="orb-test",
    )

    # current=5, victims=[ordinal-1, ordinal-2] — non-highest ordinals.
    with pytest.raises(K8sError, match="selective release refused"):
        await handler.release_hosts(["orb-deadbeef-1", "orb-deadbeef-2"], request.provider_data)

    # Nothing was mutated — neither a scale patch nor a pod deletion.
    apps_v1.patch_namespaced_stateful_set_scale.assert_not_called()
    core_v1.delete_namespaced_pod.assert_not_called()
    apps_v1.delete_namespaced_stateful_set.assert_not_called()


@pytest.mark.asyncio
async def test_release_hosts_selective_unparseable_victim_names_raises() -> None:
    """Victim names that do not parse as ``<statefulset>-<ordinal>`` are
    treated as non-top-of-stack and therefore refused, since the
    controller cannot guarantee they will be the ones evicted."""
    from orb.providers.k8s.exceptions.k8s_exceptions import K8sError

    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_status(spec_replicas=3)
    apps_v1.patch_namespaced_stateful_set_scale.return_value = SimpleNamespace()

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(
        requested_count=3,
        statefulset_name="orb-deadbeef",
        namespace="orb-test",
    )

    with pytest.raises(K8sError, match="selective release refused"):
        await handler.release_hosts(["some-other-pod"], request.provider_data)

    apps_v1.patch_namespaced_stateful_set_scale.assert_not_called()
    core_v1.delete_namespaced_pod.assert_not_called()
    apps_v1.delete_namespaced_stateful_set.assert_not_called()


# ---------------------------------------------------------------------------
# release_hosts — full release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_hosts_full_release_scales_to_zero_then_deletes() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_status(spec_replicas=3)
    apps_v1.patch_namespaced_stateful_set_scale.return_value = SimpleNamespace()
    apps_v1.delete_namespaced_stateful_set.return_value = SimpleNamespace()

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request(
        requested_count=3,
        statefulset_name="orb-deadbeef",
        namespace="orb-test",
    )

    await handler.release_hosts(
        ["orb-deadbeef-0", "orb-deadbeef-1", "orb-deadbeef-2"], request.provider_data
    )

    # Full release path: no ordinal warning, just scale-to-zero +
    # delete.
    core_v1.delete_namespaced_pod.assert_not_called()
    apps_v1.patch_namespaced_stateful_set_scale.assert_called_once()
    assert (
        apps_v1.patch_namespaced_stateful_set_scale.call_args.kwargs["body"]["spec"]["replicas"]
        == 0
    )
    apps_v1.delete_namespaced_stateful_set.assert_called_once()


@pytest.mark.asyncio
async def test_release_hosts_empty_machine_ids_is_noop() -> None:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)

    request = _make_request()
    await handler.release_hosts([], request.provider_data)

    apps_v1.read_namespaced_stateful_set.assert_not_called()
    apps_v1.patch_namespaced_stateful_set_scale.assert_not_called()
    apps_v1.delete_namespaced_stateful_set.assert_not_called()


@pytest.mark.asyncio
async def test_release_hosts_statefulset_already_gone_is_best_effort() -> None:
    from kubernetes.client.exceptions import ApiException

    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.side_effect = ApiException(status=404, reason="Not Found")

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1

    request = _make_request(statefulset_name="orb-deadbeef")
    # Must not raise — StatefulSet evaporated, treat as success.
    await handler.release_hosts(["orb-deadbeef-0"], request.provider_data)
    apps_v1.patch_namespaced_stateful_set_scale.assert_not_called()


# ---------------------------------------------------------------------------
# check_hosts_status
# ---------------------------------------------------------------------------


def test_check_hosts_status_fulfilled_uses_controller_view() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-deadbeef-0", phase="Running", ready=True),
            _make_pod(name="orb-deadbeef-1", phase="Running", ready=True),
        ]
    )
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_status(
        spec_replicas=2, ready_replicas=2, current_replicas=2, updated_replicas=2
    )

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, statefulset_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert isinstance(result, CheckHostsStatusResult)
    assert isinstance(result.fulfilment, ProviderFulfilment)
    assert result.fulfilment.state == "fulfilled"
    assert result.fulfilment.target_units == 2
    assert result.fulfilment.running_count == 2


def test_check_hosts_status_in_progress_when_replicas_not_ready() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-deadbeef-0", phase="Running", ready=True),
            _make_pod(name="orb-deadbeef-1", phase="Pending"),
        ]
    )
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_status(
        spec_replicas=2, ready_replicas=1, current_replicas=2
    )

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, statefulset_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"
    assert result.fulfilment.running_count == 1
    assert result.fulfilment.pending_count == 1


def test_check_hosts_status_partial_after_scale_down() -> None:
    """After a successful scale-down the controller view should drive
    a ``partial`` state rather than ``in_progress``."""
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _make_pod(name="orb-deadbeef-0", phase="Running", ready=True),
        ]
    )
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = _make_statefulset_status(
        spec_replicas=1, ready_replicas=1, current_replicas=1
    )

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    request = _make_request(requested_count=2, statefulset_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "partial"
    assert result.fulfilment.running_count == 1
    assert result.fulfilment.target_units == 2


def test_check_hosts_status_statefulset_missing_falls_back_to_pod_rollup() -> None:
    """If the StatefulSet is gone but pods are still terminating, the
    handler should still produce a sensible roll-up from the pod
    list."""
    from kubernetes.client.exceptions import ApiException

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.side_effect = ApiException(status=404, reason="Not Found")

    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1
    request = _make_request(requested_count=2, statefulset_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    # No pods + no StatefulSet + non-zero target => still in_progress
    # so callers retry rather than failing.
    assert result.fulfilment.state == "in_progress"
    assert result.instances == []


def test_check_hosts_status_handles_list_failure() -> None:
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.side_effect = RuntimeError("apiserver down")
    apps_v1 = MagicMock()
    client = _make_client(core_v1=core_v1, apps_v1=apps_v1)
    handler = _make_handler(client=client)
    handler._max_retries = 1
    request = _make_request(requested_count=2, statefulset_name="orb-deadbeef")

    result = handler.check_hosts_status(request)
    assert result.fulfilment.state == "in_progress"
    assert "apiserver down" in result.fulfilment.message


# ---------------------------------------------------------------------------
# Ordinal parser
# ---------------------------------------------------------------------------


def test_parse_statefulset_pod_ordinal_extracts_integer_suffix() -> None:
    from orb.providers.k8s.utilities.statefulset_spec import (
        parse_statefulset_pod_ordinal,
    )

    assert parse_statefulset_pod_ordinal("orb-deadbeef-0", "orb-deadbeef") == 0
    assert parse_statefulset_pod_ordinal("orb-deadbeef-12", "orb-deadbeef") == 12


def test_parse_statefulset_pod_ordinal_returns_none_for_mismatch() -> None:
    from orb.providers.k8s.utilities.statefulset_spec import (
        parse_statefulset_pod_ordinal,
    )

    # Wrong prefix.
    assert parse_statefulset_pod_ordinal("other-pod-0", "orb-deadbeef") is None
    # Non-numeric suffix.
    assert parse_statefulset_pod_ordinal("orb-deadbeef-foo", "orb-deadbeef") is None
    # Empty inputs.
    assert parse_statefulset_pod_ordinal("", "orb-deadbeef") is None
    assert parse_statefulset_pod_ordinal("orb-deadbeef-0", "") is None


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


def test_get_example_templates_returns_statefulset_example() -> None:
    examples = K8sStatefulSetHandler.get_example_templates()
    assert len(examples) >= 1
    example = examples[0]
    assert example.provider_api == "StatefulSet"
    assert example.provider_type == "k8s"
    assert example.machine_image == "registry.k8s.io/pause:3.9"


# ---------------------------------------------------------------------------
# Provider-API key
# ---------------------------------------------------------------------------


def test_provider_api_key_matches_value_object() -> None:
    """The handler's PROVIDER_API key must match the enum value used
    by the strategy dispatch."""
    from orb.providers.k8s.value_objects import KubernetesProviderApi

    assert K8sStatefulSetHandler.PROVIDER_API == KubernetesProviderApi.STATEFUL_SET.value
