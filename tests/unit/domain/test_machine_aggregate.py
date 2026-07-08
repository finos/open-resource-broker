"""Unit tests for Machine aggregate."""

import pytest

from orb.domain.base.value_objects import InstanceType
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.exceptions import (
    InvalidMachineStateError,
    MachineNotFoundError,
    MachineValidationError,
)
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_machine(
    machine_id="i-1234567890abcdef0",
    status=MachineStatus.PENDING,
    **kwargs,
):
    """Create a minimal valid Machine instance."""
    defaults = dict(
        machine_id=MachineId(value=machine_id),
        template_id="template-001",
        request_id="request-001",
        provider_type="aws",
        provider_name="aws-us-east-1",
        provider_api="ec2fleet",
        instance_type=InstanceType(value="t2.micro"),
        image_id="ami-12345678",
        status=status,
    )
    defaults.update(kwargs)
    return Machine(**defaults)


@pytest.mark.unit
class TestMachineAggregate:
    """Test cases for Machine aggregate."""

    def test_machine_creation(self):
        """Test basic machine creation."""
        machine = _make_machine(
            machine_id="i-1234567890abcdef0",
            status=MachineStatus.RUNNING,
        )

        assert str(machine.machine_id) == "i-1234567890abcdef0"
        assert machine.template_id == "template-001"
        assert machine.request_id == "request-001"
        assert machine.status == MachineStatus.RUNNING
        assert machine.created_at is not None

    def test_machine_creation_minimal(self):
        """Test machine creation with minimal required data."""
        machine = _make_machine(
            machine_id="i-abcdef1234567890",
            status=MachineStatus.PENDING,
        )

        assert str(machine.machine_id) == "i-abcdef1234567890"
        assert machine.status == MachineStatus.PENDING
        assert machine.private_ip is None
        assert machine.public_ip is None

    def test_machine_status_transitions(self):
        """Test valid machine status transitions."""
        machine = _make_machine(status=MachineStatus.PENDING)

        # PENDING -> LAUNCHING
        machine = machine.start_launching()
        assert machine.status == MachineStatus.LAUNCHING
        assert machine.provisioning_started_at is not None

        # LAUNCHING -> RUNNING
        machine = machine.update_status(MachineStatus.RUNNING)
        assert machine.status == MachineStatus.RUNNING

        # RUNNING -> SHUTTING_DOWN
        machine = machine.update_status(MachineStatus.SHUTTING_DOWN)
        assert machine.status == MachineStatus.SHUTTING_DOWN

        # SHUTTING_DOWN -> TERMINATED
        machine = machine.update_status(MachineStatus.TERMINATED)
        assert machine.status == MachineStatus.TERMINATED

    def test_machine_failure_transitions(self):
        """Test machine failure transitions."""
        machine = _make_machine(status=MachineStatus.LAUNCHING)

        # LAUNCHING -> FAILED
        machine = machine.update_status(MachineStatus.FAILED, reason="Insufficient capacity")

        assert machine.status == MachineStatus.FAILED
        assert machine.status_reason == "Insufficient capacity"

    def test_machine_termination(self):
        """Test machine termination."""
        machine = _make_machine(status=MachineStatus.RUNNING)

        # RUNNING -> SHUTTING_DOWN
        machine = machine.update_status(
            MachineStatus.SHUTTING_DOWN, reason="User requested termination"
        )
        assert machine.status == MachineStatus.SHUTTING_DOWN

        # SHUTTING_DOWN -> TERMINATED
        machine = machine.update_status(MachineStatus.TERMINATED)
        assert machine.status == MachineStatus.TERMINATED
        assert machine.termination_time is not None

    def test_invalid_status_transitions(self):
        """Test invalid machine status transitions."""
        # PENDING -> LAUNCHING is the only valid first transition
        machine = _make_machine(status=MachineStatus.PENDING)

        # Cannot go from PENDING to RUNNING directly via start_launching
        # (start_launching only works from PENDING, but RUNNING is not LAUNCHING)
        machine = machine.start_launching()
        assert machine.status == MachineStatus.LAUNCHING

        # Cannot call start_launching again from LAUNCHING
        with pytest.raises(InvalidMachineStateError):
            machine.start_launching()

    def test_machine_provider_data(self):
        """Test machine provider data operations."""
        machine = _make_machine()

        machine = machine.set_provider_data({"fleet_id": "fleet-123", "spot_price": "0.05"})
        assert machine.get_provider_data("fleet_id") == "fleet-123"
        assert machine.get_provider_data("spot_price") == "0.05"
        assert machine.get_provider_data("missing", "default") == "default"

    def test_machine_is_running_property(self):
        """Test machine is_running property."""
        running_machine = _make_machine(status=MachineStatus.RUNNING)
        assert running_machine.is_running is True

        pending_machine = _make_machine(status=MachineStatus.PENDING)
        assert pending_machine.is_running is False

    def test_machine_is_terminated_property(self):
        """Test machine is_terminated property."""
        terminated_machine = _make_machine(status=MachineStatus.TERMINATED)
        assert terminated_machine.is_terminated is True

        shutting_down_machine = _make_machine(status=MachineStatus.SHUTTING_DOWN)
        assert shutting_down_machine.is_terminated is True

        running_machine = _make_machine(status=MachineStatus.RUNNING)
        assert running_machine.is_terminated is False

    def test_machine_is_healthy_property(self):
        """Test machine is_healthy property."""
        pending_machine = _make_machine(status=MachineStatus.PENDING)
        assert pending_machine.is_healthy is True

        running_machine = _make_machine(status=MachineStatus.RUNNING)
        assert running_machine.is_healthy is True

        failed_machine = _make_machine(status=MachineStatus.FAILED)
        assert failed_machine.is_healthy is False

    def test_machine_equality(self):
        """Test machine equality based on ID."""
        machine1 = _make_machine(machine_id="i-1234567890abcdef0")

        machine2 = Machine(
            id=machine1.id,
            machine_id=MachineId(value="i-abcdef1234567890"),
            template_id="template-002",
            request_id="request-002",
            provider_type="aws",
            provider_name="aws-us-west-2",
            provider_api="ec2fleet",
            instance_type=InstanceType(value="t2.small"),
            image_id="ami-87654321",
            status=MachineStatus.STOPPED,
        )

        machine3 = _make_machine(machine_id="i-bbbbbbbbbbbbbbbb")

        assert machine1 == machine2  # Same aggregate id
        assert machine1 != machine3  # Different id
        assert machine2 != machine3  # Different id

    def test_machine_hash(self):
        """Test machine hashing."""
        machine1 = _make_machine(machine_id="i-1234567890abcdef0")

        machine2 = Machine(
            id=machine1.id,
            machine_id=MachineId(value="i-different12345678"),
            template_id="template-different",
            request_id="request-different",
            provider_type="aws",
            provider_name="aws-us-west-2",
            provider_api="ec2fleet",
            instance_type=InstanceType(value="t2.large"),
            image_id="ami-different",
            status=MachineStatus.STOPPED,
        )

        assert hash(machine1) == hash(machine2)  # Same aggregate id -> same hash

    def test_machine_serialization(self):
        """Test machine serialization to dict."""
        machine = _make_machine(
            machine_id="i-1234567890abcdef0",
            status=MachineStatus.RUNNING,
        )

        machine_dict = machine.model_dump()

        assert machine_dict["template_id"] == "template-001"
        assert machine_dict["request_id"] == "request-001"
        assert machine_dict["provider_type"] == "aws"
        assert "created_at" in machine_dict

    def test_machine_deserialization(self):
        """Test machine deserialization from dict."""
        machine_dict = {
            "machine_id": MachineId(value="i-1234567890abcdef0"),
            "template_id": "template-001",
            "request_id": "request-001",
            "provider_type": "aws",
            "provider_name": "aws-us-east-1",
            "provider_api": "ec2fleet",
            "instance_type": InstanceType(value="t2.micro"),
            "image_id": "ami-12345678",
            "status": MachineStatus.RUNNING,
        }

        machine = Machine(**machine_dict)

        assert str(machine.machine_id) == "i-1234567890abcdef0"
        assert machine.template_id == "template-001"
        assert machine.status == MachineStatus.RUNNING

    def test_machine_string_representation(self):
        """Test machine string representation."""
        machine = _make_machine(machine_id="i-1234567890abcdef0")

        repr_str = repr(machine)
        assert "Machine" in repr_str or str(machine.machine_id) in repr_str

    def test_machine_domain_events(self):
        """Test machine domain events generation."""
        machine = _make_machine(status=MachineStatus.PENDING)

        machine.clear_domain_events()
        machine = machine.start_launching()

        events = machine.get_domain_events()
        assert len(events) > 0

    def test_machine_update_status_generates_event(self):
        """Test that update_status generates a domain event when status changes."""
        machine = _make_machine(status=MachineStatus.LAUNCHING)
        machine.clear_domain_events()

        machine = machine.update_status(MachineStatus.RUNNING)

        events = machine.get_domain_events()
        assert len(events) > 0

    def test_machine_update_status_no_event_when_same(self):
        """Test that update_status does not generate event when status unchanged."""
        machine = _make_machine(status=MachineStatus.RUNNING)
        machine.clear_domain_events()

        machine = machine.update_status(MachineStatus.RUNNING)

        events = machine.get_domain_events()
        assert len(events) == 0

    def test_machine_provisioned_event_fired_when_running_with_ip(self):
        """MachineProvisionedEvent appears in domain events after transitioning to RUNNING with an IP."""
        from orb.domain.base.events.domain_events import MachineProvisionedEvent

        machine = _make_machine(status=MachineStatus.LAUNCHING, private_ip="10.0.0.1")
        machine.clear_domain_events()

        machine = machine.update_status(MachineStatus.RUNNING)

        events = machine.get_domain_events()
        provisioned = [e for e in events if isinstance(e, MachineProvisionedEvent)]
        assert len(provisioned) == 1
        assert provisioned[0].machine_id == "i-1234567890abcdef0"
        assert provisioned[0].private_ip == "10.0.0.1"
        assert provisioned[0].public_ip is None
        assert provisioned[0].provisioning_time is not None

    def test_machine_provisioned_event_includes_public_ip(self):
        """MachineProvisionedEvent captures public IP when present."""
        from orb.domain.base.events.domain_events import MachineProvisionedEvent

        machine = _make_machine(
            status=MachineStatus.LAUNCHING,
            private_ip="10.0.0.2",
            public_ip="54.1.2.3",
        )
        machine.clear_domain_events()

        machine = machine.update_status(MachineStatus.RUNNING)

        events = machine.get_domain_events()
        provisioned = [e for e in events if isinstance(e, MachineProvisionedEvent)]
        assert len(provisioned) == 1
        assert provisioned[0].private_ip == "10.0.0.2"
        assert provisioned[0].public_ip == "54.1.2.3"

    def test_machine_provisioned_event_not_fired_without_ip(self):
        """MachineProvisionedEvent is NOT fired when machine has no IP assigned."""
        from orb.domain.base.events.domain_events import MachineProvisionedEvent

        machine = _make_machine(status=MachineStatus.LAUNCHING)
        machine.clear_domain_events()

        machine = machine.update_status(MachineStatus.RUNNING)

        events = machine.get_domain_events()
        provisioned = [e for e in events if isinstance(e, MachineProvisionedEvent)]
        assert len(provisioned) == 0

    def test_machine_provisioned_event_not_fired_for_non_running_transitions(self):
        """MachineProvisionedEvent is NOT fired for transitions that are not RUNNING."""
        from orb.domain.base.events.domain_events import MachineProvisionedEvent

        machine = _make_machine(status=MachineStatus.RUNNING, private_ip="10.0.0.3")
        machine.clear_domain_events()

        machine = machine.update_status(MachineStatus.SHUTTING_DOWN)

        events = machine.get_domain_events()
        provisioned = [e for e in events if isinstance(e, MachineProvisionedEvent)]
        assert len(provisioned) == 0


@pytest.mark.unit
class TestMachineProviderApiValidation:
    """provider_api must be a non-empty string at the domain boundary."""

    def test_empty_provider_api_raises_validation_error(self):
        """Machine.model_validate with provider_api='' must raise ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Machine.model_validate(
                {
                    "machine_id": "i-1234567890abcdef0",
                    "template_id": "template-001",
                    "provider_type": "aws",
                    "provider_name": "aws-us-east-1",
                    "provider_api": "",
                    "instance_type": "t2.micro",
                    "image_id": "ami-12345678",
                }
            )

    def test_missing_provider_api_raises_validation_error(self):
        """Machine.model_validate without provider_api must raise ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Machine.model_validate(
                {
                    "machine_id": "i-1234567890abcdef0",
                    "template_id": "template-001",
                    "provider_type": "aws",
                    "provider_name": "aws-us-east-1",
                    "instance_type": "t2.micro",
                    "image_id": "ami-12345678",
                }
            )

    def test_nonempty_provider_api_accepted(self):
        """Machine.model_validate with a non-empty provider_api must succeed."""
        machine = _make_machine(provider_api="EC2Fleet")
        assert machine.provider_api == "EC2Fleet"


@pytest.mark.unit
class TestMachineFromProviderFormat:
    """from_provider_format correctly maps provider_api and rejects missing values."""

    def _base_data(self) -> dict:
        return {
            "instance_id": "i-aabbccddeeff0011",
            "template_id": "template-001",
            "provider_name": "aws-us-east-1",
            "instance_type": "t3.medium",
            "image_id": "ami-deadbeef",
            "status": "running",
        }

    def test_from_provider_format_with_snake_case_provider_api(self):
        """from_provider_format with snake_case provider_api returns a valid Machine."""
        data = {**self._base_data(), "provider_api": "EC2Fleet"}
        machine = Machine.from_provider_format(data, provider_type="aws")
        assert machine.provider_api == "EC2Fleet"
        assert str(machine.machine_id) == "i-aabbccddeeff0011"

    def test_from_provider_format_with_camel_case_provider_api(self):
        """from_provider_format with camelCase providerApi returns a valid Machine."""
        data = {**self._base_data(), "providerApi": "RunInstances"}
        machine = Machine.from_provider_format(data, provider_type="aws")
        assert machine.provider_api == "RunInstances"

    def test_from_provider_format_without_provider_api_raises_value_error(self):
        """from_provider_format with neither provider_api nor providerApi raises ValueError."""
        data = self._base_data()  # no provider_api key at all
        with pytest.raises(ValueError, match="provider_api"):
            Machine.from_provider_format(data, provider_type="aws")

    def test_from_provider_format_with_camel_case_provider_name(self):
        """from_provider_format with camelCase providerName returns a valid Machine."""
        data = {**self._base_data(), "provider_api": "EC2Fleet"}
        del data["provider_name"]
        data["providerName"] = "aws-eu-west-2"
        machine = Machine.from_provider_format(data, provider_type="aws")
        assert machine.provider_name == "aws-eu-west-2"

    def test_from_provider_format_without_provider_name_raises_value_error(self):
        """from_provider_format with neither provider_name nor providerName raises ValueError."""
        data = self._base_data()
        del data["provider_name"]
        data["provider_api"] = "EC2Fleet"
        with pytest.raises(ValueError, match="provider_name"):
            Machine.from_provider_format(data, provider_type="aws")

    def test_from_provider_format_with_empty_provider_name_raises_value_error(self):
        """from_provider_format with empty provider_name raises ValueError."""
        data = {**self._base_data(), "provider_api": "EC2Fleet", "provider_name": ""}
        with pytest.raises(ValueError, match="provider_name"):
            Machine.from_provider_format(data, provider_type="aws")


@pytest.mark.unit
class TestMachineValueObjects:
    """Test cases for Machine-specific value objects."""

    def test_machine_id_creation(self):
        """Test MachineId creation."""
        machine_id = MachineId(value="machine-001")
        assert str(machine_id) == "machine-001"
        assert machine_id.value == "machine-001"

    def test_machine_status_enum(self):
        """Test MachineStatus enum."""
        assert MachineStatus.PENDING.value == "pending"
        assert MachineStatus.LAUNCHING.value == "launching"
        assert MachineStatus.RUNNING.value == "running"
        assert MachineStatus.STOPPING.value == "stopping"
        assert MachineStatus.STOPPED.value == "stopped"
        assert MachineStatus.SHUTTING_DOWN.value == "shutting-down"
        assert MachineStatus.TERMINATED.value == "terminated"
        assert MachineStatus.FAILED.value == "failed"

    def test_machine_status_terminal(self):
        """Test MachineStatus terminal states."""
        assert MachineStatus.TERMINATED.is_terminal is True
        assert MachineStatus.FAILED.is_terminal is True
        assert MachineStatus.RETURNED.is_terminal is True
        assert MachineStatus.RUNNING.is_terminal is False
        assert MachineStatus.PENDING.is_terminal is False

    def test_machine_status_active(self):
        """Test MachineStatus active states."""
        assert MachineStatus.PENDING.is_active is True
        assert MachineStatus.LAUNCHING.is_active is True
        assert MachineStatus.RUNNING.is_active is True
        assert MachineStatus.TERMINATED.is_active is False
        assert MachineStatus.FAILED.is_active is False

    def test_machine_status_transitions(self):
        """Test MachineStatus valid transition checks."""
        assert MachineStatus.PENDING.can_transition_to(MachineStatus.LAUNCHING) is True
        assert MachineStatus.PENDING.can_transition_to(MachineStatus.RUNNING) is False
        assert MachineStatus.LAUNCHING.can_transition_to(MachineStatus.RUNNING) is True
        assert MachineStatus.TERMINATED.can_transition_to(MachineStatus.RUNNING) is False


@pytest.mark.unit
class TestMachineExceptions:
    """Test cases for Machine-specific exceptions."""

    def test_machine_validation_error(self):
        """Test MachineValidationError."""
        error = MachineValidationError("Invalid machine configuration")
        assert str(error) == "Invalid machine configuration"
        assert isinstance(error, Exception)

    def test_machine_not_found_error(self):
        """Test MachineNotFoundError."""
        # Constructor signature: __init__(self, machine_id: str)
        error = MachineNotFoundError("machine-001")
        assert "machine-001" in str(error)

    def test_invalid_machine_state_error(self):
        """Test InvalidMachineStateError."""
        # Constructor signature: __init__(self, current_state: str, attempted_state: str)
        error = InvalidMachineStateError(
            current_state="terminated",
            attempted_state="running",
        )
        assert "Cannot transition" in str(error)
        assert error.details["current_state"] == "terminated"
        assert error.details["attempted_state"] == "running"
