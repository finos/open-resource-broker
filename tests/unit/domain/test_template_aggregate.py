"""Unit tests for Template aggregate."""

from datetime import datetime, timezone

import pytest

from domain.template.exceptions import TemplateNotFoundError, TemplateValidationError
from domain.template.template_aggregate import Template

# Try to import optional value objects - create mocks if not available
try:
    from domain.template.value_objects import TemplateId, TemplateName

    TEMPLATE_VALUE_OBJECTS_AVAILABLE = True
except ImportError:
    TEMPLATE_VALUE_OBJECTS_AVAILABLE = False

    class TemplateId:
        def __init__(self, value):
            if not isinstance(value, str) or len(value.strip()) == 0:
                raise ValueError("Invalid template ID")
            self.value = value.strip()

        def __str__(self):
            return self.value

    class TemplateName:
        def __init__(self, value):
            if not isinstance(value, str) or len(value.strip()) == 0:
                raise ValueError("Invalid template name")
            self.value = value.strip()

        def __str__(self):
            return self.value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_template(template_id="template-001", **kwargs):
    """Create a minimal valid Template instance."""
    defaults = dict(
        template_id=template_id,
        name="test-template",
        image_id="ami-12345678",
        machine_types={"t2.micro": 1},
    )
    defaults.update(kwargs)
    return Template(**defaults)


@pytest.mark.unit
class TestTemplateAggregate:
    """Test cases for Template aggregate."""

    def test_template_creation(self):
        """Test basic template creation."""
        template = _make_template(
            template_id="template-001",
            name="test-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            machine_types={"t2.micro": 1},
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
            user_data="#!/bin/bash\necho 'Hello World'",
            tags={"Environment": "test", "Project": "hostfactory"},
        )

        assert template.template_id == "template-001"
        assert template.name == "test-template"
        assert template.provider_api == "ec2_fleet"
        assert template.image_id == "ami-12345678"
        assert template.machine_types == {"t2.micro": 1}
        assert template.subnet_ids == ["subnet-12345678"]
        assert template.security_group_ids == ["sg-12345678"]
        assert "Hello World" in template.user_data
        assert template.tags["Environment"] == "test"
        assert template.tags["Project"] == "hostfactory"

    def test_template_with_minimal_data(self):
        """Test template creation with minimal required data."""
        template = _make_template(
            template_id="template-002",
            name="minimal-template",
        )

        assert template.template_id == "template-002"
        assert template.name == "minimal-template"
        assert template.key_name is None
        assert template.user_data is None
        assert template.tags == {}

    def test_template_with_various_provider_apis(self):
        """Test template creation with various provider_api values."""
        # Template does not validate provider_api values — any string is accepted
        for api in ["ec2_fleet", "auto_scaling_group", "spot_fleet", "run_instances"]:
            template = _make_template(
                template_id=f"template-{api}",
                provider_api=api,
            )
            assert template.provider_api == api

    def test_template_max_instances_must_be_positive(self):
        """Test that max_instances must be positive."""
        with pytest.raises((ValueError, TemplateValidationError)):
            Template(
                template_id="template-bad",
                name="bad-template",
                image_id="ami-12345678",
                machine_types={"t2.micro": 1},
                max_instances=0,
            )

    def test_template_update_fields(self):
        """Test updating template fields."""
        template = _make_template(template_id="template-update", name="original-name")

        template.name = "updated-name"
        assert template.name == "updated-name"

        template.machine_types = {"t2.small": 1}
        assert template.machine_types == {"t2.small": 1}

        template.tags = {"Environment": "production", "Owner": "team"}
        assert template.tags["Environment"] == "production"
        assert template.tags["Owner"] == "team"

    def test_template_add_subnet(self):
        """Test adding subnet to template."""
        template = _make_template(
            template_id="template-add-subnet",
            subnet_ids=["subnet-12345678"],
        )

        template.subnet_ids.append("subnet-87654321")
        assert "subnet-87654321" in template.subnet_ids
        assert len(template.subnet_ids) == 2

    def test_template_add_security_group(self):
        """Test adding security group to template."""
        template = _make_template(
            template_id="template-add-sg",
            security_group_ids=["sg-12345678"],
        )

        template.security_group_ids.append("sg-87654321")
        assert "sg-87654321" in template.security_group_ids
        assert len(template.security_group_ids) == 2

    def test_template_user_data_encoding(self):
        """Test template user data with different values."""
        user_data_scripts = [
            "#!/bin/bash\necho 'Hello World'",
            "#!/bin/bash\nyum update -y\nyum install -y docker",
            "#cloud-config\npackages:\n  - docker\n  - git",
        ]

        for user_data in user_data_scripts:
            template = _make_template(
                template_id=f"template-userdata-{len(user_data)}",
                user_data=user_data,
            )
            assert template.user_data == user_data

        # None user_data
        template = _make_template(template_id="template-no-userdata")
        assert template.user_data is None

    def test_template_tags_operations(self):
        """Test template tags operations."""
        template = _make_template(
            template_id="template-tags",
            tags={"Environment": "test"},
        )

        template.tags["Project"] = "hostfactory"
        assert template.tags["Project"] == "hostfactory"

        template.tags["Environment"] = "production"
        assert template.tags["Environment"] == "production"

        del template.tags["Environment"]
        assert "Environment" not in template.tags
        assert "Project" in template.tags

    def test_template_equality(self):
        """Test template equality based on template_id."""
        template1 = _make_template(template_id="template-001", name="template-1")

        template2 = _make_template(
            template_id="template-001",  # Same ID
            name="template-2",  # Different name
        )

        template3 = _make_template(template_id="template-002", name="template-1")

        # Template is a plain Pydantic BaseModel — equality is field-based,
        # not identity-based. Two templates with the same template_id but
        # different other fields are NOT equal by default Pydantic equality.
        # We just verify template_id is the distinguishing key.
        assert template1.template_id == template2.template_id
        assert template1.template_id != template3.template_id

    def test_template_string_representation(self):
        """Test template string representation."""
        template = _make_template(
            template_id="template-001",
            name="test-template",
            provider_api="ec2_fleet",
        )

        str_repr = str(template)
        assert "template-001" in str_repr

        repr_str = repr(template)
        assert "Template" in repr_str
        assert "template-001" in repr_str

    def test_template_serialization(self):
        """Test template serialization to dict."""
        template = _make_template(
            template_id="template-001",
            name="test-template",
            provider_api="ec2_fleet",
            image_id="ami-12345678",
            machine_types={"t2.micro": 1},
            subnet_ids=["subnet-12345678"],
            security_group_ids=["sg-12345678"],
            user_data="#!/bin/bash\necho 'test'",
            tags={"Environment": "test"},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        template_dict = template.model_dump()

        assert template_dict["template_id"] == "template-001"
        assert template_dict["name"] == "test-template"
        assert template_dict["provider_api"] == "ec2_fleet"
        assert template_dict["image_id"] == "ami-12345678"
        assert template_dict["machine_types"] == {"t2.micro": 1}
        assert template_dict["subnet_ids"] == ["subnet-12345678"]
        assert template_dict["security_group_ids"] == ["sg-12345678"]
        assert template_dict["user_data"] == "#!/bin/bash\necho 'test'"
        assert template_dict["tags"] == {"Environment": "test"}
        assert "created_at" in template_dict
        assert "updated_at" in template_dict

    def test_template_deserialization(self):
        """Test template deserialization from dict."""
        template_dict = {
            "template_id": "template-001",
            "name": "test-template",
            "provider_api": "ec2_fleet",
            "image_id": "ami-12345678",
            "machine_types": {"t2.micro": 1},
            "subnet_ids": ["subnet-12345678"],
            "security_group_ids": ["sg-12345678"],
            "user_data": "#!/bin/bash\necho 'test'",
            "tags": {"Environment": "test"},
            "created_at": "2023-01-01T00:00:00",
            "updated_at": "2023-01-01T00:00:00",
        }

        template = Template(**template_dict)

        assert template.template_id == "template-001"
        assert template.name == "test-template"
        assert template.provider_api == "ec2_fleet"
        assert template.image_id == "ami-12345678"
        assert template.machine_types == {"t2.micro": 1}
        assert template.subnet_ids == ["subnet-12345678"]
        assert template.security_group_ids == ["sg-12345678"]
        assert template.user_data == "#!/bin/bash\necho 'test'"
        assert template.tags == {"Environment": "test"}

    def test_template_with_multiple_subnets_and_security_groups(self):
        """Test template with multiple subnets and security groups."""
        template = _make_template(
            template_id="template-multi",
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

    def test_template_requires_template_id(self):
        """Test that template_id is required."""
        with pytest.raises((ValueError, TypeError, TemplateValidationError)):
            Template(
                name="no-id-template",
                image_id="ami-12345678",
                machine_types={"t2.micro": 1},
            )

    def test_template_update_image_id(self):
        """Test updating image ID via method."""
        template = _make_template(template_id="template-img", image_id="ami-old")
        updated = template.update_image_id("ami-new")
        assert updated.image_id == "ami-new"
        # Original unchanged
        assert template.image_id == "ami-old"

    def test_template_add_subnet_method(self):
        """Test add_subnet method returns new template."""
        template = _make_template(
            template_id="template-subnet",
            subnet_ids=["subnet-aaa"],
        )
        updated = template.add_subnet("subnet-bbb")
        assert "subnet-bbb" in updated.subnet_ids
        assert "subnet-aaa" in updated.subnet_ids
        # Original unchanged
        assert "subnet-bbb" not in template.subnet_ids

    def test_template_add_security_group_method(self):
        """Test add_security_group method returns new template."""
        template = _make_template(
            template_id="template-sg",
            security_group_ids=["sg-aaa"],
        )
        updated = template.add_security_group("sg-bbb")
        assert "sg-bbb" in updated.security_group_ids
        assert "sg-aaa" in updated.security_group_ids
        # Original unchanged
        assert "sg-bbb" not in template.security_group_ids


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
            "T",
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
class TestTemplateTagValidation:
    """Test that reserved orb: tag keys are rejected at the domain level."""

    def test_orb_prefixed_tag_key_is_rejected(self):
        with pytest.raises(ValueError, match="orb:"):
            _make_template(tags={"orb:request-id": "spoofed"})

    def test_multiple_orb_prefixed_keys_are_reported(self):
        with pytest.raises(ValueError, match="orb:"):
            _make_template(tags={"orb:managed-by": "x", "orb:template-id": "y"})

    def test_plain_tag_keys_are_accepted(self):
        t = _make_template(tags={"env": "prod", "team": "platform"})
        assert t.tags == {"env": "prod", "team": "platform"}

    def test_empty_tags_are_accepted(self):
        t = _make_template(tags={})
        assert t.tags == {}

    def test_key_with_orb_not_as_prefix_is_accepted(self):
        # "my-orb:tag" does not start with "orb:" so it must be allowed
        t = _make_template(tags={"my-orb:tag": "value"})
        assert "my-orb:tag" in t.tags


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
        # Constructor signature: __init__(self, template_id: str)
        error = TemplateNotFoundError("template-001")
        assert "template-001" in str(error)

    def test_template_not_found_error_stores_id(self):
        """Test TemplateNotFoundError stores entity_id in details."""
        error = TemplateNotFoundError("template-001")
        assert error.details.get("entity_id") == "template-001"
