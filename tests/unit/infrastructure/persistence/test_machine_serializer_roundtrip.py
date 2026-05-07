"""Round-trip test for MachineSerializer.

Guards against drift: if a field is added to Machine but not to
MachineSerializer.to_dict / from_dict, this test fails.
"""

from datetime import datetime, timezone

import pytest

from orb.domain.base.value_objects import InstanceType, Tags
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.machine_status import MachineStatus
from orb.infrastructure.storage.repositories.machine_repository import MachineSerializer


def _make_fully_populated_machine() -> Machine:
    """Build a Machine with every field set to a non-default, non-None value."""
    return Machine(
        # Core identification
        machine_id=MachineId(value="i-0abc123def456789a"),
        name="test-machine-full",
        template_id="tpl-roundtrip-001",
        request_id="req-00000000-0000-0000-0000-000000000001",
        return_request_id="req-00000000-0000-0000-0000-000000000002",
        provider_type="aws",
        provider_name="aws-us-east-1",
        provider_api="EC2Fleet",
        resource_id="fleet-0abc123def456789a",
        # Machine configuration
        instance_type=InstanceType(value="m5.large"),
        image_id="ami-0abc123def456789a",
        price_type="spot",
        # Network configuration
        private_ip="10.0.1.42",
        public_ip="54.1.2.3",
        private_dns_name="ip-10-0-1-42.ec2.internal",
        public_dns_name="ec2-54-1-2-3.compute-1.amazonaws.com",
        subnet_id="subnet-0abc123def456789a",
        security_group_ids=["sg-0abc123def456789a", "sg-0def456abc789012b"],
        vpc_id="vpc-0abc123def456789a",
        # Machine state
        status=MachineStatus.RUNNING,
        status_reason="Machine is healthy",
        # Lifecycle timestamps
        launch_time=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        provisioning_started_at=datetime(2026, 1, 15, 9, 59, 30, tzinfo=timezone.utc),
        termination_time=None,
        # Tags and metadata
        tags=Tags(tags={"Environment": "test", "Owner": "team-orb", "Project": "roundtrip"}),
        metadata={"custom_key": "custom_value", "region": "us-east-1"},
        # Provider-specific data
        provider_data={"fleet_id": "fleet-abc", "capacity_reservation": "cr-001"},
        # Versioning
        version=7,
        # Base entity timestamps
        created_at=datetime(2026, 1, 15, 9, 58, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 15, 10, 5, 0, tzinfo=timezone.utc),
    )


@pytest.mark.unit
@pytest.mark.infrastructure
class TestMachineSerializerRoundTrip:
    """MachineSerializer must preserve every Machine field through to_dict → from_dict."""

    def test_round_trip_preserves_all_fields(self):
        """If a field is added to Machine but not to MachineSerializer, this test fails.

        Note: vcpus, availability_zone, and region are migrated from metadata to
        provider_data on read (normalize_on_read migration). The round-trip therefore
        produces a Machine whose metadata no longer contains those keys and whose
        provider_data does. We compare the post-migration state, not the raw input.
        """
        machine = _make_fully_populated_machine()
        serializer = MachineSerializer()

        serialized = serializer.to_dict(machine)
        restored = serializer.from_dict(serialized)

        original_dump = machine.model_dump(mode="json")
        restored_dump = restored.model_dump(mode="json")

        # Keys migrated from metadata → provider_data on read; skip direct comparison
        # and verify the migration happened correctly instead.
        _migrated_keys = {"vcpus", "availability_zone", "region"}

        for field_name in original_dump:
            if field_name == "metadata":
                # After migration, migrated keys are removed from metadata
                expected_metadata = {
                    k: v for k, v in original_dump["metadata"].items() if k not in _migrated_keys
                }
                assert restored_dump["metadata"] == expected_metadata, (
                    f"Field 'metadata' mismatch after migration: "
                    f"{restored_dump['metadata']!r} != {expected_metadata!r}"
                )
            elif field_name == "provider_data":
                # After migration, migrated keys are added to provider_data
                expected_provider_data = dict(original_dump["provider_data"])
                for k in _migrated_keys:
                    if k in original_dump.get("metadata", {}):
                        expected_provider_data[k] = original_dump["metadata"][k]
                assert restored_dump["provider_data"] == expected_provider_data, (
                    f"Field 'provider_data' mismatch after migration: "
                    f"{restored_dump['provider_data']!r} != {expected_provider_data!r}"
                )
            else:
                assert original_dump[field_name] == restored_dump[field_name], (
                    f"Field '{field_name}' lost in round-trip: "
                    f"{original_dump[field_name]!r} != {restored_dump[field_name]!r}"
                )

    def test_round_trip_vpc_id(self):
        """vpc_id must survive serialization — it is a network field absent from early serializer versions."""
        machine = _make_fully_populated_machine()
        serializer = MachineSerializer()

        restored = serializer.from_dict(serializer.to_dict(machine))

        assert restored.vpc_id == machine.vpc_id

    def test_round_trip_provisioning_started_at(self):
        """provisioning_started_at must survive serialization — it records when ORB initiated the launch."""
        machine = _make_fully_populated_machine()
        serializer = MachineSerializer()

        restored = serializer.from_dict(serializer.to_dict(machine))

        assert restored.provisioning_started_at == machine.provisioning_started_at

    def test_serialized_dict_contains_vpc_id_key(self):
        """to_dict output must include a 'vpc_id' key so storage backends persist it."""
        machine = _make_fully_populated_machine()
        serialized = MachineSerializer().to_dict(machine)
        assert "vpc_id" in serialized, "to_dict is missing 'vpc_id'"
        assert serialized["vpc_id"] == "vpc-0abc123def456789a"

    def test_serialized_dict_contains_provisioning_started_at_key(self):
        """to_dict output must include a 'provisioning_started_at' key so storage backends persist it."""
        machine = _make_fully_populated_machine()
        serialized = MachineSerializer().to_dict(machine)
        assert "provisioning_started_at" in serialized, (
            "to_dict is missing 'provisioning_started_at'"
        )

    def test_round_trip_with_none_optional_fields(self):
        """Optional fields set to None must also survive the round-trip without becoming something else."""
        machine = Machine(
            machine_id=MachineId(value="i-minimal000000001"),
            template_id="tpl-minimal",
            provider_type="aws",
            provider_name="aws-us-east-1",
            instance_type=InstanceType(value="t3.micro"),
            image_id="ami-00000000",
            status=MachineStatus.PENDING,
        )
        serializer = MachineSerializer()
        restored = serializer.from_dict(serializer.to_dict(machine))

        # All optional network fields should remain None
        assert restored.vpc_id is None
        assert restored.provisioning_started_at is None
        assert restored.private_ip is None
        assert restored.public_ip is None
        assert restored.subnet_id is None
        assert restored.return_request_id is None
