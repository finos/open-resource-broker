"""Additional unit tests for K8sStartStopService — per-machine coordinate paths.

Covers the uncovered branches in start_stop_service.py:
- Lines 132-154: start_instances per-machine with Pod/Job (skip) and ValueError
- Lines 179-189: start_instances per-machine SDK exception
- Lines 297-319: stop_instances per-machine with Pod/Job (skip) and ValueError
- Lines 342-352: stop_instances per-machine SDK exception
- Lines 461, 470, 477, 480: _extract_workload_coords_from_data StatefulSet paths
- Lines 514, 590: _extract_workload_coords instance_ids fallback
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.k8s.services.start_stop_service import K8sStartStopService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    logger = MagicMock()
    for m in ("debug", "info", "warning", "error", "critical"):
        setattr(logger, m, MagicMock())
    return logger


def _make_service(
    deployment_scale_raises: Exception | None = None,
    statefulset_scale_raises: Exception | None = None,
) -> tuple[K8sStartStopService, MagicMock]:
    """Return (service, mock_apps_v1) pair."""
    mock_apps_v1 = MagicMock()
    if deployment_scale_raises is not None:
        mock_apps_v1.patch_namespaced_deployment_scale.side_effect = deployment_scale_raises
    if statefulset_scale_raises is not None:
        mock_apps_v1.patch_namespaced_stateful_set_scale.side_effect = statefulset_scale_raises
    mock_k8s_client = MagicMock()
    mock_k8s_client.apps_v1 = mock_apps_v1
    service = K8sStartStopService(
        kubernetes_client=mock_k8s_client,
        logger=_make_logger(),
    )
    return service, mock_apps_v1


def _machine_coords_op(
    op_type: ProviderOperationType,
    machines: dict[str, dict[str, Any]],
) -> ProviderOperation:
    """Build an operation with machine_coordinates from a dict of machine specs."""
    return ProviderOperation(
        operation_type=op_type,
        parameters={"machine_coordinates": machines},
    )


# ---------------------------------------------------------------------------
# start_instances — per-machine coordinator paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_start_machine_coordinates_pod_skipped_marked_false() -> None:
    """START with Pod in machine_coordinates: machine marked False (not raised)."""
    service, mock_apps_v1 = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.START_INSTANCES,
        {
            "pod-abc": {
                "provider_data": {"namespace": "ns", "deployment_name": "dep"},
                "provider_api": "Pod",
                "resource_id": "dep",
            }
        },
    )
    result = asyncio.run(service.start_instances(op))

    assert result.success is True
    assert result.data["results"]["pod-abc"] is False
    mock_apps_v1.patch_namespaced_deployment_scale.assert_not_called()


@pytest.mark.unit
def test_start_machine_coordinates_job_skipped_marked_false() -> None:
    """START with Job in machine_coordinates: machine marked False (not raised)."""
    service, _ = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.START_INSTANCES,
        {
            "job-abc": {
                "provider_data": {},
                "provider_api": "Job",
                "resource_id": "",
            }
        },
    )
    result = asyncio.run(service.start_instances(op))

    assert result.success is True
    assert result.data["results"]["job-abc"] is False


@pytest.mark.unit
def test_start_machine_coordinates_missing_workload_name_marked_false() -> None:
    """START per-machine ValueError (no workload name) → machine marked False, not raised."""
    service, _ = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.START_INSTANCES,
        {
            "dep-abc": {
                "provider_data": {"namespace": "ns"},  # no deployment_name, no resource_id
                "provider_api": "Deployment",
                "resource_id": "",
            }
        },
    )
    result = asyncio.run(service.start_instances(op))

    assert result.success is True
    assert result.data["results"]["dep-abc"] is False


@pytest.mark.unit
def test_start_machine_coordinates_sdk_error_marked_false() -> None:
    """START per-machine SDK exception → machine marked False, not raised."""
    service, _ = _make_service(deployment_scale_raises=Exception("api-fail"))

    op = _machine_coords_op(
        ProviderOperationType.START_INSTANCES,
        {
            "dep-abc": {
                "provider_data": {
                    "namespace": "ns",
                    "deployment_name": "orb-dep1",
                    "replicas": 3,
                },
                "provider_api": "Deployment",
                "resource_id": "orb-dep1",
            }
        },
    )
    result = asyncio.run(service.start_instances(op))

    assert result.success is True
    assert result.data["results"]["dep-abc"] is False


@pytest.mark.unit
def test_start_machine_coordinates_mixed_success_and_failure() -> None:
    """START per-machine: one succeeds, one fails, both recorded correctly."""
    service, mock_apps_v1 = _make_service()

    # Key the failure to the specific deployment ("dep2" / machine-fail) so the
    # outcome is tied to the machine id, not merely "some machine failed".
    def _side_effect(**kwargs: Any) -> None:
        if kwargs.get("name") == "dep2":
            raise Exception("scale of dep2 fails")

    mock_apps_v1.patch_namespaced_deployment_scale.side_effect = _side_effect

    op = _machine_coords_op(
        ProviderOperationType.START_INSTANCES,
        {
            "machine-ok": {
                "provider_data": {"namespace": "ns", "deployment_name": "dep1", "replicas": 2},
                "provider_api": "Deployment",
                "resource_id": "dep1",
            },
            "machine-fail": {
                "provider_data": {"namespace": "ns", "deployment_name": "dep2", "replicas": 1},
                "provider_api": "Deployment",
                "resource_id": "dep2",
            },
        },
    )
    result = asyncio.run(service.start_instances(op))

    assert result.success is True
    results = result.data["results"]
    # The specific machine whose deployment scaled cleanly succeeds; the one
    # whose scale call raised is recorded as failed.
    assert results["machine-ok"] is True
    assert results["machine-fail"] is False


# ---------------------------------------------------------------------------
# stop_instances — per-machine coordinator paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stop_machine_coordinates_pod_skipped_marked_false() -> None:
    """STOP with Pod in machine_coordinates: machine marked False (not raised)."""
    service, mock_apps_v1 = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.STOP_INSTANCES,
        {
            "pod-abc": {
                "provider_data": {"namespace": "ns"},
                "provider_api": "Pod",
                "resource_id": "",
            }
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    assert result.data["results"]["pod-abc"] is False
    mock_apps_v1.patch_namespaced_deployment_scale.assert_not_called()


@pytest.mark.unit
def test_stop_machine_coordinates_job_skipped_marked_false() -> None:
    """STOP with Job in machine_coordinates: machine marked False (not raised)."""
    service, _ = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.STOP_INSTANCES,
        {
            "job-abc": {
                "provider_data": {},
                "provider_api": "Job",
                "resource_id": "",
            }
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    assert result.data["results"]["job-abc"] is False


@pytest.mark.unit
def test_stop_machine_coordinates_missing_workload_name_marked_false() -> None:
    """STOP per-machine ValueError (no workload name) → machine marked False, not raised."""
    service, _ = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.STOP_INSTANCES,
        {
            "dep-abc": {
                "provider_data": {"namespace": "ns"},  # no deployment_name
                "provider_api": "Deployment",
                "resource_id": "",  # no fallback
            }
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    assert result.data["results"]["dep-abc"] is False


@pytest.mark.unit
def test_stop_machine_coordinates_sdk_error_marked_false() -> None:
    """STOP per-machine SDK exception → machine marked False, no exception raised."""
    service, _ = _make_service(deployment_scale_raises=Exception("cluster-error"))

    op = _machine_coords_op(
        ProviderOperationType.STOP_INSTANCES,
        {
            "dep-abc": {
                "provider_data": {
                    "namespace": "ns",
                    "deployment_name": "orb-dep1",
                    "replicas": 5,
                },
                "provider_api": "Deployment",
                "resource_id": "orb-dep1",
            }
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    assert result.data["results"]["dep-abc"] is False
    # replicas_before_stop_per_machine must NOT contain the failed machine
    assert "dep-abc" not in result.data.get("replicas_before_stop_per_machine", {})


@pytest.mark.unit
def test_stop_machine_coordinates_sdk_error_does_not_affect_other_machines() -> None:
    """STOP: a per-machine SDK error does not prevent other machines from succeeding."""
    service, mock_apps_v1 = _make_service()

    call_count = [0]

    def _side_effect(**kwargs: Any) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            raise Exception("first call fails")

    mock_apps_v1.patch_namespaced_deployment_scale.side_effect = _side_effect

    op = _machine_coords_op(
        ProviderOperationType.STOP_INSTANCES,
        {
            "dep-fail": {
                "provider_data": {"namespace": "ns", "deployment_name": "dep1", "replicas": 2},
                "provider_api": "Deployment",
                "resource_id": "dep1",
            },
            "dep-ok": {
                "provider_data": {"namespace": "ns", "deployment_name": "dep2", "replicas": 3},
                "provider_api": "Deployment",
                "resource_id": "dep2",
            },
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    assert result.data["results"]["dep-fail"] is False
    assert result.data["results"]["dep-ok"] is True
    per_machine = result.data.get("replicas_before_stop_per_machine", {})
    assert "dep-fail" not in per_machine
    assert per_machine["dep-ok"] == 3


# ---------------------------------------------------------------------------
# _extract_workload_coords_from_data — StatefulSet paths and resource_id fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_coords_from_data_statefulset_name_used() -> None:
    """_extract_workload_coords_from_data uses statefulset_name for StatefulSet."""
    service, mock_apps_v1 = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.STOP_INSTANCES,
        {
            "sts-abc": {
                "provider_data": {
                    "namespace": "orb-ns",
                    "statefulset_name": "orb-sts1",
                    "replicas": 2,
                },
                "provider_api": "StatefulSet",
                "resource_id": "orb-sts1",
            }
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    call_kw = mock_apps_v1.patch_namespaced_stateful_set_scale.call_args.kwargs
    assert call_kw["name"] == "orb-sts1"
    assert call_kw["namespace"] == "orb-ns"
    assert call_kw["body"].spec.replicas == 0


@pytest.mark.unit
def test_extract_coords_from_data_resource_id_fallback_for_deployment() -> None:
    """_extract_workload_coords_from_data falls back to resource_id for Deployment."""
    service, mock_apps_v1 = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.STOP_INSTANCES,
        {
            "dep-abc": {
                "provider_data": {
                    "namespace": "orb-ns",
                    "replicas": 4,
                    # No deployment_name key — resource_id is the fallback
                },
                "provider_api": "Deployment",
                "resource_id": "orb-dep-fallback",
            }
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    call_kw = mock_apps_v1.patch_namespaced_deployment_scale.call_args.kwargs
    assert call_kw["name"] == "orb-dep-fallback"


@pytest.mark.unit
def test_extract_coords_from_data_resource_id_fallback_for_statefulset() -> None:
    """_extract_workload_coords_from_data uses resource_id when statefulset_name absent."""
    service, mock_apps_v1 = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.START_INSTANCES,
        {
            "sts-abc": {
                "provider_data": {
                    "namespace": "orb-ns",
                    "replicas": 2,
                    "replicas_before_stop": 2,
                    # no statefulset_name
                },
                "provider_api": "StatefulSet",
                "resource_id": "sts-from-resource-id",
            }
        },
    )
    result = asyncio.run(service.start_instances(op))

    assert result.success is True
    call_kw = mock_apps_v1.patch_namespaced_stateful_set_scale.call_args.kwargs
    assert call_kw["name"] == "sts-from-resource-id"


@pytest.mark.unit
def test_extract_coords_from_data_unknown_api_resolved_to_deployment() -> None:
    """provider_api not in _SCALE_SUPPORTED_APIS is resolved to 'Deployment'."""
    service, mock_apps_v1 = _make_service()

    op = _machine_coords_op(
        ProviderOperationType.STOP_INSTANCES,
        {
            "machine-xyz": {
                "provider_data": {
                    "namespace": "ns",
                    "deployment_name": "dep-xyz",
                    "replicas": 1,
                },
                "provider_api": "UnknownKind",  # resolved to Deployment
                "resource_id": "dep-xyz",
            }
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    mock_apps_v1.patch_namespaced_deployment_scale.assert_called_once()


# ---------------------------------------------------------------------------
# _extract_workload_coords — instance_ids fallback (legacy path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_workload_coords_instance_ids_fallback() -> None:
    """Legacy path: instance_ids used as fallback for workload name."""
    service, mock_apps_v1 = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.STOP_INSTANCES,
        parameters={
            "provider_api": "Deployment",
            "provider_data": {"namespace": "ns"},  # no deployment_name
            "instance_ids": ["orb-from-instance-ids"],
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    call_kw = mock_apps_v1.patch_namespaced_deployment_scale.call_args.kwargs
    assert call_kw["name"] == "orb-from-instance-ids"


@pytest.mark.unit
def test_extract_workload_coords_statefulset_name_from_provider_data() -> None:
    """Legacy path: statefulset_name from provider_data for StatefulSet."""
    service, mock_apps_v1 = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.START_INSTANCES,
        parameters={
            "provider_api": "StatefulSet",
            "provider_data": {
                "namespace": "ns",
                "statefulset_name": "orb-sts-legacy",
                "replicas": 2,
                "replicas_before_stop": 2,
            },
        },
    )
    result = asyncio.run(service.start_instances(op))

    assert result.success is True
    call_kw = mock_apps_v1.patch_namespaced_stateful_set_scale.call_args.kwargs
    assert call_kw["name"] == "orb-sts-legacy"


@pytest.mark.unit
def test_extract_workload_coords_default_namespace_when_absent() -> None:
    """Legacy path: namespace defaults to 'default' when provider_data has none."""
    service, mock_apps_v1 = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.STOP_INSTANCES,
        parameters={
            "provider_api": "Deployment",
            "provider_data": {
                "deployment_name": "dep-no-ns",
                "replicas": 1,
                # No namespace
            },
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    call_kw = mock_apps_v1.patch_namespaced_deployment_scale.call_args.kwargs
    assert call_kw["namespace"] == "default"


@pytest.mark.unit
def test_extract_workload_coords_unknown_api_resolved_to_deployment() -> None:
    """_extract_workload_coords resolves unknown provider_api to 'Deployment' (line 514)."""
    service, mock_apps_v1 = _make_service()

    op = ProviderOperation(
        operation_type=ProviderOperationType.STOP_INSTANCES,
        parameters={
            "provider_api": "UnknownKind",  # not in _SCALE_SUPPORTED_APIS
            "provider_data": {
                "namespace": "ns",
                "deployment_name": "dep-fallback",
                "replicas": 2,
            },
        },
    )
    result = asyncio.run(service.stop_instances(op))

    assert result.success is True
    mock_apps_v1.patch_namespaced_deployment_scale.assert_called_once()


@pytest.mark.unit
def test_patch_scale_raises_value_error_for_unsupported_api() -> None:
    """_patch_scale raises ValueError for provider_api other than Deployment/StatefulSet (line 590)."""
    service, _ = _make_service()

    try:
        import kubernetes.client  # noqa: F401 — confirm SDK available  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("kubernetes SDK not installed")

    with pytest.raises(ValueError, match="unsupported provider_api"):
        service._patch_scale(
            provider_api="Job",  # not Deployment or StatefulSet
            namespace="ns",
            name="my-job",
            replicas=0,
        )
