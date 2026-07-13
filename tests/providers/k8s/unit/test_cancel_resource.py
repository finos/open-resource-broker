"""Tests for K8sInstanceOperationService.cancel_resource.

Covers:
- Successful cancel: finds and deletes pods, deployments, statefulsets, jobs
- Partial failure: some deletes fail, result.status == "partial"
- Already-gone: 404 responses are handled gracefully as already_gone
- Not-found: no workloads for the request ID
- List-phase failure: one kind fails to list → recorded in failed, others proceed
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.services.instance_operation_service import (
    CancelResourceResult,
    K8sInstanceOperationService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUEST_ID = "test-request-id-abc"


def _make_config(namespace: str = "default") -> K8sProviderConfig:
    return K8sProviderConfig(namespace=namespace)  # type: ignore[call-arg]


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_k8s_client(
    pod_names: list[str] | None = None,
    deployment_names: list[str] | None = None,
    statefulset_names: list[str] | None = None,
    job_names: list[str] | None = None,
    delete_raises: dict[str, Exception] | None = None,
) -> Any:
    """Build a minimal K8sClient mock with pre-wired list and delete behaviours."""

    def _make_item_list(names: list[str] | None) -> Any:
        items = []
        for name in names or []:
            meta = SimpleNamespace(name=name)
            items.append(SimpleNamespace(metadata=meta))
        return SimpleNamespace(items=items)

    client = MagicMock()

    # core_v1
    client.core_v1.list_namespaced_pod.return_value = _make_item_list(pod_names)
    if delete_raises and "Pod" in delete_raises:
        client.core_v1.delete_namespaced_pod.side_effect = delete_raises["Pod"]
    else:
        client.core_v1.delete_namespaced_pod.return_value = SimpleNamespace()

    # apps_v1
    client.apps_v1.list_namespaced_deployment.return_value = _make_item_list(deployment_names)
    client.apps_v1.list_namespaced_stateful_set.return_value = _make_item_list(statefulset_names)
    if delete_raises and "Deployment" in delete_raises:
        client.apps_v1.delete_namespaced_deployment.side_effect = delete_raises["Deployment"]
    else:
        client.apps_v1.delete_namespaced_deployment.return_value = SimpleNamespace()
    if delete_raises and "StatefulSet" in delete_raises:
        client.apps_v1.delete_namespaced_stateful_set.side_effect = delete_raises["StatefulSet"]
    else:
        client.apps_v1.delete_namespaced_stateful_set.return_value = SimpleNamespace()

    # batch_v1
    client.batch_v1.list_namespaced_job.return_value = _make_item_list(job_names)
    if delete_raises and "Job" in delete_raises:
        client.batch_v1.delete_namespaced_job.side_effect = delete_raises["Job"]
    else:
        client.batch_v1.delete_namespaced_job.return_value = SimpleNamespace()

    return client


def _api_exception(status: int) -> Exception:
    """Build a kubernetes ApiException-like exception with .status attribute."""
    try:
        from kubernetes.client.exceptions import ApiException

        exc = ApiException(status=status)
        return exc
    except ImportError:
        # When kubernetes SDK is not installed, build a minimal stub.
        class _FakeApiException(Exception):
            def __init__(self, status: int) -> None:
                self.status = status

        return _FakeApiException(status)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCancelResourceResult:
    def test_status_not_found_when_all_empty(self) -> None:
        r = CancelResourceResult(request_id="x")
        assert r.status == "not_found"

    def test_status_success_when_deleted(self) -> None:
        r = CancelResourceResult(request_id="x", deleted=["Pod/my-pod"])
        assert r.status == "success"

    def test_status_success_when_already_gone(self) -> None:
        r = CancelResourceResult(request_id="x", already_gone=["Pod/my-pod"])
        assert r.status == "success"

    def test_status_partial_when_some_failed(self) -> None:
        r = CancelResourceResult(
            request_id="x",
            deleted=["Pod/my-pod"],
            failed=[("Pod/other-pod", "error")],
        )
        assert r.status == "partial"

    def test_to_dict_shape(self) -> None:
        r = CancelResourceResult(
            request_id="req1",
            deleted=["Pod/p1"],
            already_gone=["Deployment/d1"],
            failed=[("Job/j1", "timeout")],
        )
        d = r.to_dict()
        assert d["status"] == "partial"
        assert "Pod/p1" in d["deleted"]
        assert "Deployment/d1" in d["already_gone"]
        assert d["failed"][0]["resource"] == "Job/j1"
        assert d["failed"][0]["error"] == "timeout"


class TestCancelResourceSuccessful:
    def test_deletes_all_four_kinds(self) -> None:
        config = _make_config()
        logger = _make_logger()
        svc = K8sInstanceOperationService(config=config, logger=logger)

        client = _make_k8s_client(
            pod_names=["orb-abc123-0000"],
            deployment_names=["orb-abc123"],
            statefulset_names=["orb-abc123"],
            job_names=["orb-abc123"],
        )

        result = asyncio.run(
            svc.cancel_resource(
                request_id=REQUEST_ID,
                kubernetes_client=client,
            )
        )

        assert result.status == "success"
        assert len(result.deleted) == 4
        assert not result.failed
        assert not result.already_gone

    def test_deletes_only_pods(self) -> None:
        config = _make_config()
        svc = K8sInstanceOperationService(config=config, logger=_make_logger())
        client = _make_k8s_client(pod_names=["orb-abc123-0000", "orb-abc123-0001"])

        result = asyncio.run(svc.cancel_resource(REQUEST_ID, client))

        assert result.status == "success"
        assert "Pod/orb-abc123-0000" in result.deleted
        assert "Pod/orb-abc123-0001" in result.deleted

    def test_label_selector_built_correctly(self) -> None:
        config = _make_config()
        svc = K8sInstanceOperationService(config=config, logger=_make_logger())
        client = _make_k8s_client()

        asyncio.run(svc.cancel_resource("my-req-id", client))

        # core_v1 list call must use the correct label selector
        call_kwargs = client.core_v1.list_namespaced_pod.call_args
        selector = call_kwargs.kwargs.get("label_selector") or call_kwargs.args[1]
        assert "orb.io/request-id=my-req-id" in selector

    def test_namespace_override_used(self) -> None:
        config = _make_config(namespace="default")
        svc = K8sInstanceOperationService(config=config, logger=_make_logger())
        client = _make_k8s_client(pod_names=["orb-pod"])

        result = asyncio.run(svc.cancel_resource(REQUEST_ID, client, namespace="custom-ns"))

        call_kwargs = client.core_v1.list_namespaced_pod.call_args
        ns_arg = call_kwargs.kwargs.get("namespace") or call_kwargs.args[0]
        assert ns_arg == "custom-ns"
        assert result.status == "success"


class TestCancelResourceAlreadyGone:
    def test_404_treated_as_already_gone(self) -> None:
        config = _make_config()
        svc = K8sInstanceOperationService(config=config, logger=_make_logger())
        client = _make_k8s_client(
            pod_names=["orb-pod"],
            delete_raises={"Pod": _api_exception(404)},
        )

        result = asyncio.run(svc.cancel_resource(REQUEST_ID, client))

        assert "Pod/orb-pod" in result.already_gone
        assert not result.failed
        assert result.status == "success"


class TestCancelResourcePartialFailure:
    def test_non_404_delete_error_recorded_as_failed(self) -> None:
        config = _make_config()
        svc = K8sInstanceOperationService(config=config, logger=_make_logger())
        client = _make_k8s_client(
            pod_names=["orb-pod"],
            delete_raises={"Pod": _api_exception(500)},
        )

        result = asyncio.run(svc.cancel_resource(REQUEST_ID, client))

        assert result.status == "partial"
        assert any("Pod/orb-pod" in f[0] for f in result.failed)
        assert not result.deleted

    def test_mixed_success_and_failure(self) -> None:
        config = _make_config()
        svc = K8sInstanceOperationService(config=config, logger=_make_logger())
        client = _make_k8s_client(
            pod_names=["orb-pod1"],
            deployment_names=["orb-dep1"],
            delete_raises={"Deployment": _api_exception(503)},
        )

        result = asyncio.run(svc.cancel_resource(REQUEST_ID, client))

        assert result.status == "partial"
        assert "Pod/orb-pod1" in result.deleted
        assert any("Deployment/orb-dep1" in f[0] for f in result.failed)


class TestCancelResourceNotFound:
    def test_no_workloads_found_returns_not_found(self) -> None:
        config = _make_config()
        svc = K8sInstanceOperationService(config=config, logger=_make_logger())
        client = _make_k8s_client()  # all empty lists

        result = asyncio.run(svc.cancel_resource(REQUEST_ID, client))

        assert result.status == "not_found"
        assert not result.deleted
        assert not result.already_gone
        assert not result.failed


class TestCancelResourceListPhaseError:
    def test_list_failure_recorded_but_other_kinds_proceed(self) -> None:
        config = _make_config()
        svc = K8sInstanceOperationService(config=config, logger=_make_logger())
        client = _make_k8s_client(
            pod_names=["orb-pod1"],
            deployment_names=["orb-dep1"],
        )
        # Make the statefulset list fail
        client.apps_v1.list_namespaced_stateful_set.side_effect = RuntimeError("list failed")

        result = asyncio.run(svc.cancel_resource(REQUEST_ID, client))

        # Pod and Deployment should still be deleted
        assert "Pod/orb-pod1" in result.deleted
        assert "Deployment/orb-dep1" in result.deleted
        # StatefulSet list failure recorded
        assert any("list:StatefulSet" in f[0] for f in result.failed)
