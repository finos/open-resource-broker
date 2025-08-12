"""Tests for Symphony HostFactory scheduler strategy."""

from unittest.mock import Mock

from src.domain.template.aggregate import Template
from src.infrastructure.scheduler.hostfactory.strategy import (
    HostFactorySchedulerStrategy,
)


class TestSymphonyHostFactorySchedulerStrategy:
    """Test Symphony HostFactory scheduler strategy - SINGLE FIELD MAPPING POINT."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config_manager = Mock()
        self.mock_config_manager.get_app_config.return_value = {
            "scheduler": {"type": "hostfactory", "config_root": "/test/config"},
            "provider": {"active_provider": "aws-default"},
        }

        # Mock provider config to return proper values instead of Mock objects
        mock_provider_config = Mock()
        mock_provider_config.active_provider = "aws-default"
        self.mock_config_manager.get_provider_config.return_value = mock_provider_config

        # Mock resolve_file method to return actual paths
        def mock_resolve_file(file_type, filename):
            config_root = "/test/config"
            return f"{config_root}/{filename}"

        self.mock_config_manager.resolve_file.side_effect = mock_resolve_file

        self.mock_logger = Mock()
        self.strategy = HostFactorySchedulerStrategy(
            self.mock_config_manager, self.mock_logger
        )

    def test_get_templates_file_path(self):
        """Test templates file path generation."""
        path = self.strategy.get_templates_file_path()
        assert path == "/test/config/awsprov_templates.json"

    def test_get_config_file_path(self):
        """Test config file path generation."""
        path = self.strategy.get_config_file_path()
        assert path == "/test/config/awsprov_config.json"

    def test_get_paths_with_different_provider(self):
        """Test path generation with different provider."""
        self.mock_config_manager.get_app_config.return_value = {
            "scheduler": {"config_root": "/test/config"},
            "provider": {"active_provider": "azure-production"},
        }

        # Update the provider config mock as well
        mock_provider_config = Mock()
        mock_provider_config.active_provider = "azure-production"
        self.mock_config_manager.get_provider_config.return_value = mock_provider_config

        templates_path = self.strategy.get_templates_file_path()
        config_path = self.strategy.get_config_file_path()

        assert templates_path == "/test/config/azureprov_templates.json"
        assert config_path == "/test/config/azureprov_config.json"

    def test_parse_template_config_single_mapping_point(self):
        """Test template parsing - SINGLE FIELD MAPPING POINT."""
        raw_template = {
            "templateId": "test-template-123",
            "name": "Test Template",
            "description": "Test description",
            "vmType": "t2.micro",
            "imageId": "ami-12345678",
            "maxNumber": 10,
            "subnetIds": ["subnet-123", "subnet-456"],
            "securityGroupIds": ["sg-123", "sg-456"],
            "priceType": "spot",
            "allocationStrategy": "capacity_optimized",
            "maxPrice": 0.05,
            "tags": {"Environment": "test"},
            "metadata": {"owner": "test-user"},
            "providerApi": "aws",
            "createdAt": "2023-01-01T00:00:00Z",
            "updatedAt": "2023-01-02T00:00:00Z",
            "isActive": True,
            "keyName": "test-key",
            "userData": "#!/bin/bash\necho hello",
        }

        template = self.strategy.parse_template_config(raw_template)

        # Verify all Symphony → Domain field mappings
        assert template.template_id == "test-template-123"
        assert template.name == "Test Template"
        assert template.description == "Test description"
        assert template.instance_type == "t2.micro"
        assert template.image_id == "ami-12345678"
        assert template.max_instances == 10
        assert template.subnet_ids == ["subnet-123", "subnet-456"]
        assert template.security_group_ids == ["sg-123", "sg-456"]
        assert template.price_type == "spot"
        assert template.allocation_strategy == "capacity_optimized"
        assert template.max_price == 0.05
        assert template.tags == {"Environment": "test"}
        assert template.metadata == {"owner": "test-user"}
        assert template.provider_api == "aws"
        assert template.is_active is True
        assert template.key_name == "test-key"
        assert template.user_data == "#!/bin/bash\necho hello"

    def test_parse_template_config_with_defaults(self):
        """Test template parsing with default values."""
        raw_template = {
            "templateId": "minimal-template",
            "vmType": "t2.micro",
            "imageId": "ami-12345678",
            "subnetIds": ["subnet-123"],  # Required field
        }

        template = self.strategy.parse_template_config(raw_template)

        # Verify defaults are applied
        assert template.template_id == "minimal-template"
        assert template.max_instances == 1  # Default
        assert template.subnet_ids == ["subnet-123"]  # Provided
        assert template.security_group_ids == []  # Default empty list
        assert template.price_type == "ondemand"  # Default
        assert template.allocation_strategy == "lowest_price"  # Default
        assert template.tags == {}  # Default empty dict
        assert template.metadata == {}  # Default empty dict
        assert template.is_active is True  # Default

    def test_format_templates_response_single_mapping_point(self):
        """Test template response formatting - SINGLE FIELD MAPPING POINT."""
        # Create domain template
        template = Template(
            template_id="test-template",
            name="Test Template",
            description="Test description",
            instance_type="t2.micro",
            image_id="ami-12345678",
            max_instances=5,
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            price_type="spot",
            allocation_strategy="capacity_optimized",
            max_price=0.03,
            tags={"Environment": "test"},
            metadata={"owner": "test-user"},
            provider_api="aws",
            is_active=True,
            key_name="test-key",
            user_data="#!/bin/bash\necho test",
        )

        response = self.strategy.format_templates_response([template])

        # Verify all Domain → Symphony field mappings
        symphony_template = response["templates"][0]
        assert symphony_template["templateId"] == "test-template"
        assert symphony_template["name"] == "Test Template"
        assert symphony_template["description"] == "Test description"
        assert symphony_template["vmType"] == "t2.micro"
        assert symphony_template["imageId"] == "ami-12345678"
        assert symphony_template["maxNumber"] == 5
        assert symphony_template["subnetIds"] == ["subnet-123"]
        assert symphony_template["securityGroupIds"] == ["sg-123"]
        assert symphony_template["priceType"] == "spot"
        assert symphony_template["allocationStrategy"] == "capacity_optimized"
        assert symphony_template["maxPrice"] == 0.03
        assert symphony_template["tags"] == {"Environment": "test"}
        assert symphony_template["metadata"] == {"owner": "test-user"}
        assert symphony_template["providerApi"] == "aws"
        assert symphony_template["isActive"] is True
        assert symphony_template["keyName"] == "test-key"
        assert symphony_template["userData"] == "#!/bin/bash\necho test"

    def test_parse_request_data_single_mapping_point(self):
        """Test request data parsing - SINGLE FIELD MAPPING POINT."""
        raw_request = {
            "templateId": "test-template",
            "maxNumber": 3,
            "requestType": "provision",
            "metadata": {"user": "test-user"},
        }

        parsed_request = self.strategy.parse_request_data(raw_request)

        # Verify Symphony → Domain field mappings for requests
        assert parsed_request["template_id"] == "test-template"
        assert parsed_request["requested_count"] == 3
        assert parsed_request["request_type"] == "provision"
        assert parsed_request["metadata"] == {"user": "test-user"}

    def test_parse_request_data_with_defaults(self):
        """Test request data parsing with defaults."""
        raw_request = {"templateId": "test-template"}

        parsed_request = self.strategy.parse_request_data(raw_request)

        # Verify defaults
        assert parsed_request["template_id"] == "test-template"
        assert parsed_request["requested_count"] == 1  # Default
        assert parsed_request["request_type"] == "provision"  # Default
        assert parsed_request["metadata"] == {}  # Default

    def test_field_mapping_consistency(self):
        """Test that field mapping is consistent in both directions."""
        # Original Symphony data
        original_data = {
            "templateId": "consistency-test",
            "vmType": "t3.medium",
            "imageId": "ami-abcdef12",
            "maxNumber": 7,
            "subnetIds": ["subnet-abc", "subnet-def"],
            "priceType": "ondemand",
            "allocationStrategy": "lowest_price",
        }

        # Parse to domain
        domain_template = self.strategy.parse_template_config(original_data)

        # Format back to Symphony
        response = self.strategy.format_templates_response([domain_template])
        symphony_template = response["templates"][0]

        # Verify round-trip consistency for key fields
        assert symphony_template["templateId"] == original_data["templateId"]
        assert symphony_template["vmType"] == original_data["vmType"]
        assert symphony_template["imageId"] == original_data["imageId"]
        assert symphony_template["maxNumber"] == original_data["maxNumber"]
        assert symphony_template["subnetIds"] == original_data["subnetIds"]
        assert symphony_template["priceType"] == original_data["priceType"]
        assert (
            symphony_template["allocationStrategy"]
            == original_data["allocationStrategy"]
        )
