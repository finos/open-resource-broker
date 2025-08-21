"""Tests for AWS native spec service package context."""

from unittest.mock import Mock

from domain.request.request import Request
from domain.request.value_objects import RequestId
from providers.aws.domain.template import AWSTemplate
from providers.aws.infrastructure.services.aws_native_spec_service import (
    AWSNativeSpecService,
)


class TestAWSNativeSpecPackageContext:
    """Test package context in AWS native spec service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config_port = Mock()
        self.mock_native_spec_service = Mock()
        self.service = AWSNativeSpecService(
            config_port=self.mock_config_port, native_spec_service=self.mock_native_spec_service
        )

    def test_build_aws_context_includes_package_info(self):
        """Test that AWS context includes package information."""
        # Arrange
        self.mock_config_port.get_package_info.return_value = {
            "name": "open-hostfactory-plugin",
            "version": "1.0.0",
        }

        template = AWSTemplate(
            template_id="test-template", image_id="ami-12345", instance_type="t3.micro"
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=2, template_id="test-template"
        )

        # Act
        context = self.service._build_aws_context(template, request)

        # Assert
        assert context["package_name"] == "open-hostfactory-plugin"
        assert context["package_version"] == "1.0.0"
        assert context["request_id"] == str(request.request_id)
        assert context["requested_count"] == 2
        assert context["template_id"] == "test-template"

    def test_build_aws_context_package_info_fallback(self):
        """Test fallback when package info is unavailable."""
        # Arrange
        self.mock_config_port.get_package_info.side_effect = Exception("Package info unavailable")

        template = AWSTemplate(
            template_id="test-template", image_id="ami-12345", instance_type="t3.micro"
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=1, template_id="test-template"
        )

        # Act
        context = self.service._build_aws_context(template, request)

        # Assert - should use fallback values
        assert context["package_name"] == "open-hostfactory-plugin"
        assert context["package_version"] == "unknown"

    def test_build_aws_context_partial_package_info(self):
        """Test context with partial package info."""
        # Arrange
        self.mock_config_port.get_package_info.return_value = {
            "name": "custom-plugin"
            # Missing version
        }

        template = AWSTemplate(
            template_id="test-template", image_id="ami-12345", instance_type="t3.micro"
        )

        request = Request(
            request_id=RequestId.generate(), requested_count=1, template_id="test-template"
        )

        # Act
        context = self.service._build_aws_context(template, request)

        # Assert
        assert context["package_name"] == "custom-plugin"
        assert context["package_version"] == "unknown"  # fallback for missing version
