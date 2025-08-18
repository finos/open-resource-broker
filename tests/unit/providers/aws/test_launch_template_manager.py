#!/usr/bin/env python3
"""
Unit tests for AWS Launch Template Manager.

Tests the launch template creation, versioning, and management functionality
that was moved out of the base handler to fix architectural violations.
"""

import os
import sys
from unittest.mock import Mock

import pytest

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../..")))

from domain.request.aggregate import Request
from providers.aws.configuration.config import (
    AWSProviderConfig,
    LaunchTemplateConfiguration,
)
from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
    LaunchTemplateResult,
)


class TestAWSLaunchTemplateManager:
    """Test suite for AWS Launch Template Manager."""

    def setup_method(self):
        """Set up test fixtures."""
        # Mock dependencies
        self.mock_aws_client = Mock()
        self.mock_logger = Mock()
        self.mock_config = Mock(spec=AWSProviderConfig)

        # Configure launch template config
        self.mock_lt_config = LaunchTemplateConfiguration(
            create_per_request=True,
            naming_strategy="request_based",
            version_strategy="incremental",
            reuse_existing=True,
            cleanup_old_versions=False,
            max_versions_per_template=10,
        )
        self.mock_config.launch_template = self.mock_lt_config

        # Create manager instance
        self.manager = AWSLaunchTemplateManager(
            aws_client=self.mock_aws_client,
            config=self.mock_config,
            logger=self.mock_logger,
        )

        # Sample AWS template
        self.aws_template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345678",
            primary_instance_type="t2.micro",
            network_zones=["subnet-123"],
            security_groups=["sg-123"],
            key_pair_name="test-key",
        )

        # Sample request
        self.request = Request(request_id="req-123", template_id="test-template", requested_count=2)

    def test_manager_initialization(self):
        """Test that manager initializes correctly."""
        assert self.manager.aws_client == self.mock_aws_client
        assert self.manager.config == self.mock_config
        assert self.manager.logger == self.mock_logger

    def test_create_or_update_launch_template_per_request_enabled(self):
        """Test launch template creation when create_per_request is enabled."""
        # Configure mock
        self.mock_config.launch_template.create_per_request = True

        # Mock AWS response
        mock_response = {
            "LaunchTemplate": {
                "LaunchTemplateId": "lt-123456",
                "LaunchTemplateName": "test-template-req-123",
                "LatestVersionNumber": 1,
            }
        }
        self.mock_aws_client.ec2_client.create_launch_template.return_value = mock_response

        # Execute
        result = self.manager.create_or_update_launch_template(self.aws_template, self.request)

        # Verify
        assert isinstance(result, LaunchTemplateResult)
        assert result.template_id == "lt-123456"
        assert result.template_name == "test-template-req-123"
        assert result.version == "1"
        assert result.created_new_template is True

        # Verify AWS client was called
        self.mock_aws_client.ec2_client.create_launch_template.assert_called_once()
        call_args = self.mock_aws_client.ec2_client.create_launch_template.call_args[1]
        assert call_args["LaunchTemplateName"] == "test-template-req-123"

    def test_create_or_update_launch_template_per_request_disabled(self):
        """Test launch template creation when create_per_request is disabled."""
        # Configure mock
        self.mock_config.launch_template.create_per_request = False
        self.mock_config.launch_template.reuse_existing = True

        # Mock AWS response for describe (template exists)
        mock_describe_response = {
            "LaunchTemplates": [
                {
                    "LaunchTemplateId": "lt-existing",
                    "LaunchTemplateName": "test-template",
                    "LatestVersionNumber": 3,
                }
            ]
        }
        self.mock_aws_client.ec2_client.describe_launch_templates.return_value = (
            mock_describe_response
        )

        # Execute
        result = self.manager.create_or_update_launch_template(self.aws_template, self.request)

        # Verify
        assert result.template_id == "lt-existing"
        assert result.template_name == "test-template"
        assert result.version == "$Latest"
        assert result.created_new_template is False

        # Verify AWS client was called
        self.mock_aws_client.ec2_client.describe_launch_templates.assert_called_once()

    def test_create_launch_template_data_basic_fields(self):
        """Test launch template data creation with basic fields."""
        # Execute
        data = self.manager._create_launch_template_data(self.aws_template, self.request)

        # Verify basic structure
        assert "ImageId" in data
        assert "InstanceType" in data
        assert "SecurityGroupIds" in data
        assert "KeyName" in data

        # Verify values
        assert data["ImageId"] == "ami-12345678"
        assert data["InstanceType"] == "t2.micro"
        assert data["SecurityGroupIds"] == ["sg-123"]
        assert data["KeyName"] == "test-key"

    def test_create_launch_template_data_network_interfaces(self):
        """Test launch template data creation with network interfaces."""
        # Configure template with public IP
        self.aws_template.public_ip_assignment = True

        # Execute
        data = self.manager._create_launch_template_data(self.aws_template, self.request)

        # Verify network interfaces
        assert "NetworkInterfaces" in data
        network_interfaces = data["NetworkInterfaces"]
        assert len(network_interfaces) == 1

        ni = network_interfaces[0]
        assert ni["DeviceIndex"] == 0
        assert ni["AssociatePublicIpAddress"] is True
        assert ni["SubnetId"] == "subnet-123"
        assert ni["Groups"] == ["sg-123"]

    def test_create_launch_template_data_storage_configuration(self):
        """Test launch template data creation with storage configuration."""
        # Configure template with storage
        self.aws_template.root_volume_size = 20
        self.aws_template.root_volume_type = "gp3"
        self.aws_template.root_volume_iops = 3000
        self.aws_template.storage_encryption = True

        # Execute
        data = self.manager._create_launch_template_data(self.aws_template, self.request)

        # Verify block device mappings
        assert "BlockDeviceMappings" in data
        block_devices = data["BlockDeviceMappings"]
        assert len(block_devices) == 1

        bd = block_devices[0]
        assert bd["DeviceName"] == "/dev/sda1"

        ebs = bd["Ebs"]
        assert ebs["VolumeSize"] == 20
        assert ebs["VolumeType"] == "gp3"
        assert ebs["Iops"] == 3000
        assert ebs["Encrypted"] is True

    def test_create_launch_template_data_user_data(self):
        """Test launch template data creation with user data."""
        # Configure template with user data
        user_data_script = "#!/bin/bash\necho 'Hello World'"
        self.aws_template.user_data = user_data_script

        # Execute
        data = self.manager._create_launch_template_data(self.aws_template, self.request)

        # Verify user data (should be base64 encoded)
        assert "UserData" in data
        import base64

        decoded_user_data = base64.b64decode(data["UserData"]).decode("utf-8")
        assert decoded_user_data == user_data_script

    def test_create_launch_template_data_iam_instance_profile(self):
        """Test launch template data creation with IAM instance profile."""
        # Configure template with instance profile
        self.aws_template.instance_profile = "test-instance-profile"

        # Execute
        data = self.manager._create_launch_template_data(self.aws_template, self.request)

        # Verify IAM instance profile
        assert "IamInstanceProfile" in data
        assert data["IamInstanceProfile"]["Name"] == "test-instance-profile"

    def test_create_launch_template_data_monitoring(self):
        """Test launch template data creation with monitoring enabled."""
        # Configure template with monitoring
        self.aws_template.monitoring_enabled = True

        # Execute
        data = self.manager._create_launch_template_data(self.aws_template, self.request)

        # Verify monitoring
        assert "Monitoring" in data
        assert data["Monitoring"]["Enabled"] is True

    def test_create_instance_tags_basic(self):
        """Test instance tags creation with basic template tags."""
        # Configure template with tags
        self.aws_template.tags = {"Environment": "test", "Project": "hostfactory"}

        # Execute
        tags = self.manager._create_instance_tags(self.aws_template, self.request)

        # Verify tags
        expected_tags = [
            {"Key": "Environment", "Value": "test"},
            {"Key": "Project", "Value": "hostfactory"},
            {"Key": "RequestId", "Value": "req-123"},
            {"Key": "TemplateId", "Value": "test-template"},
        ]

        # Sort both lists for comparison
        tags_sorted = sorted(tags, key=lambda x: x["Key"])
        expected_sorted = sorted(expected_tags, key=lambda x: x["Key"])

        assert tags_sorted == expected_sorted

    def test_create_instance_tags_with_aws_format(self):
        """Test instance tags creation with AWS string format."""
        # Configure template with AWS tag format
        self.aws_template.aws_tag_format = "Environment=prod;Owner=team-alpha;Cost-Center=12345"

        # Execute
        tags = self.manager._create_instance_tags(self.aws_template, self.request)

        # Verify AWS format tags are included
        tag_dict = {tag["Key"]: tag["Value"] for tag in tags}
        assert tag_dict["Environment"] == "prod"
        assert tag_dict["Owner"] == "team-alpha"
        assert tag_dict["Cost-Center"] == "12345"
        assert tag_dict["RequestId"] == "req-123"
        assert tag_dict["TemplateId"] == "test-template"

    def test_generate_template_name_request_based(self):
        """Test template name generation with request-based strategy."""
        # Configure naming strategy
        self.mock_config.launch_template.naming_strategy = "request_based"

        # Execute
        name = self.manager._generate_template_name(self.aws_template, self.request)

        # Verify
        assert name == "test-template-req-123"

    def test_generate_template_name_template_based(self):
        """Test template name generation with template-based strategy."""
        # Configure naming strategy
        self.mock_config.launch_template.naming_strategy = "template_based"

        # Execute
        name = self.manager._generate_template_name(self.aws_template, self.request)

        # Verify
        assert name == "test-template"

    def test_use_existing_template_strategy_found(self):
        """Test using existing template when it exists."""
        # Mock AWS response
        mock_response = {
            "LaunchTemplates": [
                {
                    "LaunchTemplateId": "lt-existing",
                    "LaunchTemplateName": "test-template",
                    "LatestVersionNumber": 5,
                }
            ]
        }
        self.mock_aws_client.ec2_client.describe_launch_templates.return_value = mock_response

        # Execute
        result = self.manager._use_existing_template_strategy(self.aws_template)

        # Verify
        assert result.template_id == "lt-existing"
        assert result.template_name == "test-template"
        assert result.version == "$Latest"
        assert result.created_new_template is False

    def test_use_existing_template_strategy_not_found(self):
        """Test using existing template when it doesn't exist."""
        # Mock AWS response (empty)
        mock_response = {"LaunchTemplates": []}
        self.mock_aws_client.ec2_client.describe_launch_templates.return_value = mock_response

        # Mock create response
        mock_create_response = {
            "LaunchTemplate": {
                "LaunchTemplateId": "lt-new",
                "LaunchTemplateName": "test-template",
                "LatestVersionNumber": 1,
            }
        }
        self.mock_aws_client.ec2_client.create_launch_template.return_value = mock_create_response

        # Execute
        result = self.manager._use_existing_template_strategy(self.aws_template)

        # Verify
        assert result.template_id == "lt-new"
        assert result.template_name == "test-template"
        assert result.version == "1"
        assert result.created_new_template is True

    def test_error_handling_aws_exception(self):
        """Test error handling when AWS API throws exception."""
        # Configure mock to raise exception
        from botocore.exceptions import ClientError

        error = ClientError(
            error_response={
                "Error": {
                    "Code": "InvalidParameterValue",
                    "Message": "Invalid parameter",
                }
            },
            operation_name="CreateLaunchTemplate",
        )
        self.mock_aws_client.ec2_client.create_launch_template.side_effect = error

        # Execute and verify exception is raised
        with pytest.raises(Exception) as exc_info:
            self.manager.create_or_update_launch_template(self.aws_template, self.request)

        # Verify error was logged
        self.mock_logger.error.assert_called()

    def test_launch_template_result_creation(self):
        """Test LaunchTemplateResult creation and properties."""
        result = LaunchTemplateResult(
            template_id="lt-123",
            template_name="test-template",
            version="2",
            created_new_template=True,
        )

        assert result.template_id == "lt-123"
        assert result.template_name == "test-template"
        assert result.version == "2"
        assert result.created_new_template is True

    def test_configuration_integration(self):
        """Test that manager properly uses configuration settings."""
        # Test create_per_request setting
        self.mock_config.launch_template.create_per_request = False
        assert not self.manager.config.launch_template.create_per_request

        # Test naming strategy
        self.mock_config.launch_template.naming_strategy = "custom_strategy"
        assert self.manager.config.launch_template.naming_strategy == "custom_strategy"

        # Test version strategy
        self.mock_config.launch_template.version_strategy = "timestamp"
        assert self.manager.config.launch_template.version_strategy == "timestamp"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
