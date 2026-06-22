"""Unit tests for RequestStatusService.determine_status_from_machines.

The acquire path now trusts ProviderFulfilment exclusively.
The return path continues to use machine-state counting.
"""

from unittest.mock import MagicMock

import pytest

from orb.application.services.request_status_service import RequestStatusService
from orb.domain.base.exceptions import ProviderContractError
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.request_types import RequestStatus


def _make_service():
    return RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())


def _make_request(request_type="return", requested_count=2):
    req = MagicMock()
    req.request_type.value = request_type
    req.requested_count = requested_count
    req.provider_name = "aws-test"
    return req


def _make_machine(status: MachineStatus):
    m = MagicMock()
    m.status = status
    return m


def _fulfilment(state, message="test", **kwargs) -> dict:
    """Return metadata dict with a ProviderFulfilment as the acquire path expects."""
    return {"provider_fulfilment": ProviderFulfilment(state=state, message=message, **kwargs)}


# ---------------------------------------------------------------------------
# Acquire path — ProviderFulfilment state map
# ---------------------------------------------------------------------------


class TestAcquireFulfilmentStatemap:
    """Acquire path: each ProviderFulfilment state maps to the right RequestStatus."""

    def setup_method(self):
        self.svc = _make_service()

    def test_fulfilled_maps_to_completed(self):
        req = _make_request("acquire", requested_count=4)
        machines = [_make_machine(MachineStatus.RUNNING)] * 2
        status, msg = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata=_fulfilment(
                "fulfilled",
                "Fleet fulfilled",
                target_units=4,
                fulfilled_units=4,
                running_count=2,
                pending_count=0,
                failed_count=0,
            ),
        )
        assert status == RequestStatus.COMPLETED.value
        assert msg == "Fleet fulfilled"

    def test_in_progress_maps_to_in_progress(self):
        req = _make_request("acquire", requested_count=4)
        machines = [_make_machine(MachineStatus.PENDING)] * 4
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata=_fulfilment("in_progress", "waiting"),
        )
        assert status == RequestStatus.IN_PROGRESS.value

    def test_partial_maps_to_partial(self):
        req = _make_request("acquire", requested_count=4)
        machines = [_make_machine(MachineStatus.RUNNING)] * 2
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata=_fulfilment("partial", "only 2 of 4"),
        )
        assert status == RequestStatus.PARTIAL.value

    def test_failed_maps_to_failed(self):
        req = _make_request("acquire", requested_count=4)
        machines = [_make_machine(MachineStatus.RUNNING)] * 0
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata=_fulfilment("failed", "all failed"),
        )
        assert status == RequestStatus.FAILED.value


class TestAcquireMissingFulfilmentRaisesContractError:
    """Missing ProviderFulfilment raises ProviderContractError — no silent fallback."""

    def setup_method(self):
        self.svc = _make_service()

    def test_raises_contract_error_when_fulfilment_absent(self):
        req = _make_request("acquire", requested_count=2)
        machines = [_make_machine(MachineStatus.RUNNING)] * 2
        with pytest.raises(ProviderContractError):
            self.svc.determine_status_from_machines(
                db_machines=machines,  # type: ignore[arg-type]
                provider_machines=machines,  # type: ignore[arg-type]
                request=req,
                provider_metadata={},  # no provider_fulfilment key
            )

    def test_raises_contract_error_when_fulfilment_is_none(self):
        req = _make_request("acquire", requested_count=2)
        machines = [_make_machine(MachineStatus.RUNNING)] * 2
        with pytest.raises(ProviderContractError):
            self.svc.determine_status_from_machines(
                db_machines=machines,  # type: ignore[arg-type]
                provider_machines=machines,  # type: ignore[arg-type]
                request=req,
                provider_metadata={"provider_fulfilment": None},
            )

    def test_legacy_fleet_capacity_key_ignored(self):
        """Old fleet_capacity_fulfilment key must NOT unlock a legacy path."""
        req = _make_request("acquire", requested_count=2)
        machines = [_make_machine(MachineStatus.RUNNING)] * 2
        with pytest.raises(ProviderContractError):
            self.svc.determine_status_from_machines(
                db_machines=machines,  # type: ignore[arg-type]
                provider_machines=machines,  # type: ignore[arg-type]
                request=req,
                provider_metadata={
                    "fleet_capacity_fulfilment": {
                        "target_capacity_units": 2,
                        "fulfilled_capacity_units": 2.0,
                    }
                },
            )


# ---------------------------------------------------------------------------
# Return path — machine-state counting (unchanged)
# ---------------------------------------------------------------------------


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
    """Regression guard: COMPLETED must NOT be written when termination is merely accepted."""

    def setup_method(self):
        self.svc = _make_service()
        self.req = _make_request("return")

    def test_shutting_down_instance_yields_in_progress_not_completed(self):
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


class TestReturnPartialDescribeGuard:
    """Regression guard: COMPLETED must NOT fire when describe returns fewer machines."""

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
        assert status != RequestStatus.COMPLETED.value
        assert status == RequestStatus.IN_PROGRESS.value

    def test_all_requested_terminated_is_completed(self):
        req = _make_request("return", requested_count=4)
        machines = [_make_machine(MachineStatus.TERMINATED)] * 4
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata={},
        )
        assert status == RequestStatus.COMPLETED.value

    def test_more_terminated_than_requested_is_completed(self):
        req = _make_request("return", requested_count=2)
        machines = [_make_machine(MachineStatus.TERMINATED)] * 3
        status, _ = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=req,
            provider_metadata={},
        )
        assert status == RequestStatus.COMPLETED.value

    def test_one_terminated_one_shutting_down_not_complete(self):
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
