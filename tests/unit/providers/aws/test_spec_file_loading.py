"""Tests for AWS spec file loading functionality."""

import json
from unittest.mock import Mock, patch

import pytest

from application.services.native_spec_service import NativeSpecService
from domain.base.ports.configuration_port import ConfigurationPort
from providers.aws.infrastructure.services.aws_native_spec_service import (
    AWSNativeSpecService,
)


class TestSpecFileLoading:
    """Test spec file loading and resolution."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config_port = Mock(spec=ConfigurationPort)
        self.mock_native_spec_service = Mock(spec=NativeSpecService)
        self.service = AWSNativeSpecService(
            config_port=self.mock_config_port, native_spec_service=self.mock_native_spec_service
        )

    def test_load_spec_file_success(self):
        """Test successful spec file loading."""
        # Mock configuration
        self.mock_config_port.get_provider_config.return_value = {
            "provider_defaults": {
                "aws": {"extensions": {"native_spec": {"spec_file_base_path": "specs/aws"}}}
            }
        }

        # Mock file content
        spec_content = {
            "Type": "instant",
            "TargetCapacitySpecification": {"TotalTargetCapacity": "{{ requested_count }}"},
        }

        with patch("infrastructure.utilities.file.json_utils.read_json_file") as mock_read:
            mock_read.return_value = spec_content

            result = self.service._load_spec_file("examples/basic/ec2fleet-instant.json")

            assert result == spec_content
            mock_read.assert_called_once_with("specs/aws/examples/basic/ec2fleet-instant.json")

    def test_load_spec_file_with_custom_base_path(self):
        """Test spec file loading with custom base path."""
        # Mock configuration with custom base path
        self.mock_config_port.get_provider_config.return_value = {
            "provider_defaults": {
                "aws": {"extensions": {"native_spec": {"spec_file_base_path": "/opt/custom/specs"}}}
            }
        }

        spec_content = {"Type": "maintain"}

        with patch("infrastructure.utilities.file.json_utils.read_json_file") as mock_read:
            mock_read.return_value = spec_content

            result = self.service._load_spec_file("custom-template.json")

            assert result == spec_content
            mock_read.assert_called_once_with("/opt/custom/specs/custom-template.json")

    def test_load_spec_file_missing_config(self):
        """Test spec file loading with missing configuration."""
        # Mock configuration without native spec config
        self.mock_config_port.get_provider_config.return_value = {"provider_defaults": {"aws": {}}}

        spec_content = {"Type": "instant"}

        with patch("infrastructure.utilities.file.json_utils.read_json_file") as mock_read:
            mock_read.return_value = spec_content

            result = self.service._load_spec_file("test-template.json")

            # Should use default base path
            assert result == spec_content
            mock_read.assert_called_once_with("specs/aws/test-template.json")

    @patch("infrastructure.utilities.file.json_utils.read_json_file")
    def test_load_spec_file_not_found(self, mock_read):
        """Test spec file loading when file doesn't exist."""
        mock_read.side_effect = FileNotFoundError("File not found")

        self.mock_config_port.get_provider_config.return_value = {
            "provider_defaults": {
                "aws": {"extensions": {"native_spec": {"spec_file_base_path": "specs/aws"}}}
            }
        }

        with pytest.raises(FileNotFoundError):
            self.service._load_spec_file("nonexistent.json")

    @patch("infrastructure.utilities.file.json_utils.read_json_file")
    def test_load_spec_file_invalid_json(self, mock_read):
        """Test spec file loading with invalid JSON."""
        mock_read.side_effect = json.JSONDecodeError("Invalid JSON", "doc", 0)

        self.mock_config_port.get_provider_config.return_value = {
            "provider_defaults": {
                "aws": {"extensions": {"native_spec": {"spec_file_base_path": "specs/aws"}}}
            }
        }

        with pytest.raises(json.JSONDecodeError):
            self.service._load_spec_file("invalid.json")

    def test_resolve_launch_template_spec_inline(self):
        """Test resolving inline launch template spec."""
        from providers.aws.domain.template.aggregate import AWSTemplate

        inline_spec = {
            "LaunchTemplateName": "test-lt",
            "LaunchTemplateData": {"InstanceType": "t3.micro"},
        }

        template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345",
            instance_type="t3.micro",
            launch_template_spec=inline_spec,
        )

        result = self.service._resolve_launch_template_spec(template)

        assert result == inline_spec

    def test_resolve_launch_template_spec_file(self):
        """Test resolving launch template spec from file."""
        from providers.aws.domain.template.aggregate import AWSTemplate

        file_spec = {
            "LaunchTemplateName": "file-lt",
            "LaunchTemplateData": {"InstanceType": "t3.medium"},
        }

        template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345",
            instance_type="t3.micro",
            launch_template_spec_file="lt-spec.json",
        )

        with patch.object(self.service, "_load_spec_file") as mock_load:
            mock_load.return_value = file_spec

            result = self.service._resolve_launch_template_spec(template)

            assert result == file_spec
            mock_load.assert_called_once_with("lt-spec.json")

    def test_resolve_launch_template_spec_none(self):
        """Test resolving launch template spec when none specified."""
        from providers.aws.domain.template.aggregate import AWSTemplate

        template = AWSTemplate(
            template_id="test-template", image_id="ami-12345", instance_type="t3.micro"
        )

        result = self.service._resolve_launch_template_spec(template)

        assert result is None

    def test_resolve_provider_api_spec_inline(self):
        """Test resolving inline provider API spec."""
        from providers.aws.domain.template.aggregate import AWSTemplate

        inline_spec = {"Type": "instant", "TargetCapacitySpecification": {"TotalTargetCapacity": 5}}

        template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345",
            instance_type="t3.micro",
            provider_api_spec=inline_spec,
        )

        result = self.service._resolve_provider_api_spec(template)

        assert result == inline_spec

    def test_resolve_provider_api_spec_file(self):
        """Test resolving provider API spec from file."""
        from providers.aws.domain.template.aggregate import AWSTemplate

        file_spec = {"Type": "maintain", "TargetCapacitySpecification": {"TotalTargetCapacity": 10}}

        template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345",
            instance_type="t3.micro",
            provider_api_spec_file="api-spec.json",
        )

        with patch.object(self.service, "_load_spec_file") as mock_load:
            mock_load.return_value = file_spec

            result = self.service._resolve_provider_api_spec(template)

            assert result == file_spec
            mock_load.assert_called_once_with("api-spec.json")

    def test_resolve_provider_api_spec_none(self):
        """Test resolving provider API spec when none specified."""
        from providers.aws.domain.template.aggregate import AWSTemplate

        template = AWSTemplate(
            template_id="test-template", image_id="ami-12345", instance_type="t3.micro"
        )

        result = self.service._resolve_provider_api_spec(template)

        assert result is None

    def test_spec_file_path_construction(self):
        """Test spec file path construction with various configurations."""
        test_cases = [
            {"base_path": "specs/aws", "file_name": "test.json", "expected": "specs/aws/test.json"},
            {
                "base_path": "/opt/specs",
                "file_name": "subdir/test.json",
                "expected": "/opt/specs/subdir/test.json",
            },
            {
                "base_path": "relative/path",
                "file_name": "deep/nested/file.json",
                "expected": "relative/path/deep/nested/file.json",
            },
        ]

        for case in test_cases:
            self.mock_config_port.get_provider_config.return_value = {
                "provider_defaults": {
                    "aws": {
                        "extensions": {"native_spec": {"spec_file_base_path": case["base_path"]}}
                    }
                }
            }

            with patch("infrastructure.utilities.file.json_utils.read_json_file") as mock_read:
                mock_read.return_value = {"test": "data"}

                self.service._load_spec_file(case["file_name"])

                mock_read.assert_called_once_with(case["expected"])

    def test_spec_file_caching_behavior(self):
        """Test that spec files are loaded fresh each time (no caching)."""
        self.mock_config_port.get_provider_config.return_value = {
            "provider_defaults": {
                "aws": {"extensions": {"native_spec": {"spec_file_base_path": "specs/aws"}}}
            }
        }

        spec_content = {"Type": "instant"}

        with patch("infrastructure.utilities.file.json_utils.read_json_file") as mock_read:
            mock_read.return_value = spec_content

            # Load same file twice
            result1 = self.service._load_spec_file("test.json")
            result2 = self.service._load_spec_file("test.json")

            assert result1 == spec_content
            assert result2 == spec_content
            # Should be called twice (no caching)
            assert mock_read.call_count == 2

    def test_spec_file_with_yaml_extension(self):
        """Test spec file loading with YAML extension."""
        self.mock_config_port.get_provider_config.return_value = {
            "provider_defaults": {
                "aws": {"extensions": {"native_spec": {"spec_file_base_path": "specs/aws"}}}
            }
        }

        # Note: This test assumes YAML support is added to the file utilities
        with patch("infrastructure.utilities.file.json_utils.read_json_file") as mock_read:
            mock_read.return_value = {"Type": "maintain"}

            result = self.service._load_spec_file("test.yaml")

            assert result["Type"] == "maintain"
            mock_read.assert_called_once_with("specs/aws/test.yaml")

    def test_spec_resolution_priority(self):
        """Test that inline specs take priority over file specs."""
        from providers.aws.domain.template.aggregate import AWSTemplate

        # This should not be possible due to validation, but test the resolution logic
        inline_spec = {"Type": "instant"}

        template = AWSTemplate(
            template_id="test-template",
            image_id="ami-12345",
            instance_type="t3.micro",
            provider_api_spec=inline_spec,
        )

        # Even if file is specified (which validation prevents), inline should win
        result = self.service._resolve_provider_api_spec(template)

        assert result == inline_spec
