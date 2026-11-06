"""Repository serializers round-trip every aggregate field."""

from orb.domain.base.value_objects import Tags
from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.storage.repositories.machine_repository import MachineSerializer
from orb.infrastructure.storage.repositories.request_repository import RequestSerializer
from orb.infrastructure.storage.repositories.template_repository import TemplateSerializer


def test_template_serializer_round_trip():
    """A template survives to_dict/from_dict with every field intact."""
    template = Template.model_validate(
        {
            "template_id": "test-template",
            "name": "Test Template",
            "description": "Test template with additional fields",
            "machine_image": "img-123456",
            "machine_types": {"t2.micro": 1, "t2.small": 2},
            "max_machines": 5,
            "subnet_ids": ["subnet-123"],
            "network_zones": ["subnet-123", "subnet-456"],
            "machine_disk_size_gb": 20,
            "storage_encryption": True,
            "machine_ssh_key": "my-key",
            "monitoring_enabled": True,
            "price_type": "spot",
            "allocation_strategy": "capacity_optimized",
            "provider_type": "aws",
            "provider_name": "aws-primary",
        }
    )
    serializer = TemplateSerializer()

    serialized = serializer.to_dict(template)
    restored = serializer.from_dict(serialized)

    assert serialized["schema_version"] == "2.0.0"
    assert serialized["machine_types"] == {"t2.micro": 1, "t2.small": 2}
    assert serialized["network_zones"] == ["subnet-123", "subnet-456"]
    assert serialized["storage_encryption"] is True
    assert restored.model_dump() == template.model_dump()


def test_request_serializer_round_trip():
    """A request with provider tracking fields survives to_dict/from_dict."""
    request = Request(
        request_id=RequestId.generate(RequestType.ACQUIRE),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="test-template",
        requested_count=3,
        provider_name="aws-primary",
        provider_api="SpotFleet",
        resource_ids=["fleet-123", "fleet-456"],
        successful_count=2,
        failed_count=1,
        error_details={"error_1": "Instance launch failed"},
        provider_data={"fleet_config": "spot_optimized"},
    )
    serializer = RequestSerializer()

    serialized = serializer.to_dict(request)
    restored = serializer.from_dict(serialized)

    assert serialized["provider_name"] == "aws-primary"
    assert serialized["provider_api"] == "SpotFleet"
    assert serialized["resource_ids"] == ["fleet-123", "fleet-456"]
    assert restored.model_dump() == request.model_dump()


def test_machine_serializer_round_trip():
    """A machine with network and metadata fields survives to_dict/from_dict."""
    machine = Machine(
        machine_id="i-123456789",
        template_id="test-template",
        request_id="req-123",
        provider_type="aws",
        provider_name="aws-primary",
        provider_api="SpotFleet",
        instance_type="t2.micro",
        image_id="ami-123456",
        private_ip="10.0.1.100",
        public_ip="54.123.45.67",
        subnet_id="subnet-123",
        security_group_ids=["sg-123", "sg-456"],
        tags=Tags.from_dict({"Name": "test-machine", "Environment": "test"}),
        metadata={"launch_reason": "automated_test"},
        provider_data={"spot_price": "0.05", "availability_zone": "us-west-2a"},
    )
    serializer = MachineSerializer()

    serialized = serializer.to_dict(machine)
    restored = serializer.from_dict(serialized)

    assert serialized["private_ip"] == "10.0.1.100"
    assert serialized["public_ip"] == "54.123.45.67"
    assert restored.model_dump() == machine.model_dump()
    assert dict(restored.tags.tags) == {"Name": "test-machine", "Environment": "test"}
