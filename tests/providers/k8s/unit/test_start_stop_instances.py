"""Unit tests for K8sStartStopService — START/STOP via Deployment/StatefulSet scale.

These tests mock the Kubernetes SDK's ``patch_namespaced_*_scale`` calls
and exercise the full service logic without a real cluster.

Coverage:
- stop_instances scales Deployment to 0 replicas
- stop_instances scales StatefulSet to 0 replicas
- start_instances restores Deployment replicas from replicas_before_stop
- start_instances falls back to provider_data["replicas"] when no archived count
- Pod provider_api → UNSUPPORTED_OPERATION_FOR_KIND (stop)
- Job provider_api → UNSUPPORTED_OPERATION_FOR_KIND (start)
- Missing workload coordinates → MISSING_WORKLOAD_COORDINATES
- SDK exception → error result, not crash
- get_capabilities includes START_INSTANCES and STOP_INSTANCES
- execute_operation dispatches START/STOP to the service
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType, ProviderResult
from orb.providers.k8s.services.start_stop_service import K8sStartStopService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    logger = MagicMock()
    for m in ("debug", "info", "warning", "error", "critical"):
        setattr(logger, m, MagicMock())
    return logger


def _make_service(*, patch_scale_raises: bool = False) -> tuple[K8sStartStopService, MagicMock]:
    """Return (service, mock_apps_v1) pair."""
    mock_apps_v1 = MagicMock()
    if patch_scale_raises:
        mock_apps_v1.patch_namespaced_deployment_scale.side_effect = Exception("API error")
        mock_apps_v1.patch_namespaced_stateful_set_scale.side_effect = Exception("API error")

    mock_k8s_client = MagicMock()
    mock_k8s_client.apps_v1 = mock_apps_v1

    service = K8sStartStopService(
        kubernetes_client=mock_k8s_client,
        logger=_make_logger(),
    )
    return service, mock_apps_v1


def _stop_op(
    *,
    provider_api: str = "Deployment",
    namespace: str = "orb-system",
    deployment_name: str = "orb-abc12345",
    replicas: int = 3,
) -> ProviderOperation:
    """Build a STOP_INSTANCES ProviderOperation for a Deployment."""
    key = "deployment_name" if provider_api == "Deployment" else "statefulset_name"
    return ProviderOperation(
        operation_type=ProviderOperationType.STOP_INSTANCES,
        parameters={
            "provider_api": provider_api,
            "provider_data": {
                "namespace": namespace,
                key: deployment_name,
                "replicas": replicas,
            },
        },
    )


def _start_op(
    *,
    provider_api: str = "Deployment",
    namespace: str = "orb-system",
    workload_name: str = "orb-abc12345",
    replicas: int = 3,
    replicas_before_stop: int | None = None,
) -> ProviderOperation:
    """Build a START_INSTANCES ProviderOperation."""
    key = "deployment_name" if provider_api == "Deployment" else "statefulset_name"
    provider_data: dict = {
        "namespace": namespace,
        key: workload_name,
        "replicas": replicas,
    }
    if replicas_before_stop is not None:
        provider_data["replicas_before_stop"] = replicas_before_stop

    return ProviderOperation(
        operation_type=ProviderOperationType.START_INSTANCES,
        parameters={
            "provider_api": provider_api,
            "provider_data": provider_data,
        },
    )


# ---------------------------------------------------------------------------
# STOP — Deployment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_scales_deployment_to_zero() -> None:
    """STOP archives the current replica count and patches replicas to 0."""
    service, mock_apps_v1 = _make_service()

    result = await service.stop_instances(
        _stop_op(provider_api="Deployment", namespace="orb", deployment_name="orb-abc1", replicas=4)
    )

    assert result.success is True
    assert result.data["replicas_before_stop"] == 4
    mock_apps_v1.patch_namespaced_deployment_scale.assert_called_once()
    call_kwargs = mock_apps_v1.patch_namespaced_deployment_scale.call_args.kwargs
    assert call_kwargs["name"] == "orb-abc1"
    assert call_kwargs["namespace"] == "orb"
    # Verify the scale body requests 0 replicas.
    assert call_kwargs["body"].spec.replicas == 0


@pytest.mark.asyncio
async def test_stop_scales_statefulset_to_zero() -> None:
    """STOP works for StatefulSet."""
    service, mock_apps_v1 = _make_service()

    result = await service.stop_instances(
        _stop_op(provider_api="StatefulSet", namespace="ns", deployment_name="orb-sts1", replicas=2)
    )

    assert result.success is True
    mock_apps_v1.patch_namespaced_stateful_set_scale.assert_called_once()
    call_kwargs = mock_apps_v1.patch_namespaced_stateful_set_scale.call_args.kwargs
    assert call_kwargs["name"] == "orb-sts1"
    assert call_kwargs["body"].spec.replicas == 0


# ---------------------------------------------------------------------------
# START — Deployment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_restores_replicas_from_replicas_before_stop() -> None:
    """START uses replicas_before_stop when present."""
    service, mock_apps_v1 = _make_service()

    result = await service.start_instances(
        _start_op(
            provider_api="Deployment",
            namespace="orb",
            workload_name="orb-abc1",
            replicas=4,
            replicas_before_stop=4,
        )
    )

    assert result.success is True
    mock_apps_v1.patch_namespaced_deployment_scale.assert_called_once()
    call_kwargs = mock_apps_v1.patch_namespaced_deployment_scale.call_args.kwargs
    assert call_kwargs["body"].spec.replicas == 4


@pytest.mark.asyncio
async def test_start_falls_back_to_replicas_when_no_archived_count() -> None:
    """START falls back to provider_data['replicas'] when replicas_before_stop absent."""
    service, mock_apps_v1 = _make_service()

    result = await service.start_instances(
        _start_op(
            provider_api="Deployment",
            namespace="orb",
            workload_name="orb-abc1",
            replicas=5,
            replicas_before_stop=None,  # not present
        )
    )

    assert result.success is True
    call_kwargs = mock_apps_v1.patch_namespaced_deployment_scale.call_args.kwargs
    assert call_kwargs["body"].spec.replicas == 5


@pytest.mark.asyncio
async def test_start_restores_statefulset() -> None:
    """START works for StatefulSet."""
    service, mock_apps_v1 = _make_service()

    result = await service.start_instances(
        _start_op(
            provider_api="StatefulSet",
            namespace="ns",
            workload_name="orb-sts1",
            replicas=3,
            replicas_before_stop=3,
        )
    )

    assert result.success is True
    mock_apps_v1.patch_namespaced_stateful_set_scale.assert_called_once()
    assert (
        mock_apps_v1.patch_namespaced_stateful_set_scale.call_args.kwargs["body"].spec.replicas == 3
    )


# ---------------------------------------------------------------------------
# UNSUPPORTED_OPERATION_FOR_KIND
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_pod_returns_unsupported_for_kind() -> None:
    """STOP returns UNSUPPORTED_OPERATION_FOR_KIND for Pod."""
    service, mock_apps_v1 = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.STOP_INSTANCES,
        parameters={"provider_api": "Pod", "provider_data": {}},
    )
    result = await service.stop_instances(op)

    assert result.success is False
    assert result.error_code == "UNSUPPORTED_OPERATION_FOR_KIND"
    mock_apps_v1.patch_namespaced_deployment_scale.assert_not_called()


@pytest.mark.asyncio
async def test_stop_job_returns_unsupported_for_kind() -> None:
    """STOP returns UNSUPPORTED_OPERATION_FOR_KIND for Job."""
    service, _ = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.STOP_INSTANCES,
        parameters={"provider_api": "Job", "provider_data": {}},
    )
    result = await service.stop_instances(op)

    assert result.success is False
    assert result.error_code == "UNSUPPORTED_OPERATION_FOR_KIND"


@pytest.mark.asyncio
async def test_start_pod_returns_unsupported_for_kind() -> None:
    """START returns UNSUPPORTED_OPERATION_FOR_KIND for Pod."""
    service, _ = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.START_INSTANCES,
        parameters={"provider_api": "Pod", "provider_data": {}},
    )
    result = await service.start_instances(op)

    assert result.success is False
    assert result.error_code == "UNSUPPORTED_OPERATION_FOR_KIND"


@pytest.mark.asyncio
async def test_start_job_returns_unsupported_for_kind() -> None:
    """START returns UNSUPPORTED_OPERATION_FOR_KIND for Job."""
    service, _ = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.START_INSTANCES,
        parameters={"provider_api": "Job", "provider_data": {}},
    )
    result = await service.start_instances(op)

    assert result.success is False
    assert result.error_code == "UNSUPPORTED_OPERATION_FOR_KIND"


# ---------------------------------------------------------------------------
# Missing workload coordinates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_missing_workload_name_returns_error() -> None:
    """STOP without a workload name returns MISSING_WORKLOAD_COORDINATES."""
    service, _ = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.STOP_INSTANCES,
        parameters={
            "provider_api": "Deployment",
            "provider_data": {"namespace": "ns"},  # no deployment_name
        },
    )
    result = await service.stop_instances(op)

    assert result.success is False
    assert result.error_code == "MISSING_WORKLOAD_COORDINATES"


@pytest.mark.asyncio
async def test_start_missing_workload_name_returns_error() -> None:
    """START without a workload name returns MISSING_WORKLOAD_COORDINATES."""
    service, _ = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.START_INSTANCES,
        parameters={
            "provider_api": "Deployment",
            "provider_data": {"namespace": "ns"},  # no deployment_name
        },
    )
    result = await service.start_instances(op)

    assert result.success is False
    assert result.error_code == "MISSING_WORKLOAD_COORDINATES"


@pytest.mark.asyncio
async def test_stop_uses_resource_ids_fallback_for_workload_name() -> None:
    """STOP uses resource_ids[0] when provider_data has no deployment_name."""
    service, mock_apps_v1 = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.STOP_INSTANCES,
        parameters={
            "provider_api": "Deployment",
            "provider_data": {"namespace": "ns", "replicas": 2},
            "resource_ids": ["orb-from-resource-ids"],
        },
    )
    result = await service.stop_instances(op)

    assert result.success is True
    call_kwargs = mock_apps_v1.patch_namespaced_deployment_scale.call_args.kwargs
    assert call_kwargs["name"] == "orb-from-resource-ids"


# ---------------------------------------------------------------------------
# SDK exception handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_sdk_exception_returns_error_result() -> None:
    """SDK exception during scale patch returns an error result, not a crash."""
    service, mock_apps_v1 = _make_service(patch_scale_raises=True)

    result = await service.stop_instances(
        _stop_op(provider_api="Deployment", deployment_name="orb-abc1")
    )

    assert result.success is False
    assert result.error_code == "STOP_INSTANCES_ERROR"
    assert "API error" in (result.error_message or "")


@pytest.mark.asyncio
async def test_start_sdk_exception_returns_error_result() -> None:
    """SDK exception during scale patch returns an error result, not a crash."""
    service, mock_apps_v1 = _make_service(patch_scale_raises=True)

    result = await service.start_instances(
        _start_op(provider_api="Deployment", workload_name="orb-abc1", replicas=3)
    )

    assert result.success is False
    assert result.error_code == "START_INSTANCES_ERROR"


# ---------------------------------------------------------------------------
# Integration with K8sProviderStrategy — capabilities and dispatch
# ---------------------------------------------------------------------------


def test_get_capabilities_includes_start_stop() -> None:
    """K8sProviderStrategy.get_capabilities() advertises START and STOP."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(namespace="test"),  # type: ignore[call-arg]
        logger=_make_logger(),
    )
    caps = strategy.get_capabilities()
    op_types = caps.supported_operations
    assert ProviderOperationType.START_INSTANCES in op_types
    assert ProviderOperationType.STOP_INSTANCES in op_types


def test_get_capabilities_start_stop_supported_by_api_feature_flags() -> None:
    """Capabilities features dict correctly marks Deployment/StatefulSet as supported."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(namespace="test"),  # type: ignore[call-arg]
        logger=_make_logger(),
    )
    caps = strategy.get_capabilities()
    by_api = caps.features.get("start_stop_supported_by_api", {})
    assert by_api.get("Deployment") is True
    assert by_api.get("StatefulSet") is True
    assert by_api.get("Pod") is False
    assert by_api.get("Job") is False
    # Top-level flag is the lowest-common-denominator (False) because Pod/Job
    # cannot be scaled — callers must consult the per-api dict for the truth.
    assert caps.features.get("start_stop_supported") is False


@pytest.mark.asyncio
async def test_strategy_execute_operation_routes_start_to_service() -> None:
    """execute_operation dispatches START_INSTANCES to K8sStartStopService."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(namespace="test", in_cluster=True),  # type: ignore[call-arg]
        logger=_make_logger(),
    )
    strategy._initialized = True

    mock_service = MagicMock()
    mock_service.start_instances = AsyncMock(
        return_value=ProviderResult.success_result({"results": {}}, {})
    )
    strategy._start_stop_service = mock_service

    op = ProviderOperation(
        operation_type=ProviderOperationType.START_INSTANCES,
        parameters={"provider_api": "Deployment", "provider_data": {}},
    )
    await strategy.execute_operation(op)
    mock_service.start_instances.assert_called_once_with(op)


@pytest.mark.asyncio
async def test_strategy_execute_operation_routes_stop_to_service() -> None:
    """execute_operation dispatches STOP_INSTANCES to K8sStartStopService."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(namespace="test", in_cluster=True),  # type: ignore[call-arg]
        logger=_make_logger(),
    )
    strategy._initialized = True

    mock_service = MagicMock()
    mock_service.stop_instances = AsyncMock(
        return_value=ProviderResult.success_result({"results": {}}, {})
    )
    strategy._start_stop_service = mock_service

    op = ProviderOperation(
        operation_type=ProviderOperationType.STOP_INSTANCES,
        parameters={"provider_api": "Deployment", "provider_data": {}},
    )
    await strategy.execute_operation(op)
    mock_service.stop_instances.assert_called_once_with(op)
