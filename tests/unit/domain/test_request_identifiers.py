"""Unit tests for request identifier value objects."""

import pytest

from domain.request.request_identifiers import MachineReference, RequestId, ResourceIdentifier
from domain.request.request_types import RequestType


class TestRequestId:
    def test_valid_acquire_id(self):
        rid = RequestId(value="req-a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert str(rid) == "req-a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_valid_return_id(self):
        rid = RequestId(value="ret-a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert str(rid) == "ret-a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_invalid_format_raises(self):
        with pytest.raises(Exception):
            RequestId(value="bad-format")

    def test_empty_raises(self):
        with pytest.raises(Exception):
            RequestId(value="")

    def test_request_type_acquire(self):
        rid = RequestId(value="req-a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert rid.request_type == RequestType.ACQUIRE

    def test_request_type_return(self):
        rid = RequestId(value="ret-a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert rid.request_type == RequestType.RETURN

    def test_generate_acquire(self):
        rid = RequestId.generate(RequestType.ACQUIRE)
        assert rid.value.startswith("req-")
        assert len(rid.value) > 4

    def test_generate_return(self):
        rid = RequestId.generate(RequestType.RETURN)
        assert rid.value.startswith("ret-")

    def test_generate_unique(self):
        r1 = RequestId.generate(RequestType.ACQUIRE)
        r2 = RequestId.generate(RequestType.ACQUIRE)
        assert r1.value != r2.value

    def test_repr(self):
        rid = RequestId(value="req-a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert "RequestId" in repr(rid)


class TestMachineReference:
    def test_valid_reference(self):
        ref = MachineReference(
            machine_id="i-1234567890abcdef0",
            status="running",
            result="success",
        )
        assert ref.machine_id == "i-1234567890abcdef0"
        assert ref.status == "running"

    def test_short_machine_id_raises(self):
        with pytest.raises(Exception):
            MachineReference(machine_id="ab", status="running", result="success")

    def test_empty_machine_id_raises(self):
        with pytest.raises(Exception):
            MachineReference(machine_id="", status="running", result="success")

    def test_is_successful_true(self):
        ref = MachineReference(machine_id="i-abc123def456", status="running", result="success")
        assert ref.is_successful() is True

    def test_is_successful_false(self):
        ref = MachineReference(machine_id="i-abc123def456", status="failed", result="failed")
        assert ref.is_successful() is False

    def test_is_failed_true(self):
        ref = MachineReference(machine_id="i-abc123def456", status="failed", result="failed")
        assert ref.is_failed() is True

    def test_is_failed_false(self):
        ref = MachineReference(machine_id="i-abc123def456", status="running", result="success")
        assert ref.is_failed() is False

    def test_has_error_with_message(self):
        ref = MachineReference(
            machine_id="i-abc123def456",
            status="failed",
            result="failed",
            error_message="Something went wrong",
        )
        assert ref.has_error() is True

    def test_has_error_without_message(self):
        ref = MachineReference(machine_id="i-abc123def456", status="running", result="success")
        assert ref.has_error() is False

    def test_has_error_empty_message(self):
        ref = MachineReference(
            machine_id="i-abc123def456",
            status="failed",
            result="failed",
            error_message="   ",
        )
        assert ref.has_error() is False

    def test_update_status_returns_new_instance(self):
        ref = MachineReference(machine_id="i-abc123def456", status="pending", result="pending")
        updated = ref.update_status("running", "success")
        assert updated.status == "running"
        assert updated.result == "success"
        assert updated.machine_id == ref.machine_id
        # original unchanged
        assert ref.status == "pending"

    def test_update_status_with_error(self):
        ref = MachineReference(machine_id="i-abc123def456", status="pending", result="pending")
        updated = ref.update_status("failed", "failed", error_message="Capacity error")
        assert updated.error_message == "Capacity error"

    def test_str_representation(self):
        ref = MachineReference(machine_id="i-abc123def456", status="running", result="success")
        s = str(ref)
        assert "i-abc123def456" in s
        assert "running" in s


class TestResourceIdentifier:
    def test_valid_identifier(self):
        ri = ResourceIdentifier(resource_type="launch_template", resource_id="lt-abc123")
        assert ri.resource_type == "launch_template"
        assert ri.resource_id == "lt-abc123"

    def test_resource_type_normalized(self):
        ri = ResourceIdentifier(resource_type="Launch-Template", resource_id="lt-abc123")
        assert ri.resource_type == "launch_template"

    def test_resource_type_spaces_normalized(self):
        ri = ResourceIdentifier(resource_type="security group", resource_id="sg-abc123")
        assert ri.resource_type == "security_group"

    def test_empty_resource_type_raises(self):
        with pytest.raises(Exception):
            ResourceIdentifier(resource_type="", resource_id="lt-abc123")

    def test_empty_resource_id_raises(self):
        with pytest.raises(Exception):
            ResourceIdentifier(resource_type="launch_template", resource_id="")

    def test_resource_id_stripped(self):
        ri = ResourceIdentifier(resource_type="launch_template", resource_id="  lt-abc123  ")
        assert ri.resource_id == "lt-abc123"

    def test_is_arn_resource_true(self):
        ri = ResourceIdentifier(
            resource_type="iam_role",
            resource_id="my-role",
            resource_arn="arn:aws:iam::123456789012:role/my-role",
        )
        assert ri.is_arn_resource() is True

    def test_is_arn_resource_false(self):
        ri = ResourceIdentifier(resource_type="launch_template", resource_id="lt-abc123")
        assert ri.is_arn_resource() is False

    def test_get_resource_name(self):
        ri = ResourceIdentifier(resource_type="launch_template", resource_id="lt-abc123")
        name = ri.get_resource_name()
        assert "Launch Template" in name
        assert "lt-abc123" in name

    def test_str_representation(self):
        ri = ResourceIdentifier(resource_type="launch_template", resource_id="lt-abc123")
        assert str(ri) == "launch_template:lt-abc123"
