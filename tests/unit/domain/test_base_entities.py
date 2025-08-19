"""Unit tests for base domain entities."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError as PydanticValidationError

from domain.base.entity import Entity
from domain.base.exceptions import DomainException, ValidationError
from domain.base.value_objects import (
    InstanceId,
    InstanceType,
    IPAddress,
    ResourceId,
    Tags,
)


class MockEntity(Entity):
    """Test entity for testing base Entity functionality."""

    name: str
    value: int = 0


@pytest.mark.unit
class TestBaseEntity:
    """Test cases for base Entity class."""

    def test_entity_creation(self):
        """Test basic entity creation."""
        entity = MockEntity(id="test-1", name="Test Entity", value=42)

        assert entity.id == "test-1"
        assert entity.name == "Test Entity"
        assert entity.value == 42
        assert entity.created_at is None  # Not set by default
        assert entity.updated_at is None  # Not set by default

    def test_entity_equality(self):
        """Test entity equality based on ID and type."""
        entity1 = MockEntity(id="test-1", name="Entity 1")
        entity2 = MockEntity(id="test-1", name="Entity 2")  # Different name, same ID
        entity3 = MockEntity(id="test-2", name="Entity 1")  # Same name, different ID

        assert entity1 == entity2  # Same ID and type
        assert entity1 != entity3  # Different ID
        assert entity2 != entity3  # Different ID

    def test_entity_equality_different_types(self):
        """Test entity equality with different types."""

        class OtherEntity(Entity):
            name: str

        entity1 = MockEntity(id="test-1", name="Test")
        entity2 = OtherEntity(id="test-1", name="Test")

        assert entity1 != entity2  # Different types

    def test_entity_hash(self):
        """Test entity hashing based on ID and type."""
        entity1 = MockEntity(id="test-1", name="Entity 1")
        entity2 = MockEntity(id="test-1", name="Entity 2")
        entity3 = MockEntity(id="test-2", name="Entity 1")

        assert hash(entity1) == hash(entity2)  # Same ID and type
        assert hash(entity1) != hash(entity3)  # Different ID

    def test_entity_validation(self):
        """Test entity validation."""
        # Valid entity
        entity = MockEntity(id="test-1", name="Valid Entity")
        assert entity.name == "Valid Entity"

        # Test validation on assignment
        entity.name = "Updated Name"
        assert entity.name == "Updated Name"

    def test_entity_with_timestamps(self):
        """Test entity with timestamp fields."""
        now = datetime.now(timezone.utc)
        entity = MockEntity(id="test-1", name="Timestamped Entity", created_at=now, updated_at=now)

        assert entity.created_at == now
        assert entity.updated_at == now

    def test_entity_none_id(self):
        """Test entity with None ID."""
        entity = MockEntity(name="No ID Entity")
        assert entity.id is None

        # Entities with None ID should not be equal
        entity2 = MockEntity(name="Another No ID Entity")
        assert entity != entity2


@pytest.mark.unit
class TestValueObjects:
    """Test cases for domain value objects."""

    def test_instance_id_creation(self):
        """Test InstanceId value object creation."""
        instance_id = InstanceId(value="i-1234567890abcdef0")
        assert str(instance_id) == "i-1234567890abcdef0"
        assert instance_id.value == "i-1234567890abcdef0"

    def test_instance_id_validation(self):
        """Test InstanceId validation."""
        # Valid instance IDs
        valid_ids = ["i-1234567890abcdef0", "i-abcdef1234567890", "i-0123456789abcdef0"]

        for valid_id in valid_ids:
            instance_id = InstanceId(value=valid_id)
            assert instance_id.value == valid_id

    def test_instance_id_invalid(self):
        """Test InstanceId with invalid values."""
        invalid_ids = [
            "",  # Empty string
            "   ",  # Whitespace only
        ]

        for invalid_id in invalid_ids:
            with pytest.raises((ValueError, ValidationError, PydanticValidationError)):
                InstanceId(value=invalid_id)

    def test_instance_type_creation(self):
        """Test InstanceType value object creation."""
        instance_type = InstanceType(value="t2.micro")
        assert str(instance_type) == "t2.micro"
        assert instance_type.value == "t2.micro"

    def test_instance_type_validation(self):
        """Test InstanceType validation."""
        # Valid instance types
        valid_types = [
            "t2.micro",
            "t2.small",
            "t2.medium",
            "t2.large",
            "t3.micro",
            "t3.small",
            "t3.medium",
            "t3.large",
            "m5.large",
            "m5.xlarge",
            "m5.2xlarge",
            "c5.large",
            "c5.xlarge",
            "c5.2xlarge",
            "r5.large",
            "r5.xlarge",
            "r5.2xlarge",
        ]

        for valid_type in valid_types:
            instance_type = InstanceType(value=valid_type)
            assert instance_type.value == valid_type

    def test_instance_type_invalid(self):
        """Test InstanceType with invalid values."""
        invalid_types = [
            "",  # Empty string
            "   ",  # Whitespace only
        ]

        for invalid_type in invalid_types:
            with pytest.raises((ValueError, ValidationError, PydanticValidationError)):
                InstanceType(value=invalid_type)

    def test_resource_id_creation(self):
        """Test ResourceId value object creation."""
        resource_id = ResourceId(value="resource-123")
        assert str(resource_id) == "resource-123"
        assert resource_id.value == "resource-123"

    def test_resource_id_validation(self):
        """Test ResourceId validation."""
        # Valid resource IDs
        valid_ids = [
            "resource-123",
            "res-456",
            "r-789",
            "subnet-abc123",
            "sg-def456",
            "vpc-ghi789",
        ]

        for valid_id in valid_ids:
            resource_id = ResourceId(value=valid_id)
            assert resource_id.value == valid_id

    def test_resource_id_invalid(self):
        """Test ResourceId with invalid values."""
        invalid_ids = [
            "",
            " ",
            "   ",
        ]

        for invalid_id in invalid_ids:
            with pytest.raises((ValueError, ValidationError, PydanticValidationError)):
                ResourceId(value=invalid_id)

    def test_tags_creation(self):
        """Test Tags value object creation."""
        tag_dict = {"Environment": "test", "Project": "hostfactory"}
        tags = Tags(tags=tag_dict)

        assert tags.tags == tag_dict
        assert tags.get("Environment") == "test"
        assert tags.get("Project") == "hostfactory"

    def test_tags_operations(self):
        """Test Tags operations."""
        tags = Tags(tags={"Environment": "test"})

        # Test get
        assert tags.get("Environment") == "test"
        assert tags.get("NonExistent") is None
        assert tags.get("NonExistent", "default") == "default"

        # Test contains (using get method)
        assert tags.get("Environment") is not None
        assert tags.get("NonExistent") is None

        # Test underlying tags dict
        assert "Environment" in tags.tags
        assert "test" in tags.tags.values()

        # Test items through underlying dict
        items = list(tags.tags.items())
        assert ("Environment", "test") in items

    def test_tags_validation(self):
        """Test Tags validation."""
        # Valid tags
        valid_tags = [
            {"Environment": "test"},
            {"Project": "hostfactory", "Owner": "team"},
            {},  # Empty tags are valid
        ]

        for valid_tag in valid_tags:
            tags = Tags(tags=valid_tag)
            assert tags.tags == valid_tag

    def test_ip_address_creation(self):
        """Test IPAddress value object creation."""
        # IPv4 addresses
        ipv4 = IPAddress(value="192.168.1.1")
        assert str(ipv4) == "192.168.1.1"
        assert ipv4.value == "192.168.1.1"

        # IPv6 addresses
        ipv6 = IPAddress(value="2001:db8::1")
        assert str(ipv6) == "2001:db8::1"
        assert ipv6.value == "2001:db8::1"

    def test_ip_address_validation(self):
        """Test IPAddress validation."""
        # Valid IP addresses
        valid_ips = [
            "192.168.1.1",
            "10.0.0.1",
            "172.16.0.1",
            "127.0.0.1",
            "0.0.0.0",
            "255.255.255.255",
            "2001:db8::1",
            "::1",
            "fe80::1",
        ]

        for valid_ip in valid_ips:
            ip_addr = IPAddress(value=valid_ip)
            assert ip_addr.value == valid_ip

    def test_ip_address_invalid(self):
        """Test IPAddress with invalid values."""
        invalid_ips = [
            "",
            "invalid-ip",
            "256.256.256.256",
            "192.168.1",
            "192.168.1.1.1",
            "gggg::1",
        ]

        for invalid_ip in invalid_ips:
            with pytest.raises((ValueError, ValidationError)):
                IPAddress(value=invalid_ip)

    # TODO: AvailabilityZone tests - class not yet implemented
    # noqa:COMMENTED section-start
    # def test_availability_zone_creation(self):
    #     """Test AvailabilityZone value object creation."""
    #     az = AvailabilityZone("us-east-1a")
    #     assert str(az) == "us-east-1a"
    #     assert az.value == "us-east-1a"

    # TODO: AvailabilityZone tests - class not yet implemented
    # def test_availability_zone_validation(self):
    #     """Test AvailabilityZone validation."""
    #     # Valid availability zones
    #     valid_azs = [
    #         "us-east-1a", "us-east-1b", "us-east-1c",
    #         "us-west-2a", "us-west-2b", "us-west-2c",
    #         "eu-west-1a", "eu-west-1b", "eu-west-1c",
    #         "ap-southeast-1a", "ap-southeast-1b",
    #     ]
    #
    #     for valid_az in valid_azs:
    #         az = AvailabilityZone(valid_az)
    #         assert az.value == valid_az

    # def test_availability_zone_invalid(self):
    #     """Test AvailabilityZone with invalid values."""
    #     invalid_azs = [
    #         "",
    #         "invalid-az",
    #         "us-east-1",  # Missing zone letter
    #         "us-east-1aa",  # Invalid zone letter
    #         "invalid-region-1a",
    #     ]
    #
    #     for invalid_az in invalid_azs:
    #         with pytest.raises((ValueError, ValidationError)):
    #             AvailabilityZone(invalid_az)
    # noqa:COMMENTED section-end


@pytest.mark.unit
class TestDomainExceptions:
    """Test cases for domain exceptions."""

    def test_domain_exception_creation(self):
        """Test DomainException creation."""
        exception = DomainException("Test domain error")
        assert str(exception) == "Test domain error"
        assert exception.args == ("Test domain error",)

    def test_validation_error_creation(self):
        """Test ValidationError creation."""
        error = ValidationError("Invalid value")
        assert str(error) == "Invalid value"
        assert error.message == "Invalid value"

    def test_validation_error_with_details(self):
        """Test ValidationError with details."""
        error = ValidationError("Invalid value", details={"field": "test_field"})
        assert str(error) == "Invalid value"
        assert error.details["field"] == "test_field"

    def test_exception_inheritance(self):
        """Test exception inheritance hierarchy."""
        validation_error = ValidationError("Test error")

        assert isinstance(validation_error, ValidationError)
        assert isinstance(validation_error, DomainException)
        assert isinstance(validation_error, Exception)


@pytest.mark.unit
class TestValueObjectEquality:
    """Test value object equality and immutability."""

    def test_value_object_equality(self):
        """Test that value objects with same values are equal."""
        id1 = InstanceId(value="i-1234567890abcdef0")
        id2 = InstanceId(value="i-1234567890abcdef0")
        id3 = InstanceId(value="i-abcdef1234567890")

        assert id1 == id2
        assert id1 != id3
        assert id2 != id3

    def test_value_object_hash(self):
        """Test that value objects with same values have same hash."""
        id1 = InstanceId(value="i-1234567890abcdef0")
        id2 = InstanceId(value="i-1234567890abcdef0")
        id3 = InstanceId(value="i-abcdef1234567890")

        assert hash(id1) == hash(id2)
        assert hash(id1) != hash(id3)

    def test_value_object_immutability(self):
        """Test that value objects are immutable."""
        instance_id = InstanceId(value="i-1234567890abcdef0")

        # Should not be able to modify the value (Pydantic frozen models raise ValidationError)
        from pydantic_core import ValidationError
        with pytest.raises(ValidationError):
            instance_id.value = "i-new-value"

    def test_different_value_object_types(self):
        """Test that different value object types are not equal."""
        instance_id = InstanceId(value="i-1234567890abcdef0")
        resource_id = ResourceId(value="i-1234567890abcdef0")

        # Even with same underlying value, different types should not be equal
        assert instance_id != resource_id
        assert hash(instance_id) == hash(resource_id)
        # Note: InstanceId inherits from ResourceId, so they have the same hash implementation
        # This is expected behavior for inheritance hierarchies


@pytest.mark.unit
class TestValueObjectStringRepresentation:
    """Test string representation of value objects."""

    def test_instance_id_str(self):
        """Test InstanceId string representation."""
        instance_id = InstanceId(value="i-1234567890abcdef0")
        assert str(instance_id) == "i-1234567890abcdef0"
        assert "InstanceId" in repr(instance_id)
        assert "i-1234567890abcdef0" in repr(instance_id)

    def test_instance_type_str(self):
        """Test InstanceType string representation."""
        instance_type = InstanceType(value="t2.micro")
        assert str(instance_type) == "t2.micro"
        assert "InstanceType" in repr(instance_type)
        assert "t2.micro" in repr(instance_type)

    def test_tags_str(self):
        """Test Tags string representation."""
        tags = Tags(tags={"Environment": "test", "Project": "hostfactory"})
        str_repr = str(tags)
        assert "Environment" in str_repr
        assert "test" in str_repr
        assert "Project" in str_repr
        assert "hostfactory" in str_repr
