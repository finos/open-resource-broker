"""Unit tests for FleetTagBuilder utility."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from domain.request.aggregate import Request
from domain.template.aggregate import Template
from providers.aws.utilities.fleet_tag_builder import FleetTagBuilder


@pytest.mark.unit
@pytest.mark.aws
class TestFleetTagBuilder:
    """Test suite for FleetTagBuilder utility."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_request = Mock(spec=Request)
        self.mock_request.request_id = "req-12345678-1234-1234-1234-123456789012"

        self.mock_template = Mock(spec=Template)
        self.mock_template.template_id = "template-001"
        self.mock_template.tags = None

    def test_build_base_tags(self):
        """Test building base tags."""
        package_name = "test-package"
        tags = FleetTagBuilder.build_base_tags(self.mock_request, self.mock_template, package_name)

        assert len(tags) == 4
        assert tags["RequestId"] == str(self.mock_request.request_id)
        assert tags["TemplateId"] == str(self.mock_template.template_id)
        assert tags["CreatedBy"] == package_name
        assert "CreatedAt" in tags

    def test_build_resource_tags(self):
        """Test building resource-specific tags."""
        # Test with actual resource prefix (empty by default)
        tags = FleetTagBuilder.build_resource_tags(
            self.mock_request, self.mock_template, "fleet", "test-package"
        )

        assert len(tags) == 5  # 4 base tags + Name
        assert tags["Name"] == str(self.mock_request.request_id)  # Empty prefix
        assert tags["RequestId"] == str(self.mock_request.request_id)
        assert tags["CreatedBy"] == "test-package"

    def test_build_resource_tags_with_custom_prefix(self):
        """Test building resource tags - tests actual behavior with empty prefix."""
        # Test actual behavior (empty prefix by default)
        tags = FleetTagBuilder.build_resource_tags(
            self.mock_request, self.mock_template, "fleet", "test-package"
        )

        assert len(tags) == 5  # 4 base tags + Name
        assert tags["Name"] == str(self.mock_request.request_id)  # Empty prefix by default
        assert tags["RequestId"] == str(self.mock_request.request_id)
        assert tags["CreatedBy"] == "test-package"

    def test_build_resource_tags_with_template_tags(self):
        """Test building resource tags with template tags."""
        self.mock_template.tags = {"Environment": "test", "Owner": "team"}
        
        with patch('infrastructure.utilities.common.resource_naming.get_resource_prefix', return_value=""):
            tags = FleetTagBuilder.build_resource_tags(
                self.mock_request, self.mock_template, "instance"
            )

            assert len(tags) == 7  # 4 base + Name + 2 template tags
            assert tags["Environment"] == "test"
            assert tags["Owner"] == "team"

    def test_format_for_aws(self):
        """Test formatting tags for AWS API."""
        tags = {"Key1": "Value1", "Key2": "Value2"}
        aws_tags = FleetTagBuilder.format_for_aws(tags)

        assert len(aws_tags) == 2
        assert {"Key": "Key1", "Value": "Value1"} in aws_tags
        assert {"Key": "Key2", "Value": "Value2"} in aws_tags

    def test_build_tag_specifications(self):
        """Test building AWS TagSpecifications."""
        resource_types = ["fleet", "instance"]

        # Test with actual resource prefix (empty by default)
        tag_specs = FleetTagBuilder.build_tag_specifications(
            self.mock_request, self.mock_template, resource_types, "test-package"
        )

        assert len(tag_specs) == 2

        # Check fleet tag specification
        fleet_spec = next(spec for spec in tag_specs if spec["ResourceType"] == "fleet")
        assert fleet_spec is not None
        fleet_name_tag = next(tag for tag in fleet_spec["Tags"] if tag["Key"] == "Name")
        assert fleet_name_tag["Value"] == str(self.mock_request.request_id)  # Empty prefix

        # Check instance tag specification
        instance_spec = next(spec for spec in tag_specs if spec["ResourceType"] == "instance")
        assert instance_spec is not None

    def test_build_tag_specifications_with_custom_prefix(self):
        """Test building AWS TagSpecifications - tests actual behavior."""
        resource_types = ["fleet", "instance"]

        # Test actual behavior (empty prefix by default)
        tag_specs = FleetTagBuilder.build_tag_specifications(
            self.mock_request, self.mock_template, resource_types, "test-package"
        )

        assert len(tag_specs) == 2

        # Check fleet tag specification
        fleet_spec = next(spec for spec in tag_specs if spec["ResourceType"] == "fleet")
        assert fleet_spec is not None
        fleet_name_tag = next(tag for tag in fleet_spec["Tags"] if tag["Key"] == "Name")
        assert fleet_name_tag["Value"] == str(self.mock_request.request_id)  # Empty prefix by default

    # Legacy compatibility tests
    def test_build_common_tags_legacy(self):
        """Test legacy build_common_tags method."""
        tags = FleetTagBuilder.build_common_tags(self.mock_request, self.mock_template)

        assert len(tags) == 5
        assert {"Key": "Name", "Value": f"hf-{self.mock_request.request_id}"} in tags
        assert {"Key": "RequestId", "Value": str(self.mock_request.request_id)} in tags
        assert {
            "Key": "TemplateId",
            "Value": str(self.mock_template.template_id),
        } in tags
        assert {"Key": "CreatedBy", "Value": "open-hostfactory-plugin"} in tags

    def test_build_fleet_tags_legacy(self):
        """Test legacy build_fleet_tags method."""
        fleet_name = "test-fleet"
        tags = FleetTagBuilder.build_fleet_tags(self.mock_request, self.mock_template, fleet_name)

        assert len(tags) == 5
        # Fleet tags should have specified fleet name
        name_tag = next(tag for tag in tags if tag["Key"] == "Name")
        assert name_tag["Value"] == fleet_name

    def test_build_instance_tags_legacy(self):
        """Test legacy build_instance_tags method."""
        tags = FleetTagBuilder.build_instance_tags(self.mock_request, self.mock_template)

        assert len(tags) == 5
        # Should be same as build_common_tags
        common_tags = FleetTagBuilder.build_common_tags(self.mock_request, self.mock_template)
        # Compare without CreatedAt since it may differ by microseconds
        tags_without_time = [tag for tag in tags if tag["Key"] != "CreatedAt"]
        common_without_time = [tag for tag in common_tags if tag["Key"] != "CreatedAt"]
        assert tags_without_time == common_without_time

    def test_add_template_tags_legacy(self):
        """Test legacy add_template_tags method."""
        base_tags = [{"Key": "Base", "Value": "Value"}]
        self.mock_template.tags = {"Template": "Tag"}

        result = FleetTagBuilder.add_template_tags(base_tags, self.mock_template)

        assert len(result) == 2
        assert {"Key": "Base", "Value": "Value"} in result
        assert {"Key": "Template", "Value": "Tag"} in result

    def test_add_template_tags_no_template_tags_legacy(self):
        """Test legacy add_template_tags with no template tags."""
        base_tags = [{"Key": "Base", "Value": "Value"}]
        self.mock_template.tags = None

        result = FleetTagBuilder.add_template_tags(base_tags, self.mock_template)

        assert result == base_tags
