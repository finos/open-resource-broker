"""Unit tests for FleetTagBuilder utility."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from src.domain.request.aggregate import Request
from src.domain.template.aggregate import Template
from src.providers.aws.infrastructure.handlers.components import FleetTagBuilder


@pytest.mark.unit
@pytest.mark.aws
class TestFleetTagBuilder:
    """Test cases for FleetTagBuilder utility."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock request
        self.mock_request = Mock(spec=Request)
        self.mock_request.request_id = "req-12345678-1234-1234-1234-123456789012"

        # Create mock template
        self.mock_template = Mock(spec=Template)
        self.mock_template.template_id = "template-001"
        self.mock_template.tags = {"Environment": "test", "Project": "hostfactory"}

    def test_build_common_tags(self):
        """Test building common tags."""
        tags = FleetTagBuilder.build_common_tags(self.mock_request, self.mock_template)

        assert len(tags) == 5
        assert {"Key": "Name", "Value": f"hf-{self.mock_request.request_id}"} in tags
        assert {"Key": "RequestId", "Value": str(self.mock_request.request_id)} in tags
        assert {
            "Key": "TemplateId",
            "Value": str(self.mock_template.template_id),
        } in tags
        assert {"Key": "CreatedBy", "Value": "HostFactory"} in tags

        # Check CreatedAt tag exists and has proper format
        created_at_tag = next(tag for tag in tags if tag["Key"] == "CreatedAt")
        assert created_at_tag is not None
        # Verify it's a valid ISO format timestamp
        datetime.fromisoformat(created_at_tag["Value"].replace("Z", "+00:00"))

    def test_build_fleet_tags(self):
        """Test building fleet-specific tags."""
        fleet_name = "test-fleet"
        tags = FleetTagBuilder.build_fleet_tags(
            self.mock_request, self.mock_template, fleet_name
        )

        assert len(tags) == 5
        # Fleet tags should have fleet-specific name
        name_tag = next(tag for tag in tags if tag["Key"] == "Name")
        assert name_tag["Value"] == f"hf-fleet-{self.mock_request.request_id}"

    def test_build_instance_tags(self):
        """Test building instance-specific tags."""
        tags = FleetTagBuilder.build_instance_tags(
            self.mock_request, self.mock_template
        )

        assert len(tags) == 5
        # Instance tags should have standard name format
        name_tag = next(tag for tag in tags if tag["Key"] == "Name")
        assert name_tag["Value"] == f"hf-{self.mock_request.request_id}"

    def test_add_template_tags(self):
        """Test adding template-specific tags."""
        base_tags = [{"Key": "Base", "Value": "tag"}]

        extended_tags = FleetTagBuilder.add_template_tags(base_tags, self.mock_template)

        assert len(extended_tags) == 3  # 1 base + 2 template tags
        assert {"Key": "Base", "Value": "tag"} in extended_tags
        assert {"Key": "Environment", "Value": "test"} in extended_tags
        assert {"Key": "Project", "Value": "hostfactory"} in extended_tags

    def test_add_template_tags_no_template_tags(self):
        """Test adding template tags when template has no tags."""
        self.mock_template.tags = None
        base_tags = [{"Key": "Base", "Value": "tag"}]

        extended_tags = FleetTagBuilder.add_template_tags(base_tags, self.mock_template)

        assert len(extended_tags) == 1
        assert extended_tags == base_tags

    def test_build_tag_specifications(self):
        """Test building AWS TagSpecifications."""
        resource_types = ["fleet", "instance"]

        tag_specs = FleetTagBuilder.build_tag_specifications(
            self.mock_request, self.mock_template, resource_types
        )

        assert len(tag_specs) == 2

        # Check fleet tag specification
        fleet_spec = next(spec for spec in tag_specs if spec["ResourceType"] == "fleet")
        assert fleet_spec is not None
        fleet_name_tag = next(tag for tag in fleet_spec["Tags"] if tag["Key"] == "Name")
        assert fleet_name_tag["Value"] == f"hf-fleet-{self.mock_request.request_id}"

        # Check instance tag specification
        instance_spec = next(
            spec for spec in tag_specs if spec["ResourceType"] == "instance"
        )
        assert instance_spec is not None
        instance_name_tag = next(
            tag for tag in instance_spec["Tags"] if tag["Key"] == "Name"
        )
        assert instance_name_tag["Value"] == f"hf-{self.mock_request.request_id}"

        # Both should have template tags
        for spec in tag_specs:
            assert {"Key": "Environment", "Value": "test"} in spec["Tags"]
            assert {"Key": "Project", "Value": "hostfactory"} in spec["Tags"]

    def test_build_asg_tags(self):
        """Test building ASG-specific tags."""
        asg_tags = FleetTagBuilder.build_asg_tags(self.mock_request, self.mock_template)

        # ASG tags should have PropagateAtLaunch property
        for tag in asg_tags:
            assert "PropagateAtLaunch" in tag
            assert tag["PropagateAtLaunch"] is True
            assert "ResourceId" in tag
            assert "ResourceType" in tag
            assert tag["ResourceType"] == "auto-scaling-group"

        # Check that common tags are present
        tag_keys = [tag["Key"] for tag in asg_tags]
        assert "Name" in tag_keys
        assert "RequestId" in tag_keys
        assert "TemplateId" in tag_keys
        assert "CreatedBy" in tag_keys
        assert "CreatedAt" in tag_keys

        # Check that template tags are present
        assert "Environment" in tag_keys
        assert "Project" in tag_keys

    def test_tag_consistency_across_methods(self):
        """Test that common tags are consistent across different methods."""
        common_tags = FleetTagBuilder.build_common_tags(
            self.mock_request, self.mock_template
        )
        fleet_tags = FleetTagBuilder.build_fleet_tags(
            self.mock_request, self.mock_template, "test"
        )
        instance_tags = FleetTagBuilder.build_instance_tags(
            self.mock_request, self.mock_template
        )

        # Extract non-Name tags for comparison (Name varies by resource type)
        def extract_non_name_tags(tags):
            return [tag for tag in tags if tag["Key"] != "Name"]

        common_non_name = extract_non_name_tags(common_tags)
        fleet_non_name = extract_non_name_tags(fleet_tags)
        instance_non_name = extract_non_name_tags(instance_tags)

        # All should have the same non-Name tags
        assert common_non_name == fleet_non_name == instance_non_name
