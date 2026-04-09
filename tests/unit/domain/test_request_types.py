"""Unit tests for request type and status enumerations."""

import pytest

from orb.domain.request.request_types import MachineResult, RequestStatus, RequestType


class TestRequestType:
    def test_values(self):
        assert RequestType.ACQUIRE.value == "acquire"
        assert RequestType.RETURN.value == "return"

    def test_from_str_valid(self):
        assert RequestType.from_str("acquire") == RequestType.ACQUIRE
        assert RequestType.from_str("ACQUIRE") == RequestType.ACQUIRE
        assert RequestType.from_str("return") == RequestType.RETURN
        assert RequestType.from_str("RETURN") == RequestType.RETURN

    def test_from_str_invalid(self):
        with pytest.raises(ValueError, match="Invalid RequestType"):
            RequestType.from_str("unknown")

    def test_to_operation_type(self):
        assert RequestType.ACQUIRE.to_operation_type() == "provision"
        assert RequestType.RETURN.to_operation_type() == "terminate"

    def test_is_acquire(self):
        assert RequestType.ACQUIRE.is_acquire() is True
        assert RequestType.RETURN.is_acquire() is False

    def test_is_return(self):
        assert RequestType.RETURN.is_return() is True
        assert RequestType.ACQUIRE.is_return() is False


class TestRequestStatus:
    def test_values(self):
        assert RequestStatus.PENDING.value == "pending"
        assert RequestStatus.IN_PROGRESS.value == "in_progress"
        assert RequestStatus.COMPLETED.value == "complete"
        assert RequestStatus.FAILED.value == "failed"
        assert RequestStatus.CANCELLED.value == "cancelled"
        assert RequestStatus.PARTIAL.value == "partial"
        assert RequestStatus.TIMEOUT.value == "timeout"
        assert RequestStatus.ACQUIRING.value == "acquiring"

    def test_from_str_valid(self):
        assert RequestStatus.from_str("pending") == RequestStatus.PENDING
        assert RequestStatus.from_str("FAILED") == RequestStatus.FAILED
        assert RequestStatus.from_str("complete") == RequestStatus.COMPLETED

    def test_from_str_invalid(self):
        with pytest.raises(ValueError, match="Invalid RequestStatus"):
            RequestStatus.from_str("bogus")

    def test_is_terminal(self):
        assert RequestStatus.COMPLETED.is_terminal() is True
        assert RequestStatus.FAILED.is_terminal() is True
        assert RequestStatus.CANCELLED.is_terminal() is True
        assert RequestStatus.TIMEOUT.is_terminal() is True
        assert RequestStatus.PARTIAL.is_terminal() is True
        assert RequestStatus.PENDING.is_terminal() is False
        assert RequestStatus.IN_PROGRESS.is_terminal() is False
        assert RequestStatus.ACQUIRING.is_terminal() is False

    def test_is_active(self):
        assert RequestStatus.PENDING.is_active() is True
        assert RequestStatus.IN_PROGRESS.is_active() is True
        assert RequestStatus.ACQUIRING.is_active() is True
        assert RequestStatus.COMPLETED.is_active() is False
        assert RequestStatus.FAILED.is_active() is False

    def test_can_transition_to_valid(self):
        assert RequestStatus.PENDING.can_transition_to(RequestStatus.IN_PROGRESS) is True
        assert RequestStatus.PENDING.can_transition_to(RequestStatus.CANCELLED) is True
        assert RequestStatus.IN_PROGRESS.can_transition_to(RequestStatus.COMPLETED) is True
        assert RequestStatus.IN_PROGRESS.can_transition_to(RequestStatus.PARTIAL) is True
        assert RequestStatus.IN_PROGRESS.can_transition_to(RequestStatus.FAILED) is True
        assert RequestStatus.IN_PROGRESS.can_transition_to(RequestStatus.CANCELLED) is True
        assert RequestStatus.IN_PROGRESS.can_transition_to(RequestStatus.TIMEOUT) is True
        assert RequestStatus.IN_PROGRESS.can_transition_to(RequestStatus.ACQUIRING) is True
        assert RequestStatus.ACQUIRING.can_transition_to(RequestStatus.ACQUIRING) is True
        assert RequestStatus.ACQUIRING.can_transition_to(RequestStatus.COMPLETED) is True
        assert RequestStatus.ACQUIRING.can_transition_to(RequestStatus.PARTIAL) is True
        assert RequestStatus.ACQUIRING.can_transition_to(RequestStatus.FAILED) is True
        assert RequestStatus.ACQUIRING.can_transition_to(RequestStatus.TIMEOUT) is True
        assert RequestStatus.ACQUIRING.can_transition_to(RequestStatus.CANCELLED) is True

    def test_can_transition_to_invalid(self):
        # Terminal states cannot transition
        assert RequestStatus.COMPLETED.can_transition_to(RequestStatus.PENDING) is False
        assert RequestStatus.FAILED.can_transition_to(RequestStatus.IN_PROGRESS) is False
        assert RequestStatus.CANCELLED.can_transition_to(RequestStatus.COMPLETED) is False
        assert RequestStatus.TIMEOUT.can_transition_to(RequestStatus.FAILED) is False
        assert RequestStatus.PARTIAL.can_transition_to(RequestStatus.ACQUIRING) is False

    def test_can_transition_to_valid_pending_complete(self):
        # PENDING→COMPLETED is valid for instant provisioning (e.g. RunInstances)
        assert RequestStatus.PENDING.can_transition_to(RequestStatus.COMPLETED) is True


class TestMachineResult:
    def test_values(self):
        assert MachineResult.SUCCESS.value == "success"
        assert MachineResult.FAILED.value == "failed"
        assert MachineResult.PENDING.value == "pending"
        assert MachineResult.SKIPPED.value == "skipped"

    def test_from_str_valid(self):
        assert MachineResult.from_str("success") == MachineResult.SUCCESS
        assert MachineResult.from_str("FAILED") == MachineResult.FAILED
        assert MachineResult.from_str("Pending") == MachineResult.PENDING

    def test_from_str_invalid(self):
        with pytest.raises(ValueError, match="Invalid MachineResult"):
            MachineResult.from_str("unknown")

    def test_is_terminal(self):
        assert MachineResult.SUCCESS.is_terminal() is True
        assert MachineResult.FAILED.is_terminal() is True
        assert MachineResult.SKIPPED.is_terminal() is True
        assert MachineResult.PENDING.is_terminal() is False

    def test_is_successful(self):
        assert MachineResult.SUCCESS.is_successful() is True
        assert MachineResult.FAILED.is_successful() is False
        assert MachineResult.PENDING.is_successful() is False
        assert MachineResult.SKIPPED.is_successful() is False
