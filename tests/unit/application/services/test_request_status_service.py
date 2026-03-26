"""Unit tests for RequestStatusService.determine_status_from_machines — return request logic."""

from unittest.mock import MagicMock

from orb.application.services.request_status_service import RequestStatusService
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.request_types import RequestStatus


def _make_service():
    return RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())


def _make_request(request_type="return"):
    req = MagicMock()
    req.request_type.value = request_type
    req.requested_count = 2
    return req


def _make_machine(status: MachineStatus):
    m = MagicMock()
    m.status = status
    return m


class TestReturnRequestCompletion:
    def setup_method(self):
        self.svc = _make_service()
        self.req = _make_request("return")

    def test_return_request_with_shutting_down_machine_is_not_complete(self):
        """1 shutting-down + 1 terminated → IN_PROGRESS (shutting-down is not terminal)."""
        machines = [
            _make_machine(MachineStatus.SHUTTING_DOWN),
            _make_machine(MachineStatus.TERMINATED),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=self.req,
            provider_metadata={},
        )
        assert status == RequestStatus.IN_PROGRESS.value

    def test_return_request_with_all_shutting_down_is_not_complete(self):
        """All shutting-down → IN_PROGRESS (shutting-down is not terminal)."""
        machines = [
            _make_machine(MachineStatus.SHUTTING_DOWN),
            _make_machine(MachineStatus.SHUTTING_DOWN),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=self.req,
            provider_metadata={},
        )
        assert status == RequestStatus.IN_PROGRESS.value

    def test_return_request_with_all_terminated_is_complete(self):
        """All terminated → COMPLETED (regression guard)."""
        machines = [
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=self.req,
            provider_metadata={},
        )
        assert status == RequestStatus.COMPLETED.value

    def test_return_request_with_all_stopped_is_complete(self):
        """All stopped → COMPLETED (regression guard)."""
        machines = [
            _make_machine(MachineStatus.STOPPED),
            _make_machine(MachineStatus.STOPPED),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=self.req,
            provider_metadata={},
        )
        assert status == RequestStatus.COMPLETED.value

    def test_return_request_without_provider_machines_stays_in_progress_while_follow_up_pending(self):
        status, message = self.svc.determine_status_from_machines(
            db_machines=[],
            provider_machines=[],
            request=self.req,
            provider_metadata={"termination_follow_up_pending": True},
        )
        assert status == RequestStatus.IN_PROGRESS.value
        assert "follow-up cleanup" in message

    def test_return_request_with_all_terminated_stays_in_progress_while_follow_up_pending(self):
        machines = [
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),
        ]
        status, message = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=self.req,
            provider_metadata={"termination_follow_up_pending": True},
        )
        assert status == RequestStatus.IN_PROGRESS.value
        assert "follow-up cleanup" in message
