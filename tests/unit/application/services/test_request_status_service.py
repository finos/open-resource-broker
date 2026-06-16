"""Unit tests for RequestStatusService.determine_status_from_machines.

Covers both return-request completion logic and the partial-fulfillment guards
for async-acceptance providers (EC2Fleet maintain/request, ASG, SpotFleet).
"""

from unittest.mock import MagicMock

from orb.application.services.request_status_service import RequestStatusService
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.request_types import RequestStatus


def _make_service():
    return RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())


def _make_request(request_type="return", requested_count=2):
    req = MagicMock()
    req.request_type.value = request_type
    req.requested_count = requested_count
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


class TestAcquirePartialFulfillmentGuard:
    """Regression guard: COMPLETED must NOT fire for acquire when only fulfilled_capacity
    reaches the target while instances are still pending.

    Root cause: for EC2Fleet maintain/request and SpotFleet, FulfilledCapacity reflects
    capacity *allocated* by the fleet, not instances that are actually running.  The fleet
    can show FulfilledCapacity == target while instances are still in ``pending`` state.
    Firing COMPLETED at that point exposes fewer running machines than the caller requested.
    """

    def setup_method(self):
        self.svc = _make_service()

    def _fleet_metadata(
        self,
        target: int,
        fulfilled: float,
        fulfillment_final: bool = False,
    ) -> dict:
        return {
            "fleet_capacity_fulfilment": {
                "target_capacity_units": target,
                "fulfilled_capacity_units": fulfilled,
                "fulfillment_final": fulfillment_final,
            }
        }

    def test_fleet_fulfilled_but_instances_pending_is_in_progress(self):
        """Fleet FulfilledCapacity == target, but 2/4 instances still pending → IN_PROGRESS."""
        req = _make_request("acquire", requested_count=4)
        machines = [
            _make_machine(MachineStatus.RUNNING),
            _make_machine(MachineStatus.RUNNING),
            _make_machine(MachineStatus.PENDING),
            _make_machine(MachineStatus.PENDING),
        ]
        metadata = self._fleet_metadata(target=4, fulfilled=4.0)
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata=metadata,
        )
        # Must NOT be COMPLETED — 2 instances are still pending
        assert status != RequestStatus.COMPLETED.value
        assert status == RequestStatus.IN_PROGRESS.value

    def test_all_running_with_fleet_metadata_is_completed(self):
        """Fleet FulfilledCapacity == target AND all instances running → COMPLETED."""
        req = _make_request("acquire", requested_count=4)
        machines = [
            _make_machine(MachineStatus.RUNNING),
            _make_machine(MachineStatus.RUNNING),
            _make_machine(MachineStatus.RUNNING),
            _make_machine(MachineStatus.RUNNING),
        ]
        metadata = self._fleet_metadata(target=4, fulfilled=4.0)
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata=metadata,
        )
        assert status == RequestStatus.COMPLETED.value

    def test_no_fleet_metadata_running_count_gates_completed(self):
        """Without fleet metadata, running_count >= requested_count triggers COMPLETED."""
        req = _make_request("acquire", requested_count=2)
        machines = [
            _make_machine(MachineStatus.RUNNING),
            _make_machine(MachineStatus.RUNNING),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata={},
        )
        assert status == RequestStatus.COMPLETED.value

    def test_instant_fleet_fulfillment_final_no_pending_is_partial(self):
        """Instant fleet: fulfillment_final=True, pending=0, running < target → PARTIAL."""
        req = _make_request("acquire", requested_count=4)
        machines = [
            _make_machine(MachineStatus.RUNNING),
            _make_machine(MachineStatus.RUNNING),
        ]
        metadata = self._fleet_metadata(target=4, fulfilled=2.0, fulfillment_final=True)
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata=metadata,
        )
        assert status == RequestStatus.PARTIAL.value

    def test_fleet_partial_fulfilled_not_yet_complete_is_in_progress(self):
        """Fleet FulfilledCapacity < target → IN_PROGRESS (waiting for more instances)."""
        req = _make_request("acquire", requested_count=4)
        machines = [
            _make_machine(MachineStatus.RUNNING),
            _make_machine(MachineStatus.RUNNING),
            _make_machine(MachineStatus.PENDING),
        ]
        metadata = self._fleet_metadata(target=4, fulfilled=3.0)
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata=metadata,
        )
        assert status == RequestStatus.IN_PROGRESS.value


class TestReturnPartialDescribeGuard:
    """Regression guard: COMPLETED must NOT fire for return when describe returns fewer
    machines than requested_count (partial describe window before AWS propagates state).

    Root cause: comparing ``effectively_done_count`` against ``len(provider_machines)``
    allows an early COMPLETED when AWS hasn't yet returned all terminating instances.
    Comparing against ``request.requested_count`` (== len(machine_ids) for return requests)
    prevents this.
    """

    def setup_method(self):
        self.svc = _make_service()

    def test_partial_describe_terminated_not_complete(self):
        """3 terminated visible, requested_count=4 → IN_PROGRESS (1 not yet in response)."""
        req = _make_request("return", requested_count=4)
        machines = [
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata={},
        )
        # Only 3 of 4 terminated → not complete
        assert status != RequestStatus.COMPLETED.value
        assert status == RequestStatus.IN_PROGRESS.value

    def test_all_requested_terminated_is_completed(self):
        """requested_count terminated instances visible → COMPLETED."""
        req = _make_request("return", requested_count=4)
        machines = [
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata={},
        )
        assert status == RequestStatus.COMPLETED.value

    def test_more_terminated_than_requested_is_completed(self):
        """terminated_count > requested_count → COMPLETED (safe: at least all done)."""
        req = _make_request("return", requested_count=2)
        machines = [
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),  # extra synthetic entry
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata={},
        )
        assert status == RequestStatus.COMPLETED.value

    def test_one_terminated_one_shutting_down_not_complete(self):
        """1 terminated + 1 shutting-down (requested_count=2) → IN_PROGRESS."""
        req = _make_request("return", requested_count=2)
        machines = [
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.SHUTTING_DOWN),
        ]
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata={},
        )
        assert status == RequestStatus.IN_PROGRESS.value
