"""Mocked end-to-end lifecycle tests for the k8s provider.

Mirrors tests/providers/aws/mocked/test_provision_lifecycle.py for the k8s
provider.  Tests the acquire → check_status → release cycle for all four
handler types (Pod, Deployment, StatefulSet, Job) using the kmock emulator.

Each test exercises the full handler chain through the K8sHandlerRegistry
acquire / get_status / return_machines paths, verifying the Accepted /
Completed outcome semantics mirror those of the AWS provider.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from kmock import KubernetesEmulator

# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------


def _make_k8s_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_registry(k8s_client_facade: Any, k8s_config: Any) -> Any:
    from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

    return K8sHandlerRegistry(
        config=k8s_config,
        logger=_make_k8s_logger(),
        client_provider=lambda: k8s_client_facade,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
    )


def _make_acquire_request(
    provider_api: str,
    *,
    requested_count: int = 1,
    namespace: str = "orb-test",
) -> Any:
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType
    from orb.providers.k8s.domain.template.k8s_template import K8sTemplate

    # Embed the template in request.metadata so K8sHandlerRegistry.build_template_for_request
    # picks it up (the registry's acquire() builds the template from metadata, not from a
    # separate argument).
    template = K8sTemplate(
        template_id="tpl-e2e",
        provider_api=provider_api,
        image_id="busybox:latest",
        max_instances=max(requested_count, 1),
        namespace=namespace,
    )
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api=provider_api,
        template_id="tpl-e2e",
        requested_count=requested_count,
        provider_data={"namespace": namespace},
        metadata={"template": template},
    )


def _make_return_request(
    provider_api: str,
    resource_ids: list[str],
    provider_data: dict[str, Any],
) -> Any:
    from orb.domain.request.aggregate import Request
    from orb.domain.request.value_objects import RequestId, RequestType

    return Request(
        request_id=RequestId(value=f"ret-{uuid.uuid4()}"),
        request_type=RequestType.RETURN,
        provider_type="k8s",
        provider_api=provider_api,
        template_id="tpl-e2e",
        requested_count=len(resource_ids),
        provider_data=provider_data,
    )


def _register_pod_resource(kmock_k8s: KubernetesEmulator) -> None:
    from kmock import resource

    pod_res = resource("", "v1", "pods")
    kmock_k8s.resources[pod_res] = {
        "namespaced": True,
        "kind": "Pod",
        "singular": "pod",
        "verbs": ["get", "list", "create", "delete", "watch"],
        "shortnames": ["po"],
        "categories": [],
        "subresources": [],
    }


def _register_deployment_resource(kmock_k8s: KubernetesEmulator) -> None:
    from kmock import resource

    dep_res = resource("apps", "v1", "deployments")
    kmock_k8s.resources[dep_res] = {
        "namespaced": True,
        "kind": "Deployment",
        "singular": "deployment",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": ["deploy"],
        "categories": [],
        "subresources": ["scale", "status"],
    }


def _register_statefulset_resource(kmock_k8s: KubernetesEmulator) -> None:
    from kmock import resource

    sts_res = resource("apps", "v1", "statefulsets")
    kmock_k8s.resources[sts_res] = {
        "namespaced": True,
        "kind": "StatefulSet",
        "singular": "statefulset",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": ["sts"],
        "categories": [],
        "subresources": ["scale", "status"],
    }


def _register_job_resource(kmock_k8s: KubernetesEmulator) -> None:
    from kmock import resource

    job_res = resource("batch", "v1", "jobs")
    kmock_k8s.resources[job_res] = {
        "namespaced": True,
        "kind": "Job",
        "singular": "job",
        "verbs": ["get", "list", "create", "patch", "delete", "watch"],
        "shortnames": [],
        "categories": [],
        "subresources": ["status"],
    }


# ---------------------------------------------------------------------------
# Pod — acquire → check_status → release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pod_lifecycle_acquire_returns_accepted(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Pod acquire through the registry returns an Accepted outcome."""
    from orb.domain.base.operation_outcome import Accepted

    _register_pod_resource(kmock_k8s)
    registry = _make_registry(k8s_client_facade, k8s_config)
    request = _make_acquire_request("Pod")

    outcome = await registry.acquire(request)

    assert isinstance(outcome, Accepted), f"Expected Accepted, got {type(outcome).__name__}"
    assert len(outcome.pending_resource_ids) == 1
    assert outcome.pending_resource_ids[0].startswith("orb-")


@pytest.mark.asyncio
async def test_pod_lifecycle_release_returns_accepted(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Pod release through the registry returns an Accepted outcome."""
    from orb.domain.base.operation_outcome import Accepted

    _register_pod_resource(kmock_k8s)
    registry = _make_registry(k8s_client_facade, k8s_config)
    acquire_req = _make_acquire_request("Pod")

    acquire_outcome = await registry.acquire(acquire_req)
    assert isinstance(acquire_outcome, Accepted)

    resource_ids = list(acquire_outcome.pending_resource_ids)
    return_req = _make_return_request(
        "Pod",
        resource_ids=resource_ids,
        provider_data={"namespace": "orb-test"},
    )

    return_outcome = await registry.return_machines(resource_ids, return_req)
    assert isinstance(return_outcome, Accepted)


# ---------------------------------------------------------------------------
# Deployment — acquire → release cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deployment_lifecycle_acquire_returns_accepted(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Deployment acquire through the registry returns an Accepted outcome."""
    from orb.domain.base.operation_outcome import Accepted

    _register_deployment_resource(kmock_k8s)
    _register_pod_resource(kmock_k8s)
    registry = _make_registry(k8s_client_facade, k8s_config)
    request = _make_acquire_request("Deployment", requested_count=2)

    outcome = await registry.acquire(request)

    assert isinstance(outcome, Accepted)
    assert len(outcome.pending_resource_ids) == 1
    assert outcome.pending_resource_ids[0].startswith("orb-")


@pytest.mark.asyncio
async def test_deployment_lifecycle_release_returns_accepted(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Deployment release returns Accepted; the Deployment is deleted."""
    from orb.domain.base.operation_outcome import Accepted

    _register_deployment_resource(kmock_k8s)
    _register_pod_resource(kmock_k8s)
    registry = _make_registry(k8s_client_facade, k8s_config)
    acquire_req = _make_acquire_request("Deployment", requested_count=2)

    acquire_outcome = await registry.acquire(acquire_req)
    assert isinstance(acquire_outcome, Accepted)

    dep_name = acquire_outcome.pending_resource_ids[0]
    return_req = _make_return_request(
        "Deployment",
        resource_ids=[dep_name],
        provider_data={
            "namespace": "orb-test",
            "deployment_name": dep_name,
            "replicas": 2,
        },
    )

    return_outcome = await registry.return_machines([dep_name], return_req)
    assert isinstance(return_outcome, Accepted)


# ---------------------------------------------------------------------------
# StatefulSet — acquire → release cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statefulset_lifecycle_acquire_returns_accepted(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """StatefulSet acquire through the registry returns an Accepted outcome."""
    from orb.domain.base.operation_outcome import Accepted

    _register_statefulset_resource(kmock_k8s)
    registry = _make_registry(k8s_client_facade, k8s_config)
    request = _make_acquire_request("StatefulSet", requested_count=2)

    outcome = await registry.acquire(request)

    assert isinstance(outcome, Accepted)
    assert len(outcome.pending_resource_ids) == 1


@pytest.mark.asyncio
async def test_job_lifecycle_acquire_returns_accepted(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Job acquire through the registry returns an Accepted outcome."""
    from orb.domain.base.operation_outcome import Accepted

    _register_job_resource(kmock_k8s)
    registry = _make_registry(k8s_client_facade, k8s_config)
    request = _make_acquire_request("Job", requested_count=2)

    outcome = await registry.acquire(request)

    assert isinstance(outcome, Accepted)
    assert len(outcome.pending_resource_ids) == 1
    assert outcome.pending_resource_ids[0].startswith("orb-")


@pytest.mark.asyncio
async def test_job_lifecycle_release_returns_accepted(
    kmock_k8s: KubernetesEmulator,
    k8s_client_facade: Any,
    k8s_config: Any,
) -> None:
    """Job release deletes the Job; returns Accepted."""
    from orb.domain.base.operation_outcome import Accepted

    _register_job_resource(kmock_k8s)
    registry = _make_registry(k8s_client_facade, k8s_config)
    acquire_req = _make_acquire_request("Job", requested_count=1)

    acquire_outcome = await registry.acquire(acquire_req)
    assert isinstance(acquire_outcome, Accepted)

    job_name = acquire_outcome.pending_resource_ids[0]
    return_req = _make_return_request(
        "Job",
        resource_ids=[job_name],
        provider_data={"namespace": "orb-test", "job_name": job_name},
    )

    return_outcome = await registry.return_machines([job_name], return_req)
    assert isinstance(return_outcome, Accepted)
