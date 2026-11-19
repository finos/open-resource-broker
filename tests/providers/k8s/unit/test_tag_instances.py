"""TAG_INSTANCES for the Kubernetes provider: machine tags become pod labels.

The kubernetes SDK is mocked at ``K8sClient.core_v1``; the Pod handler, its
retry layer and the tagging service run for real.
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
from orb.providers.k8s.services.instance_tagging_service import K8sInstanceTaggingService
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy
from orb.providers.k8s.utilities.instance_tag_labels import tags_to_labels

POD_A = "orb-1a2b3c4d-0000"
POD_B = "orb-1a2b3c4d-0001"


def _config(**overrides: Any) -> K8sProviderConfig:
    values: dict[str, Any] = {"namespace": "orb-test", "in_cluster": True}
    values.update(overrides)
    return K8sProviderConfig(**values)  # type: ignore[arg-type]


def _handler(config: K8sProviderConfig, core_v1: MagicMock) -> K8sPodHandler:
    client = MagicMock()
    client.core_v1 = core_v1
    return K8sPodHandler(
        kubernetes_client=client,
        config=config,
        logger=MagicMock(),
        max_retries=0,
        base_delay=0.0,
    )


def _service(config: K8sProviderConfig, core_v1: MagicMock) -> K8sInstanceTaggingService:
    handler = _handler(config, core_v1)
    return K8sInstanceTaggingService(
        config=config, logger=MagicMock(), pod_handler_provider=lambda: handler
    )


def _op(instance_tags: dict[str, dict[str, str]]) -> ProviderOperation:
    return ProviderOperation(
        operation_type=ProviderOperationType.TAG_INSTANCES,
        parameters={"instance_tags": instance_tags},
    )


def _patches(core_v1: MagicMock) -> list[tuple[str, str, dict]]:
    return [
        (c.kwargs["namespace"], c.kwargs["name"], c.kwargs["body"])
        for c in core_v1.patch_namespaced_pod.call_args_list
    ]


def _missing_in(*namespaces: str):
    """``patch_namespaced_pod`` side effect: 404 in ``namespaces``, success elsewhere."""

    def _patch(*, name: str, namespace: str, body: dict) -> Optional[Any]:
        if namespace in namespaces:
            raise ApiException(status=404, reason="Not Found")
        return MagicMock()

    return _patch


# ---------------------------------------------------------------------------
# Capability and dispatch
# ---------------------------------------------------------------------------


def test_capabilities_declare_tag_instances() -> None:
    strategy = K8sProviderStrategy(config=_config(), logger=MagicMock())
    assert strategy.get_capabilities().supports_operation(ProviderOperationType.TAG_INSTANCES)


@pytest.mark.asyncio
async def test_execute_operation_labels_the_pod() -> None:
    config = _config()
    core_v1 = MagicMock()
    strategy = K8sProviderStrategy(
        config=config,
        logger=MagicMock(),
        handler_overrides={"Pod": _handler(config, core_v1)},
    )
    strategy._initialized = True

    result = await strategy.execute_operation(_op({POD_A: {"orb:node-name": "k8s-001"}}))

    assert result.success, result.error_message
    assert result.data["tagged_count"] == 1
    assert _patches(core_v1) == [
        ("orb-test", POD_A, {"metadata": {"labels": {"orb.io/node-name": "k8s-001"}}})
    ]


# ---------------------------------------------------------------------------
# Tag -> label mapping
# ---------------------------------------------------------------------------


class TestTagsToLabels:
    def test_orb_keys_use_the_label_prefix(self) -> None:
        labels, skipped = tags_to_labels({"orb:node-name": "k8s-001"}, "orb.io")
        assert labels == {"orb.io/node-name": "k8s-001"}
        assert skipped == {}

    def test_custom_label_prefix(self) -> None:
        labels, _ = tags_to_labels({"orb:node-name": "k8s-001"}, "hpc.example.com")
        assert labels == {"hpc.example.com/node-name": "k8s-001"}

    def test_valid_label_keys_pass_through(self) -> None:
        labels, skipped = tags_to_labels(
            {"team": "risk", "example.com/cost-centre": "42"}, "orb.io"
        )
        assert labels == {"team": "risk", "example.com/cost-centre": "42"}
        assert skipped == {}

    def test_invalid_keys_are_skipped_with_a_reason(self) -> None:
        labels, skipped = tags_to_labels(
            {"Cost Centre": "42", "aws:cloudformation:stack": "x", "orb:a:b": "y"}, "orb.io"
        )
        assert labels == {}
        assert set(skipped) == {"Cost Centre", "aws:cloudformation:stack", "orb:a:b"}

    def test_orb_identity_labels_are_not_overwritten(self) -> None:
        labels, skipped = tags_to_labels(
            {"orb:request-id": "req-x", "orb.io/managed": "false", "orb:node-name": "n1"},
            "orb.io",
        )
        assert labels == {"orb.io/node-name": "n1"}
        assert set(skipped) == {"orb:request-id", "orb.io/managed"}
        assert "reserved" in skipped["orb.io/managed"]

    @pytest.mark.parametrize(
        "value",
        ["x" * 64, "has space", "-leading", "trailing.", "k8s[001]"],
    )
    def test_invalid_values_are_skipped(self, value: str) -> None:
        labels, skipped = tags_to_labels({"orb:node-name": value}, "orb.io")
        assert labels == {}
        assert "orb:node-name" in skipped

    def test_empty_and_max_length_values_are_valid(self) -> None:
        labels, skipped = tags_to_labels({"orb:a": "", "orb:b": "a" * 63}, "orb.io")
        assert labels == {"orb.io/a": "", "orb.io/b": "a" * 63}
        assert skipped == {}


# ---------------------------------------------------------------------------
# Service behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_body_and_namespace_with_custom_prefix() -> None:
    config = _config(namespace="hpc", label_prefix="hpc.example.com")
    core_v1 = MagicMock()

    result = await _service(config, core_v1).tag_instances(
        _op({POD_A: {"orb:node-name": "k8s-001"}, POD_B: {"orb:node-name": "k8s-002"}})
    )

    assert result.success, result.error_message
    assert result.data["tagged_count"] == 2
    assert sorted(_patches(core_v1)) == [
        ("hpc", POD_A, {"metadata": {"labels": {"hpc.example.com/node-name": "k8s-001"}}}),
        ("hpc", POD_B, {"metadata": {"labels": {"hpc.example.com/node-name": "k8s-002"}}}),
    ]
    assert result.data["results"][POD_A]["status"] == "tagged"
    assert result.data["results"][POD_A]["namespace"] == "hpc"


@pytest.mark.asyncio
async def test_invalid_value_is_skipped_and_reported_without_failing() -> None:
    config = _config()
    core_v1 = MagicMock()

    result = await _service(config, core_v1).tag_instances(
        _op(
            {
                POD_A: {"orb:node-name": "k8s-001", "Bad Key": "v"},
                POD_B: {"orb:node-name": "n" * 64},
            }
        )
    )

    assert result.success, result.error_message
    assert result.data["tagged_count"] == 1
    assert _patches(core_v1) == [
        ("orb-test", POD_A, {"metadata": {"labels": {"orb.io/node-name": "k8s-001"}}})
    ]
    a, b = result.data["results"][POD_A], result.data["results"][POD_B]
    assert a["status"] == "tagged" and set(a["skipped_tags"]) == {"Bad Key"}
    assert b["status"] == "skipped" and set(b["skipped_tags"]) == {"orb:node-name"}


@pytest.mark.asyncio
async def test_missing_pod_is_reported_per_machine() -> None:
    config = _config()
    core_v1 = MagicMock()

    def _patch(*, name: str, namespace: str, body: dict) -> Any:
        if name == POD_B:
            raise ApiException(status=404, reason="Not Found")
        return MagicMock()

    core_v1.patch_namespaced_pod.side_effect = _patch

    result = await _service(config, core_v1).tag_instances(
        _op({POD_A: {"orb:node-name": "k8s-001"}, POD_B: {"orb:node-name": "k8s-002"}})
    )

    assert not result.success
    assert result.error_code == "TAG_OPERATION_FAILED"
    assert POD_B in (result.error_message or "")
    assert result.data["tagged_count"] == 1
    assert result.data["results"][POD_A]["status"] == "tagged"
    assert result.data["results"][POD_B]["status"] == "not_found"


@pytest.mark.asyncio
async def test_api_error_is_reported_per_machine() -> None:
    config = _config()
    core_v1 = MagicMock()
    core_v1.patch_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")

    result = await _service(config, core_v1).tag_instances(_op({POD_A: {"orb:node-name": "n1"}}))

    assert not result.success
    assert result.data["results"][POD_A]["status"] == "failed"
    assert "Forbidden" in result.data["results"][POD_A]["error"]


@pytest.mark.asyncio
async def test_namespaces_list_is_searched_in_order() -> None:
    config = _config(namespaces=["team-a", "team-b"])
    core_v1 = MagicMock()
    core_v1.patch_namespaced_pod.side_effect = _missing_in("team-a")

    result = await _service(config, core_v1).tag_instances(_op({POD_A: {"orb:node-name": "n1"}}))

    assert result.success, result.error_message
    assert [ns for ns, _, _ in _patches(core_v1)] == ["team-a", "team-b"]
    assert result.data["results"][POD_A]["namespace"] == "team-b"


@pytest.mark.asyncio
async def test_wildcard_namespaces_look_the_pod_up_cluster_wide() -> None:
    config = _config(namespaces=["*"])
    core_v1 = MagicMock()
    core_v1.patch_namespaced_pod.side_effect = _missing_in("orb-test")
    pod = MagicMock()
    pod.metadata.namespace = "elsewhere"
    core_v1.list_pod_for_all_namespaces.return_value = MagicMock(items=[pod])

    result = await _service(config, core_v1).tag_instances(_op({POD_A: {"orb:node-name": "n1"}}))

    assert result.success, result.error_message
    assert [ns for ns, _, _ in _patches(core_v1)] == ["orb-test", "elsewhere"]
    lookup = core_v1.list_pod_for_all_namespaces.call_args.kwargs
    assert lookup["field_selector"] == f"metadata.name={POD_A}"
    assert lookup["label_selector"] == "orb.io/managed=true"


@pytest.mark.asyncio
async def test_invalid_pod_name_is_not_sent_to_the_apiserver() -> None:
    config = _config()
    core_v1 = MagicMock()

    result = await _service(config, core_v1).tag_instances(
        _op({"Not/A,Pod": {"orb:node-name": "n1"}})
    )

    assert not result.success
    assert result.data["results"]["Not/A,Pod"]["status"] == "failed"
    core_v1.patch_namespaced_pod.assert_not_called()


@pytest.mark.asyncio
async def test_no_tags_is_a_successful_no_op() -> None:
    core_v1 = MagicMock()
    result = await _service(_config(), core_v1).tag_instances(_op({}))
    assert result.success
    assert result.data == {"tagged_count": 0, "results": {}}
    core_v1.patch_namespaced_pod.assert_not_called()


# ---------------------------------------------------------------------------
# Status sync -> TAG_INSTANCES -> pod labels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_sync_labels_pods_with_their_slurm_node_names(tmp_path) -> None:
    """Machines the sync names get ``orb.io/node-name`` on their pods.

    Drives ``MachineSyncService.sync_machines_with_provider`` (whose node-name
    reconciler pushes missing tags) against a real ``K8sProviderStrategy``
    whose kubernetes SDK is mocked, with JSON storage.
    """
    from unittest.mock import AsyncMock

    from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork
    from tests.unit.application.services.test_machine_sync_node_names import (
        NODE_NAMES,
        _names,
        _pod,
        _service,
        _stored_request,
        _sync,
    )

    config = _config()
    core_v1 = MagicMock()
    strategy = K8sProviderStrategy(
        config=config,
        logger=MagicMock(),
        handler_overrides={"Pod": _handler(config, core_v1)},
    )
    strategy._initialized = True
    registry = MagicMock()
    registry.get_strategy_capabilities.return_value = strategy.get_capabilities()

    async def _execute(_provider_name: str, operation: ProviderOperation) -> Any:
        return await strategy.execute_operation(operation)

    registry.execute_operation = AsyncMock(side_effect=_execute)

    def make_uow() -> JSONUnitOfWork:
        return JSONUnitOfWork(str(tmp_path), backup_enabled=False)

    request = _stored_request(make_uow, NODE_NAMES)
    svc = _service(make_uow, registry)

    await _sync(
        svc, make_uow, request.request_id, [_pod(request, i) for i in range(len(NODE_NAMES))]
    )

    labelled = {
        name: body["metadata"]["labels"]["orb.io/node-name"]
        for ns, name, body in _patches(core_v1)
        if ns == "orb-test"
    }
    assert labelled == _names(make_uow, request)
    assert sorted(labelled.values()) == NODE_NAMES
