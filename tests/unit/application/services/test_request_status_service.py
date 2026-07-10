"""Unit tests for RequestStatusService."""

from contextlib import AbstractContextManager
from unittest.mock import MagicMock

import pytest

from orb.application.services.request_status_service import RequestStatusService
from orb.domain.base.exceptions import ProviderContractError
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestId, RequestType


def _make_service():
    return RequestStatusService(uow_factory=MagicMock(), logger=MagicMock())


def _make_request(request_type="return"):
    req = MagicMock()
    req.request_type.value = request_type
    req.provider_name = "test-provider"
    req.requested_count = 2
    req.provider_data = {}
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
        self.req.provider_data = {"follow_up_context": {"follow_up_kind": "termination"}}

        status, message = self.svc.determine_status_from_machines(
            db_machines=[],
            provider_machines=[],
            request=self.req,
            provider_metadata={},
        )
        assert status == RequestStatus.IN_PROGRESS.value
        assert "follow-up cleanup" in message

    def test_return_request_with_all_terminated_stays_in_progress_while_follow_up_pending(self):
        self.req.provider_data = {"follow_up_context": {"follow_up_kind": "termination"}}

        machines = [
            _make_machine(MachineStatus.TERMINATED),
            _make_machine(MachineStatus.TERMINATED),
        ]
        status, message = self.svc.determine_status_from_machines(
            db_machines=machines,  # type: ignore[arg-type]
            provider_machines=machines,  # type: ignore[arg-type]
            request=self.req,
            provider_metadata={},
        )
        assert status == RequestStatus.IN_PROGRESS.value
        assert "follow-up cleanup" in message


class _FakeUnitOfWork(AbstractContextManager):
    def __init__(self, requests_repo) -> None:
        self.requests = requests_repo

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.asyncio
async def test_update_request_status_preserves_newer_persisted_machine_ids():
    stale_request = Request(
        request_id=RequestId(value="req-00000000-0000-0000-0000-000000000099"),
        request_type=RequestType.ACQUIRE,
        provider_type="azure",
        template_id="tmpl-1",
        requested_count=1,
        status=RequestStatus.IN_PROGRESS,
        resource_ids=["req-00000000-0000-0000-0000-000000000099"],
        machine_ids=[],
    )
    current_request = stale_request.update_machine_ids(["node-1"])

    requests_repo = MagicMock()
    requests_repo.get_by_id.return_value = current_request
    requests_repo.save = MagicMock()

    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.return_value = _FakeUnitOfWork(requests_repo)

    service = RequestStatusService(uow_factory=uow_factory, logger=MagicMock())

    updated = await service.update_request_status(
        stale_request,
        RequestStatus.COMPLETED.value,
        "All instances running successfully",
    )

    requests_repo.save.assert_called_once()
    saved_request = requests_repo.save.call_args.args[0]
    assert saved_request.machine_ids == ["node-1"]
    assert updated.machine_ids == ["node-1"]
    assert updated.status == RequestStatus.COMPLETED


def test_acquire_request_with_pending_machine_does_not_complete_from_fulfillment_metadata():
    svc = _make_service()
    req = _make_request("acquire")
    req.requested_count = 1

    pending_machine = _make_machine(MachineStatus.PENDING)
    status, message = svc.determine_status_from_machines(
        db_machines=[],
        provider_machines=[pending_machine],  # type: ignore[arg-type]
        request=req,
        provider_metadata={
            "provider_fulfilment": ProviderFulfilment(
                state="in_progress",
                message="0/1 instances running, waiting for 1 more",
                target_units=1,
                fulfilled_units=0,
                pending_count=1,
            )
        },
    )

    assert status == RequestStatus.IN_PROGRESS.value
    assert message == "0/1 instances running, waiting for 1 more"


def test_acquire_request_with_terminal_planned_shortfall_becomes_partial():
    svc = _make_service()
    req = _make_request("acquire")
    req.requested_count = 2

    running_machine = _make_machine(MachineStatus.RUNNING)
    status, message = svc.determine_status_from_machines(
        db_machines=[],
        provider_machines=[running_machine],  # type: ignore[arg-type]
        request=req,
        provider_metadata={
            "provider_fulfilment": ProviderFulfilment(
                state="partial",
                message="1/2 instances running: OperationNotAllowed: quota exceeded",
                target_units=2,
                fulfilled_units=1,
                running_count=1,
                failed_count=1,
            )
        },
    )

    assert status == RequestStatus.PARTIAL.value
    assert message == "1/2 instances running: OperationNotAllowed: quota exceeded"


def test_acquire_request_with_terminal_planned_shortfall_and_no_instances_becomes_failed():
    svc = _make_service()
    req = _make_request("acquire")
    req.requested_count = 2

    status, message = svc.determine_status_from_machines(
        db_machines=[],
        provider_machines=[],
        request=req,
        provider_metadata={
            "provider_fulfilment": ProviderFulfilment(
                state="failed",
                message="OperationNotAllowed: quota exceeded",
                target_units=2,
                fulfilled_units=0,
                failed_count=2,
            )
        },
    )

    assert status == RequestStatus.FAILED.value
    assert message == "OperationNotAllowed: quota exceeded"


def test_acquire_request_without_provider_fulfilment_raises_contract_error():
    svc = _make_service()
    req = _make_request("acquire")
    req.requested_count = 2

    with pytest.raises(ProviderContractError, match="ProviderFulfilment"):
        svc.determine_status_from_machines(
            db_machines=[],
            provider_machines=[],
            request=req,
            provider_metadata={},
        )
