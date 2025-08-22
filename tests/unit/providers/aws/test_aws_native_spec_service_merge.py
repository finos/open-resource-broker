"""Tests for AWSNativeSpecService merge functionality."""

import pytest
from unittest.mock import Mock, patch
from providers.aws.infrastructure.services.aws_native_spec_service import AWSNativeSpecService
from providers.aws.domain.template.aggregate import AWSTemplate
from domain.request.aggregate import Request


class TestAWSNativeSpecServiceMerge:
    """Test merge functionality in AWSNativeSpecService."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_native_spec_service = Mock()
        self.mock_config_port = Mock()
        self.service = AWSNativeSpecService(self.mock_native_spec_service, self.mock_config_port)

    def test_merge_mode_replace(self):
        """Test replace mode returns only native spec."""
        # Setup
        self.mock_native_spec_service.is_native_spec_enabled.return_value = True
        self.mock_native_spec_service.render_spec.return_value = {
            "TargetCapacitySpecification": {"TotalTargetCapacity": 10}
        }
        self.mock_config_port.get_native_spec_config.return_value = {
            "enabled": True, 
            "merge_mode": "replace"
        }
        
        # Mock template with native spec
        template = Mock()
        template.provider_api_spec = {"TargetCapacitySpecification": {"TotalTargetCapacity": 10}}
        template.provider_api_spec_file = None
        
        request = Mock()
        context = {"test": "context"}
        
        # Test
        result = self.service.process_provider_api_spec_with_merge(
            template, request, "ec2fleet", context
        )
        
        # Verify
        assert result == {"TargetCapacitySpecification": {"TotalTargetCapacity": 10}}
        self.mock_native_spec_service.render_spec.assert_called_once()

    def test_merge_mode_merge(self):
        """Test merge mode combines default template with native spec."""
        # Setup
        self.mock_native_spec_service.is_native_spec_enabled.return_value = True
        self.mock_native_spec_service.render_spec.return_value = {
            "TargetCapacitySpecification": {"TotalTargetCapacity": 10},
            "SpotOptions": {"AllocationStrategy": "diversified"}
        }
        self.mock_config_port.get_native_spec_config.return_value = {
            "enabled": True, 
            "merge_mode": "merge"
        }
        
        # Mock render_default_spec
        default_spec = {
            "LaunchTemplateConfigs": [{"LaunchTemplateSpecification": {}}],
            "TargetCapacitySpecification": {"TotalTargetCapacity": 5, "DefaultTargetCapacityType": "on-demand"},
            "Type": "maintain"
        }
        self.service.render_default_spec = Mock(return_value=default_spec)
        
        # Mock template with native spec
        template = Mock()
        template.provider_api_spec = {"TargetCapacitySpecification": {"TotalTargetCapacity": 10}}
        template.provider_api_spec_file = None
        
        request = Mock()
        context = {"test": "context"}
        
        # Test
        result = self.service.process_provider_api_spec_with_merge(
            template, request, "ec2fleet", context
        )
        
        # Verify merge happened correctly
        assert result["TargetCapacitySpecification"]["TotalTargetCapacity"] == 10  # From native spec
        assert result["TargetCapacitySpecification"]["DefaultTargetCapacityType"] == "on-demand"  # From default
        assert result["Type"] == "maintain"  # From default
        assert result["LaunchTemplateConfigs"] == [{"LaunchTemplateSpecification": {}}]  # From default
        assert result["SpotOptions"]["AllocationStrategy"] == "diversified"  # From native spec

    def test_merge_mode_disabled_native_spec(self):
        """Test returns None when native spec is disabled."""
        self.mock_native_spec_service.is_native_spec_enabled.return_value = False
        
        result = self.service.process_provider_api_spec_with_merge(
            Mock(), Mock(), "ec2fleet", {}
        )
        
        assert result is None

    def test_merge_mode_no_native_spec(self):
        """Test returns None when no native spec provided."""
        self.mock_native_spec_service.is_native_spec_enabled.return_value = True
        
        # Mock template without native spec
        template = Mock()
        template.provider_api_spec = None
        template.provider_api_spec_file = None
        
        result = self.service.process_provider_api_spec_with_merge(
            template, Mock(), "ec2fleet", {}
        )
        
        assert result is None

    def test_merge_mode_fallback_to_replace(self):
        """Test fallback to replace behavior for unknown merge mode."""
        # Setup
        self.mock_native_spec_service.is_native_spec_enabled.return_value = True
        self.mock_native_spec_service.render_spec.return_value = {
            "TargetCapacitySpecification": {"TotalTargetCapacity": 10}
        }
        self.mock_config_port.get_native_spec_config.return_value = {
            "enabled": True, 
            "merge_mode": "unknown_mode"
        }
        
        # Mock template with native spec
        template = Mock()
        template.provider_api_spec = {"TargetCapacitySpecification": {"TotalTargetCapacity": 10}}
        template.provider_api_spec_file = None
        
        request = Mock()
        context = {"test": "context"}
        
        # Test
        result = self.service.process_provider_api_spec_with_merge(
            template, request, "ec2fleet", context
        )
        
        # Should fallback to replace behavior
        assert result == {"TargetCapacitySpecification": {"TotalTargetCapacity": 10}}

    def test_merge_mode_with_spec_file(self):
        """Test merge mode works with spec file instead of inline spec."""
        # Setup
        self.mock_native_spec_service.is_native_spec_enabled.return_value = True
        self.mock_native_spec_service.render_spec.return_value = {
            "SpotOptions": {"AllocationStrategy": "diversified"}
        }
        self.mock_config_port.get_native_spec_config.return_value = {
            "enabled": True, 
            "merge_mode": "merge"
        }
        
        # Mock render_default_spec
        default_spec = {"Type": "maintain"}
        self.service.render_default_spec = Mock(return_value=default_spec)
        
        # Mock _load_spec_file
        with patch.object(self.service, '_load_spec_file') as mock_load:
            mock_load.return_value = {"SpotOptions": {"AllocationStrategy": "diversified"}}
            
            # Mock template with spec file
            template = Mock()
            template.provider_api_spec = None
            template.provider_api_spec_file = "spec.json"
            
            request = Mock()
            context = {"test": "context"}
            
            # Test
            result = self.service.process_provider_api_spec_with_merge(
                template, request, "ec2fleet", context
            )
            
            # Verify merge happened
            assert result["Type"] == "maintain"  # From default
            assert result["SpotOptions"]["AllocationStrategy"] == "diversified"  # From spec file
