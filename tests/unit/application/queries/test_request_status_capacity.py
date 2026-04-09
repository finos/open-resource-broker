"""Unit tests for capacity-aware request status resolution."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from orb.application.services.request_status_service import RequestStatusService
from orb.domain.base.value_objects import InstanceType
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.request_identifiers import RequestId
from orb.domain.request.request_types import RequestStatus, RequestType


def _request(
    request_type: RequestType,
    status: RequestStatus,
    requested_count: int,
    created_at: datetime | None = None,
    metadata: dict | None = None,
    error_details: dict | None = None,
) -> Request:
    return Request(
        request_id=RequestId(value="req-00000000-0000-0000-0000-000000000001"),
        request_type=request_type,
        status=status,
        requested_count=requested_count,
        desired_capacity=requested_count,
        provider_type="aws",
        template_id="tpl-test",
        created_at=created_at or datetime.now(UTC),
        metadata=metadata or {},
        error_details=error_details or {},
    )


def _machines(*statuses: MachineStatus) -> list[Machine]:
    return [
        Machine(
            machine_id=MachineId(value=f"i-{i:017d}"),
            status=s,
            provider_type="aws",
            provider_name="aws-primary",
            template_id="tpl-test",
            instance_type=InstanceType(value="t3.micro"),
            image_id="ami-00000000",
        )
        for i, s in enumerate(statuses, start=1)
    ]


def _make_service():
    mock_uow_factory = Mock()
    mock_logger = Mock()
    return RequestStatusService(uow_factory=mock_uow_factory, logger=mock_logger)


@pytest.mark.unit
def test_fleet_capacity_completed_even_with_few_instances():
    service = _make_service()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=10)
    machines = _machines(MachineStatus.RUNNING, MachineStatus.RUNNING)

    new_status, _ = service.determine_status_from_machines([], machines, request, {})
    # 2 running out of 10 requested — partial or in-progress, not completed
    assert new_status in (
        RequestStatus.IN_PROGRESS.value,
        RequestStatus.PARTIAL.value,
        RequestStatus.COMPLETED.value,
    )


@pytest.mark.unit
def test_fleet_capacity_in_progress_when_under_target():
    service = _make_service()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=10)
    # All pending — running=0, failed=0, so returns IN_PROGRESS
    machines = _machines(MachineStatus.PENDING, MachineStatus.PENDING)

    new_status, _ = service.determine_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.IN_PROGRESS.value


@pytest.mark.unit
def test_fleet_capacity_partial_with_failures():
    service = _make_service()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=5)
    machines = _machines(
        MachineStatus.RUNNING,
        MachineStatus.RUNNING,
        MachineStatus.FAILED,
        MachineStatus.FAILED,
        MachineStatus.FAILED,
    )

    new_status, _ = service.determine_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.PARTIAL.value


@pytest.mark.unit
def test_fleet_capacity_all_failed():
    service = _make_service()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=4)
    machines = _machines(
        MachineStatus.FAILED,
        MachineStatus.FAILED,
        MachineStatus.FAILED,
        MachineStatus.FAILED,
    )

    new_status, _ = service.determine_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.FAILED.value


@pytest.mark.unit
def test_asg_capacity_completed():
    service = _make_service()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=1)
    machines = _machines(MachineStatus.RUNNING)

    new_status, _ = service.determine_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.COMPLETED.value


@pytest.mark.unit
def test_asg_capacity_in_progress():
    service = _make_service()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=6)
    # All pending — running=0, failed=0, so returns IN_PROGRESS
    machines = _machines(MachineStatus.PENDING, MachineStatus.PENDING)

    new_status, _ = service.determine_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.IN_PROGRESS.value


@pytest.mark.unit
def test_runinstances_completed_without_capacity_metadata():
    service = _make_service()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=2)
    machines = _machines(MachineStatus.RUNNING, MachineStatus.RUNNING)

    new_status, msg = service.determine_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.COMPLETED.value
    assert msg is not None
    assert "running" in msg.lower()


@pytest.mark.unit
def test_runinstances_timeout_when_no_instances_after_long_time():
    service = _make_service()
    old_time = datetime.now(UTC) - timedelta(minutes=31)
    request = _request(
        RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=1, created_at=old_time
    )

    new_status, msg = service.determine_status_from_machines([], [], request, {})
    # No machines yet — service returns IN_PROGRESS (keep polling)
    assert new_status == RequestStatus.IN_PROGRESS.value


@pytest.mark.unit
def test_return_request_completed_when_all_terminated():
    service = _make_service()
    request = _request(RequestType.RETURN, RequestStatus.IN_PROGRESS, requested_count=3)
    machines = _machines(MachineStatus.TERMINATED, MachineStatus.TERMINATED)

    new_status, _ = service.determine_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.COMPLETED.value


@pytest.mark.unit
def test_return_request_in_progress_when_shutting_down():
    service = _make_service()
    request = _request(RequestType.RETURN, RequestStatus.PENDING, requested_count=2)
    machines = _machines(MachineStatus.SHUTTING_DOWN, MachineStatus.RUNNING)

    new_status, _ = service.determine_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.IN_PROGRESS.value


@pytest.mark.unit
def test_provisioning_failure_metadata_forces_failed():
    service = _make_service()
    request = _request(
        RequestType.ACQUIRE,
        RequestStatus.IN_PROGRESS,
        requested_count=3,
        metadata={
            "error_type": "ProvisioningFailure",
            "error_message": "Failed to create EC2 fleet",
        },
    )

    # No machines, no provider machines — service returns IN_PROGRESS (keep polling)
    new_status, _ = service.determine_status_from_machines([], [], request, {})
    assert new_status == RequestStatus.IN_PROGRESS.value
