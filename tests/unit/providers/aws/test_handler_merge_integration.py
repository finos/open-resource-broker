"""Tests for handler integration with merge functionality."""

import pytest
from unittest.mock import Mock, patch
from providers.aws.infrastructure.handlers.ec2_fleet_handler import EC2FleetHandler
from providers.aws.infrastructure.handlers.spot_fleet_handler import SpotFleetHandler


class TestHandlerMergeIntegration:
    """Test handler integration with merge functionality."""

    def _create_mock_template(self):
        """Create properly mocked template."""
        template = Mock()
        template.tags = {}
        template.template_id = "test-template"
        template.instance_types = []
        template.subnet_ids = []
        template.image_id = "ami-123"
        template.security_group_ids = []
        return template

    def _create_mock_request(self):
        """Create properly mocked request."""
        request = Mock()
        request.request_id = "req-123"
        return request

    def test_ec2fleet_handler_uses_merge_method(self):
        """Test EC2FleetHandler uses new merge method."""
        mock_aws_client = Mock()
        mock_logger = Mock()
        mock_aws_ops = Mock()
        mock_launch_template_manager = Mock()
        
        handler = EC2FleetHandler(
            mock_aws_client, mock_logger, mock_aws_ops, mock_launch_template_manager
        )
        
        mock_native_service = Mock()
        mock_native_service.process_provider_api_spec_with_merge.return_value = {
            "LaunchTemplateConfigs": [{"LaunchTemplateSpecification": {"LaunchTemplateId": "lt-123", "Version": "1"}}],
            "TargetCapacitySpecification": {"TotalTargetCapacity": 10},
            "Type": "maintain"
        }
        handler.aws_native_spec_service = mock_native_service
        
        template = self._create_mock_template()
        request = self._create_mock_request()
        
        result = handler._create_fleet_config(template, request, "lt-123", "1")
        
        mock_native_service.process_provider_api_spec_with_merge.assert_called_once()
        assert result["LaunchTemplateConfigs"][0]["LaunchTemplateSpecification"]["LaunchTemplateId"] == "lt-123"
        assert result["LaunchTemplateConfigs"][0]["LaunchTemplateSpecification"]["Version"] == "1"

    def test_spotfleet_handler_uses_merge_method(self):
        """Test SpotFleetHandler uses new merge method."""
        mock_aws_client = Mock()
        mock_logger = Mock()
        mock_aws_ops = Mock()
        mock_launch_template_manager = Mock()
        
        handler = SpotFleetHandler(
            mock_aws_client, mock_logger, mock_aws_ops, mock_launch_template_manager
        )
        
        mock_native_service = Mock()
        mock_native_service.process_provider_api_spec_with_merge.return_value = {
            "LaunchSpecifications": [{"LaunchTemplate": {}}],
            "TargetCapacity": 5,
            "AllocationStrategy": "diversified"
        }
        handler.aws_native_spec_service = mock_native_service
        
        template = self._create_mock_template()
        request = self._create_mock_request()
        
        result = handler._create_spot_fleet_config(template, request, "lt-123", "1")
        
        mock_native_service.process_provider_api_spec_with_merge.assert_called_once()
        for spec in result["LaunchSpecifications"]:
            assert spec["LaunchTemplate"]["LaunchTemplateId"] == "lt-123"
            assert spec["LaunchTemplate"]["Version"] == "1"

    def test_handler_fallback_when_no_merge_result(self):
        """Test handler falls back to default template when merge returns None."""
        mock_aws_client = Mock()
        mock_logger = Mock()
        mock_aws_ops = Mock()
        mock_launch_template_manager = Mock()
        
        handler = EC2FleetHandler(
            mock_aws_client, mock_logger, mock_aws_ops, mock_launch_template_manager
        )
        
        mock_native_service = Mock()
        mock_native_service.process_provider_api_spec_with_merge.return_value = None
        mock_native_service.render_default_spec.return_value = {
            "LaunchTemplateConfigs": [{"LaunchTemplateSpecification": {}}],
            "Type": "maintain"
        }
        handler.aws_native_spec_service = mock_native_service
        
        template = self._create_mock_template()
        request = self._create_mock_request()
        
        result = handler._create_fleet_config(template, request, "lt-123", "1")
        
        mock_native_service.process_provider_api_spec_with_merge.assert_called_once()
        mock_native_service.render_default_spec.assert_called_once()
        assert result["Type"] == "maintain"

    def test_handler_without_native_service(self):
        """Test handler behavior when native service is not available."""
        mock_aws_client = Mock()
        mock_logger = Mock()
        mock_aws_ops = Mock()
        mock_launch_template_manager = Mock()
        
        handler = EC2FleetHandler(
            mock_aws_client, mock_logger, mock_aws_ops, mock_launch_template_manager
        )
        handler.aws_native_spec_service = None
        
        template = self._create_mock_template()
        request = self._create_mock_request()
        
        with patch.object(handler, '_create_fleet_config_legacy') as mock_legacy:
            mock_legacy.return_value = {"Type": "legacy"}
            
            result = handler._create_fleet_config(template, request, "lt-123", "1")
            
            mock_legacy.assert_called_once_with(template, request, "lt-123", "1")
            assert result["Type"] == "legacy"
