"""Unit tests for capacity-aware request status resolution."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from application.queries.handlers import GetRequestHandler
from domain.machine.machine_status import MachineStatus
from domain.request.request_types import RequestStatus, RequestType


def _request(request_type, status, requested_count, created_at=None):
    return SimpleNamespace(
        request_id="req-1",
        request_type=request_type,
        status=status,
        requested_count=requested_count,
        created_at=created_at or datetime.now(UTC),
    )


def _machines(*statuses):
    return [SimpleNamespace(status=s) for s in statuses]


class DummyHandler(GetRequestHandler):
    """Expose the protected method for unit testing."""

    def __init__(self):
        # Minimal init; these members are unused in the tested method
        self.logger = SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        )


@pytest.mark.unit
def test_fleet_capacity_completed_even_with_few_instances():
    handler = DummyHandler()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=10)
    provider_metadata = {
        "fleet_capacity_fulfilment": {
            "target_capacity_units": 10,
            "fulfilled_capacity_units": 10,
            "provisioned_instance_count": 10,
            "state": "active",
        }
    }
    machines = _machines(MachineStatus.RUNNING, MachineStatus.RUNNING)  # fewer than target

    new_status, msg = handler._determine_request_status_from_machines(
        [], machines, request, provider_metadata
    )
    assert new_status == RequestStatus.COMPLETED.value
    assert "Capacity fulfilled" in msg


@pytest.mark.unit
def test_fleet_capacity_in_progress_when_under_target():
    handler = DummyHandler()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=10)
    provider_metadata = {
        "fleet_capacity_fulfilment": {
            "target_capacity_units": 10,
            "fulfilled_capacity_units": 6,
            "provisioned_instance_count": 6,
            "state": "modifying",
        }
    }
    machines = _machines(MachineStatus.RUNNING, MachineStatus.PENDING)

    new_status, msg = handler._determine_request_status_from_machines(
        [], machines, request, provider_metadata
    )
    assert new_status is None  # current status already in-progress; no transition
    assert msg is None


@pytest.mark.unit
def test_fleet_capacity_partial_with_failures():
    handler = DummyHandler()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=5)
    provider_metadata = {
        "fleet_capacity_fulfilment": {
            "target_capacity_units": 5,
            "fulfilled_capacity_units": 5,
            "provisioned_instance_count": 5,
            "state": "active",
        }
    }
    machines = _machines(
        MachineStatus.RUNNING,
        MachineStatus.RUNNING,
        MachineStatus.FAILED,
        MachineStatus.FAILED,
        MachineStatus.FAILED,
    )

    new_status, msg = handler._determine_request_status_from_machines(
        [], machines, request, provider_metadata
    )
    assert new_status == RequestStatus.PARTIAL.value
    assert "Partial success" in msg


@pytest.mark.unit
def test_fleet_capacity_all_failed():
    handler = DummyHandler()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=4)
    provider_metadata = {
        "fleet_capacity_fulfilment": {
            "target_capacity_units": 4,
            "fulfilled_capacity_units": 0,
            "provisioned_instance_count": 0,
            "state": "deleted",
        }
    }
    machines = _machines(
        MachineStatus.FAILED,
        MachineStatus.FAILED,
        MachineStatus.FAILED,
        MachineStatus.FAILED,
    )

    new_status, msg = handler._determine_request_status_from_machines(
        [], machines, request, provider_metadata
    )
    assert new_status == RequestStatus.FAILED.value
    assert "failed" in msg.lower()


@pytest.mark.unit
def test_asg_capacity_completed():
    handler = DummyHandler()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=6)
    provider_metadata = {
        "fleet_capacity_fulfilment": {
            "target_capacity_units": 6,
            "fulfilled_capacity_units": 6,
            "provisioned_instance_count": 6,
            "state": None,
        }
    }
    machines = _machines(MachineStatus.RUNNING)

    new_status, msg = handler._determine_request_status_from_machines(
        [], machines, request, provider_metadata
    )
    assert new_status == RequestStatus.COMPLETED.value
    assert "fulfilled" in msg


@pytest.mark.unit
def test_asg_capacity_in_progress():
    handler = DummyHandler()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=6)
    provider_metadata = {
        "fleet_capacity_fulfilment": {
            "target_capacity_units": 6,
            "fulfilled_capacity_units": 2,
            "provisioned_instance_count": 2,
            "state": None,
        }
    }
    machines = _machines(MachineStatus.RUNNING, MachineStatus.PENDING)

    new_status, msg = handler._determine_request_status_from_machines(
        [], machines, request, provider_metadata
    )
    assert new_status is None  # current status already in-progress; no transition
    assert msg is None


@pytest.mark.unit
def test_runinstances_completed_without_capacity_metadata():
    handler = DummyHandler()
    request = _request(RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=2)
    machines = _machines(MachineStatus.RUNNING, MachineStatus.RUNNING)

    new_status, msg = handler._determine_request_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.COMPLETED.value
    assert "running" in msg


@pytest.mark.unit
def test_runinstances_timeout_when_no_instances_after_long_time():
    handler = DummyHandler()
    old_time = datetime.now(UTC) - timedelta(minutes=31)
    request = _request(
        RequestType.ACQUIRE, RequestStatus.IN_PROGRESS, requested_count=1, created_at=old_time
    )

    new_status, msg = handler._determine_request_status_from_machines([], [], request, {})
    assert new_status is None  # remains in-progress until timeout check is reached
    assert msg is None


@pytest.mark.unit
def test_return_request_completed_when_all_terminated():
    handler = DummyHandler()
    request = _request(RequestType.RETURN, RequestStatus.IN_PROGRESS, requested_count=3)
    machines = _machines(MachineStatus.TERMINATED, MachineStatus.TERMINATED)

    new_status, msg = handler._determine_request_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.COMPLETED.value
    assert "completed" in msg


@pytest.mark.unit
def test_return_request_in_progress_when_shutting_down():
    handler = DummyHandler()
    request = _request(RequestType.RETURN, RequestStatus.PENDING, requested_count=2)
    machines = _machines(MachineStatus.SHUTTING_DOWN, MachineStatus.RUNNING)

    new_status, msg = handler._determine_request_status_from_machines([], machines, request, {})
    assert new_status == RequestStatus.IN_PROGRESS.value
    assert "in progress" in msg.lower()
