"""Unit tests for Template aggregate."""

from datetime import datetime, timezone

import pytest

from domain.base.value_objects import InstanceType
from domain.template.aggregate import Template
from domain.template.exceptions import TemplateNotFoundError, TemplateValidationError
from domain.template.value_objects import TemplateId

# Try to import optional classes - create mocks if not available
try:
    from domain.template.value_objects import TemplateName

    TEMPLATE_NAME_AVAILABLE = True
except ImportError:
    TEMPLATE_NAME_AVAILABLE = False

    class TemplateName:
        def __init__(self, value):
            if not isinstance(value, str) or len(value.strip()) == 0:
                raise ValueError("Invalid template name")
            self.value = value.strip()


@pytest.mark.unit
class TestTemplateAggregate:
    """Test cases for Template aggregate."""

    def test_template_creation(self):
        """Test basic template creation."""
        template = Template(
            id="template-001",
            name="test-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
            key_name="test-key",
            user_data="#!/bin/bash\necho 'Hello World'",
            tags={"Environment": "test", "Project": "hostfactory"},
        )

        assert template.id == "template-001"
        assert template.name == "test-template"
        assert template.provider_api == "ec2_fleet"
        assert template.image_id == "ami-12345678"
        assert template.instance_type.value == "t2.micro"
        assert template.subnet_ids == ["subnet-12345678"]
        assert template.security_group_ids == ["sg-12345678"]
        assert template.key_name == "test-key"
        assert "Hello World" in template.user_data
        assert template.tags["Environment"] == "test"
        assert template.tags["Project"] == "hostfactory"

    def test_template_with_minimal_data(self):
        """Test template creation with minimal required data."""
        template = Template(
            id="template-002",
            name="minimal-template",
            provider_api="run_instances",
            image_id="ami-87654321",
            instance_type=InstanceType("t3.small"),
            subnet_ids=["subnet-87654321"],
            security_group_ids=["sg-87654321"],
        )

        assert template.id == "template-002"
        assert template.name == "minimal-template"
        assert template.provider_api == "run_instances"
        assert template.key_name is None
        assert template.user_data is None
        assert template.tags == {}

    def test_template_validation_valid_provider_apis(self):
        """Test template validation with valid provider APIs."""
        valid_apis = ["ec2_fleet", "auto_scaling_group", "spot_fleet", "run_instances"]

        for api in valid_apis:
            template = Template(
                id=f"template-{api}",
                name=f"template-{api}",
                provider_api=api,
                image_id="ami-12345678",
                instance_type=InstanceType("t2.micro"),
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
            )
            assert template.provider_api == api

    def test_template_validation_invalid_provider_api(self):
        """Test template validation with invalid provider API."""
        with pytest.raises((ValueError, TemplateValidationError)):
            Template(
                id="template-invalid",
                name="invalid-template",
                provider_api="invalid_api",
                image_id="ami-12345678",
                instance_type=InstanceType("t2.micro"),
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
            )

    def test_template_validation_empty_subnet_ids(self):
        """Test template validation with empty subnet IDs."""
        with pytest.raises((ValueError, TemplateValidationError)):
            Template(
                id="template-no-subnets",
                name="no-subnets-template",
                provider_api="ec2_fleet",
                image_id="ami-12345678",
                instance_type=InstanceType("t2.micro"),
                subnet_ids=[],  # Empty list should be invalid
                security_group_ids=["sg-12345678"],
            )

    def test_template_validation_empty_security_group_ids(self):
        """Test template validation with empty security group IDs."""
        with pytest.raises((ValueError, TemplateValidationError)):
            Template(
                id="template-no-sgs",
                name="no-sgs-template",
                provider_api="ec2_fleet",
                image_id="ami-12345678",
                instance_type=InstanceType("t2.micro"),
                subnet_ids=["subnet-12345678"],
                security_group_ids=[],  # Empty list should be invalid
            )

    def test_template_validation_invalid_ami_id(self):
        """Test template validation with invalid AMI ID."""
        invalid_ami_ids = ["", "invalid-ami", "ami-", "ami-123"]

        for invalid_ami in invalid_ami_ids:
            with pytest.raises((ValueError, TemplateValidationError)):
                Template(
                    id="template-invalid-ami",
                    name="invalid-ami-template",
                    provider_api="ec2_fleet",
                    image_id=invalid_ami,
                    instance_type=InstanceType("t2.micro"),
                    subnet_ids=["subnet-12345678"],
                    security_group_ids=["sg-12345678"],
                )

    def test_template_update_fields(self):
        """Test updating template fields."""
        template = Template(
            id="template-update",
            name="original-name",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
        )

        # Update name
        template.name = "updated-name"
        assert template.name == "updated-name"

        # Update instance type
        template.instance_type = InstanceType("t2.small")
        assert template.instance_type.value == "t2.small"

        # Update tags
        template.tags = {"Environment": "production", "Owner": "team"}
        assert template.tags["Environment"] == "production"
        assert template.tags["Owner"] == "team"

    def test_template_add_subnet(self):
        """Test adding subnet to template."""
        template = Template(
            id="template-add-subnet",
            name="add-subnet-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
        )

        # Add subnet
        template.subnet_ids.append("subnet-87654321")
        assert "subnet-87654321" in template.subnet_ids
        assert len(template.subnet_ids) == 2

    def test_template_add_security_group(self):
        """Test adding security group to template."""
        template = Template(
            id="template-add-sg",
            name="add-sg-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
        )

        # Add security group
        template.security_group_ids.append("sg-87654321")
        assert "sg-87654321" in template.security_group_ids
        assert len(template.security_group_ids) == 2

    def test_template_user_data_encoding(self):
        """Test template user data with different encodings."""
        user_data_scripts = [
            "#!/bin/bash\necho 'Hello World'",
            "#!/bin/bash\nyum update -y\nyum install -y docker",
            "#cloud-config\npackages:\n  - docker\n  - git",
            "",  # Empty user data
        ]

        for user_data in user_data_scripts:
            template = Template(
                id=f"template-userdata-{len(user_data)}",
                name="userdata-template",
                provider_api="ec2_fleet",
                image_id="ami-12345678",
                instance_type=InstanceType("t2.micro"),
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                user_data=user_data if user_data else None,
            )
            assert template.user_data == (user_data if user_data else None)

    def test_template_tags_operations(self):
        """Test template tags operations."""
        template = Template(
            id="template-tags",
            name="tags-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
            tags={"Environment": "test"},
        )

        # Add tag
        template.tags["Project"] = "hostfactory"
        assert template.tags["Project"] == "hostfactory"

        # Update tag
        template.tags["Environment"] = "production"
        assert template.tags["Environment"] == "production"

        # Remove tag
        del template.tags["Environment"]
        assert "Environment" not in template.tags
        assert "Project" in template.tags

    def test_template_equality(self):
        """Test template equality based on ID."""
        template1 = Template(
            id="template-001",
            name="template-1",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
        )

        template2 = Template(
            id="template-001",  # Same ID
            name="template-2",  # Different name
            provider_api="run_instances",  # Different API
            image_id="ami-87654321",  # Different AMI
            instance_type=InstanceType("t2.small"),  # Different instance type
            subnet_ids=["subnet-87654321"],  # Different subnet
            security_group_ids=["sg-87654321"],  # Different security group
        )

        template3 = Template(
            id="template-002",  # Different ID
            name="template-1",  # Same name as template1
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
        )

        assert template1 == template2  # Same ID
        assert template1 != template3  # Different ID
        assert template2 != template3  # Different ID

    def test_template_hash(self):
        """Test template hashing."""
        template1 = Template(
            id="template-001",
            name="template-1",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
        )

        template2 = Template(
            id="template-001",  # Same ID
            name="different-name",
            provider_api="run_instances",
            image_id="ami-87654321",
            instance_type=InstanceType("t2.small"),
            subnet_ids=["subnet-87654321"],
            security_group_ids=["sg-87654321"],
        )

        assert hash(template1) == hash(template2)  # Same ID should have same hash

    def test_template_string_representation(self):
        """Test template string representation."""
        template = Template(
            id="template-001",
            name="test-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
        )

        str_repr = str(template)
        assert "template-001" in str_repr
        assert "test-template" in str_repr

        repr_str = repr(template)
        assert "Template" in repr_str
        assert "template-001" in repr_str

    def test_template_serialization(self):
        """Test template serialization to dict."""
        template = Template(
            id="template-001",
            name="test-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
            key_name="test-key",
            user_data="#!/bin/bash\necho 'test'",
            tags={"Environment": "test"},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        # Test model_dump (Pydantic v2)
        template_dict = template.model_dump()

        assert template_dict["id"] == "template-001"
        assert template_dict["name"] == "test-template"
        assert template_dict["provider_api"] == "ec2_fleet"
        assert template_dict["image_id"] == "ami-12345678"
        assert template_dict["instance_type"] == "t2.micro"
        assert template_dict["subnet_ids"] == ["subnet-12345678"]
        assert template_dict["security_group_ids"] == ["sg-12345678"]
        assert template_dict["key_name"] == "test-key"
        assert template_dict["user_data"] == "#!/bin/bash\necho 'test'"
        assert template_dict["tags"] == {"Environment": "test"}
        assert "created_at" in template_dict
        assert "updated_at" in template_dict

    def test_template_deserialization(self):
        """Test template deserialization from dict."""
        template_dict = {
            "id": "template-001",
            "name": "test-template",
            "provider_api": "ec2_fleet",
            "image_id": "ami-12345678",
            "instance_type": "t2.micro",
            "subnet_ids": ["subnet-12345678"],
            "security_group_ids": ["sg-12345678"],
            "key_name": "test-key",
            "user_data": "#!/bin/bash\necho 'test'",
            "tags": {"Environment": "test"},
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z",
        }

        template = Template(**template_dict)

        assert template.id == "template-001"
        assert template.name == "test-template"
        assert template.provider_api == "ec2_fleet"
        assert template.image_id == "ami-12345678"
        assert template.instance_type.value == "t2.micro"
        assert template.subnet_ids == ["subnet-12345678"]
        assert template.security_group_ids == ["sg-12345678"]
        assert template.key_name == "test-key"
        assert template.user_data == "#!/bin/bash\necho 'test'"
        assert template.tags == {"Environment": "test"}

    def test_template_validation_comprehensive(self):
        """Test comprehensive template validation."""
        # Test all required fields
        required_fields = [
            "id",
            "name",
            "provider_api",
            "image_id",
            "instance_type",
            "subnet_ids",
            "security_group_ids",
        ]

        base_template_data = {
            "id": "template-001",
            "name": "test-template",
            "provider_api": "ec2_fleet",
            "image_id": "ami-12345678",
            "instance_type": InstanceType("t2.micro"),
            "subnet_ids": ["subnet-12345678"],
            "security_group_ids": ["sg-12345678"],
        }

        # Test missing each required field
        for field in required_fields:
            template_data = base_template_data.copy()
            del template_data[field]

            with pytest.raises((ValueError, TypeError, TemplateValidationError)):
                Template(**template_data)

    def test_template_with_multiple_subnets_and_security_groups(self):
        """Test template with multiple subnets and security groups."""
        template = Template(
            id="template-multi",
            name="multi-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            instance_type=InstanceType("t2.micro"),
            subnet_ids=["subnet-12345678", "subnet-87654321", "subnet-abcdef12"],
            security_group_ids=["sg-12345678", "sg-87654321", "sg-abcdef12"],
        )

        assert len(template.subnet_ids) == 3
        assert len(template.security_group_ids) == 3
        assert "subnet-12345678" in template.subnet_ids
        assert "subnet-87654321" in template.subnet_ids
        assert "subnet-abcdef12" in template.subnet_ids
        assert "sg-12345678" in template.security_group_ids
        assert "sg-87654321" in template.security_group_ids
        assert "sg-abcdef12" in template.security_group_ids


@pytest.mark.unit
class TestTemplateValueObjects:
    """Test cases for Template-specific value objects."""

    def test_template_id_creation(self):
        """Test TemplateId creation."""
        template_id = TemplateId("template-001")
        assert str(template_id) == "template-001"
        assert template_id.value == "template-001"

    def test_template_id_validation(self):
        """Test TemplateId validation."""
        valid_ids = ["template-001", "tpl-123", "t-456", "my-template-789"]

        for valid_id in valid_ids:
            template_id = TemplateId(valid_id)
            assert template_id.value == valid_id

    def test_template_id_invalid(self):
        """Test TemplateId with invalid values."""
        invalid_ids = ["", " ", "   "]

        for invalid_id in invalid_ids:
            with pytest.raises((ValueError, TemplateValidationError)):
                TemplateId(invalid_id)

    def test_template_name_creation(self):
        """Test TemplateName creation."""
        template_name = TemplateName("My Test Template")
        assert str(template_name) == "My Test Template"
        assert template_name.value == "My Test Template"

    def test_template_name_validation(self):
        """Test TemplateName validation."""
        valid_names = [
            "Test Template",
            "My-Template-123",
            "template_with_underscores",
            "Template With Spaces",
            "T",  # Single character
        ]

        for valid_name in valid_names:
            template_name = TemplateName(valid_name)
            assert template_name.value == valid_name

    def test_template_name_invalid(self):
        """Test TemplateName with invalid values."""
        invalid_names = ["", " ", "   "]

        for invalid_name in invalid_names:
            with pytest.raises((ValueError, TemplateValidationError)):
                TemplateName(invalid_name)


@pytest.mark.unit
class TestTemplateExceptions:
    """Test cases for Template-specific exceptions."""

    def test_template_validation_error(self):
        """Test TemplateValidationError."""
        error = TemplateValidationError("Invalid template configuration")
        assert str(error) == "Invalid template configuration"
        assert isinstance(error, Exception)

    def test_template_not_found_error(self):
        """Test TemplateNotFoundError."""
        error = TemplateNotFoundError("Template not found", template_id="template-001")
        assert str(error) == "Template not found"
        assert error.template_id == "template-001"

    def test_template_not_found_error_without_id(self):
        """Test TemplateNotFoundError without template ID."""
        error = TemplateNotFoundError("Template not found")
        assert str(error) == "Template not found"
        assert error.template_id is None
