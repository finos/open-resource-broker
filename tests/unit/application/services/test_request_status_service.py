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

    def test_return_request_mix_shutting_down_and_running_is_in_progress(self):
        """Some shutting-down, some running → IN_PROGRESS (not complete)."""
        machines = [
            _make_machine(MachineStatus.SHUTTING_DOWN),
            _make_machine(MachineStatus.RUNNING),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=self.req,
            provider_metadata={},
        )
        assert status == RequestStatus.IN_PROGRESS.value

    def test_return_request_empty_provider_machines_is_complete(self):
        """No instances visible in provider → all gone, COMPLETED."""
        db_machines = [_make_machine(MachineStatus.TERMINATED)]
        status, msg = self.svc.determine_status_from_machines(
            db_machines=db_machines,  # type: ignore[arg-type]
            provider_machines=[],
            request=self.req,
            provider_metadata={},
        )
        assert status == RequestStatus.COMPLETED.value
        assert "no longer visible" in (msg or "")


class TestPrematureCompletedRegression:
    """Regression guard: COMPLETED must NOT be written when termination is merely accepted.

    The bug: request_creation_handlers wrote COMPLETED immediately on TerminateInstances
    accept, while instances were still shutting-down.  The fix writes IN_PROGRESS so that
    background sync can poll and transition to COMPLETED only when all instances reach
    the terminated state.
    """

    def setup_method(self):
        self.svc = _make_service()
        self.req = _make_request("return")

    def test_shutting_down_instance_yields_in_progress_not_completed(self):
        """Single shutting-down instance → IN_PROGRESS, never COMPLETED."""
        machines = [_make_machine(MachineStatus.SHUTTING_DOWN)]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=self.req,
            provider_metadata={},
        )
        assert status != RequestStatus.COMPLETED.value
        assert status == RequestStatus.IN_PROGRESS.value

    def test_mix_shutting_down_terminated_yields_in_progress(self):
        """Not all terminated → IN_PROGRESS (shutting-down counts as still processing)."""
        machines = [
            _make_machine(MachineStatus.SHUTTING_DOWN),
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.SHUTTING_DOWN),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=self.req,
            provider_metadata={},
        )
        assert status == RequestStatus.IN_PROGRESS.value

    def test_all_terminated_yields_completed(self):
        """All terminated → COMPLETED (the honest transition the poller should see)."""
        machines = [
            _make_machine(MachineStatus.TERMINATED),
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
