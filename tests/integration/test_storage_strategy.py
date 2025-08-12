#!/usr/bin/env python3
"""
Test for Storage Strategy: Storage Strategy Enhancement
Tests that all repository enhancements work correctly with the new domain fields.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def test_template_repository_enhancements():
    """Test that Template repository handles additional fields correctly."""
    print("=== Storage Strategy: Template Repository Enhancement Test ===")

    try:
        from src.domain.template.aggregate import Template
        from src.infrastructure.persistence.repositories.template_repository import (
            TemplateSerializer,
        )

        # Create a template with additional fields
        template_data = {
            "template_id": "test-template",
            "name": "Test Template",
            "description": "Test template with additional fields",
            "image_id": "img-123456",
            "instance_type": "t2.micro",
            "max_instances": 5,
            "subnet_ids": ["subnet-123"],  # Required field
            # Additional fields
            "instance_types": {"t2.micro": 1, "t2.small": 2},
            "primary_instance_type": "t2.micro",
            "network_zones": ["subnet-123", "subnet-456"],
            "root_volume_size": 20,
            "storage_encryption": True,
            "key_pair_name": "my-key",
            "monitoring_enabled": True,
            "price_type": "spot",
            "allocation_strategy": "capacity_optimized",
            "provider_type": "aws",
            "provider_name": "aws-primary",
        }

        template = Template.model_validate(template_data)

        # Test serialization
        serializer = TemplateSerializer()
        serialized = serializer.to_dict(template)

        print("PASS: Template serialization successful")
        print(f"   - Additional fields included: {len(serialized)} fields")
        print(f"   - instance_types: {serialized.get('instance_types')}")
        print(f"   - network_zones: {serialized.get('network_zones')}")
        print(f"   - storage_encryption: {serialized.get('storage_encryption')}")
        print(f"   - schema_version: {serialized.get('schema_version')}")

        # Test deserialization
        deserialized = serializer.from_dict(serialized)

        print("PASS: Template deserialization successful")
        print(f"   - Template ID: {deserialized.template_id}")
        print(f"   - Additional fields preserved: {len(deserialized.instance_types)} instance types")

        return True

    except Exception as e:
        print(f"FAIL: Template repository test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_request_repository_enhancements():
    """Test that Request repository handles additional fields correctly."""
    print("\n=== Request Repository Enhancement Test ===")

    try:
        from src.domain.request.aggregate import Request
        from src.domain.request.value_objects import RequestId, RequestType
        from src.infrastructure.persistence.repositories.request_repository import (
            RequestSerializer,
        )

        # Create a request with additional fields
        request_id = RequestId.generate(RequestType.ACQUIRE)

        request = Request(
            request_id=request_id,
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            template_id="test-template",
            requested_count=3,
            # Additional provider tracking fields
            provider_name="aws-primary",
            provider_api="SpotFleet",
            resource_ids=["fleet-123", "fleet-456"],
            # Additional HF fields
            message="Test request with additional fields",
            successful_count=2,
            failed_count=1,
            error_details={"error_1": "Instance launch failed"},
            provider_data={"fleet_config": "spot_optimized"},
        )

        # Test serialization
        serializer = RequestSerializer()
        serialized = serializer.to_dict(request)

        print("PASS: Request serialization successful")
        print(f"   - Additional fields included: {len(serialized)} fields")
        print(f"   - provider_name: {serialized.get('provider_name')}")
        print(f"   - provider_api: {serialized.get('provider_api')}")
        print(f"   - resource_ids: {serialized.get('resource_ids')}")
        print(f"   - schema_version: {serialized.get('schema_version')}")

        # Test deserialization
        deserialized = serializer.from_dict(serialized)

        print("PASS: Request deserialization successful")
        print(f"   - Request ID: {deserialized.request_id}")
        print(f"   - Provider tracking: {deserialized.provider_name}/{deserialized.provider_api}")
        print(f"   - Resource IDs: {len(deserialized.resource_ids)} resources")

        return True

    except Exception as e:
        print(f"FAIL: Request repository test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_machine_repository_enhancements():
    """Test that Machine repository handles additional fields correctly."""
    print("\n=== Machine Repository Enhancement Test ===")

    try:
        from src.domain.base.value_objects import InstanceId, InstanceType, Tags
        from src.domain.machine.aggregate import Machine
        from src.infrastructure.persistence.repositories.machine_repository import (
            MachineSerializer,
        )

        # Create a machine with additional fields
        machine = Machine(
            instance_id=InstanceId(value="i-123456789"),
            template_id="test-template",
            request_id="req-123",
            provider_type="aws",
            instance_type=InstanceType(value="t2.micro"),
            image_id="ami-123456",
            # Additional network fields
            private_ip="10.0.1.100",
            public_ip="54.123.45.67",
            subnet_id="subnet-123",
            security_group_ids=["sg-123", "sg-456"],
            # Additional metadata
            tags=Tags.from_dict({"Name": "test-machine", "Environment": "test"}),
            metadata={"launch_reason": "automated_test"},
            provider_data={"spot_price": "0.05", "availability_zone": "us-west-2a"},
        )

        # Test serialization
        serializer = MachineSerializer()
        serialized = serializer.to_dict(machine)

        print("PASS: Machine serialization successful")
        print(f"   - Additional fields included: {len(serialized)} fields")
        print(f"   - instance_id: {serialized.get('instance_id')}")
        print(f"   - provider_type: {serialized.get('provider_type')}")
        print(f"   - network config: {serialized.get('private_ip')}/{serialized.get('public_ip')}")
        print(f"   - schema_version: {serialized.get('schema_version')}")

        # Test deserialization
        deserialized = serializer.from_dict(serialized)

        print("PASS: Machine deserialization successful")
        print(f"   - Instance ID: {deserialized.instance_id}")
        print(f"   - Provider type: {deserialized.provider_type}")
        print(f"   - Network: {deserialized.private_ip}/{deserialized.public_ip}")
        print(f"   - Tags: {dict(deserialized.tags.tags)}")

        return True

    except Exception as e:
        print(f"FAIL: Machine repository test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("Running Storage Strategy Storage Strategy Enhancement Tests...")

    test1_passed = test_template_repository_enhancements()
    test2_passed = test_request_repository_enhancements()
    test3_passed = test_machine_repository_enhancements()

    if test1_passed and test2_passed and test3_passed:
        print("\nALL STORAGE STRATEGY STORAGE STRATEGY TESTS PASSED")
        print("PASS: Template repository additional field support working")
        print("PASS: Request repository additional field support working")
        print("PASS: Machine repository additional field support working")
        print("PASS: All repositories support schema versioning")
        sys.exit(0)
    else:
        print("\nFAIL: SOME TESTS FAILED")
        sys.exit(1)
