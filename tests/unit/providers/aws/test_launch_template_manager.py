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

from orb.domain.base.exceptions import InfrastructureError
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
    LaunchTemplateResult,
)

REQUEST_ID = "req-00000000-0000-0000-0000-000000000123"


class TestAWSLaunchTemplateManager:
    """Test suite for AWS Launch Template Manager."""

    def setup_method(self):
        """Set up test fixtures."""
        # Mock dependencies
        self.mock_aws_client = Mock()
        self.mock_logger = Mock()

        self.mock_config_port = Mock()
        self.mock_config_port.get_resource_prefix.return_value = ""
        provider_config = Mock()
        provider_config.provider_defaults = {}
        self.mock_config_port.get_provider_config.return_value = provider_config

        # Create manager instance
        self.manager = AWSLaunchTemplateManager(
            aws_client=self.mock_aws_client,
            logger=self.mock_logger,
            config_port=self.mock_config_port,
        )

        # Sample AWS template
        self.aws_template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345678",
            subnet_ids=["subnet-123"],
            security_group_ids=["sg-123"],
            key_name="test-key",
        )

        # Sample request
        self.request = Request(
            request_id=RequestId(value=REQUEST_ID),
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            template_id="test-template",
            requested_count=2,
        )

    def test_manager_initialization(self):
        """Test that manager initializes correctly."""
        assert self.manager.aws_client == self.mock_aws_client
        assert self.manager._logger == self.mock_logger

    def test_create_or_update_launch_template_creates_new(self):
        """Test launch template creation when template does not exist."""
        from botocore.exceptions import ClientError

        # describe raises NotFoundException so manager creates a new template
        self.mock_aws_client.ec2_client.describe_launch_templates.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "InvalidLaunchTemplateName.NotFoundException",
                    "Message": "Not found",
                }
            },
            operation_name="DescribeLaunchTemplates",
        )

        mock_create_response = {
            "LaunchTemplate": {
                "LaunchTemplateId": "lt-123456",
                "LaunchTemplateName": "orb-req-00000000-0000-0000-0000-000000000123",
                "LatestVersionNumber": 1,
            }
        }
        self.mock_aws_client.ec2_client.create_launch_template.return_value = mock_create_response

        result = self.manager.create_or_update_launch_template(self.aws_template, self.request)

        assert isinstance(result, LaunchTemplateResult)
        assert result.template_id == "lt-123456"
        assert result.version == "1"
        assert result.is_new_template is True
        self.mock_aws_client.ec2_client.create_launch_template.assert_called_once()

    def test_create_or_update_launch_template_reuses_existing(self):
        """Test that an existing launch template is reused at its default version."""
        mock_describe_response = {
            "LaunchTemplates": [
                {
                    "LaunchTemplateId": "lt-existing",
                    "LaunchTemplateName": "orb-req-00000000-0000-0000-0000-000000000123",
                    "LatestVersionNumber": 3,
                    "DefaultVersionNumber": 3,
                }
            ]
        }
        self.mock_aws_client.ec2_client.describe_launch_templates.return_value = (
            mock_describe_response
        )

        result = self.manager.create_or_update_launch_template(self.aws_template, self.request)

        assert result.template_id == "lt-existing"
        assert result.version == "3"
        assert result.is_new_template is False
        assert result.is_new_version is False
        self.mock_aws_client.ec2_client.create_launch_template_version.assert_not_called()

    def test_create_launch_template_data_basic_fields(self):
        """Test launch template data creation with basic fields."""
        data = self.manager._create_launch_template_data_legacy(self.aws_template, self.request)

        assert "ImageId" in data
        assert "InstanceType" in data
        assert data["ImageId"] == "ami-12345678"
        # No machine_types set, falls back to t3.medium
        assert data["InstanceType"] == "t3.medium"

    def test_create_launch_template_data_key_name(self):
        """Test that key_name is included when set via key_name field."""
        self.aws_template.key_name = "my-key"

        data = self.manager._create_launch_template_data_legacy(self.aws_template, self.request)

        assert data["KeyName"] == "my-key"

    def test_create_launch_template_data_network_interfaces(self):
        """Test launch template data includes NetworkInterfaces when subnet_id is set."""
        # subnet_id property returns first element of subnet_ids
        data = self.manager._create_launch_template_data_legacy(self.aws_template, self.request)

        assert "NetworkInterfaces" in data
        ni = data["NetworkInterfaces"][0]
        assert ni["DeviceIndex"] == 0
        assert ni["SubnetId"] == "subnet-123"
        assert ni["AssociatePublicIpAddress"] is True

    def test_create_launch_template_data_storage_configuration(self):
        """Test launch template data creation with storage configuration."""
        self.aws_template.root_device_volume_size = 20
        self.aws_template.volume_type = "gp3"
        self.aws_template.iops = 3000

        data = self.manager._create_launch_template_data_legacy(self.aws_template, self.request)

        assert "BlockDeviceMappings" in data
        block_devices = data["BlockDeviceMappings"]
        assert len(block_devices) == 1

        bd = block_devices[0]
        assert bd["DeviceName"] == "/dev/xvda"

        ebs = bd["Ebs"]
        assert ebs["VolumeSize"] == 20
        assert ebs["VolumeType"] == "gp3"
        assert ebs["Iops"] == 3000

    def test_create_launch_template_data_user_data(self):
        """Test launch template data creation with user data."""
        import base64

        user_data_script = "#!/bin/bash\necho 'Hello World'"
        self.aws_template.user_data = user_data_script

        data = self.manager._create_launch_template_data_legacy(self.aws_template, self.request)

        assert "UserData" in data
        decoded_user_data = base64.b64decode(data["UserData"]).decode("utf-8")
        assert decoded_user_data == user_data_script

    def test_create_launch_template_data_iam_instance_profile(self):
        """Test launch template data creation with IAM instance profile."""
        self.aws_template.instance_profile = "test-instance-profile"

        data = self.manager._create_launch_template_data_legacy(self.aws_template, self.request)

        assert "IamInstanceProfile" in data
        assert data["IamInstanceProfile"]["Name"] == "test-instance-profile"

    def test_create_launch_template_data_monitoring(self):
        """Test launch template data creation with monitoring enabled."""
        self.aws_template.monitoring_enabled = True

        data = self.manager._create_launch_template_data_legacy(self.aws_template, self.request)

        assert "Monitoring" in data
        assert data["Monitoring"]["Enabled"] is True

    def test_create_instance_tags_basic(self):
        """Test instance tags always include orb: system tags."""
        tags = self.manager._create_instance_tags(self.aws_template, self.request)

        tag_dict = {tag["Key"]: tag["Value"] for tag in tags}
        assert tag_dict["orb:request-id"] == REQUEST_ID
        assert tag_dict["orb:template-id"] == "test-template"
        assert tag_dict["orb:managed-by"] == "open-resource-broker"
        assert "Name" in tag_dict

    def test_create_instance_tags_includes_template_tags(self):
        """Test instance tags include custom tags from the template."""
        self.aws_template.tags = {"Environment": "test", "Project": "hostfactory"}

        tags = self.manager._create_instance_tags(self.aws_template, self.request)

        tag_dict = {tag["Key"]: tag["Value"] for tag in tags}
        assert tag_dict["Environment"] == "test"
        assert tag_dict["Project"] == "hostfactory"
        assert tag_dict["orb:request-id"] == REQUEST_ID
        assert tag_dict["orb:template-id"] == "test-template"

    def test_use_existing_template_strategy_found(self):
        """Test using existing template when launch_template_id is set and exists."""
        self.aws_template.launch_template_id = "lt-existing"

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

        result = self.manager._use_existing_template_strategy(self.aws_template)

        assert result.template_id == "lt-existing"
        assert result.template_name == "test-template"
        assert result.version == "$Latest"
        assert result.is_new_template is False

    def test_use_existing_template_strategy_not_found_raises(self):
        """Test that _use_existing_template_strategy raises when template is not found."""
        from botocore.exceptions import ClientError

        from orb.providers.aws.exceptions.aws_exceptions import AWSValidationError

        self.aws_template.launch_template_id = "lt-missing"

        self.mock_aws_client.ec2_client.describe_launch_templates.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "InvalidLaunchTemplateId.NotFound",
                    "Message": "Not found",
                }
            },
            operation_name="DescribeLaunchTemplates",
        )

        with pytest.raises(AWSValidationError):
            self.manager._use_existing_template_strategy(self.aws_template)

    def test_error_handling_wraps_client_error(self):
        """Test that AWS ClientError is wrapped in InfrastructureError."""
        from botocore.exceptions import ClientError

        self.mock_aws_client.ec2_client.describe_launch_templates.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "InvalidParameterValue",
                    "Message": "Invalid parameter",
                }
            },
            operation_name="DescribeLaunchTemplates",
        )

        with pytest.raises(InfrastructureError):
            self.manager.create_or_update_launch_template(self.aws_template, self.request)

        self.mock_logger.error.assert_called()

    def test_launch_template_result_creation(self):
        """Test LaunchTemplateResult creation and properties."""
        result = LaunchTemplateResult(
            template_id="lt-123",
            template_name="test-template",
            version="2",
            is_new_template=True,
            is_new_version=True,
        )

        assert result.template_id == "lt-123"
        assert result.template_name == "test-template"
        assert result.version == "2"
        assert result.is_new_template is True
        assert result.is_new_version is True

    def test_generate_client_token_is_deterministic(self):
        """Test that the same inputs always produce the same client token."""
        token1 = self.manager._generate_client_token(self.request, self.aws_template)
        token2 = self.manager._generate_client_token(self.request, self.aws_template)

        assert token1 == token2
        assert len(token1) == 32


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
